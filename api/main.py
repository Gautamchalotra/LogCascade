from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request

from api.schemas import IngestRequest, IngestResponse
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
    def status(eng=Depends(get_engine)):
        return eng.status()

    @app.get("/alerts")
    def alerts(limit: int = 50, eng=Depends(get_engine)):
        return [a.to_dict() for a in list(eng.alerts)[-max(1, min(limit, 1000)):]][::-1]

    @app.get("/cascade")
    def cascade(eng=Depends(get_engine)):
        return eng.cascade_state()

    @app.get("/templates")
    def templates(limit: int = 100, eng=Depends(get_engine)):
        t = eng.parser.templates()
        return dict(list(t.items())[:max(1, min(limit, 5000))])

    @app.post("/threshold/reset")
    def reset(eng=Depends(get_engine)):
        eng.reset_threshold()
        return {"ok": True}

    return app


app = create_app()
