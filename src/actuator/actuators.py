"""Alert sinks / remediation hooks, fanned out with severity gating and cooldown."""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from abc import ABC, abstractmethod

from src.common.logging_utils import get_logger
from src.common.schemas import Alert

log = get_logger("actuator")
SEV = {"info": 0, "warning": 1, "critical": 2}


class Actuator(ABC):
    name = "actuator"

    def __init__(self, min_severity: str = "warning"):
        self.min_severity = min_severity

    @abstractmethod
    def handle(self, alert: Alert) -> None: ...


class LogActuator(Actuator):
    name = "log"

    def handle(self, alert: Alert) -> None:
        log.warning("[%s] %s score=%.3f thr=%.3f cascade=%s at_risk=%s suspect=%r",
                    alert.severity.upper(), alert.component, alert.score, alert.threshold,
                    alert.cascade_level, alert.at_risk, alert.suspect_template)


class CallbackActuator(Actuator):
    name = "callback"

    def __init__(self, fn, min_severity="warning"):
        super().__init__(min_severity)
        self.fn = fn

    def handle(self, alert: Alert) -> None:
        self.fn(alert)


class WebhookActuator(Actuator):
    name = "webhook"

    def __init__(self, url: str, timeout_s: float = 2.0, min_severity="critical"):
        super().__init__(min_severity)
        self.url, self.timeout_s = url, timeout_s

    def handle(self, alert: Alert) -> None:
        req = urllib.request.Request(self.url, data=json.dumps(alert.to_dict()).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=self.timeout_s).read()


class CommandActuator(Actuator):
    """Runs an allowlisted argv template (never a shell). dry_run=True only logs."""
    name = "command"

    def __init__(self, argv: list[str], dry_run: bool = True, min_severity="critical"):
        super().__init__(min_severity)
        self.argv, self.dry_run = argv, dry_run

    def handle(self, alert: Alert) -> None:
        safe = re.sub(r"[^\w.\-:]", "_", alert.component)        # log-derived text is untrusted
        argv = [a.format(component=safe, severity=alert.severity) for a in self.argv]
        if self.dry_run:
            log.info("[dry-run] would execute: %s", argv)
        else:
            subprocess.run(argv, check=False, timeout=30)


class ActuatorChain:
    def __init__(self, actuators: list[Actuator], cooldown_s: float = 60.0):
        self.actuators, self.cooldown_s = actuators, cooldown_s
        self._last: dict[tuple[str, str], float] = {}

    def dispatch(self, alert: Alert) -> list[str]:
        key = (alert.component, alert.severity)
        if alert.ts - self._last.get(key, -1e18) < self.cooldown_s and alert.severity != "critical":
            return []
        self._last[key] = alert.ts
        fired = []
        for a in self.actuators:
            if SEV[alert.severity] < SEV[a.min_severity]:
                continue
            try:
                a.handle(alert)
                fired.append(a.name)
            except Exception as e:                      # one broken sink must not stop the others
                log.error("actuator %s failed: %s", a.name, e)
        return fired


def build_actuators(cfg: dict) -> ActuatorChain:
    c = cfg["actuator"]
    acts: list[Actuator] = []
    if c["log"]["enabled"]:
        acts.append(LogActuator(c["log"]["min_severity"]))
    if c["webhook"]["enabled"]:
        w = c["webhook"]
        acts.append(WebhookActuator(w["url"], w["timeout_s"], w["min_severity"]))
    if c["command"]["enabled"]:
        m = c["command"]
        acts.append(CommandActuator(m["argv"], m["dry_run"], m["min_severity"]))
    return ActuatorChain(acts, c["cooldown_s"])
