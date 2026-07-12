"""Fixtures compartilhadas dos testes.

O cliente Redis do app é um singleton lazy por processo. O pytest-asyncio
cria um event loop NOVO por teste, então uma pool criada no teste anterior
ficaria presa a um loop já fechado ("Event loop is closed"). Fechar a pool
ao fim de cada teste força a recriação no loop corrente.
"""
import pytest

from app.queue.redis_client import close_redis


@pytest.fixture(autouse=True)
async def _fresh_redis_pool():
    yield
    await close_redis()
