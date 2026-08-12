"""Espérance comparée des deux sources du pilier G5, sur clôtures réelles.

    python tools/mesure_candle_source.py
    python tools/mesure_candle_source.py --json out.json

Demandé par Hermes (revue 5982eb27) : « pas de baisse quorum ; mesurer
candle_source sur clôtures futures ».

Le secours `displacement` a été justifié par un A/B sur des TAUX DE PASSAGE.
Un taux de passage ne dit rien de la rentabilité : un pilier qui s'allume plus
souvent peut très bien s'allumer sur des perdants. Seule la comparaison des
espérances sur des trades CLOS tranche.

    formes         le pilier vient d'un motif de chandelier (englobante,
                   marteau, harami…) — le moteur historique
    displacement   le pilier vient de l'amplitude ICT (2 ATR en 1-3 bougies)
                   — le secours ajouté le 12/08/2026
    (vide)         trade ouvert avant le correctif, ou G5 absent du setup

CE QUE CET OUTIL REFUSE DE FAIRE
---------------------------------
Conclure sur un échantillon insuffisant. En dessous de `N_MINIMAL` clôtures
par branche, il affiche les chiffres mais REFUSE le verdict, et le dit. Une
différence d'espérance sur cinq trades est du bruit ; l'annoncer comme un
résultat est la façon la plus rapide de câbler une erreur pour de bon.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

JOURNAL = RACINE / "results" / "trades.ndjson"

#: Clôtures minimales PAR BRANCHE avant d'accepter un verdict.
#:
#: Aligné sur le seuil de promotion d'edge du projet (20 trades par contexte).
#: En dessous, l'écart-type de l'espérance dépasse l'écart qu'on cherche à
#: mesurer : le test n'a pas la puissance de distinguer les deux sources.
N_MINIMAL = 20


def charger(chemin: Path) -> list[dict]:
    """Clôtures lisibles portant un pnl_r exploitable. Ne lève pas sur une ligne cassée."""
    out: list[dict] = []
    if not chemin.exists():
        return out
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        try:
            t = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        r = t.get("pnl_r")
        if not isinstance(r, (int, float)) or r != r:
            continue
        out.append(t)
    return out


def stats(trades: list[dict]) -> dict:
    """Espérance, PF, winrate. Le PF vaut None si aucune perte — pas l'infini."""
    n = len(trades)
    if not n:
        return {"n": 0}
    rs = [float(t["pnl_r"]) for t in trades]
    gains = sum(r for r in rs if r > 0)
    pertes = -sum(r for r in rs if r < 0)
    moy = sum(rs) / n
    var = sum((r - moy) ** 2 for r in rs) / (n - 1) if n > 1 else 0.0
    ecart = math.sqrt(var)
    return {
        "n": n,
        "esperance": moy,
        # Erreur-type de la moyenne : c'est elle qui dit si l'écart observé
        # entre deux branches pourrait n'être que du hasard.
        "erreur_type": ecart / math.sqrt(n) if n else 0.0,
        "ecart_type": ecart,
        "pf": (gains / pertes) if pertes > 0 else None,
        "winrate": sum(1 for r in rs if r > 0) / n,
        "somme_r": sum(rs),
    }


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return f"{'—':>6}"
    pf = f"{s['pf']:.3f}" if s["pf"] is not None else "  ∞  "
    return (f"{s['n']:>4}  {s['esperance']:+7.4f}R ± {s['erreur_type']:.4f}"
            f"   PF {pf:>6}   win {100*s['winrate']:5.1f} %"
            f"   cumul {s['somme_r']:+8.3f}R")


def rapport(trades: list[dict]) -> str:
    out: list[str] = []
    w = out.append

    par_source: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        src = t.get("candle_source")
        # `None` = trade clos avant que le champ existe. Le distinguer de la
        # chaîne vide (= champ présent, G5 non fourni) évite de mélanger
        # « on ne sait pas » et « on sait que non ».
        par_source["(hors époque)" if src is None else (src or "(aucune)")].append(t)

    w(f"CLÔTURES LUES : {len(trades)}  ·  journal {JOURNAL.name}")
    w("")
    w("ESPÉRANCE PAR SOURCE DU PILIER G5")
    w(f"  {'source':<16} {'n':>4}  {'espérance':>17}   {'PF':>9}   "
      f"{'winrate':>11}   {'cumul':>13}")
    w("  " + "-" * 78)
    for src in sorted(par_source, key=lambda k: -len(par_source[k])):
        w(f"  {src:<16} {_fmt(stats(par_source[src]))}")
    w("")

    formes = stats(par_source.get("formes", []))
    depl = stats(par_source.get("displacement", []))

    w("VERDICT — le secours displacement mérite-t-il de rester ?")
    if formes["n"] < N_MINIMAL or depl["n"] < N_MINIMAL:
        manque_f = max(0, N_MINIMAL - formes["n"])
        manque_d = max(0, N_MINIMAL - depl["n"])
        w(f"  AUCUN VERDICT — échantillon insuffisant.")
        w(f"  Il manque {manque_f} clôture(s) « formes » et {manque_d} "
          f"« displacement » pour atteindre {N_MINIMAL} par branche.")
        w("")
        w("  En dessous de ce seuil, l'erreur-type de l'espérance dépasse")
        w("  l'écart qu'on cherche à mesurer. Les chiffres ci-dessus sont")
        w("  affichés pour le suivi, pas pour décider.")
    else:
        ecart = depl["esperance"] - formes["esperance"]
        # Erreur-type de la DIFFÉRENCE de deux moyennes indépendantes.
        et = math.sqrt(formes["erreur_type"] ** 2 + depl["erreur_type"] ** 2)
        if et > 0:
            z = ecart / et
            aff_z = f"z = {z:+.2f}"
        elif ecart == 0:
            # Deux branches sans dispersion et de même moyenne : identiques.
            z, aff_z = 0.0, "z = 0 (branches identiques)"
        else:
            # Variance nulle des DEUX côtés avec un écart non nul. Cas
            # dégénéré mais réaliste — une branche dont tous les trades
            # meurent au stop clôture à −1R pile. C'est la configuration la
            # PLUS décisive qui soit, surtout pas la moins : la traiter comme
            # « indécis » parce qu'une division par zéro donne 0 inverserait
            # complètement la lecture.
            z = math.inf if ecart > 0 else -math.inf
            aff_z = "z infini (aucune dispersion)"
        w(f"  écart d'espérance  {ecart:+.4f}R  (± {et:.4f}, {aff_z})")
        w("")
        if abs(z) < 2.0:
            w("  INDÉCIS — l'écart n'est pas distinguable du bruit (|z| < 2).")
            w("  Le secours ne nuit pas ; il ne se prouve pas non plus.")
            w("  Continuer la collecte.")
        elif ecart < 0:
            w("  DÉFAVORABLE — les trades issus du displacement perdent plus")
            w("  que ceux issus des formes, au-delà du bruit.")
            w("  Action : DISPLACEMENT_FALLBACK = False dans")
            w("  titanium/features/builder.py, sur arbitrage Prime.")
        else:
            w("  FAVORABLE — le secours apporte des trades meilleurs que la")
            w("  moyenne des formes. Le garder.")
    w("")

    # Croisement utile : le displacement fournit-il surtout des S=3 ?
    w("RÉPARTITION PAR NOMBRE DE PILIERS")
    w(f"  {'source':<16} " + "  ".join(f"S={s}" for s in (1, 2, 3, 4)))
    for src in sorted(par_source, key=lambda k: -len(par_source[k])):
        compte = defaultdict(int)
        for t in par_source[src]:
            compte[int(t.get("support_pillars") or 0)] += 1
        w(f"  {src:<16} " + "  ".join(f"{compte[s]:>3}" for s in (1, 2, 3, 4)))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    trades = charger(JOURNAL)
    if not trades:
        print(f"aucune clôture lisible dans {JOURNAL}")
        return 1

    txt = rapport(trades)
    if a.json:
        Path(a.json).write_text(json.dumps({"rapport": txt, "n": len(trades)},
                                           ensure_ascii=False, indent=2),
                                encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
