from src.actuator.actuators import ActuatorChain, CallbackActuator
from src.cascade.graph import CascadeGraph
from src.cascade.predictor import CascadePredictor
from src.common.schemas import Alert


def history():
    out = []
    for i in range(10):
        t = i * 1000.0
        out += [(t, "db"), (t + 30, "api"), (t + 500, "cache")]   # db->api always, cache unrelated
    return out


def test_graph_learns_edges():
    g = CascadeGraph.fit(history(), horizon_s=120, gap_s=30)
    assert g.prob("db", "api") > 0.8 and g.prob("db", "cache") == 0.0
    assert g.downstream("db")[0][0] == "api"


def test_predictor_flags_next_victim(tmp_path):
    g = CascadeGraph.fit(history(), horizon_s=120, gap_s=30)
    g.save(tmp_path / "g.json")
    p = CascadePredictor(CascadeGraph.load(tmp_path / "g.json"), horizon_s=120, tau_s=60, learn_online=False)
    p.observe(50_000.0, "db")
    a = p.assess(50_010.0)
    assert a["at_risk"][0][0] == "api" and a["level"] in ("watch", "imminent")
    assert p.assess(50_000.0 + 1000)["level"] == "none"      # expired


def test_actuator_cooldown_and_gating():
    got = []
    chain = ActuatorChain([CallbackActuator(got.append, "critical")], cooldown_s=60)
    mk = lambda ts, sev: Alert(ts, "n1", "k", 2.0, 1.0, sev)
    chain.dispatch(mk(0, "warning"))                 # below sink's min severity
    assert got == []
    chain.dispatch(mk(1, "critical"))
    chain.dispatch(mk(2, "critical"))                # critical bypasses cooldown
    assert len(got) == 2
