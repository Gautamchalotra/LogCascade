"""Offline pipeline: prepare (parse+window) -> train -> evaluate (+ fit cascade graph)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, precision_recall_fscore_support, roc_auc_score

from src.buffer.sliding_window import SlidingWindowBuffer, group_key
from src.cascade.graph import CascadeGraph
from src.common.config import dataset_cfg, get_paths
from src.common.logging_utils import get_logger
from src.model.baselines import IsolationForestBaseline
from src.model.scorer import AnomalyScorer
from src.model.threshold import build_threshold
from src.model.trainer import train_model
from src.parser.drain_parser import LogTemplateParser
from src.parser.loaders import iter_records, load_hdfs_labels

log = get_logger("pipeline")


def prepare(cfg: dict, dataset: str, max_lines: int | None = None) -> dict:
    ds, P = dataset_cfg(cfg, dataset), get_paths(cfg, dataset)
    raw = P.raw / ds["file"]
    labels = None
    if ds.get("labels") and (P.raw / ds["labels"]).exists():
        labels = load_hdfs_labels(str(P.raw / ds["labels"]))
    parser = LogTemplateParser(**cfg["parser"])
    buf = SlidingWindowBuffer(ds["window_size"], ds["stride"])   # unbounded keys offline
    windows, n = [], 0
    for rec in iter_records(dataset, str(raw), max_lines, labels):
        n += 1
        eid = parser.parse(rec.content, learn=True)
        w = buf.push(group_key(ds["group_by"], rec), rec.ts, eid, rec.label, rec.component)
        if w:
            windows.append(w)
        if n % 500_000 == 0:
            log.info("parsed %d lines, %d templates, %d windows", n, len(parser.templates()), len(windows))
    windows.extend(buf.flush())
    windows.sort(key=lambda w: w.end_ts)
    X = np.asarray([w.event_ids for w in windows], dtype=np.int32)
    y = np.asarray([w.label for w in windows], dtype=np.int8)
    ts = np.asarray([w.end_ts for w in windows], dtype=np.float64)
    comp = np.asarray([w.component for w in windows], dtype=str)
    cut = int(len(X) * ds["train_ratio"])
    for name, arr in [("X", X), ("y", y), ("ts", ts), ("comp", comp)]:
        np.save(P.processed / f"{name}_train.npy", arr[:cut])
        np.save(P.processed / f"{name}_test.npy", arr[cut:])
    parser.save(str(P.processed / "drain_state.bin"))
    (P.processed / "templates.json").write_text(json.dumps(parser.templates(), indent=1))
    meta = {"vocab_size": parser.vocab_size, "window_size": ds["window_size"], "lines": n,
            "windows": len(X), "n_train": cut, "n_test": len(X) - cut,
            "anomalous_test_windows": int(y[cut:].sum())}
    (P.processed / "meta.json").write_text(json.dumps(meta, indent=1))
    log.info("prepare done: %s", meta)
    return meta


def train(cfg: dict, dataset: str) -> dict:
    P = get_paths(cfg, dataset)
    meta = json.loads((P.processed / "meta.json").read_text())
    X, y = np.load(P.processed / "X_train.npy"), np.load(P.processed / "y_train.npy")
    normal = X[y == 0]
    log.info("training on %d/%d normal windows", len(normal), len(X))
    return train_model(normal, meta["vocab_size"], meta["window_size"], cfg, P.ckpt / "model.pt")


def _best_f1(y, s):
    p, r, t = precision_recall_curve(y, s)
    f = 2 * p * r / np.maximum(p + r, 1e-12)
    i = int(np.argmax(f[:-1])) if len(t) else 0
    return float(f[i]), float(p[i]), float(r[i]), float(t[i])


def _metrics(y, s):
    if y.sum() == 0 or y.sum() == len(y):
        return {"note": "test split has a single class; supervised metrics undefined"}
    f1, p, r, t = _best_f1(y, s)
    return {"auroc": float(roc_auc_score(y, s)), "auprc": float(average_precision_score(y, s)),
            "best_f1": f1, "precision@best": p, "recall@best": r, "threshold@best": t}


def evaluate(cfg: dict, dataset: str) -> dict:
    P = get_paths(cfg, dataset)
    meta = json.loads((P.processed / "meta.json").read_text())
    L = lambda n: np.load(P.processed / f"{n}.npy")
    Xtr, ytr, Xte, yte, tte, cte = L("X_train"), L("y_train"), L("X_test"), L("y_test"), L("ts_test"), L("comp_test")

    scorer = AnomalyScorer.from_checkpoint(P.ckpt / "model.pt", cfg["train"].get("device", "auto"))
    s_ae = scorer.score(Xte)
    s_if = IsolationForestBaseline(meta["vocab_size"], cfg["baseline"]["n_estimators"],
                                   cfg["baseline"]["max_fit_samples"]).fit(Xtr[ytr == 0]).score(Xte)
    res = {"autoencoder": _metrics(yte, s_ae), "isolation_forest": _metrics(yte, s_if)}

    # streaming run with the dynamic threshold, exactly as in serving
    thr = build_threshold(cfg, scorer.calibration)
    flags = np.zeros(len(s_ae), dtype=bool)
    for i, s in enumerate(s_ae):
        flags[i] = thr.update(float(s))[1]
    if yte.sum():
        pr, rc, f1, _ = precision_recall_fscore_support(yte, flags, average="binary", zero_division=0)
        res["autoencoder"]["dynamic_threshold"] = {"precision": float(pr), "recall": float(rc), "f1": float(f1),
                                                   "alerts": int(flags.sum())}
    # failure-cascade graph from the alert stream
    cc = cfg["cascade"]
    alerts = [(float(t), str(c)) for t, c, f in zip(tte, cte, flags) if f]
    graph = CascadeGraph.fit(alerts, horizon_s=cc["horizon_s"], gap_s=cc["gap_s"], prior=cc["prior"])
    graph.save(P.ckpt / "cascade_graph.json")
    res["cascade"] = {"alerts": len(alerts), "components": len(graph.n_onsets),
                      "edges": sum(len(v) for v in graph.hits.values())}
    np.save(P.processed / "test_scores.npy", s_ae)
    (P.ckpt / "eval.json").write_text(json.dumps(res, indent=1))
    log.info("eval: %s", json.dumps(res, indent=1))
    return res
