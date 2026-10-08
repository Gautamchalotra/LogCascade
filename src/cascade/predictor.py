from __future__ import annotations

import math

from src.cascade.graph import CascadeGraph


class CascadePredictor:
    """Turns live alerts + the learned graph into per-component 'next to fail' risk.

    risk(B) = 1 - prod_A (1 - P(A->B) * exp(-(now - t_A)/tau))   (noisy-OR over active sources)
    """

    def __init__(self, graph: CascadeGraph, horizon_s=300.0, tau_s=120.0, watch=0.3,
                 imminent=0.6, min_spread=2, learn_online=True):
        self.graph, self.horizon_s, self.tau_s = graph, horizon_s, tau_s
        self.watch, self.imminent, self.min_spread = watch, imminent, min_spread
        self.learn_online = learn_online
        self.active: dict[str, float] = {}

    def observe(self, ts: float, comp: str) -> None:
        self.active[comp] = ts
        if self.learn_online:
            self.graph.observe_alert(ts, comp)

    def assess(self, now: float, top: int = 5) -> dict:
        self.active = {c: t for c, t in self.active.items() if now - t <= self.horizon_s}
        risks: dict[str, float] = {}
        for a, ta in self.active.items():
            w = math.exp(-max(now - ta, 0.0) / self.tau_s)
            for b in self.graph.hits.get(a, {}):
                if b in self.active:
                    continue
                p = self.graph.prob(a, b) * w
                risks[b] = 1 - (1 - risks.get(b, 0.0)) * (1 - p)
        ranked = sorted(risks.items(), key=lambda t: -t[1])[:top]
        max_risk = ranked[0][1] if ranked else 0.0
        spread = len(self.active)
        if max_risk >= self.imminent or (spread >= self.min_spread and max_risk >= self.watch):
            level = "imminent"
        elif max_risk >= self.watch or spread >= self.min_spread:
            level = "watch"
        else:
            level = "none"
        return {"level": level, "spread": spread, "active": sorted(self.active),
                "at_risk": [(c, round(r, 4)) for c, r in ranked]}
