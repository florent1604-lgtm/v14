"""Ordres limites adaptatifs, derrière le même mur DEMO que l'exécuteur.

La voie au marché reste intacte. Cette brique calcule une entrée passive à
partir du spread courant et du R initial (lui-même dérivé de la volatilité de
l'actif), puis laisse l'ordre expirer plutôt que de poursuivre le prix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from titanium.data.mt5_vendor import (
    SymbolSpec,
    decalage_serveur,
    ensure_symbol,
    mt5_session,
)
from titanium.execution.limit_pricing import (
    LimitPlan,
    arrondi_passif,
    plan_limite_entree,
)
from titanium.execution.mt5_executor import (
    ExecutionPolicy,
    ExecutionRefused,
    LotComputationError,
    OrderResult,
    assert_can_trade,
    compute_lot,
)

#: Le prix passif est calcule dans ``titanium.execution.limit_pricing``, qui
#: n'importe pas MetaTrader5. C'est la SEULE definition du prix d'entree de la
#: boucle : le simulateur d'execution appelle la meme fonction au lieu de la
#: recopier. Une copie avait deja diverge en trois points le 24/08/2026.
__all__ = ["LimitPlan", "LimitOrderResult", "plan_limit_entry",
           "place_limit_order", "reset_limit_idempotency"]


@dataclass
class LimitOrderResult(OrderResult):
    pending: bool = False
    expires_at: str = ""
    market_reference_price: float = 0.0
    spread_saved_price: float = 0.0
    spread_r: float = 0.0


_pending_keys: set[str] = set()


def reset_limit_idempotency() -> None:
    _pending_keys.clear()


def tick_du_symbole(spec: SymbolSpec) -> float:
    """Pas de cotation retenu : ``tick_size``, a defaut ``point``."""
    return float(spec.tick_size or spec.point or 0.0)


def _passive_round(value: float, spec: SymbolSpec, side: int) -> float:
    return arrondi_passif(value, tick=tick_du_symbole(spec),
                          digits=spec.digits, side=side)


def plan_limit_entry(spec: SymbolSpec, *, bid: float, ask: float, side: int,
                     stop_distance: float) -> LimitPlan:
    """Prix passif de la boucle, pour un symbole du courtier.

    La formule ne vit plus ici : elle est dans
    ``titanium.execution.limit_pricing.plan_limite_entree``, appelee aussi par
    la politique ``v14_live`` du simulateur. Cette fonction ne fait que lui
    donner le pas de cotation et le nombre de decimales du symbole.
    """
    return plan_limite_entree(bid=bid, ask=ask, side=side,
                              stop_distance=stop_distance,
                              tick=tick_du_symbole(spec), digits=spec.digits)


#: L'expiration part dans le fuseau du SERVEUR : calculee en UTC, elle paraît
#: passee a un courtier a UTC+3, qui refuse l'ordre (retcode 10022). La mesure
#: du decalage est partagee avec le journal des clotures — voir
#: `titanium/data/mt5_vendor.decalage_serveur`.


def place_limit_order(symbol: str, side: int, risk_money: float,
                      stop_distance: float, *, policy: ExecutionPolicy,
                      tp_distance: float | None = None,
                      idempotency_key: str = "") -> LimitOrderResult:
    """Place un BUY_LIMIT/SELL_LIMIT expirant, sans jamais lever d'exception."""
    r = LimitOrderResult(
        symbol=symbol,
        side=int(side or 0),
        risk_money_requested=float(risk_money or 0.0),
        idempotency_key=idempotency_key,
    )
    if r.side not in (-1, 1):
        r.reason = "SIDE_INVALIDE"
        r._add("side", False, f"side={side!r}")
        return r
    r._add("side", True)

    if idempotency_key and idempotency_key in _pending_keys:
        r.reason = "DEJA_ENVOYE"
        r._add("idempotency", False, f"clé déjà vue : {idempotency_key}")
        return r
    r._add("idempotency", True)

    # Le mur d'abord, le courtier ensuite : désarmé, aucune lecture MT5.
    try:
        acc = assert_can_trade(policy)
        r._add("wall", True, f"compte {acc.login} {acc.server} trade_mode={acc.trade_mode}")
    except ExecutionRefused as exc:
        r.reason = exc.code
        r._add("wall", False, exc.detail)
        return r
    except Exception as exc:  # noqa: BLE001
        r.reason = "WALL_ERREUR"
        r._add("wall", False, f"{type(exc).__name__}: {exc}")
        return r

    try:
        spec = ensure_symbol(symbol)
        lot, overrisk = compute_lot(
            spec, stop_distance, risk_money,
            allow_min_lot_overrisk=policy.allow_min_lot_overrisk,
        )
    except LotComputationError as exc:
        r.reason = exc.code
        r._add("lot", False, exc.detail)
        return r
    except Exception as exc:  # noqa: BLE001
        r.reason = "LOT_ERREUR"
        r._add("lot", False, f"{type(exc).__name__}: {exc}")
        return r

    r.lot = lot
    r.overrisk_ratio = overrisk
    r.risk_money_effective = round(risk_money * overrisk, 2)
    r._add("lot", True, f"lot={lot} overrisk=×{overrisk}")

    try:
        with mt5_session() as mt5:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                r.reason = "PAS_DE_PRIX"
                r._add("send", False, "symbol_info_tick vide")
                return r
            plan = plan_limit_entry(
                spec, bid=float(tick.bid), ask=float(tick.ask), side=r.side,
                stop_distance=stop_distance,
            )
            sign = 1 if r.side > 0 else -1
            sl = round(plan.price - sign * stop_distance, spec.digits)
            tp = (round(plan.price + sign * tp_distance, spec.digits)
                  if tp_distance and tp_distance > 0 else 0.0)
            expiration = datetime.now(timezone.utc) + timedelta(seconds=plan.ttl_seconds)
            decalage = decalage_serveur(mt5, (symbol,))
            order_type = (mt5.ORDER_TYPE_BUY_LIMIT if r.side > 0
                          else mt5.ORDER_TYPE_SELL_LIMIT)
            requete = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": plan.price,
                "sl": sl,
                "tp": tp,
                "deviation": policy.deviation_points,
                "magic": policy.magic,
                "comment": policy.comment,
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                # MetaTrader5 attend un horodatage POSIX entier. Un objet
                # datetime fait echouer order_send AVANT tout envoi : la
                # fonction renvoie None avec last_error
                # (-2, 'Invalid "expiration" argument'), ce qui s est traduit
                # par 100 % de refus ORDER_SEND_NUL et zero limite posee
                # depuis la mise en production. Constate le 12/08/2026.
                "expiration": int(expiration.timestamp()) + decalage,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            res = mt5.order_send(requete)
            if res is None:
                r.reason = "ORDER_SEND_NUL"
                r._add("send", False, f"last_error={mt5.last_error()}")
                return r

            r.retcode = int(res.retcode)
            r.price = plan.price
            r.sl, r.tp = sl, (tp or None)
            r.expires_at = expiration.isoformat()
            r.market_reference_price = (
                plan.price + r.side * plan.saving_vs_market
            )
            r.spread_saved_price = plan.saving_vs_market
            r.spread_r = plan.spread_r
            accepted = {
                int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
                int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)),
            }
            if r.retcode not in accepted:
                r.reason = f"RETCODE_{r.retcode}"
                r._add("send", False, str(getattr(res, "comment", "")))
                return r

            r.sent = True
            r.pending = True
            r.ticket = int(getattr(res, "order", 0)) or None
            r.reason = "PLACED_LIMIT"
            r._add(
                "limit_plan", True,
                f"prix={plan.price} économie={plan.saving_vs_market:.8g} "
                f"spread={plan.spread_r:.2%}R ttl={plan.ttl_seconds}s",
            )
            r._add("send", True, f"ordre={r.ticket} @ {r.price}")
            if idempotency_key:
                _pending_keys.add(idempotency_key)
            return r
    except Exception as exc:  # noqa: BLE001
        r.reason = "ENVOI_ERREUR"
        r._add("send", False, f"{type(exc).__name__}: {exc}")
        return r
