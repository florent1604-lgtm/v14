"""Etat du compte et positions restantes. Ne leve jamais.

    python tools/etat_compte.py

Appele a l'arret pour dire ce qui reste ouvert. Les positions gardent leur
stop et leur objectif chez le courtier : elles restent protegees bot
eteint, et ce script ne les ferme pas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.data.mt5_vendor import mt5_session

        with mt5_session():
            i = mt5.account_info()
            positions = list(mt5.positions_get() or [])
    except Exception as exc:  # noqa: BLE001
        print(f"  compte illisible ({type(exc).__name__}) — "
              "MetaTrader est-il ouvert ?")
        return 0

    print(f"  compte {i.login} · solde {i.balance:.2f} · "
          f"equite {i.equity:.2f} {i.currency}")

    if not positions:
        print("  aucune position ouverte")
        return 0

    flottant = sum(p.profit for p in positions)
    print(f"  {len(positions)} position(s) TOUJOURS OUVERTE(S) · "
          f"flottant {flottant:+.2f} {i.currency}")
    for p in sorted(positions, key=lambda x: x.profit):
        sens = "LONG" if p.type == 0 else "SHORT"
        stop = f"SL {p.sl}" if p.sl else "SANS STOP"
        print(f"    {p.symbol:<11} {sens:<5} lot {p.volume:<6} "
              f"{p.profit:>+8.2f}  {stop}")
    print()
    print("  Leurs stops et objectifs restent actifs chez le courtier.")
    print("  Pour les fermer :")
    print("    .venv\Scripts\python.exe tools\fermer_positions.py --confirmer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
