"""Schemas de Job — a unidade de trabalho que a fila processa.

Um Job agrega uma ou mais Scenes e carrega o estado da geração de ponta a ponta.
A API cria o Job, o worker o consome, e o frontend faz polling do estado.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .scene import Scene


class JobStatus(str, Enum):
    """Máquina de estados de um job. Transições válidas:
    PENDING -> RUNNING -> {DONE, FAILED}
    qualquer estado -> CANCELLED (a pedido do usuário)
    """
    PENDING = "pending"     # na fila, aguardando worker
    RUNNING = "running"     # worker pegou e está gerando
    DONE = "done"           # vídeo final pronto
    FAILED = "failed"       # erro irrecuperável
    CANCELLED = "cancelled"


# Estados a partir dos quais nada mais muda.
TERMINAL_STATES = {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}


class JobProgress(BaseModel):
    """Progresso granular para a UI mostrar barra/etapas."""
    current_scene: int = 0
    total_scenes: int = 0
    stage: str = Field(
        default="queued",
        description="Etapa legível: 'queued', 'generating', 'chaining', 'audio'...",
    )
    percent: float = Field(default=0.0, ge=0.0, le=100.0)


class JobCreateRequest(BaseModel):
    """Payload que o frontend envia para criar um job."""
    title: str = Field(default="Untitled", max_length=200)
    scenes: list[Scene] = Field(min_length=1, max_length=50)


class Job(BaseModel):
    """Estado completo de um job. Serializado em JSON no Redis."""
    id: str
    title: str = "Untitled"
    status: JobStatus = JobStatus.PENDING
    scenes: list[Scene]
    progress: JobProgress = Field(default_factory=JobProgress)

    # Preenchido quando DONE.
    final_asset_id: Optional[str] = None
    # Preenchido quando FAILED.
    error: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def touch(self) -> None:
        """Atualiza o timestamp. Chamar a cada mutação de estado."""
        self.updated_at = datetime.now(timezone.utc)

    def can_transition_to(self, new: JobStatus) -> bool:
        """Valida transições para evitar estados impossíveis."""
        if self.status in TERMINAL_STATES:
            return False
        if new == JobStatus.RUNNING:
            return self.status == JobStatus.PENDING
        if new in {JobStatus.DONE, JobStatus.FAILED}:
            return self.status == JobStatus.RUNNING
        if new == JobStatus.CANCELLED:
            return True
        return False
