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


class RemediationActuator(Actuator):
    """Bridges alerts to the autonomous RemediationAgent."""
    name = "remediation"

    def __init__(self, agent, min_severity: str = "warning"):
        super().__init__(min_severity)
        self.agent = agent

    def handle(self, alert: Alert) -> None:
        self.agent.process_alert(alert, {"level": alert.cascade_level, "at_risk": alert.at_risk})


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


def build_actuators(cfg: dict, remediation=None) -> ActuatorChain:
    c = cfg.get("actuator", {})
    acts: list[Actuator] = []
    if c.get("log", {}).get("enabled", True):
        acts.append(LogActuator(c.get("log", {}).get("min_severity", "warning")))
    if c.get("webhook", {}).get("enabled", False):
        w = c["webhook"]
        acts.append(WebhookActuator(w["url"], w.get("timeout_s", 2.0), w.get("min_severity", "critical")))
    if c.get("command", {}).get("enabled", False):
        m = c["command"]
        acts.append(CommandActuator(m["argv"], m.get("dry_run", True), m.get("min_severity", "critical")))
    if remediation is not None:
        acts.append(RemediationActuator(remediation, min_severity="warning"))
    return ActuatorChain(acts, c.get("cooldown_s", 60.0))
