from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any


class Side(IntEnum):
    BUY = 1
    SELL = -1


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    POST_ONLY = "post_only"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = {
    OrderStatus.CANCELLED,
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_levels: tuple[BookLevel, ...] = ()
    ask_levels: tuple[BookLevel, ...] = ()
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    volatility_bps: float = 0.0
    event_id: str = ""

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    symbol: str
    side: Side
    quantity: float
    limit_price: float | None = None
    reduce_only: bool = False
    legs: tuple[ExecutionIntent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderEvent:
    status: OrderStatus
    timestamp: datetime
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    fill_id: str
    timestamp: datetime
    quantity: float
    price: float
    fee: float
    liquidity: str


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType
    created_at: datetime
    limit_price: float | None = None
    reduce_only: bool = False
    expires_at: datetime | None = None
    scheduled_offset_ms: int = 0
    arrival_price: float = 0.0
    queue_ahead: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    replaces: str = ""
    oco_group: str = ""
    status: OrderStatus = field(init=False, default=OrderStatus.CREATED)
    filled_quantity: float = field(init=False, default=0.0)
    avg_fill_price: float = field(init=False, default=0.0)
    total_fees: float = field(init=False, default=0.0)
    fills: list[Fill] = field(init=False, default_factory=list)
    events: list[OrderEvent] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.side = Side(self.side)
        self.order_type = OrderType(self.order_type)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        self.events.append(OrderEvent(OrderStatus.CREATED, self.created_at))

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["side"] = int(self.side)
        data["order_type"] = self.order_type.value
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        data["events"] = [
            {"status": e.status.value, "timestamp": e.timestamp.isoformat(), "reason": e.reason}
            for e in self.events
        ]
        data["fills"] = [{**asdict(f), "timestamp": f.timestamp.isoformat()} for f in self.fills]
        return data
