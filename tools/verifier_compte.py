"""Le compte connecte est-il bien le demo attendu ?

    python tools/verifier_compte.py

Code de sortie : 0 = concordant, 2 = mauvais compte, 1 = illisible.

Sert de garde au demarrage. Le mur d'execution refuserait de toute facon
chaque ordre, mais le dire AVANT evite de laisser la boucle tourner une
heure en croyant trader. Le 08/08/2026, MT5 s'etait reconnecte seul au
compte REEL pendant la nuit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from titanium.execution.mt5_executor import ExecutionPolicy
        from titanium.data.mt5_vendor import account_snapshot

        c = account_snapshot()
        attendu = ExecutionPolicy.from_config().expected_demo_login
    except Exception as exc:  # noqa: BLE001
        print(f"  [ECHEC] compte illisible : {type(exc).__name__}")
        return 1

    genre = "DEMO" if c.is_demo else ("CONCOURS" if c.trade_mode == 1 else "REEL")
    print(f"  [{'ok' if (c.is_demo and c.login == attendu) else '!!'}]  "
          f"compte {c.login} · {c.server} · {genre} · "
          f"{c.equity:.2f} {c.currency}")

    if not c.is_demo:
        print(f"        COMPTE {genre} — le mur refusera tout ordre.")
        return 2
    if attendu and c.login != attendu:
        print(f"        login {c.login} au lieu de {attendu} attendu.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
