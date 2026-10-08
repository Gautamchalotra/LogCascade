from typing import Any, Optional

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    lines: list[str] = Field(..., min_length=1, max_length=5000)


class IngestResponse(BaseModel):
    lines: int
    alerts: list[dict[str, Any]]
    last_score: Optional[float] = None
    last_threshold: Optional[float] = None


class ActionRequest(BaseModel):
    action: str = Field(..., description="remediation action type e.g. restart_pod, scale_deployment, circuit_break")
    target: str = Field(..., description="target component or node name")
    reason: Optional[str] = "manual_trigger"

