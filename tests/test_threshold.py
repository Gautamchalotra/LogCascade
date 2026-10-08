import numpy as np

from src.model.threshold import DynamicThreshold


def test_spike_flagged_and_robust():
    rng = np.random.default_rng(0)
    t = DynamicThreshold(window=500, k=6, min_samples=50)
    flags = [t.update(float(s))[1] for s in rng.normal(1.0, 0.05, 300)]
    assert sum(flags) <= 2
    assert t.update(3.0)[1]
    for _ in range(20):                    # a burst must not inflate the threshold
        t.update(3.0)
    assert t.update(3.0)[1]


def test_fallback_and_floor():
    t = DynamicThreshold(min_samples=10, fallback=2.0, floor=1.5)
    assert t.current() == 2.0
    for _ in range(50):
        t.update(0.1)
    assert t.current() >= 1.5


def test_adapts_to_sustained_shift():
    t = DynamicThreshold(window=100, k=4, min_samples=20)
    for _ in range(100):
        t.update(1.0)
    first = t.current()
    for _ in range(400):
        t.update(1.6)
    assert t.current() > first
