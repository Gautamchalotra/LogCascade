from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from api.schemas import ActionRequest, IngestRequest, IngestResponse
from src.common.config import load_config
from src.common.logging_utils import get_logger

log = get_logger("api")


def create_app(engine=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.engine is None:
            try:
                from src.engine.stream_engine import StreamEngine
                app.state.engine = StreamEngine.from_artifacts(load_config())
                log.info("engine loaded")
            except FileNotFoundError as e:
                log.error("engine not loaded: %s", e)
        yield

    app = FastAPI(title="Log Cascade Agent", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine

    def get_engine(request: Request):
        eng = request.app.state.engine
        if eng is None:
            raise HTTPException(503, "model artifacts not loaded; run prepare/train first")
        return eng
        
    def get_engine_opt(request: Request):
        return request.app.state.engine

    @app.get("/")
    def index():
        p = Path(__file__).resolve().parent.parent / 'ui' / 'dashboard.html'
        if not p.exists():
            raise HTTPException(404, "dashboard.html not found")
        return HTMLResponse(p.read_text(encoding="utf-8"))

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok", "engine_loaded": request.app.state.engine is not None}

    # plain `def` endpoints run in FastAPI's threadpool; the engine serialises with its own lock
    @app.post("/ingest", response_model=IngestResponse)
    def ingest(req: IngestRequest, eng=Depends(get_engine)):
        alerts = eng.ingest_lines(req.lines)
        st = eng.status()
        return IngestResponse(lines=len(req.lines), alerts=[a.to_dict() for a in alerts],
                              last_score=st["last_score"], last_threshold=st["last_threshold"])

    @app.get("/status")
    def status(eng=Depends(get_engine_opt)):
        if eng is None:
            return {"dataset": None, "model": None, "vocab_size": 0, "window_size": 0,
                    "threshold_history": 0, "lines": 0, "unparsed": 0,
                    "unknown_templates": 0, "windows": 0, "alerts": 0,
                    "last_score": None, "last_threshold": None}
        return eng.status()

    @app.get("/alerts")
    def alerts(limit: int = 50, eng=Depends(get_engine_opt)):
        if eng is None:
            return []
        return [a.to_dict() for a in list(eng.alerts)[-max(1, min(limit, 1000)):]][::-1]

    @app.get("/cascade")
    def cascade(eng=Depends(get_engine_opt)):
        if eng is None:
            return {"level": "none", "spread": 0, "active": [], "at_risk": []}
        return eng.cascade_state()

    @app.get("/templates")
    def templates(limit: int = 100, eng=Depends(get_engine_opt)):
        if eng is None:
            return {}
        t = eng.parser.templates()
        return dict(list(t.items())[:max(1, min(limit, 5000))])

    @app.post("/threshold/reset")
    def reset(eng=Depends(get_engine)):
        eng.reset_threshold()
        return {"ok": True}
        
    @app.post("/pipeline/prepare")
    def pipeline_prepare(dataset: str | None = None):
        from src.engine.pipeline import prepare
        cfg = load_config()
        ds = dataset or cfg["dataset"]
        return prepare(cfg, ds)

    @app.post("/pipeline/train")
    def pipeline_train(dataset: str | None = None):
        from src.engine.pipeline import train
        cfg = load_config()
        ds = dataset or cfg["dataset"]
        result = train(cfg, ds)
        # Reload engine after training
        try:
            from src.engine.stream_engine import StreamEngine
            app.state.engine = StreamEngine.from_artifacts(cfg, ds)
            log.info("engine reloaded after training")
        except Exception as e:
            log.warning("engine reload failed: %s", e)
        return result

    @app.post("/pipeline/evaluate")
    def pipeline_evaluate(dataset: str | None = None):
        from src.engine.pipeline import evaluate
        cfg = load_config()
        ds = dataset or cfg["dataset"]
        return evaluate(cfg, ds)
        
    @app.get("/eval")
    def get_eval(dataset: str | None = None):
        import json
        cfg = load_config()
        ds = dataset or cfg["dataset"]
        from src.common.config import get_paths
        P = get_paths(cfg, ds)
        eval_path = P.ckpt / "eval.json"
        if not eval_path.exists():
            raise HTTPException(404, "no evaluation results; run evaluate first")
        return json.loads(eval_path.read_text())

    @app.get("/remediation/state")
    def remediation_state(eng=Depends(get_engine_opt)):
        if eng is None:
            return {"mode": "simulation", "auto_remediate": False, "stats": {}, "cluster": {}, "mesh": {}}
        return eng.remediation_state()

    @app.get("/remediation/history")
    def remediation_history(limit: int = 50, eng=Depends(get_engine_opt)):
        if eng is None:
            return []
        return eng.remediation_history(limit)

    @app.post("/remediation/action")
    def trigger_remediation_action(req: ActionRequest, eng=Depends(get_engine)):
        event = eng.remediation.trigger_manual_action(req.action, req.target, req.reason)
        return event.to_dict()

    @app.post("/remediation/reset")
    def reset_remediation(eng=Depends(get_engine)):
        eng.remediation.reset()
        return {"ok": True}

    return app


app = create_app()
