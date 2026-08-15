"""Rapport de backtest des 15 politiques d execution, depuis la matrice complete.

Deux mesures distinctes, et c est le point du rapport :
  - la moyenne brute par politique (ce que le rapport du 14/08 donnait deja) ;
  - la difference APPARIEE contre market, scenario par scenario. Les 864
    scenarios sont identiques d une politique a l autre et portent le meme
    alpha : l ecart appariee isole le delta d execution du bruit de scenario.

Usage : python tools/rapport_politiques_execution.py [chemin/execution_matrix.csv]

Reproduit integralement docs/RAPPORT_BACKTEST_15_POLITIQUES.md. Ne regenere
aucune simulation : il ne fait que relire la matrice deja produite.
"""
import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEFAUT = Path(__file__).resolve().parent.parent / "results" / "execution_matrix_full" / "execution_matrix.csv"
CHEMIN = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAUT
if not CHEMIN.exists():
    sys.exit(f"Matrice introuvable : {CHEMIN}")

ORDRE = [
    "market", "limit_passive", "post_only", "ioc", "fok", "cancel_replace",
    "pegged", "iceberg", "twap", "vwap", "pov", "adaptive",
    "market_making", "multi_leg_simultaneous", "maker_then_hedge_taker",
]


def nombre(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


with CHEMIN.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
par_pol = defaultdict(list)
for r in rows:
    par_pol[r["policy"]].append(r)


def cle(r):
    """Clef de scenario : la graine identifie le tirage, identique entre politiques."""
    return (r["seed"], r["scenario_id"])


net = {}
for p, rs in par_pol.items():
    for r in rs:
        v = nombre(r["net_pnl"])
        if v is not None:
            net[(p, cle(r))] = v

communs = {cle(r) for r in par_pol["market"]}
for p in par_pol:
    communs &= {cle(r) for r in par_pol[p]}
print(f"scenarios communs aux 15 politiques : {len(communs)}")
print(f"lignes totales : {len(rows)}")
print()


def moy(v):
    return st.mean(v) if v else float("nan")


def se(v):
    return st.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else 0.0


def pf(v):
    g = sum(x for x in v if x > 0)
    p = -sum(x for x in v if x < 0)
    return g / p if p else float("inf")


def col(rs, nom):
    return [x for x in (nombre(r.get(nom)) for r in rs) if x is not None]


# ─────────────────────────── 1. Fiche par politique
print("=" * 108)
print("1. RESULTAT BRUT PAR POLITIQUE (864 scenarios chacune)")
print("=" * 108)
entete = (f"{'politique':<24}{'net moy':>9}{'±se':>8}{'PF':>8}{'fill':>7}"
          f"{'partiel':>8}{'rate':>7}{'rejet':>7}{'cout bps':>10}{'IS bps':>8}{'DD':>7}")
print(entete)
for p in ORDRE:
    rs = par_pol[p]
    v = col(rs, "net_pnl")
    f = pf(v)
    print(f"{p:<24}{moy(v):>9.4f}{se(v):>8.4f}"
          f"{(f if f != float('inf') else 999):>8.2f}"
          f"{100*moy(col(rs,'fill_ratio')):>6.1f}%"
          f"{100*moy(col(rs,'partial_fill_ratio')):>7.1f}%"
          f"{100*moy(col(rs,'expiration_rate')):>6.1f}%"
          f"{100*moy(col(rs,'rejection_rate')):>6.1f}%"
          f"{moy(col(rs,'total_cost_bps')):>10.3f}"
          f"{moy(col(rs,'implementation_shortfall_bps')):>8.3f}"
          f"{moy(col(rs,'max_drawdown')):>7.3f}")

# ─────────────────────────── 2. Ecart apparie contre market
print()
print("=" * 108)
print("2. ECART APPARIE CONTRE MARKET (meme scenario, meme alpha, meme graine)")
print("=" * 108)
print(f"{'politique':<24}{'delta moy':>11}{'±se':>9}{'z':>8}{'gagne':>8}{'perd':>8}{'nul':>7}{'pire cas':>10}")
resultats = []
for p in ORDRE:
    if p == "market":
        continue
    d = [net[(p, k)] - net[("market", k)] for k in communs
         if (p, k) in net and ("market", k) in net]
    if not d:
        continue
    m, s = moy(d), se(d)
    z = m / s if s else float("nan")
    gagne = sum(1 for x in d if x > 1e-12)
    perd = sum(1 for x in d if x < -1e-12)
    resultats.append((m, p, s, z, gagne, perd, len(d) - gagne - perd, min(d)))
for m, p, s, z, g, pe, nu, pire in sorted(resultats, reverse=True):
    print(f"{p:<24}{m:>+11.4f}{s:>9.4f}{z:>8.2f}"
          f"{100*g/(g+pe+nu):>7.1f}%{100*pe/(g+pe+nu):>7.1f}%{100*nu/(g+pe+nu):>6.1f}%{pire:>10.3f}")

# ─────────────────────────── 3. Structure du cout
print()
print("=" * 108)
print("3. D OU VIENT LE COUT (moyennes, unites du simulateur)")
print("=" * 108)
print(f"{'politique':<24}{'spread':>9}{'slippage':>10}{'impact':>9}{'maker f':>9}{'taker f':>9}"
      f"{'capte':>9}{'rate opp':>10}{'maker %':>9}")
for p in ORDRE:
    rs = par_pol[p]
    print(f"{p:<24}{moy(col(rs,'spread_cost')):>9.4f}{moy(col(rs,'slippage_cost')):>10.4f}"
          f"{moy(col(rs,'impact_cost')):>9.4f}{moy(col(rs,'maker_fees')):>9.4f}"
          f"{moy(col(rs,'taker_fees')):>9.4f}{moy(col(rs,'spread_captured')):>9.4f}"
          f"{moy(col(rs,'lost_opportunity')):>10.4f}{100*moy(col(rs,'maker_ratio')):>8.1f}%")

# ─────────────────────────── 4. Risque operationnel
print()
print("=" * 108)
print("4. RISQUE OPERATIONNEL ET FIDELITE")
print("=" * 108)
print(f"{'politique':<24}{'delai ms':>10}{'p95 ms':>9}{'expo res':>10}{'non exec':>10}"
      f"{'jambe orph':>11}{'fidelite':>10}{'complex':>9}{'compat':>8}")
for p in ORDRE:
    rs = par_pol[p]
    print(f"{p:<24}{moy(col(rs,'fill_delay_mean_ms')):>10.1f}{moy(col(rs,'fill_delay_p95_ms')):>9.1f}"
          f"{moy(col(rs,'residual_exposure_max')):>10.4f}{moy(col(rs,'unexecuted_quantity')):>10.4f}"
          f"{moy(col(rs,'orphan_leg_loss')):>11.4f}{moy(col(rs,'model_fidelity')):>10.2f}"
          f"{moy(col(rs,'complexity')):>9.1f}{moy(col(rs,'data_compatibility')):>8.2f}")

# ─────────────────────────── 5. Tenue hors echantillon
print()
print("=" * 108)
print("5. TENUE SUR LES TROIS TIERS")
print("=" * 108)
S = ("development", "validation", "final_oos")
print(f"{'politique':<24}{'dev':>10}{'validation':>12}{'OOS':>10}{'OOS-dev':>10}")
for p in ORDRE:
    m = {}
    for s in S:
        v = [nombre(r["net_pnl"]) for r in par_pol[p] if r["split"] == s]
        v = [x for x in v if x is not None]
        m[s] = moy(v)
    print(f"{p:<24}{m['development']:>10.4f}{m['validation']:>12.4f}"
          f"{m['final_oos']:>10.4f}{m['final_oos']-m['development']:>+10.4f}")

# ─────────────────────────── 6. Regimes
print()
print("=" * 108)
print("6. ECART APPARIE CONTRE MARKET, PAR REGIME")
print("=" * 108)
regime = {}
for p, rs in par_pol.items():
    for r in rs:
        regime[(p, cle(r))] = r
axes = [("liquidity", ("high", "low")), ("spread", ("normal", "wide")),
        ("volatility", ("low", "medium", "high")), ("size", ("small", "large"))]
for axe, valeurs in axes:
    print(f"\n--- {axe} ---")
    print(f"{'politique':<24}" + "".join(f"{v:>12}" for v in valeurs))
    for p in ORDRE:
        if p == "market":
            continue
        ligne = f"{p:<24}"
        for val in valeurs:
            d = [net[(p, k)] - net[("market", k)] for k in communs
                 if regime.get((p, k), {}).get(axe) == val
                 and (p, k) in net and ("market", k) in net]
            ligne += f"{moy(d):>+12.4f}" if d else f"{'-':>12}"
        print(ligne)
