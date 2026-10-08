from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


def load_config(path: str | None = None) -> dict:
    path = path or os.environ.get("LOGCASCADE_CONFIG", "configs/default.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("LOGCASCADE_DATASET"):
        cfg["dataset"] = os.environ["LOGCASCADE_DATASET"]
    return cfg


def dataset_cfg(cfg: dict, dataset: str | None = None) -> dict:
    return cfg["datasets"][dataset or cfg["dataset"]]


@dataclass
class Paths:
    raw: Path
    processed: Path
    ckpt: Path


def get_paths(cfg: dict, dataset: str | None = None) -> Paths:
    ds = dataset or cfg["dataset"]
    p = Paths(
        raw=Path(cfg["paths"]["raw"]),
        processed=Path(cfg["paths"]["processed"]) / ds,
        ckpt=Path(cfg["paths"]["checkpoints"]) / ds,
    )
    p.processed.mkdir(parents=True, exist_ok=True)
    p.ckpt.mkdir(parents=True, exist_ok=True)
    return p
