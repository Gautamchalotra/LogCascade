"""Loghub line parsers (HDFS, BGL, Thunderbird, ZooKeeper) -> LogRecord."""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from src.common.schemas import LogRecord

_HDFS_RE = re.compile(r"^(\d{6})\s+(\d{6})\s+(\d+)\s+(\w+)\s+([\w.$\-]+):\s?(.*)$")
_BLK_RE = re.compile(r"blk_-?\d+")
_ZK_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+) - (\w+)\s+\[(.*?)\] - (.*)$")
_ZK_CLASS_RE = re.compile(r"([A-Za-z][\w.$]*)@\d+")


def parse_hdfs(line: str) -> Optional[LogRecord]:
    m = _HDFS_RE.match(line.strip())
    if not m:
        return None
    d, t, _pid, _level, cls, msg = m.groups()
    ts = datetime.strptime(d + t, "%y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    blk = _BLK_RE.search(msg)
    return LogRecord(ts=ts, content=msg, component=cls, entity=blk.group(0) if blk else None)


def parse_bgl(line: str) -> Optional[LogRecord]:
    p = line.rstrip("\n").split(None, 9)
    if len(p) < 10:
        return None
    try:
        ts = float(p[1])
    except ValueError:
        return None
    rack_mid = "-".join(p[3].split("-")[:2])
    return LogRecord(ts=ts, content=" ".join(p[7:]), component=rack_mid,
                     label=0 if p[0] == "-" else 1)


def parse_thunderbird(line: str) -> Optional[LogRecord]:
    p = line.rstrip("\n").split(None, 7)
    if len(p) < 8:
        return None
    try:
        ts = float(p[1])
    except ValueError:
        return None
    rest = p[7].split(None, 1)          # drop "src@host" / "host/host" token
    content = rest[1] if len(rest) == 2 else rest[0]
    return LogRecord(ts=ts, content=content, component=p[3], label=0 if p[0] == "-" else 1)


def parse_zookeeper(line: str) -> Optional[LogRecord]:
    m = _ZK_RE.match(line.strip())
    if not m:
        return None
    d, ms, level, thread, msg = m.groups()
    ts = datetime.strptime(d, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() + int(ms) / 1000
    cm = _ZK_CLASS_RE.search(thread)
    comp = cm.group(1) if cm else (re.split(r"[:\[/]", thread)[0] or thread)
    return LogRecord(ts=ts, content=f"{level} {msg}", component=comp)


PARSERS: dict[str, Callable[[str], Optional[LogRecord]]] = {
    "HDFS": parse_hdfs, "BGL": parse_bgl, "Thunderbird": parse_thunderbird, "ZooKeeper": parse_zookeeper,
}


def parse_line(dataset: str, line: str) -> Optional[LogRecord]:
    return PARSERS[dataset](line)


def load_hdfs_labels(path: str) -> dict[str, int]:
    with open(path, newline="") as f:
        return {r["BlockId"]: int(r["Label"].strip().lower() == "anomaly") for r in csv.DictReader(f)}


def iter_records(dataset: str, path: str, max_lines: Optional[int] = None,
                 hdfs_labels: Optional[dict] = None) -> Iterator[LogRecord]:
    fn = PARSERS[dataset]
    with open(path, "r", errors="ignore") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            rec = fn(line)
            if rec is None:
                continue
            if hdfs_labels is not None and rec.entity:
                rec.label = hdfs_labels.get(rec.entity, 0)
            yield rec
