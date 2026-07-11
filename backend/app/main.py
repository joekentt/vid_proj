"""Entrypoint do FastAPI — junta config, storage, fila e rotas.

Sobe com:  uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_jobs
from app.config import get_settings
from app.queue.redis_client import close_redis
from app.storage.s3_client import ensure_bucket


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: garante que o bucket existe.
    try:
        ensure_bucket()
    except Exception as exc:  # storage pode estar fora no dev; não derruba a API
        print(f"[startup] aviso: storage indisponível ({exc})")
    yield
    # Shutdown: fecha a pool do Redis.
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Video Studio API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_jobs.router)

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
