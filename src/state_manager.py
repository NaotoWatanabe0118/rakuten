import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models import ItemState

logger = logging.getLogger(__name__)

_DT_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, _DT_FMT)
    except ValueError:
        return None


def _from_dt(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.strftime(_DT_FMT)


class StateManager:
    def __init__(self, state_file: Path) -> None:
        self._file = state_file
        self._lock = threading.Lock()
        if not self._file.exists():
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._write_raw({})

    def _read_raw(self) -> dict:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_raw(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._file)
        except Exception:
            os.unlink(tmp)
            raise

    def _deserialize(self, raw: dict) -> ItemState:
        return ItemState(
            last_checked=_to_dt(raw.get("last_checked")),
            in_stock=raw.get("in_stock"),
            last_notified=_to_dt(raw.get("last_notified")),
            last_purchase_attempt=_to_dt(raw.get("last_purchase_attempt")),
            purchase_status=raw.get("purchase_status", "none"),
            consecutive_errors=raw.get("consecutive_errors", 0),
            session_id=raw.get("session_id"),
        )

    def _serialize(self, state: ItemState) -> dict:
        return {
            "last_checked": _from_dt(state.last_checked),
            "in_stock": state.in_stock,
            "last_notified": _from_dt(state.last_notified),
            "last_purchase_attempt": _from_dt(state.last_purchase_attempt),
            "purchase_status": state.purchase_status,
            "consecutive_errors": state.consecutive_errors,
            "session_id": state.session_id,
        }

    def get_item_state(self, item_id: str) -> ItemState:
        with self._lock:
            raw = self._read_raw()
            return self._deserialize(raw.get(item_id, {}))

    def update_item_state(self, item_id: str, **kwargs) -> None:
        with self._lock:
            raw = self._read_raw()
            state = self._deserialize(raw.get(item_id, {}))
            for key, value in kwargs.items():
                setattr(state, key, value)
            raw[item_id] = self._serialize(state)
            self._write_raw(raw)

    def mark_purchase_started(self, item_id: str, session_id: str) -> None:
        self.update_item_state(
            item_id,
            purchase_status="in_progress",
            last_purchase_attempt=datetime.utcnow(),
            session_id=session_id,
        )

    def mark_purchase_complete(self, item_id: str) -> None:
        self.update_item_state(item_id, purchase_status="success", session_id=None)

    def mark_purchase_failed(self, item_id: str) -> None:
        self.update_item_state(item_id, purchase_status="failed", session_id=None)

    def increment_errors(self, item_id: str) -> None:
        with self._lock:
            raw = self._read_raw()
            state = self._deserialize(raw.get(item_id, {}))
            state.consecutive_errors += 1
            raw[item_id] = self._serialize(state)
            self._write_raw(raw)

    def reset_errors(self, item_id: str) -> None:
        self.update_item_state(item_id, consecutive_errors=0)

    def recover_stale_purchases(self, timeout_minutes: int = 10) -> None:
        """in_progress かつ last_purchase_attempt が timeout_minutes 以上前の
        アイテムを 'none' にリセットする（クラッシュリカバリ）。"""
        now = datetime.utcnow()
        with self._lock:
            raw = self._read_raw()
            changed = False
            for item_id, entry in raw.items():
                state = self._deserialize(entry)
                if state.purchase_status != "in_progress":
                    continue
                if state.last_purchase_attempt is None:
                    state.purchase_status = "none"
                    state.session_id = None
                    raw[item_id] = self._serialize(state)
                    changed = True
                    logger.warning(
                        "[%s] in_progress (タイムスタンプなし) → none にリセット", item_id
                    )
                    continue
                age_minutes = (now - state.last_purchase_attempt).total_seconds() / 60
                if age_minutes >= timeout_minutes:
                    logger.warning(
                        "[%s] in_progress が %.1f 分経過 → none にリセット（クラッシュリカバリ）",
                        item_id,
                        age_minutes,
                    )
                    state.purchase_status = "none"
                    state.session_id = None
                    raw[item_id] = self._serialize(state)
                    changed = True
            if changed:
                self._write_raw(raw)
