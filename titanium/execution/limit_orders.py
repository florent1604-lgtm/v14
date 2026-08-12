"""Ordres limites adaptatifs, derrière le même mur DEMO que l'exécuteur.

La voie au marché reste intacte. Cette brique calcule une entrée passive à
partir du spread courant et du R initial (lui-même dérivé de la volatilité de
l'actif), puis laisse l'ordre expirer plutôt que de poursuivre le prix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from titanium.data.mt5_vendor import SymbolSpec, account_snapshot, ensure_symbol, mt5_session
from titanium.execution.mt5_executor import (
    ExecutionPolicy,
    ExecutionRefused,
    LotComputationError,
    OrderResult,
    assert_can_trade,
    compute_lot,
)


@dataclass(frozen=True)
class LimitPlan:
    price: float
    spread: float
    spread_r: float
    passive_extra: float
    saving_vs_market: float
    ttl_seconds: int


@dataclass
class LimitOrderResult(OrderResult):
    pending: bool = False
    expires_at: str = ""
    spread_saved_price: float = 0.0
    spread_r: float = 0.0


_pending_keys: set[str] = set()


def reset_limit_idempotency() -> None:
    _pending_keys.clear()


def _passive_round(value: float, spec: SymbolSpec, side: int) -> float:
    tick = float(spec.tick_size or spec.point or 0.0)
    if tick <= 0:
        raise ValueError("TICK_INVALIDE")
    units = value / tick
    quantized = math.floor(units + 1e-10) if side > 0 else math.ceil(units - 1e-10)
    return round(quantized * tick, spec.digits)


def plan_limit_entry(spec: SymbolSpec, *, bid: float, ask: float, side: int,
                     stop_distance: float) -> LimitPlan:
    """Construit un prix passif équilibrant économie et probabilité de fill.

    - spread <= 5 % de R : achat au bid / vente à l'ask ;
    - spread plus coûteux : exige jusqu'à 5 % de R d'amélioration en plus ;
    - plus le spread pèse dans R, plus la durée de validité est courte.
    """
    if side not in (-1, 1):
        raise ValueError("SIDE_INVALIDE")
    valeurs = (float(bid), float(ask), float(stop_distance))
    if not all(math.isfinite(v) and v > 0 for v in valeurs) or ask < bid:
        raise ValueError("PRIX_INVALIDE")

    spread = ask - bid
    spread_r = spread / stop_distance
    seuil_confort = 0.05 * stop_distance
    extra = min(seuil_confort, max(0.0, spread - seuil_confort))
    brut = bid - extra if side > 0 else ask + extra
    price = _passive_round(brut, spec, side)
    # Une limite passive ne doit jamais traverser le carnet au moment du plan.
    price = min(price, bid) if side > 0 else max(price, ask)

    if spread_r > 0.15:
        ttl = 120
    elif spread_r > 0.08:
        ttl = 300
    else:
        ttl = 600

    reference_marche = ask if side > 0 else bid
    saving = (reference_marche - price) * side
    return LimitPlan(
        price=round(price, spec.digits),
        spread=spread,
        spread_r=spread_r,
        passive_extra=extra,
        saving_vs_market=max(0.0, saving),
        ttl_seconds=ttl,
    )


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

    try:
        acc = account_snapshot()
        assert_can_trade(policy, acc)
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
                "expiration": expiration,
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
