"""Isolation Forest baseline on bag-of-events count vectors (no sequence information)."""
from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest

from src.buffer.sliding_window import count_vectors


class IsolationForestBaseline:
    def __init__(self, vocab_size: int, n_estimators=200, max_fit_samples=100_000, seed=42, n_jobs=1):
        self.vocab_size, self.max_fit, self.seed = vocab_size, max_fit_samples, seed
        # A single worker is portable in restricted Windows/container environments.
        # Callers can opt into parallelism explicitly when the host permits it.
        self.model = IsolationForest(n_estimators=n_estimators, random_state=self.seed, n_jobs=n_jobs)

    def fit(self, windows: np.ndarray):
        w = np.unique(windows, axis=0)
        if len(w) > self.max_fit:
            w = w[np.random.default_rng(self.seed).choice(len(w), self.max_fit, replace=False)]
        self.model.fit(count_vectors(w, self.vocab_size))
        return self

    def score(self, windows: np.ndarray, chunk=50_000) -> np.ndarray:
        out = [-self.model.score_samples(count_vectors(windows[i:i + chunk], self.vocab_size))
               for i in range(0, len(windows), chunk)]
        return np.concatenate(out) if out else np.zeros(0)
