"""Registro de Assets no Redis.

O binário vive no storage S3-like; o JSON do Asset (id, storage_key, kind,
metadados) vive no Redis com o mesmo TTL dos jobs. API, worker e testes usam
este módulo — o prefixo da chave não deve ser duplicado em lugar nenhum.
"""
from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.models import Asset
from app.queue.redis_client import get_redis

ASSET_KEY_PREFIX = "video_jobs:asset:"


async def save_asset(asset: Asset) -> None:
    """Grava (ou atualiza) o JSON do asset, renovando o TTL."""
    await get_redis().set(
        ASSET_KEY_PREFIX + asset.id,
        asset.model_dump_json(),
        ex=get_settings().job_ttl_seconds,
    )


async def get_asset(asset_id: str) -> Optional[Asset]:
    """Lê um asset pelo ID, ou None se não existir/expirou."""
    raw = await get_redis().get(ASSET_KEY_PREFIX + asset_id)
    if raw is None:
        return None
    return Asset.model_validate_json(raw)
