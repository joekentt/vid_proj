"""Cliente Redis assíncrono compartilhado.

Usa redis.asyncio para não bloquear o event loop do FastAPI. Uma única
connection pool é reutilizada em todo o processo.

Funciona tanto com o Redis local do docker-compose (redis://) quanto com um
Redis gerenciado com TLS (rediss://, ex.: Upstash) — o esquema da REDIS_URL
decide; nenhum código muda.
"""
from __future__ import annotations

import redis.asyncio as redis

from app.config import get_settings

_pool: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Retorna o cliente Redis singleton (lazy)."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            # Conexões remotas (Colab -> Upstash) atravessam NATs que derrubam
            # sockets ociosos; keepalive + PING periódico detectam e refazem a
            # conexão em vez de travar num BRPOP morto.
            socket_keepalive=True,
            health_check_interval=30,
            socket_connect_timeout=10,
        )
    return _pool


async def close_redis() -> None:
    """Fecha a pool no shutdown da aplicação."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
