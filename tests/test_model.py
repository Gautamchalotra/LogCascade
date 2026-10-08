import numpy as np
import pytest
import torch

from src.model.autoencoders import build_model
from src.model.baselines import IsolationForestBaseline
from src.model.scorer import AnomalyScorer, score_array
from src.model.trainer import train_model

T, V = 8, 12


def normal_windows(n, rng):
    base = np.arange(2, 10)
    return np.stack([np.roll(base, -rng.integers(0, 8)) for _ in range(n)])


def cfg(kind):
    return {"train": {"epochs": 40, "batch_size": 64, "lr": 0.01, "weight_decay": 0.0, "val_frac": 0.2,
                      "patience": 8, "dedupe": False, "device": "cpu", "seed": 0, "calib_quantile": 0.99},
            "model": {"kind": kind,
                      "lstm": {"embed_dim": 16, "hidden_dim": 32, "latent_dim": 8, "num_layers": 1, "dropout": 0.0},
                      "transformer": {"d_model": 32, "nhead": 4, "num_layers": 1, "ff_dim": 64,
                                      "latent_dim": 8, "dropout": 0.0}}}


@pytest.mark.parametrize("kind", ["lstm", "transformer"])
def test_shapes_and_padding(kind):
    params = cfg(kind)["model"][kind]
    m = build_model(kind, V, T, params)
    x = torch.randint(2, V, (4, T))
    x[0, 5:] = 0
    logits, z = m(x)
    assert logits.shape == (4, T, V) and z.shape[0] == 4 and torch.isfinite(logits).all()


@pytest.mark.parametrize("kind", ["lstm", "transformer"])
def test_trains_and_separates(kind, tmp_path):
    rng = np.random.default_rng(0)
    Xn = normal_windows(600, rng)
    path = tmp_path / "m.pt"
    calib = train_model(Xn, V, T, cfg(kind), path)
    sc = AnomalyScorer.from_checkpoint(path, "cpu")
    anomal = np.stack([rng.permutation(np.arange(2, 10)) for _ in range(100)])
    sn, sa = sc.score(normal_windows(100, rng)), sc.score(anomal)
    assert sa.mean() > 3 * sn.mean() and calib["quantile_value"] >= calib["median"]
    s, tok = sc.score(anomal[:3], token_errors=True)
    assert tok.shape == (3, T)
    assert np.isfinite(sc.score(np.full((2, T), 999))).all()      # unseen ids -> UNK, no crash


def test_isolation_forest_baseline():
    rng = np.random.default_rng(0)
    base = normal_windows(1000, rng)
    rows = rng.integers(0, 1000, 600)
    base[rows, rng.integers(0, T, 600)] = rng.integers(2, 10, 600)   # light noise -> many unique windows
    ifb = IsolationForestBaseline(V, n_estimators=100).fit(base)
    odd = np.full((10, T), 2)       # one event dominating the window
    assert ifb.score(odd).mean() > ifb.score(base[:50]).mean()
