"""Run d'analyse V14 — délibération multi-agents sur données MT5.

    .venv\\Scripts\\python.exe run_v14.py EURUSD 2026-08-05

Les chemins de sortie et le fournisseur LLM viennent du `.env` (variables
`TITANIUM_*`), pas d'ici : une seule configuration pour la CLI et les scripts.

Ce script ne passe AUCUN ordre. Il produit une décision ; l'exécution est un
étage séparé, derrière le mur démo↔réel.
"""

import sys
import time

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

TICKER = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2026-08-05"


def main() -> None:
    config = DEFAULT_CONFIG.copy()

    print(f"=== V14 : {TICKER} @ {DATE} ===", flush=True)
    print(f"LLM      : {config['llm_provider']} — {config['deep_think_llm']} "
          f"/ {config['quick_think_llm']}", flush=True)
    print(f"données  : {config['data_vendors']['core_stock_apis']}", flush=True)
    print(f"sorties  : {config['results_dir']}", flush=True)
    print(f"exécution: {'ARMÉE' if config['exec_enabled'] else 'désarmée'}", flush=True)

    t0 = time.time()
    ta = TradingAgentsGraph(debug=True, config=config)
    _, decision = ta.propagate(TICKER, DATE)

    print(f"\n=== DÉCISION ({time.time() - t0:.0f}s) ===", flush=True)
    print(decision, flush=True)


if __name__ == "__main__":
    main()
