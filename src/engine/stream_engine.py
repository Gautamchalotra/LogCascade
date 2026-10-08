"""Real-time engine: raw lines -> Event IDs -> windows -> reconstruction error -> alerts."""
from __future__ import annotations

import threading
from collections import deque
from typing import Iterable

import numpy as np

from src.actuator.actuators import ActuatorChain, build_actuators
from src.actuator.remediation_agent import RemediationAgent
from src.buffer.sliding_window import SlidingWindowBuffer, group_key
from src.cascade.graph import CascadeGraph
from src.cascade.predictor import CascadePredictor
from src.common.config import dataset_cfg, get_paths
from src.common.logging_utils import get_logger
from src.common.schemas import Alert, Window
from src.model.scorer import AnomalyScorer
from src.model.threshold import DynamicThreshold, build_threshold
from src.parser.drain_parser import UNK_ID, LogTemplateParser
from src.parser.loaders import parse_line

log = get_logger("engine")


class StreamEngine:
    def __init__(self, cfg: dict, dataset: str, parser: LogTemplateParser, scorer: AnomalyScorer,
                 threshold: DynamicThreshold, cascade: CascadePredictor, chain: ActuatorChain,
                 remediation: RemediationAgent | None = None):
        self.cfg, self.dataset = cfg, dataset
        ds, e = dataset_cfg(cfg, dataset), cfg["engine"]
        self.group_by = ds["group_by"]
        self.parser, self.scorer, self.threshold = parser, scorer, threshold
        self.cascade, self.chain = cascade, chain
        self.remediation = remediation or RemediationAgent.from_config(cfg)
        self.buffer = SlidingWindowBuffer(scorer.window_size, ds["stride"], max_keys=e["max_keys"])
        self.crit_ratio, self.online_learning = e["crit_ratio"], e["online_learning"]
        self.alerts: deque = deque(maxlen=e["max_alerts"])
        self.stats = {"lines": 0, "unparsed": 0, "unknown_templates": 0, "windows": 0, "alerts": 0,
                       "last_score": None, "last_threshold": None}
        self._lock = threading.Lock()

    @classmethod
    def from_artifacts(cls, cfg: dict, dataset: str | None = None) -> "StreamEngine":
        dataset = dataset or cfg["dataset"]
        P = get_paths(cfg, dataset)
        ckpt, drain = P.ckpt / "model.pt", P.processed / "drain_state.bin"
        if not ckpt.exists() or not drain.exists():
            raise FileNotFoundError(f"missing artifacts for {dataset}: run `prepare` and `train` first")
        parser = LogTemplateParser(load_state_from=str(drain), **cfg["parser"])
        scorer = AnomalyScorer.from_checkpoint(ckpt, cfg["train"].get("device", "auto"))
        cc = cfg["cascade"]
        gpath = P.ckpt / "cascade_graph.json"
        graph = CascadeGraph.load(gpath) if gpath.exists() else CascadeGraph(cc["horizon_s"], cc["gap_s"], cc["prior"])
        pred = CascadePredictor(graph, cc["horizon_s"], cc["tau_s"], cc["watch"], cc["imminent"],
                                cc["min_spread"], cc["learn_online"])
        remediation = RemediationAgent.from_config(cfg)
        chain = build_actuators(cfg, remediation=remediation)
        return cls(cfg, dataset, parser, scorer, build_threshold(cfg, scorer.calibration), pred,
                   chain, remediation=remediation)

    # ------------------------------------------------------------------ ingest
    def ingest_lines(self, lines: Iterable[str]) -> list[Alert]:
        with self._lock:
            windows: list[Window] = []
            for line in lines:
                self.stats["lines"] += 1
                rec = parse_line(self.dataset, line)
                if rec is None:
                    self.stats["unparsed"] += 1
                    continue
                eid = self.parser.parse(rec.content, learn=self.online_learning)
                if eid == UNK_ID or eid >= self.scorer.vocab_size:
                    eid = UNK_ID
                    self.stats["unknown_templates"] += 1
                w = self.buffer.push(group_key(self.group_by, rec), rec.ts, eid, rec.label, rec.component)
                if w is not None:
                    windows.append(w)
            return self._score(windows)

    def flush(self) -> list[Alert]:
        with self._lock:
            return self._score(self.buffer.flush())

    def _score(self, windows: list[Window]) -> list[Alert]:
        if not windows:
            return []
        x = np.asarray([w.event_ids for w in windows], dtype=np.int64)
        scores, tok = self.scorer.score(x, token_errors=True)
        out: list[Alert] = []
        for w, s, te in zip(windows, scores, tok):
            s = float(s)
            thr, flagged = self.threshold.update(s)
            self.stats.update(windows=self.stats["windows"] + 1, last_score=s, last_threshold=thr)
            self.remediation.verify_telemetry(w.component, s, thr, w.end_ts)
            if not flagged:
                continue
            self.cascade.observe(w.end_ts, w.component)
            ass = self.cascade.assess(w.end_ts)
            sev = "critical" if (s >= self.crit_ratio * thr or ass["level"] == "imminent") else "warning"
            eid = int(w.event_ids[int(np.argmax(te))])      # worst-reconstructed event = likely culprit
            alert = Alert(ts=w.end_ts, component=w.component, key=w.key, score=s, threshold=thr,
                          severity=sev, cascade_level=ass["level"], at_risk=ass["at_risk"],
                          suspect_event_id=eid, suspect_template=self.parser.template_of(eid))
            self.stats["alerts"] += 1
            self.alerts.append(alert)
            self.chain.dispatch(alert)
            self.remediation.process_alert(alert, ass)
            out.append(alert)
        return out

    # ------------------------------------------------------------------ admin
    def status(self) -> dict:
        with self._lock:
            return {"dataset": self.dataset, "model": self.cfg["model"]["kind"],
                    "vocab_size": self.scorer.vocab_size, "window_size": self.scorer.window_size,
                    "threshold_history": len(self.threshold.hist),
                    "remediation_mode": self.remediation.mode,
                    "remediation_actions": len(self.remediation.history),
                    "remediation_in_flight": len(self.remediation.guard._in_flight),
                    **self.stats}

    def cascade_state(self) -> dict:
        with self._lock:
            now = max((a.ts for a in self.alerts), default=0.0)
            return self.cascade.assess(now)

    def remediation_state(self) -> dict:
        with self._lock:
            return self.remediation.get_state()

    def remediation_history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return self.remediation.get_history(limit)

    def reset_threshold(self) -> None:
        with self._lock:
            self.threshold.reset()
