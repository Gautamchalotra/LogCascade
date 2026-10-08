from __future__ import annotations

import numpy as np
import torch

from src.model.autoencoders import PAD_ID, build_model, per_token_ce, window_error

UNK_ID = 1


def resolve_device(name: str = "auto") -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


@torch.no_grad()
def score_array(model, windows: np.ndarray, device, batch_size=1024, token_errors=False):
    model.eval()
    scores, toks = [], []
    for i in range(0, len(windows), batch_size):
        x = torch.as_tensor(windows[i:i + batch_size], dtype=torch.long, device=device)
        logits, _ = model(x)
        ce = per_token_ce(logits, x, PAD_ID)
        scores.append(window_error(ce, x, PAD_ID).cpu().numpy())
        if token_errors:
            toks.append(ce.cpu().numpy())
    s = np.concatenate(scores) if scores else np.zeros(0, dtype=np.float32)
    return (s, np.concatenate(toks) if toks else np.zeros((0, windows.shape[1]))) if token_errors else s


class AnomalyScorer:
    def __init__(self, model, vocab_size, window_size, calibration, device):
        self.model, self.vocab_size, self.window_size = model, vocab_size, window_size
        self.calibration, self.device = calibration, device

    @classmethod
    def from_checkpoint(cls, path, device="auto"):
        dev = resolve_device(device)
        ck = torch.load(path, map_location=dev, weights_only=True)
        model = build_model(ck["model_kind"], ck["vocab_size"], ck["window_size"], ck["model_params"])
        model.load_state_dict(ck["state_dict"])
        model.to(dev).eval()
        return cls(model, ck["vocab_size"], ck["window_size"], ck["calibration"], dev)

    def score(self, windows: np.ndarray, token_errors=False):
        w = np.asarray(windows, dtype=np.int64)
        w = np.where(w >= self.vocab_size, UNK_ID, w)      # templates unseen at training time
        return score_array(self.model, w, self.device, token_errors=token_errors)
