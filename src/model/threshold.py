"""Dynamic threshold on reconstruction error.

threshold = max(floor, median + k * 1.4826 * MAD) over a rolling history of recent scores.
- Robust statistics (median/MAD) keep a burst of anomalies from inflating the threshold.
- Exceeding scores enter the history *clipped to the threshold*, so a sustained
  legitimate shift (deploy, traffic change) lets the threshold creep up slowly
  instead of alerting forever.
- Before `min_samples` is reached, the static calibrated threshold is used.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Optional

import numpy as np


class DynamicThreshold:
    def __init__(self, window=2000, k=6.0, min_samples=200, min_consecutive=1,
                 floor=0.0, fallback: Optional[float] = None):
        self.hist: deque = deque(maxlen=window)
        self.k, self.min_samples, self.min_consecutive = k, min_samples, min_consecutive
        self.floor, self.fallback = floor, fallback
        self._consec = 0

    def current(self) -> float:
        if len(self.hist) < self.min_samples:
            return self.fallback if self.fallback is not None else math.inf
        a = np.fromiter(self.hist, dtype=float, count=len(self.hist))
        med = float(np.median(a))
        mad = float(np.median(np.abs(a - med))) * 1.4826
        mad = max(mad, 0.05 * abs(med) + 1e-6)          # avoid a zero-width band on very clean data
        return max(self.floor, med + self.k * mad)

    def update(self, score: float) -> tuple[float, bool]:
        thr = self.current()
        exceed = score > thr
        self._consec = self._consec + 1 if exceed else 0
        self.hist.append(min(score, thr) if (exceed and math.isfinite(thr)) else score)
        return thr, exceed and self._consec >= self.min_consecutive

    def reset(self):
        self.hist.clear()
        self._consec = 0


def build_threshold(cfg: dict, calibration: dict) -> DynamicThreshold:
    c = cfg["threshold"]
    q = calibration["quantile_value"]
    return DynamicThreshold(window=c["window"], k=c["k"], min_samples=c["min_samples"],
                            min_consecutive=c["min_consecutive"],
                            floor=c["floor_mult"] * q, fallback=q)
