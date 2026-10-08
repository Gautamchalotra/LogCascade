"""python -m src.engine.cli {prepare,train,evaluate,replay} --dataset HDFS"""
from __future__ import annotations

import argparse
import json

from src.common.config import load_config
from src.engine import pipeline


def main():
    ap = argparse.ArgumentParser(prog="log-cascade-agent")
    ap.add_argument("cmd", choices=["prepare", "train", "evaluate", "replay"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--max-lines", type=int, default=None, help="prepare/replay: cap lines (huge datasets)")
    ap.add_argument("--file", default=None, help="replay: log file to stream through the engine")
    ap.add_argument("--model", choices=["lstm", "transformer"], default=None)
    a = ap.parse_args()

    cfg = load_config(a.config)
    ds = a.dataset or cfg["dataset"]
    cfg["dataset"] = ds
    if a.model:
        cfg["model"]["kind"] = a.model

    if a.cmd == "prepare":
        pipeline.prepare(cfg, ds, a.max_lines)
    elif a.cmd == "train":
        pipeline.train(cfg, ds)
    elif a.cmd == "evaluate":
        print(json.dumps(pipeline.evaluate(cfg, ds), indent=2))
    else:
        from src.engine.stream_engine import StreamEngine
        eng = StreamEngine.from_artifacts(cfg, ds)
        n, buf = 0, []
        with open(a.file, errors="ignore") as f:
            for line in f:
                buf.append(line)
                n += 1
                if len(buf) >= 512 or (a.max_lines and n >= a.max_lines):
                    for al in eng.ingest_lines(buf):
                        print(json.dumps(al.to_dict()))
                    buf = []
                if a.max_lines and n >= a.max_lines:
                    break
        for al in eng.ingest_lines(buf) + eng.flush():
            print(json.dumps(al.to_dict()))
        print(json.dumps(eng.status(), indent=2))


if __name__ == "__main__":
    main()
