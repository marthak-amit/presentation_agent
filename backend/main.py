"""FastAPI app factory. Run: uvicorn backend.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import decks as decks_api
from .services import Services, build_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(svc: Services | None = None) -> FastAPI:
    svc = svc or build_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="PresenterAgent", lifespan=lifespan)
    app.state.svc = svc
    app.state.tasks = set()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(decks_api.router)
    app.mount("/media", StaticFiles(directory=str(svc.settings.decks_dir)), name="media")

    @app.get("/health")
    async def health():
        s = svc.settings
        return {"ok": True, "llm": svc.llm.name, "presenter": s.presenter_name}

    return app


app = create_app()
