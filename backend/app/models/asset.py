"""Schemas de Asset — qualquer arquivo binário que o sistema produz ou consome.

Imagens iniciais, clipes gerados, áudio, e o vídeo final são todos Assets.
Ficam no storage (R2/MinIO) e são referenciados por ID em todo o sistema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class AssetKind(str, Enum):
    IMAGE = "image"
    VIDEO_CLIP = "video_clip"   # clipe de uma cena individual
    VIDEO_FINAL = "video_final"  # resultado concatenado
    AUDIO = "audio"             # TTS, música, mixagem (Fase 4)


class Asset(BaseModel):
    id: str = Field(description="ID único do asset.")
    kind: AssetKind
    storage_key: str = Field(
        description="Chave no bucket S3-like, ex: 'jobs/abc/scene-0.mp4'.",
    )
    url: Optional[HttpUrl] = Field(
        default=None,
        description="URL pré-assinada para download/preview. Pode expirar.",
    )
    mime_type: str = "video/mp4"
    size_bytes: Optional[int] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # Metadados úteis para depuração e UI.
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
