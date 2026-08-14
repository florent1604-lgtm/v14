from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from titanium.execution_sim.models import ExecutionIntent, Side


class AlphaSource(Protocol):
    """Produces target intents without choosing any execution detail."""

    def intents(self) -> list[ExecutionIntent]: ...


@dataclass(frozen=True)
class StaticAlpha:
    _intents: tuple[ExecutionIntent, ...]

    @classmethod
    def from_tuples(cls, values: Iterable[tuple[str, int, float]]) -> StaticAlpha:
        intents = tuple(
            ExecutionIntent(f"static:{index}:{symbol}", symbol, Side(side), quantity)
            for index, (symbol, side, quantity) in enumerate(values, 1)
        )
        return cls(intents)

    def intents(self) -> list[ExecutionIntent]:
        return list(self._intents)


@dataclass(frozen=True)
class LegacyTradeAlphaAdapter:
    """Adapts frozen `titanium.backtest.Trade` objects without recomputing alpha."""

    default_quantity: float = 1.0

    def intents(self, trades: Iterable[object]) -> list[ExecutionIntent]:
        result = []
        for index, trade in enumerate(trades, 1):
            symbol = str(trade.symbol)
            side = Side(int(trade.side))
            entry_time = str(getattr(trade, "bar_entree", ""))
            result.append(
                ExecutionIntent(
                    intent_id=f"legacy:{symbol}:{entry_time or index}",
                    symbol=symbol,
                    side=side,
                    quantity=self.default_quantity,
                    metadata={
                        "legacy_entry_price": float(getattr(trade, "prix_entree", 0.0)),
                        "legacy_context": str(getattr(trade, "contexte", "")),
                        "alpha_source": "titanium.backtest.Trade",
                    },
                )
            )
        return result
