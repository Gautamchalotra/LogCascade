from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class LogRecord:
    ts: float                      # epoch seconds
    content: str                   # message body fed to the template miner
    component: str = "unknown"     # node / rack / java class ... (cascade graph nodes)
    entity: Optional[str] = None   # session id (e.g. HDFS block id)
    label: int = 0                 # 1 = known anomaly (evaluation only)


@dataclass
class Window:
    event_ids: list
    end_ts: float
    key: str
    component: str
    label: int = 0


@dataclass
class Alert:
    ts: float
    component: str
    key: str
    score: float
    threshold: float
    severity: str                  # info | warning | critical
    cascade_level: str = "none"    # none | watch | imminent
    at_risk: list = field(default_factory=list)   # [(component, risk)] likely next victims
    suspect_event_id: Optional[int] = None
    suspect_template: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
