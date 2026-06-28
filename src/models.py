from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SearchItem:
    id: str
    name: str
    itemid: str
    shopid: str
    units: int = 1
    enabled: bool = True
    notes: str = ""


@dataclass
class ItemState:
    last_checked: Optional[datetime] = None
    in_stock: Optional[bool] = None
    last_notified: Optional[datetime] = None
    last_purchase_attempt: Optional[datetime] = None
    purchase_status: str = "none"  # none | in_progress | success | failed
    consecutive_errors: int = 0
    session_id: Optional[str] = None

    @classmethod
    def default(cls) -> "ItemState":
        return cls()


@dataclass
class CartAddResult:
    item_id: str
    success: bool
    result_code: str
    result_message: str
    checked_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PurchaseResult:
    item_id: str
    session_id: str
    success: bool
    failure_reason: Optional[str] = None
    order_number: Optional[str] = None
    completed_at: datetime = field(default_factory=datetime.utcnow)
