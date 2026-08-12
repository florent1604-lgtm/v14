"""Mesure G4/G5 sur données réelles — et ce que l'ICT y ajouterait.

    python tools/mesure_piliers_ict.py              # tout le catalogue
    python tools/mesure_piliers_ict.py --limite 40  # échantillon
    python tools/mesure_piliers_ict.py --json out.json

Mission Prime ba74e58a. Deux questions, deux mesures :

1. POURQUOI G4 et G5 échouent-ils ? Le taux global ne dit rien : « 91,6 % de
   manquants » peut vouloir dire « le détecteur est cassé » ou « la condition
   est rarement remplie, et c'est normal ». On décompose donc chaque échec en
   sa cause nommée, au même endroit du code que la porte.

2. Les modules ICT alimenteraient-ils vraiment ces piliers ? On rejoue, sur
   les MÊMES barres, ce que `ict_structure` répondrait. La colonne « ICT
   ajouterait » ne compte que les setups où le pilier échoue AUJOURD'HUI et
   où l'ICT fournirait le signal manquant, dans le bon sens.

Aucun ordre, aucune écriture d'état. Lecture seule.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

# Console Windows en cp1252 : la mesure ne doit pas mourir sur un accent.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# DÉCOMPOSITION — rejoue la logique de la porte, étage par étage
# ═══════════════════════════════════════════════════════════════════════════════

def cause_g4(ltf, prix: float, fib_ctx: dict, side: int) -> str:
    """Pourquoi G4 (ote_ob) n'est pas passé. Miroir de `builder._ote_ob`."""
    from titanium.features import smc

    if not fib_ctx.get("available"):
        return "fib indisponible"
    if not fib_ctx.get("in_ote"):
        return "prix hors golden zone"
    if fib_ctx.get("invalidated"):
        return "golden zone invalidée"
    direction = int(fib_ctx.get("direction") or 0)
    if direction == 0:
        return "direction fib nulle"
    cote = smc.BUY if direction > 0 else smc.SELL
    aligne, statut, _q = smc.has_ob_or_fvg_alignment(ltf, cote, prix)
    if not aligne:
        return "aucun OB/FVG aligné"
    if statut not in ("intact", "tested", "fvg"):
        return f"OB statut « {statut} »"
    if direction != side:
        return "fib contre le setup"
    return "PASSE"


def cause_g5(cndl: dict, side: int) -> str:
    """Pourquoi G5 (candle) n'est pas passé. Miroir de `candlesticks`."""
    direction = int(cndl.get("direction") or 0)
    if not cndl.get("patterns"):
        if cndl.get("pending_confirmation"):
            return "motif en attente de confirmation"
        return "aucun motif détecté"
    if direction == 0:
        return "motifs contradictoires (net nul)"
    if direction != side:
        return "motif contre le setup"
    return "PASSE"


# ═══════════════════════════════════════════════════════════════════════════════
# CONTREFACTUEL ICT — sur les mêmes barres, que dirait `ict_structure` ?
# ═══════════════════════════════════════════════════════════════════════════════

#: « Au contact » d'une zone, en multiples d'ATR. Une tolérance en POURCENTAGE
#: du prix ne veut rien dire d'un actif à l'autre : 0,2 % vaut 6 ATR sur EURUSD
#: et 0,3 ATR sur BTCUSD. Première version de cette mesure : 0,2 % — elle
#: donnait 68,7 % de passage, artefact intégral de la tolérance.
TOL_CONTACT_ATR = 0.25


def ict_g4(ltf, prix: float, side: int, atr: float) -> dict:
    """Signaux ICT candidats pour G4 (zone institutionnelle).

    `breaker` est le seul candidat sérieux : un Order Block cassé qui a changé
    de rôle EST un order block, et `has_ob_or_fvg_alignment` ne le voit pas.
    `discount` est mesuré pour mémoire — il découpe le range en deux, donc il
    passe une fois sur deux par construction. On le compte pour pouvoir le
    REJETER sur pièces, pas pour s'en servir.
    """
    from titanium.features.ict_structure import (
        detect_breaker_blocks, premium_discount_zone,
    )

    out = {"breaker": False, "discount": False}
    tol = TOL_CONTACT_ATR * atr if atr > 0 else 0.0
    try:
        for bb in detect_breaker_blocks(ltf):
            # Un breaker devenu support porte les achats, devenu résistance
            # les ventes. On exige aussi que le prix soit AU CONTACT.
            if bb.new_role == "support" and side > 0:
                if bb.bottom - tol <= prix <= bb.top + tol:
                    out["breaker"] = True
            elif bb.new_role == "resistance" and side < 0:
                if bb.bottom - tol <= prix <= bb.top + tol:
                    out["breaker"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        pd_ctx = premium_discount_zone(ltf, prix)
        if pd_ctx.get("available"):
            out["discount"] = ((pd_ctx["zone"] == "discount" and side > 0)
                               or (pd_ctx["zone"] == "premium" and side < 0))
    except Exception:  # noqa: BLE001
        pass
    return out


def ict_g5(ltf, side: int) -> dict:
    """Signaux ICT candidats pour G5 (confirmation).

    Un displacement — 2 ATR ou plus en 1 à 3 bougies, corps pleins — est une
    bougie de confirmation au sens propre. C'est même la plus forte : elle dit
    que quelqu'un de gros est passé. Le moteur de chandeliers actuel ne la
    connaît pas ; il cherche des motifs de forme (englobante, marteau…), pas
    d'amplitude.
    """
    from titanium.features.ict_structure import analyze_structure, detect_displacement

    out = {"displacement": False, "bos": False}
    lookback = 20  # défaut de detect_displacement ; les index y sont relatifs
    try:
        for d in detect_displacement(ltf, lookback=lookback):
            # « Récent » = les 4 dernières barres de la fenêtre. Un displacement
            # vieux de 15 barres ne confirme plus rien.
            if d.direction == side and d.end_index >= lookback - 4:
                out["displacement"] = True
                break
    except Exception:  # noqa: BLE001
        pass
    try:
        bos = analyze_structure(ltf).last_bos
        if bos:
            sens = 1 if bos.get("direction") == "bullish" else -1
            out["bos"] = sens == side
    except Exception:  # noqa: BLE001
        pass
    return out


# ═══════════════════════════════════════════════════════════════════════════════

def analyser(sym: str, ouverts: dict) -> dict | None:
    """Une ligne de mesure, ou None si l'actif n'est pas mesurable."""
    from titanium.data.mt5_vendor import get_rates, get_rates_cache
    from titanium.features import candlesticks
    from titanium.features.builder import _setup_side, build_feats
    from titanium.features.structure import fib_context

    if not ouverts.get(sym, True):
        return None
    try:
        ltf = get_rates(sym, "M15", 400)
        htf = get_rates_cache(sym, "H4", 400)
        feats = build_feats(ltf, htf, with_indicators=False)
    except Exception:  # noqa: BLE001
        return None
    if not feats.get("data_valid"):
        return None

    side = feats.get("setup_side")
    if side not in (-1, 1):
        return None  # pas de sens : les piliers n'ont rien à confirmer

    prix = float((feats.get("_trace") or {}).get("price") or 0.0)
    atr = float((feats.get("_trace") or {}).get("atr") or 0.0)
    trend = int(feats.get("trend") or 0)
    fib_ctx = fib_context(ltf, prix)
    cndl = candlesticks.net_bias_on_df(
        ltf, uptrend=(trend > 0) if trend != 0 else None)

    g2 = bool(feats.get("fair_value"))
    g3 = int(feats.get("liquidity") or 0) == side
    g4 = int(feats.get("ote") or 0) == side
    g5 = int(feats.get("candle") or 0) == side

    return {
        "symbol": sym, "side": side,
        "g2": g2, "g3": g3, "g4": g4, "g5": g5,
        "support": sum((g2, g3, g4, g5)),
        "cause_g4": "PASSE" if g4 else cause_g4(ltf, prix, fib_ctx, side),
        "cause_g5": "PASSE" if g5 else cause_g5(cndl, side),
        "ict_g4": ict_g4(ltf, prix, side, atr),
        "ict_g5": ict_g5(ltf, side),
    }


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:5.1f} %" if d else "    — "


def rapport(lignes: list[dict]) -> str:
    """Le compte rendu texte. Chaque chiffre est un dénombrement, pas une estimation."""
    n = len(lignes)
    out: list[str] = []
    w = out.append

    w(f"SETUPS DIRECTIONNELS MESURÉS : {n}")
    w("")
    w("TAUX DE PASSAGE PAR PILIER")
    for k, nom in (("g2", "G2 fair_value"), ("g3", "G3 liquidity"),
                   ("g4", "G4 ote_ob"), ("g5", "G5 candle")):
        p = sum(1 for l in lignes if l[k])
        w(f"  {nom:<18} {p:>4} / {n}   {_pct(p, n)}   "
          f"manquant {_pct(n - p, n)}")
    w("")

    dist = Counter(l["support"] for l in lignes)
    w("PILIERS PAR SETUP")
    for s in sorted(dist):
        w(f"  {s} pilier(s)  {dist[s]:>4}   {_pct(dist[s], n)}")
    w("")

    for cle, titre in (("cause_g4", "POURQUOI G4 ÉCHOUE"),
                       ("cause_g5", "POURQUOI G5 ÉCHOUE")):
        causes = Counter(l[cle] for l in lignes if l[cle] != "PASSE")
        total = sum(causes.values())
        w(f"{titre}  ({total} échecs)")
        for cause, c in causes.most_common():
            w(f"  {cause:<34} {c:>4}   {_pct(c, total)}")
        w("")

    w("CE QUE L'ICT AJOUTERAIT — setups où le pilier échoue ET l'ICT le fournit")
    for cle, sig, nom in (("g4", "breaker", "G4 <- breaker block"),
                          ("g4", "discount", "G4 <- discount/premium"),
                          ("g5", "displacement", "G5 <- displacement"),
                          ("g5", "bos", "G5 <- BOS structurel")):
        src = f"ict_{cle}"
        gain = sum(1 for l in lignes if not l[cle] and l[src].get(sig))
        w(f"  {nom:<26} +{gain:>4} setups   {_pct(gain, n)} du catalogue")
    w("")

    # Effet réel : le quorum est ce qui décide, pas le pilier isolé.
    quorum = 2
    avant = sum(1 for l in lignes if l["support"] >= quorum)

    def _apres(sigs_g4: tuple[str, ...], sigs_g5: tuple[str, ...]) -> int:
        c = 0
        for l in lignes:
            s = l["support"]
            if not l["g4"] and any(l["ict_g4"].get(x) for x in sigs_g4):
                s += 1
            if not l["g5"] and any(l["ict_g5"].get(x) for x in sigs_g5):
                s += 1
            if s >= quorum:
                c += 1
        return c

    w(f"EFFET SUR LE QUORUM ({quorum} piliers exigés)")
    w(f"  aujourd'hui                {avant:>4} / {n}   {_pct(avant, n)}")
    a1 = _apres(("breaker",), ("displacement",))
    w(f"  + breaker + displacement   {a1:>4} / {n}   {_pct(a1, n)}   "
      f"({a1 - avant:+d})")
    a2 = _apres(("breaker", "discount"), ("displacement", "bos"))
    w(f"  + tous signaux ICT         {a2:>4} / {n}   {_pct(a2, n)}   "
      f"({a2 - avant:+d})")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    import MetaTrader5 as mt5  # noqa: N813

    from titanium.data.mt5_vendor import mt5_session
    from titanium.sizing import marches_ouverts

    with mt5_session():
        catalogue = [s.name for s in (mt5.symbols_get() or []) if s.visible]
    if a.limite:
        catalogue = catalogue[:a.limite]
    print(f"catalogue : {len(catalogue)} actifs visibles")

    ouverts = marches_ouverts(catalogue)
    lignes = []
    with mt5_session():
        for i, sym in enumerate(catalogue, 1):
            r = analyser(sym, ouverts)
            if r:
                lignes.append(r)
            if i % 25 == 0:
                print(f"  … {i}/{len(catalogue)} · {len(lignes)} mesurables")

    if not lignes:
        print("aucun setup directionnel mesurable")
        return 1

    txt = rapport(lignes)
    # La mesure est écrite AVANT d'être affichée : une console cp1252 ne doit
    # pas pouvoir détruire cinq minutes de balayage sur un caractère.
    if a.json:
        Path(a.json).write_text(
            json.dumps({"lignes": lignes, "rapport": txt},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(txt)
    if a.json:
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
