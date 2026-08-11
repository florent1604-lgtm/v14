"""Ferme les positions ouvertes, pour repartir d'une base propre.

    python tools/fermer_positions.py --confirmer

Le mur démo↔réel s'applique ici comme à l'ouverture : ce module refuse de
fonctionner sur un compte réel. Fermer des positions est une action
irréversible ; l'exiger deux fois — le drapeau et le mur — n'est pas de la
paperasse, c'est ce qui évite de vider un compte par erreur de terminal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirmer", action="store_true",
                    help="sans ce drapeau, on liste sans rien fermer")
    ap.add_argument("--magic", type=int, default=None,
                    help="ne fermer que ce magic (défaut : toutes)")
    a = ap.parse_args()

    import MetaTrader5 as mt5  # noqa: N813

    from titanium.data.mt5_vendor import mt5_session

    with mt5_session():
        info = mt5.account_info()
        if info is None:
            print("✗ compte illisible")
            return 1

        reel = int(getattr(info, "trade_mode", 0)) != 0
        print(f"compte {info.login} · {'RÉEL' if reel else 'DÉMO'} · "
              f"{info.equity:.2f} {info.currency}")
        if reel:
            print("\n✗ compte RÉEL — ce module ne ferme rien hors démo.")
            print("  Une fermeture en masse sur du réel doit être un geste")
            print("  manuel, pas une commande scriptée.")
            return 1

        positions = list(mt5.positions_get() or [])
        if a.magic is not None:
            positions = [p for p in positions if p.magic == a.magic]
        if not positions:
            print("\naucune position à fermer.")
            return 0

        total = sum(p.profit for p in positions)
        print(f"\n{len(positions)} position(s), P/L cumulé "
              f"{total:+.2f} {info.currency} :")
        for p in positions:
            print(f"  #{p.ticket} {p.symbol:10} "
                  f"{'LONG' if p.type == 0 else 'SHORT':5} "
                  f"lot {p.volume:<6} {p.profit:+.2f}")

        if not a.confirmer:
            print("\n[simulation] relance avec --confirmer pour fermer.")
            return 0

        print()
        ferme = echoue = 0
        for p in positions:
            tick = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                print(f"  ✗ #{p.ticket} {p.symbol} — pas de cotation")
                echoue += 1
                continue
            # Fermer = envoyer l'ordre INVERSE au prix opposé.
            sens = mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY
            prix = tick.bid if p.type == 0 else tick.ask
            r = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "position": p.ticket,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": sens,
                "price": prix,
                # Large : on veut que la fermeture ABOUTISSE. Un slippage
                # serré ferait échouer l'ordre et laisserait la position
                # ouverte — l'inverse du but recherché.
                "deviation": 50,
                "magic": p.magic,
                "comment": "titanium close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            })
            if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"  ✓ #{p.ticket} {p.symbol:10} fermé @ {r.price}")
                ferme += 1
            else:
                motif = getattr(r, "comment", "") or mt5.last_error()
                print(f"  ✗ #{p.ticket} {p.symbol:10} — {motif}")
                echoue += 1

        restantes = len(mt5.positions_get() or [])
        print(f"\n{ferme} fermée(s), {echoue} en échec · "
              f"{restantes} position(s) restante(s)")
        print(f"equity {mt5.account_info().equity:.2f} {info.currency}")
        return 0 if echoue == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
