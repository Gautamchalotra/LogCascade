"""Learned failure-propagation graph.

Nodes are components (node / rack / service class). An *onset* is the first alert on a
component after `gap_s` of silence. P(A->B) = (# A-onsets followed by an alert on B
within `horizon_s`) / (# A-onsets + prior). Updated incrementally, so it can be
fit offline from historical alerts and keep learning online.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Iterable


class CascadeGraph:
    def __init__(self, horizon_s=300.0, gap_s=60.0, prior=1.0):
        self.horizon_s, self.gap_s, self.prior = horizon_s, gap_s, prior
        self.n_onsets: dict[str, int] = {}
        self.hits: dict[str, dict[str, int]] = {}
        self._last: dict[str, float] = {}
        self._recent: deque = deque()          # (ts, comp, credited:set)

    def observe_alert(self, ts: float, comp: str) -> bool:
        """Returns True if this alert started a new onset."""
        last = self._last.get(comp)
        self._last[comp] = ts
        if last is not None and 0 <= ts - last <= self.gap_s:
            return False
        while self._recent and ts - self._recent[0][0] > self.horizon_s:
            self._recent.popleft()
        for o_ts, o_comp, credited in self._recent:
            if o_comp != comp and comp not in credited and ts >= o_ts:
                credited.add(comp)
                row = self.hits.setdefault(o_comp, {})
                row[comp] = row.get(comp, 0) + 1
        self._recent.append((ts, comp, set()))
        self.n_onsets[comp] = self.n_onsets.get(comp, 0) + 1
        return True

    def prob(self, a: str, b: str) -> float:
        return self.hits.get(a, {}).get(b, 0) / (self.n_onsets.get(a, 0) + self.prior)

    def downstream(self, a: str, top: int = 5) -> list[tuple[str, float]]:
        return sorted(((b, self.prob(a, b)) for b in self.hits.get(a, {})), key=lambda t: -t[1])[:top]

    @classmethod
    def fit(cls, alerts: Iterable[tuple[float, str]], **kw) -> "CascadeGraph":
        g = cls(**kw)
        for ts, comp in sorted(alerts):
            g.observe_alert(ts, comp)
        return g

    def to_dict(self) -> dict:
        return {"horizon_s": self.horizon_s, "gap_s": self.gap_s, "prior": self.prior,
                "n_onsets": self.n_onsets, "hits": self.hits}

    @classmethod
    def from_dict(cls, d: dict) -> "CascadeGraph":
        g = cls(d["horizon_s"], d["gap_s"], d["prior"])
        g.n_onsets, g.hits = dict(d["n_onsets"]), {k: dict(v) for k, v in d["hits"].items()}
        return g

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path) -> "CascadeGraph":
        return cls.from_dict(json.loads(Path(path).read_text()))
