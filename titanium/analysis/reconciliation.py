"""Rapprochement en lecture seule entre l'historique MT5 et le journal d'edge.

Ce module ne répare et n'importe rien. Il mesure l'écart avant toute décision
de promotion : une position V14 close dans MT5 doit avoir exactement une ligne
live dans ``trades.ndjson``, avec un P&L en R cohérent lorsque le risque d'entrée
est connu.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from titanium.data.mt5_vendor import decalage_serveur_cache, heure_serveur_en_utc
from titanium.edge import PNL_R_MAX

_ENTRY_OUT = frozenset({1, 2, 3})
_MONETARY_FIELDS = ("profit", "commission", "swap", "fee")
_MANUAL_REASONS = frozenset({0, 1, 2})  # terminal, mobile, web
# Sous ce seuil, un signe oppose peut venir d'un breakeven reconstruit avant
# quelques centiemes de R de frais. Sans risk_money, mieux vaut ne pas crier
# a la corruption sur ce bruit ; le rapprochement exact le fera s'il est connu.
_PNL_SIGN_NOISE_R = 0.05
_REASON_NAMES = {
    0: "CLIENT",
    1: "MOBILE",
    2: "WEB",
    3: "EXPERT",
    4: "SL",
    5: "TP",
    6: "STOP_OUT",
}


def _number(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _monetary_complete(rows: Iterable) -> bool:
    """Tous les composants du net existent et sont numeriques finis."""
    for row in rows:
        for field in _MONETARY_FIELDS:
            if not hasattr(row, field):
                return False
            try:
                if not math.isfinite(float(getattr(row, field))):
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _iso(epoch, server_offset_seconds: int = 0) -> str:
    try:
        return heure_serveur_en_utc(float(epoch), server_offset_seconds)
    except (TypeError, ValueError, OSError):
        return ""


def _position_id(deal) -> str:
    """Identite MT5 commune au gestionnaire et au rapprochement."""
    value = getattr(deal, "position_id", None)
    if value is None:
        value = getattr(deal, "position", 0)
    return str(int(_number(value)))


@dataclass(frozen=True)
class Mt5ClosedPosition:
    position_id: str
    symbol: str
    opened_at: str
    closed_at: str
    net_currency: float
    profit: float
    commission: float
    swap: float
    fee: float
    close_reason: str
    exit_class: str
    manual_intervention: bool
    explicit_titanium_close: bool
    strategy_observation: bool
    censored: bool
    deal_count: int
    exit_price: float = 0.0
    entry_deal_count: int = 0
    entry_volume: float = 0.0
    exit_volume: float = 0.0
    accounting_complete: bool = False

    def __post_init__(self) -> None:
        # Invariant de mesure : une sortie interrompue manuellement ne décrit
        # pas la politique de sortie du moteur. La normalisation protège aussi
        # les anciens appelants qui construiraient encore un état incohérent.
        object.__setattr__(
            self,
            "strategy_observation",
            not (self.manual_intervention or self.censored),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def aggregate_mt5_deals(
    deals: Iterable,
    *,
    magic: int = 14_000,
    comment_marker: str = "titanium-v14",
    open_position_ids: Iterable[str | int] = (),
    server_offset_seconds: int | None = None,
) -> list[Mt5ClosedPosition]:
    """Agrège les deals par position et garde les positions attribuables à V14.

    Les deals de sortie créés par le serveur portent souvent ``magic=0``. On
    groupe donc d'abord TOUS les deals par ``position_id``, puis on attribue le
    groupe à V14 si au moins un deal porte son magic ou son commentaire.
    """
    groups: dict[str, list] = {}
    for deal in deals or ():
        position_id = _position_id(deal)
        if position_id == "0":
            continue
        groups.setdefault(position_id, []).append(deal)

    if server_offset_seconds is None:
        symbols = tuple({
            str(getattr(row, "symbol", "") or "")
            for rows in groups.values()
            for row in rows
            if str(getattr(row, "symbol", "") or "")
        })
        server_offset_seconds = decalage_serveur_cache(symbols)

    still_open = {str(value) for value in open_position_ids}
    marker = str(comment_marker or "").lower()
    result: list[Mt5ClosedPosition] = []

    for position_id, rows in groups.items():
        tagged = any(
            int(_number(getattr(row, "magic", 0))) == int(magic)
            or (marker and marker in str(getattr(row, "comment", "")).lower())
            for row in rows
        )
        exits = [
            row for row in rows
            if int(_number(getattr(row, "entry", -1))) in _ENTRY_OUT
        ]
        entries = [
            row for row in rows
            if int(_number(getattr(row, "entry", -1))) == 0
        ]
        entry_volume = sum(
            abs(_number(getattr(row, "volume", 0.0))) for row in entries
        )
        exit_volume = sum(
            abs(_number(getattr(row, "volume", 0.0))) for row in exits
        )
        has_inout = any(
            int(_number(getattr(row, "entry", -1))) == 2 for row in rows
        )
        accounting_complete = (
            bool(entries)
            and entry_volume > 0
            and not has_inout
            and _monetary_complete(rows)
            and math.isclose(entry_volume, exit_volume, rel_tol=1e-6, abs_tol=1e-9)
        )
        if not tagged or not exits or position_id in still_open:
            continue

        rows = sorted(rows, key=lambda row: _number(getattr(row, "time_msc", 0))
                      or _number(getattr(row, "time", 0)))
        exits = sorted(exits, key=lambda row: _number(getattr(row, "time_msc", 0))
                       or _number(getattr(row, "time", 0)))
        profit = sum(_number(getattr(row, "profit", 0.0)) for row in rows)
        commission = sum(_number(getattr(row, "commission", 0.0)) for row in rows)
        swap = sum(_number(getattr(row, "swap", 0.0)) for row in rows)
        fee = sum(_number(getattr(row, "fee", 0.0)) for row in rows)
        close_reasons = {
            int(_number(getattr(row, "reason", -1))) for row in exits
        }
        comments = " ".join(str(getattr(row, "comment", "")) for row in exits).lower()
        explicit_titanium_close = "titanium close" in comments
        client_exit = bool(close_reasons & _MANUAL_REASONS)
        manual = client_exit or explicit_titanium_close
        if explicit_titanium_close:
            exit_class = "manual_batch_titanium_close"
        elif client_exit:
            exit_class = "client_exit_unlabeled"
        else:
            exit_class = "server_exit"
        reason = "+".join(
            _REASON_NAMES.get(value, str(value)) for value in sorted(close_reasons)
        )

        result.append(Mt5ClosedPosition(
            position_id=position_id,
            symbol=str(getattr(rows[0], "symbol", "") or ""),
            opened_at=_iso(
                getattr(rows[0], "time", 0), server_offset_seconds),
            closed_at=_iso(
                getattr(exits[-1], "time", 0), server_offset_seconds),
            net_currency=round(profit + commission + swap + fee, 8),
            profit=round(profit, 8),
            commission=round(commission, 8),
            swap=round(swap, 8),
            fee=round(fee, 8),
            close_reason=reason,
            exit_class=exit_class,
            manual_intervention=manual,
            explicit_titanium_close=explicit_titanium_close,
            strategy_observation=not manual,
            censored=manual,
            deal_count=len(rows),
            exit_price=_number(getattr(exits[-1], "price", 0.0)),
            entry_deal_count=len(entries),
            entry_volume=entry_volume,
            exit_volume=exit_volume,
            accounting_complete=accounting_complete,
        ))

    return sorted(result, key=lambda row: (row.closed_at, row.position_id))


def reconcile(mt5_positions: Iterable[Mt5ClosedPosition], journal_trades: Iterable,
              journal_rejections: Iterable = ()) -> dict:
    """Produit un rapport d'écart déterministe, sans modifier les sources."""
    mt5_rows = list(mt5_positions)
    mt5_by_id = {row.position_id: row for row in mt5_rows}
    live = [trade for trade in (journal_trades or ())
            if str(getattr(trade, "source", "live")) == "live"]

    def ticket_of(trade) -> str:
        raw = str(getattr(trade, "ticket", "") or "")
        return raw.removeprefix("live:")

    ticket_counts = Counter(ticket_of(trade) for trade in live if ticket_of(trade))
    journal_by_id = {ticket_of(trade): trade for trade in live if ticket_of(trade)}

    def rejected_ticket(row) -> str:
        if isinstance(row, dict):
            raw = str(row.get("ticket", "") or "")
        else:
            raw = str(getattr(row, "ticket", "") or "")
        return raw.removeprefix("live:")

    rejected_ids = {
        rejected_ticket(row) for row in (journal_rejections or ())
        if rejected_ticket(row)
    }

    missing_edge = sorted(set(mt5_by_id) - set(journal_by_id))
    recovered_without_context = sorted(
        (set(mt5_by_id) & rejected_ids) - set(journal_by_id)
    )
    missing = sorted(set(mt5_by_id) - set(journal_by_id) - rejected_ids)
    orphan = sorted(set(journal_by_id) - set(mt5_by_id))
    duplicates = sorted(ticket for ticket, count in ticket_counts.items() if count > 1)
    pnl_mismatches = []
    pnl_implausible = []
    exact_net_missing = []
    exact_cost_missing = []

    for position_id in sorted(set(mt5_by_id) & set(journal_by_id)):
        mt5_row = mt5_by_id[position_id]
        trade = journal_by_id[position_id]
        risk_money = _number(getattr(trade, "risk_money", 0.0))
        journal_r = _number(getattr(trade, "pnl_r", 0.0))
        journal_exact = getattr(
            trade, "exact_net", getattr(trade, "exact_cost", False),
        ) is True
        if not journal_exact or not mt5_row.accounting_complete:
            exact_net_missing.append(position_id)
        if not bool(getattr(trade, "exact_cost", False)):
            exact_cost_missing.append(position_id)

        # Les journaux historiques ne portaient pas toujours `risk_money`.
        # Cela ne doit pas rendre la reconciliation aveugle a une valeur
        # impossible ou au signe oppose a la verite comptable MT5.
        motif_implausible = ""
        if not math.isfinite(journal_r):
            motif_implausible = "PNL_R_NON_FINI"
        elif abs(journal_r) > PNL_R_MAX:
            motif_implausible = "PNL_R_HORS_BORNES"
        elif (abs(journal_r) > _PNL_SIGN_NOISE_R
              and ((mt5_row.net_currency > 0 > journal_r)
                   or (mt5_row.net_currency < 0 < journal_r))):
            motif_implausible = "SIGNE_OPPOSE_MT5"
        if motif_implausible:
            pnl_implausible.append({
                "position_id": position_id,
                "journal_r": round(journal_r, 6) if math.isfinite(journal_r) else None,
                "reason": motif_implausible,
            })

        if risk_money <= 0 or not math.isfinite(journal_r):
            continue
        expected_r = mt5_row.net_currency / risk_money
        delta_r = journal_r - expected_r
        if abs(delta_r) > 0.02:
            pnl_mismatches.append({
                "position_id": position_id,
                "journal_r": round(journal_r, 6),
                "mt5_net_r": round(expected_r, 6),
                "delta_r": round(delta_r, 6),
            })

    manual = [row.position_id for row in mt5_rows if row.manual_intervention]
    explicit_batch = [
        row.position_id for row in mt5_rows if row.explicit_titanium_close
    ]
    unlabeled_client = [
        row.position_id for row in mt5_rows
        if row.exit_class == "client_exit_unlabeled"
    ]
    strategy_rows = [row for row in mt5_rows if row.strategy_observation]
    exit_class_counts = Counter(row.exit_class for row in mt5_rows)
    accounting_ok = not any((missing, orphan, duplicates, pnl_mismatches,
                             pnl_implausible))
    # Le net comptable exact suffit a prouver la rentabilite apres tous les
    # couts. La decomposition exacte du spread reste reportee separement :
    # elle ne doit ni etre inventee, ni rendre la collecte impossible.
    edge_ok = accounting_ok and not any((missing_edge, exact_net_missing))
    return {
        # Alias conservateur maintenu pour les anciens consommateurs : un vert
        # signifie desormais que la comptabilite ET la cohorte edge sont saines.
        "ok": edge_ok,
        "accounting_ok": accounting_ok,
        "edge_ok": edge_ok,
        "mt5_closed": len(mt5_rows),
        "journal_live": len(live),
        "matched": len(set(mt5_by_id) & set(journal_by_id)),
        "accounted": len(set(mt5_by_id) & (set(journal_by_id) | rejected_ids)),
        "mt5_net_currency": round(sum(row.net_currency for row in mt5_rows), 8),
        "strategy_observations": len(strategy_rows),
        "strategy_net_currency": round(
            sum(row.net_currency for row in strategy_rows), 8,
        ),
        "exit_class_counts": dict(sorted(exit_class_counts.items())),
        "missing_in_journal": missing,
        "missing_in_edge": missing_edge,
        "recovered_without_context": recovered_without_context,
        "orphan_in_journal": orphan,
        "duplicate_journal_tickets": duplicates,
        "pnl_mismatches": pnl_mismatches,
        "pnl_implausible": pnl_implausible,
        "exact_net_missing": exact_net_missing,
        "exact_cost_missing": exact_cost_missing,
        "manual_censored_mt5": manual,
        "explicit_titanium_close_mt5": explicit_batch,
        "client_exit_unlabeled_mt5": unlabeled_client,
        "positions": [row.to_dict() for row in mt5_rows],
    }
