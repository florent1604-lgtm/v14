"""Classe les actifs par espérance MESURÉE, en walk-forward.

    python tools/classement_backtest.py            # tout le catalogue jouable
    python tools/classement_backtest.py --max 40   # les 40 premiers

Écrit `results/selection_actifs.json`, que la boucle relit à chaud pour
régler sa cadence et sa profondeur d'historique.

Pourquoi ce classement plutôt qu'un indice de liquidité
--------------------------------------------------------
Le sélecteur initial notait le coût du spread et le volume — des
conjectures sur ce qui *devrait* bien se comporter. Ici on rejoue
réellement la stratégie sur chaque actif et on garde ce qui **a payé**,
segment par segment.

La différence est celle entre « cet actif est liquide donc probablement
tradable » et « cet actif a rendu +0.24 R sur 180 trades, positif sur les
trois segments ». Le second se vérifie.

Anti-sur-ajustement
-------------------
Un actif n'est promu au rang A que s'il est positif sur **au moins deux
segments sur trois**, pas seulement en moyenne. Une espérance globale
positive tirée d'un seul segment favorable est du bruit — c'est exactement
ce qui a fait croire que le R:R 3.0 valait pour tout le catalogue.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.selection import (  # noqa: E402
    FORCES, RANG_A, RANG_B, RANG_C, Fiche, ecrire,
)

SORTIE = RACINE / "results" / "selection_actifs.json"

#: Trades minimaux pour qu'une espérance veuille dire quelque chose.
MIN_TRADES = 25

#: Segments positifs exigés pour le rang A.
SEGMENTS_POSITIFS_MIN = 2


def rejouer(symbole: str, barres: int, rr: float):
    """Rejoue la stratégie et rend (espérance, n, segments). None si échec."""
    from titanium.backtest import decouper_walk_forward, rejouer as bt
    from titanium.data.mt5_vendor import ensure_symbol, get_rates

    spec = ensure_symbol(symbole)
    cout = spec.spread * spec.point if spec else 0.0
    ltf = get_rates(symbole, "M15", barres)
    htf = get_rates(symbole, "H4", max(300, barres // 16))
    res = bt(symbole, ltf, htf, spread=cout, rr_ratio=rr)
    if not res.trades:
        return None

    xs = [t.pnl_r for t in res.trades]
    esperance = sum(xs) / len(xs)
    segments = []
    for seg in decouper_walk_forward(res, 3):
        s = [t.pnl_r for t in seg.trades]
        segments.append(sum(s) / len(s) if s else 0.0)
    return esperance, len(xs), segments


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--barres", type=int, default=12000)
    ap.add_argument("--rr", type=float, default=2.0,
                    help="doit refléter le R:R de la boucle")
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()

    import MetaTrader5 as mt5  # noqa: N813

    from titanium.data.mt5_vendor import account_snapshot, mt5_session
    from titanium.edge import asset_class_of
    from titanium.sizing import tradable_universe

    with mt5_session():
        catalogue = [s.name for s in (mt5.symbols_get() or []) if s.visible]
        equity = account_snapshot().equity

    # On ne rejoue QUE ce qui passe déjà les portes de coût et de vitalité :
    # mesurer un instrument éteint ou un spread à 60 % est du temps perdu.
    budgets = tradable_universe(catalogue, equity, timeframe="M15")
    jouables = [s for s, b in budgets.items() if b.tradable]
    if a.max:
        jouables = jouables[:a.max]

    print(f"catalogue {len(catalogue)} → {len(jouables)} jouables · "
          f"rejeu {a.barres} barres · R:R {a.rr}\n")

    fiches, mesures = [], 0
    for i, sym in enumerate(jouables, 1):
        f = Fiche(symbol=sym, classe=asset_class_of(sym))
        try:
            r = rejouer(sym, a.barres, a.rr)
        except Exception as exc:  # noqa: BLE001
            f.motif = f"rejeu impossible : {type(exc).__name__}"
            fiches.append(f)
            continue

        if r is None:
            f.motif = "aucun trade sur la période"
            fiches.append(f)
            continue

        esp, n, segments = r
        f.edge_r, f.edge_n = esp, n
        positifs = sum(1 for s in segments if s > 0)
        f.motif = (f"{esp:+.3f} R sur {n} trades · "
                   f"{positifs}/3 segments positifs")
        # Le score sert au tri ; le rang, lui, exige la stabilité.
        f.score = esp if n >= MIN_TRADES else esp * 0.3
        f.activite = float(positifs)
        mesures += 1
        fiches.append(f)
        if i % 10 == 0:
            print(f"  {i}/{len(jouables)}…", flush=True)

    # ── Rangs. Le mérite se prouve sur les segments, pas sur la moyenne.
    for f in fiches:
        force = f.symbol.upper() in FORCES
        f.forcee = force
        stable = (f.edge_n or 0) >= MIN_TRADES and \
            f.activite >= SEGMENTS_POSITIFS_MIN and (f.edge_r or 0) > 0
        if force:
            f.rang = RANG_A
            f.motif = f"{f.motif} · suivi permanent (traité manuellement)"
        elif stable:
            f.rang = RANG_A
        elif (f.edge_r or -1) > 0:
            f.rang = RANG_B
        else:
            f.rang = RANG_C

    fiches.sort(key=lambda x: (-x.forcee, -(x.edge_r or -9)))
    ecrire(fiches, SORTIE)

    rangs = {r: [f for f in fiches if f.rang == r]
             for r in (RANG_A, RANG_B, RANG_C)}
    print(f"\n{mesures} actif(s) mesuré(s)\n")
    print(f"{'rang':5} {'n':>4}  cadence        historique")
    for r, cad, bar in ((RANG_A, "chaque tour", 600),
                        (RANG_B, "1 tour / 3", 400),
                        (RANG_C, "1 tour / 10", 300)):
        print(f"  {r:3} {len(rangs[r]):>4}  {cad:<14} {bar} barres")

    print(f"\nRang A — espérance positive ET stable sur ≥2 segments :")
    for f in rangs[RANG_A][:20]:
        print(f"  {f.symbol:12} {f.classe:8} {f.motif}")

    if not rangs[RANG_A]:
        print("  AUCUN. Sur cet univers, aucun actif ne tient en walk-forward.")
        print("  C'est un résultat, pas une panne : la stratégie n'a d'edge")
        print("  mesurable nulle part au réglage actuel.")

    print(f"\n→ {SORTIE}")
    print("La boucle relit ce fichier à chaud, sans redémarrage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
