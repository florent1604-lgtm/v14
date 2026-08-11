"""Tableau de confluence — ce que le bot voit, actif par actif.

    python tools/confluence_crypto.py                 # les cryptos
    python tools/confluence_crypto.py --classe fx     # une autre classe
    python tools/confluence_crypto.py --tout          # tout le catalogue
    python tools/confluence_crypto.py --json out.json # pour le tableau de bord

Rejoue la chaîne complète de décision et montre **chaque étage**, pas
seulement le verdict final. Un actif refusé l'est toujours pour une raison
nommée, et c'est cette raison qu'on veut lire.

Ce que la colonne « blocage » désigne
--------------------------------------
Le PREMIER étage qui a dit non. Les étages en aval ne sont pas évalués — un
actif dont le marché est fermé n'a pas de piliers à montrer, et prétendre le
contraire serait inventer une mesure.

    marché     l'actif ne cote plus (week-end, instrument éteint)
    coût       le spread mange plus de 12,5 % de la distance de stop
    piliers    quorum de confluence non atteint
    risque     le RiskGate refuse (contre-tendance, exposition…)
    grappe     la famille de corrélation porte déjà son plafond
    dérive     le prix a bougé depuis la décision, le setup est périmé
    plafond    8 positions ou 6 % de risque déjà engagés
    ENVOI      rien ne s'y oppose — l'ordre partirait

Ce script n'envoie **aucun** ordre. Il rejoue la décision, il ne l'exécute pas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

#: Les quatre piliers de micro-structure, dans l'ordre d'affichage.
PILIERS = ("fair_value", "liquidity", "ote_ob", "candle_confirmed")
SYMBOLES = {"fair_value": "FV", "liquidity": "LQ",
            "ote_ob": "OT", "candle_confirmed": "CH"}


def analyser(sym: str, equity: float, ouverts: dict, budgets: dict,
             grappes, deja: dict, mt5) -> dict:
    """Rejoue la chaîne pour un actif. Ne lève jamais."""
    from titanium.confiance import (
        evaluer as evaluer_confiance, piliers_de, total_piliers,
    )
    from titanium.correlation import place_disponible
    from titanium.data.mt5_vendor import get_rates, get_rates_cache
    from titanium.features.builder import build_feats, risk_context_from
    from titanium.gates import confluence_gate as cg
    from titanium.orchestrator import OrchestratorConfig, run_once

    ligne = {"symbol": sym, "blocage": "", "detail": "", "sens": "",
             "verdict": "", "piliers": {}, "support": 0, "rr": 2.0,
             "risque_pct": None, "grappe": "", "prix": None}

    # ── Étage 1 : le marché cote-t-il ?
    if not ouverts.get(sym, True):
        ligne["blocage"] = "marché"
        ligne["detail"] = "ne cote plus"
        return ligne

    # ── Étage 2 : le coût est-il supportable ?
    b = budgets.get(sym)
    if b is not None and not b.tradable:
        ligne["blocage"] = "coût"
        ligne["detail"] = b.reason[:44]
        return ligne

    # ── Étage 3 : les portes de confluence.
    try:
        ltf = get_rates(sym, "M15", 400)
        htf = get_rates_cache(sym, "H4", 400)
        feats = build_feats(ltf, htf, with_indicators=False)
    except Exception as exc:  # noqa: BLE001
        ligne["blocage"] = "données"
        ligne["detail"] = type(exc).__name__
        return ligne

    cfg = OrchestratorConfig(require_edge=False, deliberate=False,
                             execute=False, rr_ratio=2.0)
    d = cg.evaluate(feats, require_edge=False)
    ligne["piliers"] = {g.name: bool(g.passed) for g in d.gates}
    ligne["support"] = int(getattr(d, "support_passed", 0) or 0)
    ligne["verdict"] = d.verdict
    ligne["sens"] = "LONG" if (d.side or 0) > 0 else (
        "SHORT" if (d.side or 0) < 0 else "")
    ligne["prix"] = float((feats.get("_trace") or {}).get("price") or 0.0)
    ligne["grappe"] = grappes.grappe_de(sym) if grappes else ""

    if d.verdict != "ENTER":
        ligne["blocage"] = "piliers"
        ligne["detail"] = f"{ligne['support']}/4 · quorum {d.quorum}"
        return ligne

    # ── Étage 4 : le RiskGate.
    ctx = risk_context_from(feats, equity=equity, risk_pct=1.0)
    out = run_once(sym, feats, ctx, config=cfg)
    if out.risk_verdict == "DENY":
        ligne["blocage"] = "risque"
        ligne["detail"] = str(out.reason)[:44]
        return ligne

    # ── Étage 5 : déjà une position sur cet actif ?
    if deja.get(sym, 0) >= 1:
        ligne["blocage"] = "doublon"
        ligne["detail"] = f"{deja[sym]} position(s) ouverte(s)"
        return ligne

    conf = evaluer_confiance(piliers_de(d), total_piliers=total_piliers(),
                             conviction=0.5, quorum=cg.QUORUM_EXPLORE)
    ligne["risque_pct"] = round(conf.pct, 2)

    # ── Étage 6 : la grappe de corrélation.
    if grappes is not None and grappes.par_actif:
        ok, motif = place_disponible(sym, conf.pct, mt5, grappes, equity)
        if not ok:
            ligne["blocage"] = "grappe"
            ligne["detail"] = motif[:44]
            return ligne

    ligne["blocage"] = "ENVOI"
    ligne["detail"] = f"risque {conf.pct:.2f} % · R:R 2.0"
    return ligne


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--classe", default="crypto")
    ap.add_argument("--tout", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    import MetaTrader5 as mt5  # noqa: N813

    from titanium.correlation import charger as charger_grappes
    from titanium.data.mt5_vendor import account_snapshot, mt5_session
    from titanium.edge import asset_class_of
    from titanium.sizing import (
        MAX_COUT_SPREAD_PCT, marches_ouverts, tradable_universe,
    )
    from tools.live_demo import MAX_POSITIONS, MAX_RISQUE_CUMULE_PCT

    with mt5_session():
        catalogue = [s.name for s in (mt5.symbols_get() or []) if s.visible]
        compte = account_snapshot()
        positions = list(mt5.positions_get() or [])

    cibles = (catalogue if a.tout
              else [s for s in catalogue if asset_class_of(s) == a.classe])
    if not cibles:
        print(f"aucun actif de classe « {a.classe} »")
        return 1

    deja = {}
    for p in positions:
        deja[p.symbol] = deja.get(p.symbol, 0) + 1

    print(f"compte {compte.login} · {compte.equity:.2f} {compte.currency} · "
          f"{len(positions)}/{MAX_POSITIONS} positions")
    print(f"balayage de {len(cibles)} actif(s) de classe « "
          f"{'tout' if a.tout else a.classe} »…\n")

    ouverts = marches_ouverts(cibles)
    budgets = tradable_universe(cibles, compte.equity, timeframe="M15")
    try:
        grappes = charger_grappes(cibles)
    except Exception:  # noqa: BLE001
        grappes = None

    lignes = []
    with mt5_session():
        for sym in cibles:
            lignes.append(analyser(sym, compte.equity, ouverts, budgets,
                                   grappes, deja, mt5))

    # Les plus avancés dans la chaîne d'abord.
    rang = {"ENVOI": 0, "grappe": 1, "doublon": 2, "dérive": 3, "risque": 4,
            "piliers": 5, "coût": 6, "marché": 7, "données": 8}
    lignes.sort(key=lambda x: (rang.get(x["blocage"], 9), -x["support"]))

    entete = (f"{'ACTIF':<12} {'FV LQ OT CH':<12} {'S':>2} {'SENS':<6} "
              f"{'VERDICT':<7} {'GRAPPE':<7} {'BLOCAGE':<8}  DÉTAIL")
    print(entete)
    print("-" * len(entete))

    for l in lignes:
        if l["piliers"]:
            p = " ".join(" +" if l["piliers"].get(k) else " ."
                         for k in PILIERS)
        else:
            p = "  .  .  .  ."
        s = str(l["support"]) if l["piliers"] else "-"
        print(f"{l['symbol']:<12} {p:<12} {s:>2} {l['sens']:<6} "
              f"{l['verdict']:<7} {l['grappe']:<7} {l['blocage']:<8}  "
              f"{l['detail']}")

    # ── Bilan par étage : où la chaîne s'arrête, et combien de fois.
    from collections import Counter
    par_etage = Counter(l["blocage"] for l in lignes)
    print()
    print("Où la chaîne s'arrête :")
    for etage in ("marché", "coût", "données", "piliers", "risque",
                  "doublon", "grappe", "ENVOI"):
        n = par_etage.get(etage, 0)
        if n:
            barre = "#" * min(40, n)
            print(f"  {etage:<9} {n:>3}  {barre}")

    prets = [l for l in lignes if l["blocage"] == "ENVOI"]
    print()
    if prets:
        print(f"{len(prets)} actif(s) déclencheraient un ordre MAINTENANT :")
        for l in prets:
            print(f"  {l['symbol']:<12} {l['sens']:<6} {l['support']}/4 "
                  f"piliers · {l['detail']}")
    else:
        print("Aucun ordre ne partirait sur ce balayage.")

    print()
    print(f"Rappel des plafonds : {MAX_POSITIONS} positions · "
          f"{MAX_RISQUE_CUMULE_PCT:.0f} % de risque cumulé · "
          f"spread ≤ {MAX_COUT_SPREAD_PCT:.0%} du stop")
    print("Ce balayage n'a envoyé AUCUN ordre — il rejoue la décision.")

    if a.json:
        Path(a.json).write_text(json.dumps(lignes, ensure_ascii=False,
                                           indent=1), encoding="utf-8")
        print(f"\n→ {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
