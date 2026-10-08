"""Streaming sliding-window buffer.

The SAME class builds training windows offline and live windows online, so
there is no train/serve skew in how event sequences are grouped.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from typing import Optional

import numpy as np

from src.common.schemas import LogRecord, Window


def group_key(group_by: str, rec: LogRecord) -> str:
    if group_by == "entity":
        return rec.entity or rec.component
    if group_by == "component":
        return rec.component
    return "global"


class SlidingWindowBuffer:
    def __init__(self, size: int, stride: int = 1, pad_id: int = 0,
                 min_len: int = 1, max_keys: Optional[int] = None):
        assert 1 <= stride <= size
        self.size, self.stride, self.pad_id = size, stride, pad_id
        self.min_len, self.max_keys = min_len, max_keys
        self._ev: "OrderedDict[str, deque]" = OrderedDict()
        self._since: dict[str, int] = {}
        self._emitted: dict[str, bool] = {}
        self._meta: dict[str, tuple[float, str]] = {}

    def _make(self, key: str) -> Window:
        d = self._ev[key]
        ids = [e for e, _ in d]
        ids += [self.pad_id] * (self.size - len(ids))
        ts, comp = self._meta[key]
        return Window(ids, ts, key, comp, max(l for _, l in d))

    def push(self, key: str, ts: float, event_id: int, label: int = 0,
             component: str = "unknown") -> Optional[Window]:
        d = self._ev.get(key)
        if d is None:
            d = self._ev[key] = deque(maxlen=self.size)
            self._since[key], self._emitted[key] = 0, False
            if self.max_keys and len(self._ev) > self.max_keys:
                old, _ = self._ev.popitem(last=False)
                for m in (self._since, self._emitted, self._meta):
                    m.pop(old, None)
        else:
            self._ev.move_to_end(key)
        d.append((event_id, label))
        self._since[key] += 1
        self._meta[key] = (ts, component)
        if len(d) == self.size and self._since[key] >= self.stride:
            self._since[key] = 0
            self._emitted[key] = True
            return self._make(key)
        return None

    def flush(self) -> list[Window]:
        """Emit right-padded windows for keys that never filled a window (short sessions)."""
        out = []
        for key, d in self._ev.items():
            if not self._emitted[key] and len(d) >= self.min_len:
                out.append(self._make(key))
                self._emitted[key] = True
        return out


def count_vectors(windows: np.ndarray, vocab_size: int, pad_id: int = 0) -> np.ndarray:
    """(N,T) event ids -> (N,V) length-normalised bag-of-events (Isolation Forest features)."""
    n = len(windows)
    feat = np.zeros((n, vocab_size), dtype=np.float32)
    rows = np.repeat(np.arange(n), windows.shape[1])
    np.add.at(feat, (rows, np.clip(windows.ravel(), 0, vocab_size - 1)), 1.0)
    feat[:, pad_id] = 0.0
    return feat / np.maximum(feat.sum(1, keepdims=True), 1.0)
