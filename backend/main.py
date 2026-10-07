"""FastAPI app factory. Run: uvicorn backend.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import decks as decks_api
from .api import preflight as preflight_api
from .api import sessions as sessions_api
from .session import ws as ws_api
from .ingest import pipeline
from .services import Services, build_services
from .tts.pregen import pregenerate_deck_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(svc: Services | None = None) -> FastAPI:
    svc = svc or build_services()
    if pregenerate_deck_audio not in pipeline.POST_HOOKS:
        pipeline.POST_HOOKS.append(pregenerate_deck_audio)

    async def warm_up() -> None:
        """Pay the slow first-use costs at startup, not during the first question."""
        from .tts.clips import ensure_all_clips

        async def embedder():
            try:
                await asyncio.to_thread(lambda: svc.kb.embedder.embed(["warm up"]))
            except Exception as e:
                logging.getLogger("warmup").warning("embedder warm-up failed: %s", e)

        async def clips():
            try:
                await ensure_all_clips(svc.audio, svc.settings.stock_dir, svc.settings.presenter_name)
            except Exception as e:
                logging.getLogger("warmup").warning("stock clips warm-up failed: %s", e)

        async def llm():
            warm = getattr(svc.llm, "warm", None)
            if warm:
                await warm()

        await asyncio.gather(embedder(), clips(), llm())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(warm_up())
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        yield

    app = FastAPI(title="PresenterAgent", lifespan=lifespan)
    app.state.svc = svc
    app.state.tasks = set()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(decks_api.router)
    app.include_router(sessions_api.router)
    app.include_router(preflight_api.router)
    app.include_router(ws_api.router)
    app.mount("/media", StaticFiles(directory=str(svc.settings.decks_dir)), name="media")
    app.mount("/stock", StaticFiles(directory=str(svc.settings.stock_dir)), name="stock")

    @app.get("/health")
    async def health():
        s = svc.settings
        return {"ok": True, "presenter": s.presenter_name,
                "services": {"llm": svc.llm.name, "stt": "deepgram" if s.use_real_stt else "fake",
                             "tts": svc.tts.primary_name, "embeddings": svc.kb.embedder.name if svc._kb else "lazy"}}

    return app


app = create_app()
