"""FastAPI app factory. Run: uvicorn backend.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import decks as decks_api
from .session import ws as ws_api
from .ingest import pipeline
from .services import Services, build_services
from .tts.pregen import pregenerate_deck_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(svc: Services | None = None) -> FastAPI:
    svc = svc or build_services()
    if pregenerate_deck_audio not in pipeline.POST_HOOKS:
        pipeline.POST_HOOKS.append(pregenerate_deck_audio)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="PresenterAgent", lifespan=lifespan)
    app.state.svc = svc
    app.state.tasks = set()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(decks_api.router)
    app.include_router(ws_api.router)
    app.mount("/media", StaticFiles(directory=str(svc.settings.decks_dir)), name="media")
    app.mount("/stock", StaticFiles(directory=str(svc.settings.stock_dir)), name="stock")

    @app.get("/health")
    async def health():
        s = svc.settings
        return {"ok": True, "llm": svc.llm.name, "presenter": s.presenter_name}

    return app


app = create_app()
