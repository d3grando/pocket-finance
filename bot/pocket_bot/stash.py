"""Small in-memory LRU+TTL store for data that does not fit into 64-byte callback_data
(several action ids for «Отменить все», saved keyboards for «‹ Назад»). Lost on restart —
handlers then answer «Кнопка устарела» instead of guessing."""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class Stash:
    def __init__(self, max_items: int = 2000, ttl_seconds: float = 3 * 24 * 3600,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_items
        self._ttl = ttl_seconds
        self._clock = clock

    def _evict(self) -> None:
        now = self._clock()
        while self._data:
            key, (ts, _) = next(iter(self._data.items()))
            if now - ts > self._ttl or len(self._data) > self._max:
                self._data.pop(key, None)
            else:
                break

    def put(self, value: Any, key: str | None = None) -> str:
        key = key or secrets.token_hex(5)
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        self._evict()
        return key

    def get(self, key: str) -> Any:
        self._evict()
        item = self._data.get(key)
        if item is None:
            return None
        ts, value = item
        if self._clock() - ts > self._ttl:
            self._data.pop(key, None)
            return None
        return value

    def pop(self, key: str) -> Any:
        value = self.get(key)
        self._data.pop(key, None)
        return value

    def __len__(self) -> int:
        return len(self._data)
