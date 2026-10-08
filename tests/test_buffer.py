import numpy as np

from src.buffer.sliding_window import SlidingWindowBuffer, count_vectors


def test_sliding_and_flush():
    b = SlidingWindowBuffer(size=3, stride=1)
    out = [b.push("k", float(i), i + 2, component="c") for i in range(5)]
    assert out[:2] == [None, None]
    assert [w.event_ids for w in out[2:]] == [[2, 3, 4], [3, 4, 5], [4, 5, 6]]
    b.push("short", 9.0, 7)
    fl = b.flush()
    assert len(fl) == 1 and fl[0].event_ids == [7, 0, 0] and fl[0].key == "short"


def test_stride_and_label():
    b = SlidingWindowBuffer(size=4, stride=2)
    ws = [b.push("k", i, 2, label=int(i == 5)) for i in range(8)]
    ws = [w for w in ws if w]
    assert len(ws) == 3 and ws[1].label == 1 and ws[0].label == 0


def test_count_vectors():
    x = np.array([[2, 2, 3, 0]])
    f = count_vectors(x, 5)
    assert np.allclose(f[0], [0, 0, 2 / 3, 1 / 3, 0])
