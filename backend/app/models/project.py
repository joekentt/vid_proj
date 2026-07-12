"""Schemas de Project (storyboard) — Fase 3.

Um Project agrupa uma sequência ordenada de Scenes que viram UM vídeo final.
Ao renderizar, cada cena é enfileirada como um Job independente (paraleliza
entre workers); um orquestrador no backend espera todos terminarem e
concatena os clipes com FFmpeg aplicando as transições configuradas.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .scene import Scene


class RenderQuality(str, Enum):
    PREVIEW = "preview"  # rápido/barato: menos passos, metade da resolução
    FINAL = "final"      # parâmetros exatamente como definidos nas cenas


class RenderStatus(str, Enum):
    """Máquina de estados de um render de projeto:
    RENDERING -> COMPOSITING -> DONE | FAILED
    """
    RENDERING = "rendering"        # cenas enfileiradas, aguardando workers
    COMPOSITING = "compositing"    # todas prontas; FFmpeg concatenando
    DONE = "done"
    FAILED = "failed"


RENDER_TERMINAL = {RenderStatus.DONE, RenderStatus.FAILED}


class ProjectRender(BaseModel):
    """Estado de UMA renderização do projeto (a mais recente fica no Project)."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    quality: RenderQuality = RenderQuality.FINAL
    status: RenderStatus = RenderStatus.RENDERING
    # scene_id -> job_id: o elo entre o projeto e a fila de jobs.
    scene_jobs: dict[str, str] = Field(default_factory=dict)
    scenes_done: int = 0
    total_scenes: int = 0
    final_asset_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class ProjectCreateRequest(BaseModel):
    """Payload de criação/atualização de projeto (PUT substitui tudo)."""
    title: str = Field(default="Untitled", max_length=200)
    description: str = Field(default="", max_length=2000)
    scenes: list[Scene] = Field(min_length=1, max_length=50)
    # Imagem de referência compartilhada (personagem/estilo) — Asset kind=image.
    reference_image_asset_id: Optional[str] = None
    # Método do registry (app.registry) para aplicar a referência nas cenas.
    consistency_method: str = "none"


class Project(BaseModel):
    """Estado completo de um projeto. Serializado em JSON no Redis."""
    id: str
    title: str = "Untitled"
    description: str = ""
    scenes: list[Scene]
    reference_image_asset_id: Optional[str] = None
    consistency_method: str = "none"

    # Render mais recente (histórico completo fica fora do escopo da Fase 3).
    current_render: Optional[ProjectRender] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    @property
    def ordered_scenes(self) -> list[Scene]:
        return sorted(self.scenes, key=lambda s: s.order)
