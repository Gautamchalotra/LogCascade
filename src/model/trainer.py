from __future__ import annotations

import copy

import numpy as np
import torch

from src.common.logging_utils import get_logger
from src.model.autoencoders import PAD_ID, build_model, per_token_ce, window_error
from src.model.scorer import resolve_device, score_array

log = get_logger("trainer")


def train_model(windows: np.ndarray, vocab_size: int, window_size: int, cfg: dict, ckpt_path) -> dict:
    """Train on NORMAL windows only; calibrate on a held-out normal split."""
    t, mc = cfg["train"], cfg["model"]
    kind = mc["kind"]
    params = mc[kind]
    device = resolve_device(t.get("device", "auto"))
    rng = np.random.default_rng(t.get("seed", 42))
    torch.manual_seed(t.get("seed", 42))

    if t.get("dedupe", True):
        windows = np.unique(windows, axis=0)
    perm = rng.permutation(len(windows))
    n_val = max(1, int(len(windows) * t["val_frac"]))
    val, tr = windows[perm[:n_val]], windows[perm[n_val:]]
    if len(tr) < 8:       # tiny / near-constant normal data: don't starve training of samples
        log.warning("only %d unique normal windows; using them for both training and calibration", len(windows))
        tr = val = windows
    log.info("train=%d val=%d vocab=%d kind=%s device=%s", len(tr), len(val), vocab_size, kind, device)

    model = build_model(kind, vocab_size, window_size, params).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=t["lr"], weight_decay=t["weight_decay"])
    best, best_state, bad, history = float("inf"), None, 0, []

    for epoch in range(t["epochs"]):
        model.train()
        idx = rng.permutation(len(tr))
        tot = n = 0
        for i in range(0, len(idx), t["batch_size"]):
            x = torch.as_tensor(tr[idx[i:i + t["batch_size"]]], dtype=torch.long, device=device)
            logits, _ = model(x)
            loss = window_error(per_token_ce(logits, x, PAD_ID), x, PAD_ID).mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item() * len(x)
            n += len(x)
        v = float(score_array(model, val, device).mean())
        history.append({"epoch": epoch, "train": tot / max(n, 1), "val": v})
        log.info("epoch %02d train=%.4f val=%.4f", epoch, tot / max(n, 1), v)
        if v < best - 1e-5:
            best, best_state, bad = v, copy.deepcopy(model.state_dict()), 0
        else:
            bad += 1
            if bad >= t["patience"]:
                break

    model.load_state_dict(best_state)
    vs = score_array(model, val, device)
    med = float(np.median(vs))
    calib = {
        "median": med,
        "mad": float(np.median(np.abs(vs - med)) * 1.4826),
        "quantile": float(t["calib_quantile"]),
        "quantile_value": float(np.quantile(vs, t["calib_quantile"])),
        "max": float(vs.max()),
    }
    torch.save({
        "state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
        "model_kind": kind, "model_params": dict(params),
        "vocab_size": int(vocab_size), "window_size": int(window_size),
        "calibration": calib, "history": history,
    }, ckpt_path)
    log.info("saved %s calib=%s", ckpt_path, calib)
    return calib
