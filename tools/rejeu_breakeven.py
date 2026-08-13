"""Coût réel d'un breakeven plus bas — rejeu barre à barre des trades clos.

    python tools/rejeu_breakeven.py
    python tools/rejeu_breakeven.py --json out.json

Tâche Prime 65541466. `tools/contrefactuel_breakeven.py` donne une BORNE
SUPÉRIEURE : il crédite les stops évités sans débiter les gagnants qu'un
breakeven plus bas aurait coupés. Le journal donne `mae_r` mais pas sa
position dans le temps — or tout dépend de l'ordre. Un creux ATTEINT AVANT
que le breakeven ne s'arme est sans effet ; le même creux APRÈS coupe le
trade à zéro.

CE QUE FAIT CE REJEU
---------------------
Pour chaque trade clos, on recharge les barres M15 réellement cotées entre
l'ouverture et la sortie, et on les fait passer par `decide_new_sl` — la
FONCTION DU MOTEUR, pas une réimplémentation. Le seul paramètre qui change
d'un scénario à l'autre est `breakeven_r`. Tout écart mesuré est donc
imputable au seuil, et à rien d'autre.

L'AMBIGUÏTÉ INTRA-BARRE, ET COMMENT ELLE EST TRAITÉE
-----------------------------------------------------
Une barre M15 donne O/H/L/C mais pas l'ordre dans lequel le haut et le bas ont
été touchés. Selon l'ordre retenu, un même trade peut sortir au breakeven ou
courir jusqu'au trailing. Plutôt que de choisir en silence, on rejoue les DEUX
ordres et on publie l'intervalle :

    défavorable   l'extrême adverse d'abord — le pire cas pour le trade
    favorable     l'extrême favorable d'abord — le meilleur cas

Le vrai chiffre est entre les deux. Si les deux bornes s'accordent sur le
signe, la conclusion tient ; sinon elle ne tient pas, et le dire est le
résultat.

Aucun seuil n'est modifié. Lecture seule, aucun ordre.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

EXCURSIONS = RACINE / "results" / "excursions.ndjson"

#: Seuils de breakeven testés, du plus agressif à l'actuel.
SEUILS = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80)

#: Marge de barres ajoutée autour de la fenêtre du trade, pour absorber les
#: décalages d'horodatage entre le journal (UTC) et le serveur du courtier.
MARGE_BARRES = 8


@dataclass
class Trade:
    symbol: str
    side: int
    entry: float
    sl_initial: float
    tp_initial: float
    r_unit: float
    ts_open: datetime
    ts_exit: datetime
    pnl_r: float
    mae_r: float
    mfe_r: float
    exit_reason: str
    context: str


def _dt(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


#: Seule convention d'horloge qu'un rejeu peut croiser avec des barres.
#:
#: Les lignes écrites avant le 12/08/2026 portent l'heure du SERVEUR (UTC+3
#: chez ce courtier) tout en étant étiquetées « +00:00 ». Avant le correctif
#: `get_rates`, les barres l'étaient aussi : les deux erreurs s'annulaient et
#: le croisement paraissait juste. Depuis que les barres sont en vrai UTC, la
#: fenêtre de rejeu d'une ligne ancienne s'étend TROIS HEURES au-delà de la
#: sortie réelle — un trade stoppé peut y survivre jusqu'au trailing, et la
#: fidélité du rejeu passe de +0,105R à +0,32R (mesure Prime, 0f452c5).
#:
#: On refuse donc ce qu'on ne peut pas interpréter, plutôt que de croiser en
#: silence deux horloges différentes.
HORLOGE_ACCEPTEE = "utc"


def charger(chemin: Path) -> tuple[list[Trade], int]:
    """Trades rejouables, et le nombre de lignes refusées pour cause d'horloge.

    Ne lève jamais.
    """
    out: list[Trade] = []
    refuses_horloge = 0
    if not chemin.exists():
        return out, 0
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        try:
            d = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        # Le refus d'horloge passe AVANT toute autre validation : une ligne
        # dont on ne sait pas lire le temps n'a pas à être examinée plus loin.
        if str(d.get("horloge") or "") != HORLOGE_ACCEPTEE:
            refuses_horloge += 1
            continue
        ouv, sor = _dt(d.get("ts_open")), _dt(d.get("ts_exit"))
        r = d.get("r_unit")
        if not (ouv and sor and isinstance(r, (int, float)) and r > 0):
            continue
        if sor <= ouv:
            continue
        try:
            out.append(Trade(
                symbol=str(d["symbol"]), side=int(d["side"]),
                entry=float(d["entry"]), sl_initial=float(d["sl_initial"]),
                tp_initial=float(d.get("tp_initial") or 0.0),
                r_unit=float(r), ts_open=ouv, ts_exit=sor,
                pnl_r=float(d["pnl_r"]), mae_r=float(d.get("mae_r") or 0.0),
                mfe_r=float(d.get("mfe_r") or 0.0),
                exit_reason=str(d.get("exit_reason") or ""),
                context=str(d.get("context") or ""),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out, refuses_horloge


def barres_du_trade(t: Trade, lecteur) -> list | None:
    """Barres M15 couvrant la vie du trade, marge comprise. None si indisponible.

    Le nombre de barres à demander se compte depuis MAINTENANT jusqu'à
    l'ouverture, pas sur la DURÉE du trade. Première version de cet outil :
    `besoin = durée du trade` — un trade court d'avant-hier était alors
    demandé sur 120 barres, soit 30 heures, qui ne remontaient pas jusqu'à
    lui. Douze trades sur trente-sept ont été écartés pour cette raison, et
    c'étaient les plus gros gagnants : le biais aurait exactement inversé la
    conclusion de cet outil.
    """
    maintenant = datetime.now(timezone.utc)
    anciennete = max(0.0, (maintenant - t.ts_open).total_seconds())
    besoin = int(anciennete // 900) + 2 * MARGE_BARRES + 8
    try:
        # ×1.4 : les barres n'existent pas le week-end ni hors séance, donc
        # « N barres en arrière » couvre moins de N×15 minutes calendaires.
        df = lecteur(t.symbol, "M15", max(240, min(20000, int(besoin * 1.4))))
    except Exception:  # noqa: BLE001
        return None
    if df is None or len(df) == 0:
        return None
    debut = t.ts_open - timedelta(minutes=15 * MARGE_BARRES)
    fin = t.ts_exit + timedelta(minutes=15 * MARGE_BARRES)
    try:
        fenetre = df[(df.index >= debut) & (df.index <= fin)]
    except TypeError:
        return None
    if len(fenetre) < 2:
        return None
    # La fenêtre doit COMMENCER avant l'ouverture. Sinon les premières barres
    # du trade manquent — et ce sont elles qui décident si le breakeven s'arme.
    if df.index[0] > t.ts_open:
        return None
    return fenetre


def _chemin_intra(bar, side: int, adverse_dabord: bool) -> list[float]:
    """Suite de prix parcourus dans une barre, selon l'ordre postulé."""
    o, h, l, c = (float(bar["open"]), float(bar["high"]),
                  float(bar["low"]), float(bar["close"]))
    extreme_adverse, extreme_favorable = (l, h) if side > 0 else (h, l)
    if adverse_dabord:
        return [o, extreme_adverse, extreme_favorable, c]
    return [o, extreme_favorable, extreme_adverse, c]


def rejouer(t: Trade, barres, seuil: float, *, adverse_dabord: bool) -> dict:
    """Rejoue un trade avec `breakeven_r = seuil`. Rend le résultat en R.

    Le SL est celui du journal, puis déplacé par `decide_new_sl` — la vraie
    fonction du moteur. Aucune règle n'est réécrite ici.
    """
    from titanium.execution.position_manager import (
        ManageParams, PositionSnapshot, TrackedState, decide_new_sl,
    )

    params = ManageParams(breakeven_r=seuil)
    etat = TrackedState(r=t.r_unit, symbol=t.symbol, side=t.side,
                        entry=t.entry, sl_initial=t.sl_initial,
                        tp_initial=t.tp_initial)
    sl = t.sl_initial
    tp = t.tp_initial or None

    for _, bar in barres.iterrows():
        for prix in _chemin_intra(bar, t.side, adverse_dabord):
            # 1) Le stop se déclenche AVANT toute décision : le courtier ne
            #    demande la permission à personne.
            if t.side * (prix - sl) <= 0:
                return {"pnl_r": (sl - t.entry) / t.r_unit * t.side,
                        "sortie": "stop", "phase": etat.phase}
            if tp is not None and t.side * (prix - tp) >= 0:
                return {"pnl_r": (tp - t.entry) / t.r_unit * t.side,
                        "sortie": "tp", "phase": etat.phase}
            # 2) Puis le moteur décide, exactement comme en production.
            pos = PositionSnapshot(
                ticket="rejeu", symbol=t.symbol, side=t.side, entry=t.entry,
                current=prix, sl=sl, tp=tp, digits=8,
                min_stop_distance=0.0, spread=0.0)
            d = decide_new_sl(pos, etat, params)
            if d.new_sl is not None:
                sl = d.new_sl

    # Les barres s'épuisent avant la sortie réelle : on solde au dernier cours.
    dernier = float(barres.iloc[-1]["close"])
    return {"pnl_r": (dernier - t.entry) / t.r_unit * t.side,
            "sortie": "fin_barres", "phase": etat.phase}


def _fmt_r(x: float) -> str:
    return f"{x:+7.4f}R"


def analyser(trades: list[Trade], lecteur) -> dict:
    """Rejoue tous les trades pour tous les seuils, dans les deux ordres."""
    rejouables: list[Trade] = []
    barres_par_trade = {}
    ignores: list[tuple[str, str]] = []

    for i, t in enumerate(trades):
        b = barres_du_trade(t, lecteur)
        if b is None:
            ignores.append((t.symbol, "barres indisponibles"))
            continue
        rejouables.append(t)
        barres_par_trade[i] = b

    resultats: dict[float, dict[str, list[float]]] = {}
    for seuil in SEUILS:
        for ordre, adverse in (("defavorable", True), ("favorable", False)):
            rs = []
            for i, t in enumerate(trades):
                if i not in barres_par_trade:
                    continue
                rs.append(rejouer(t, barres_par_trade[i], seuil,
                                  adverse_dabord=adverse)["pnl_r"])
            resultats.setdefault(seuil, {})[ordre] = rs

    return {"resultats": resultats, "n": len(rejouables),
            "ignores": ignores, "reels": [t.pnl_r for t in rejouables],
            "trades": rejouables, "barres": barres_par_trade,
            "index": {id(t): i for i, t in enumerate(trades)}}


def rapport(a: dict, trades: list[Trade]) -> str:
    out: list[str] = []
    w = out.append
    n = a["n"]
    reels = a["reels"]

    w(f"TRADES REJOUÉS : {n} / {len(trades)}")
    if a["ignores"]:
        w(f"  {len(a['ignores'])} écartés faute de barres : "
          + ", ".join(sorted({s for s, _ in a['ignores']}))[:70])
    w("")
    esp_reelle = sum(reels) / n if n else 0.0
    w(f"ESPÉRANCE RÉELLE (journal, mêmes trades)  {_fmt_r(esp_reelle)}"
      f"   cumul {sum(reels):+8.3f}R")
    w("")

    # Contrôle de fidélité : à 0.80, le rejeu doit retrouver le réel. S'il en
    # est loin, le modèle est faux et TOUS les autres chiffres le sont aussi.
    ref = a["resultats"].get(0.80, {})
    w("FIDÉLITÉ DU REJEU — à 0.80 (le seuil réellement appliqué)")
    for ordre in ("defavorable", "favorable"):
        rs = ref.get(ordre) or []
        if rs:
            e = sum(rs) / len(rs)
            w(f"  ordre {ordre:<12} rejeu {_fmt_r(e)}   "
              f"écart au réel {e - esp_reelle:+.4f}R")
    w("")

    w("ESPÉRANCE PAR SEUIL — intervalle dû à l'ordre intra-barre")
    w(f"  {'BE armé à':>10}  {'défavorable':>13}  {'favorable':>13}   "
      f"{'écart vs actuel':>17}")
    w("  " + "-" * 62)
    base = None
    for seuil in SEUILS:
        d = a["resultats"][seuil]
        ed = sum(d["defavorable"]) / len(d["defavorable"])
        ef = sum(d["favorable"]) / len(d["favorable"])
        if seuil == 0.80:
            base = (ed, ef)
        marque = "  <- actuel" if seuil == 0.80 else ""
        if base and seuil != 0.80:
            # Écart pire-cas : borne basse du candidat contre borne haute de
            # l'actuel. C'est le gain qu'on peut affirmer sans se tromper.
            pire = ed - base[1]
            ecart = f"{pire:+.4f}R (pire cas)"
        else:
            ecart = ""
        w(f"  {seuil:>10.2f}  {_fmt_r(ed):>13}  {_fmt_r(ef):>13}   "
          f"{ecart:>17}{marque}")
    w("")

    # Le cœur de la tâche : QUI a été coupé, et pour combien.
    w("GAGNANTS COUPÉS PAR UN SEUIL PLUS BAS (ordre défavorable)")
    w("  Un gagnant est « coupé » si le rejeu à ce seuil le sort au breakeven")
    w("  alors que le rejeu à 0.80 le laissait courir.")
    w("")
    idx = {t.symbol + str(t.ts_open): k for k, t in enumerate(trades)}
    for seuil in SEUILS:
        if seuil == 0.80:
            continue
        coupes, perdu, sauves, gagne = [], 0.0, 0, 0.0
        rs_c = a["resultats"][seuil]["defavorable"]
        rs_b = a["resultats"][0.80]["defavorable"]
        k = 0
        for t in trades:
            if idx.get(t.symbol + str(t.ts_open)) is None:
                continue
            if k >= len(rs_c):
                break
            avant, apres = rs_b[k], rs_c[k]
            if apres < avant - 1e-6:
                coupes.append((t.symbol, avant, apres))
                perdu += avant - apres
            elif apres > avant + 1e-6:
                sauves += 1
                gagne += apres - avant
            k += 1
        net = gagne - perdu
        w(f"  BE {seuil:.2f} : {sauves} stop(s) évité(s) {gagne:+.3f}R  ·  "
          f"{len(coupes)} gagnant(s) coupé(s) {-perdu:+.3f}R  ·  "
          f"NET {net:+.3f}R")
        for sym, av, ap in coupes[:4]:
            w(f"        coupé : {sym:<10} {av:+.3f}R -> {ap:+.3f}R")
    w("")

    # Robustesse : un net positif porté par un seul trade n'est pas un
    # résultat, c'est une anecdote. On retire le trade le plus influent.
    w("ROBUSTESSE — le net tient-il sans son trade le plus influent ?")
    for seuil in SEUILS:
        if seuil == 0.80:
            continue
        rs_c = a["resultats"][seuil]["defavorable"]
        rs_b = a["resultats"][0.80]["defavorable"]
        deltas = [c - b for c, b in zip(rs_c, rs_b)]
        net = sum(deltas)
        if not deltas:
            continue
        # Le trade dont le retrait dégrade le plus le net.
        pire = max(deltas)
        net_sans = net - pire
        verdict = "tient" if (net > 0) == (net_sans > 0) else "NE TIENT PAS"
        w(f"  BE {seuil:.2f} : net {net:+7.3f}R  ·  sans le plus influent "
          f"{net_sans:+7.3f}R  ·  {verdict}")
    w("")

    # Verdict, borné par l'ambiguïté intra-barre.
    w("VERDICT")
    meilleur = min(SEUILS, key=lambda s: -(
        sum(a["resultats"][s]["defavorable"]) / n))
    ed_m = sum(a["resultats"][meilleur]["defavorable"]) / n
    ef_m = sum(a["resultats"][meilleur]["favorable"]) / n
    ed_b = sum(a["resultats"][0.80]["defavorable"]) / n
    ef_b = sum(a["resultats"][0.80]["favorable"]) / n
    if ed_m > ef_b:
        w(f"  Le seuil {meilleur:.2f} bat l'actuel même en comparant sa BORNE")
        w(f"  BASSE ({ed_m:+.4f}R) à la BORNE HAUTE de l'actuel ({ef_b:+.4f}R).")
        w("  La conclusion résiste à l'ambiguïté intra-barre.")
    elif ed_m > ed_b and ef_m > ef_b:
        w(f"  Le seuil {meilleur:.2f} domine l'actuel dans les DEUX ordres,")
        w(f"  mais les intervalles se chevauchent — la conclusion dépend en")
        w("  partie d'une hypothèse invérifiable sur l'intérieur des barres.")
    else:
        w("  Aucun seuil ne domine l'actuel de façon robuste. Ne rien changer.")
    w("")
    w(f"  Échantillon : {n} trades. Aucun seuil n'a été modifié par ce script.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    from titanium.data.mt5_vendor import get_rates, mt5_session

    trades, refuses = charger(EXCURSIONS)
    if refuses:
        print(f"{refuses} ligne(s) REFUSÉE(S) : horloge ≠ « {HORLOGE_ACCEPTEE} ».")
        print("  Écrites avant le 12/08/2026, elles portent l'heure du serveur")
        print("  (UTC+3) sous une étiquette « +00:00 ». Les croiser avec des")
        print("  barres en vrai UTC décale la fenêtre de trois heures.")
    if not trades:
        print()
        print(f"AUCUN trade rejouable dans {EXCURSIONS}.")
        if refuses:
            print("Toutes les lignes disponibles sont d'avant le correctif")
            print("d'horloge. Le rejeu redeviendra possible quand assez de")
            print("clôtures marquées « utc » se seront accumulées — et pas")
            print("avant : un chiffre calculé sur ces lignes serait faux.")
        return 1
    print(f"{len(trades)} trade(s) rejouable(s) · chargement des barres M15…")

    with mt5_session():
        res = analyser(trades, get_rates)

    if not res["n"]:
        print("aucune barre disponible : rejeu impossible")
        return 1

    txt = rapport(res, trades)
    if a.json:
        Path(a.json).write_text(json.dumps({
            "rapport": txt, "n": res["n"],
            "resultats": {str(k): v for k, v in res["resultats"].items()},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
