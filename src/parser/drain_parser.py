"""Drain3 wrapper: raw log message -> stable integer Event ID.

Event ID = Drain3 cluster_id + 1, so 0 = PAD and 1 = UNK are reserved.
Cluster ids never change when a template generalises, so IDs are stable
between training and serving.
"""
from __future__ import annotations

from typing import Optional

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

PAD_ID = 0
UNK_ID = 1

DEFAULT_MASKS = [
    (r"blk_-?\d+", "BLK"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "IP"),
    (r"0x[0-9a-fA-F]+", "HEX"),
    (r"(?<![A-Za-z0-9_])[-+]?\d+(?![A-Za-z0-9_])", "NUM"),
]


class LogTemplateParser:
    def __init__(self, depth: int = 4, sim_th: float = 0.4, max_children: int = 100,
                 max_clusters: Optional[int] = None, load_state_from: Optional[str] = None,
                 masks=None):
        cfg = TemplateMinerConfig()
        cfg.drain_depth = depth
        cfg.drain_sim_th = sim_th
        cfg.drain_max_children = max_children
        cfg.drain_max_clusters = max_clusters
        cfg.profiling_enabled = False
        cfg.masking_instructions = [MaskingInstruction(p, n) for p, n in (masks or DEFAULT_MASKS)]
        persistence = FilePersistence(load_state_from) if load_state_from else None
        self.miner = TemplateMiner(persistence, config=cfg)   # loads state if file exists
        # Detach: Drain3 would otherwise re-serialise state on every template change.
        self.miner.persistence_handler = None

    def parse(self, content: str, learn: bool = True) -> int:
        content = content.strip()
        if learn:
            return self.miner.add_log_message(content)["cluster_id"] + 1
        cluster = self.miner.match(content, full_search_strategy="fallback")
        return UNK_ID if cluster is None else cluster.cluster_id + 1

    def template_of(self, event_id: int) -> Optional[str]:
        c = self.miner.drain.id_to_cluster.get(event_id - 1)
        return c.get_template() if c is not None else None

    def templates(self) -> dict[int, str]:
        return {c.cluster_id + 1: c.get_template() for c in self.miner.drain.clusters}

    @property
    def vocab_size(self) -> int:
        ids = [c.cluster_id + 1 for c in self.miner.drain.clusters]
        return (max(ids) if ids else 1) + 1

    def save(self, path: str) -> None:
        self.miner.persistence_handler = FilePersistence(path)
        self.miner.save_state("manual")
        self.miner.persistence_handler = None
