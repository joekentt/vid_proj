"""Fila de jobs sobre Redis.

Dois papéis:
  - PRODUTOR (API): enqueue(job) -> salva estado + empilha o ID.
  - CONSUMIDOR (worker Colab): claim() -> bloqueia até um ID chegar.

O estado completo do Job vive numa chave separada (JSON). A fila carrega só
o ID. Assim qualquer ponta lê/atualiza o estado sem disputar a fila.
"""
from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.models import Job, JobStatus
from app.queue.redis_client import get_redis


def _state_key(job_id: str) -> str:
    return f"{get_settings().job_state_prefix}{job_id}"


async def save_job(job: Job) -> None:
    """Persiste (ou atualiza) o estado completo do job."""
    settings = get_settings()
    r = get_redis()
    job.touch()
    await r.set(
        _state_key(job.id),
        job.model_dump_json(),
        ex=settings.job_ttl_seconds,
    )


async def get_job(job_id: str) -> Optional[Job]:
    """Lê o estado de um job, ou None se não existir/expirou."""
    r = get_redis()
    raw = await r.get(_state_key(job_id))
    if raw is None:
        return None
    return Job.model_validate_json(raw)


async def enqueue(job: Job) -> None:
    """PRODUTOR: salva o estado e empilha o ID para um worker pegar."""
    settings = get_settings()
    r = get_redis()
    await save_job(job)
    # LPUSH + BRPOP = fila FIFO.
    await r.lpush(settings.job_queue_key, job.id)


async def claim(timeout_seconds: int = 30) -> Optional[Job]:
    """CONSUMIDOR: bloqueia até timeout esperando um job.

    Retorna o Job já transicionado para RUNNING, ou None se nada chegou
    (o worker faz polling chamando isto em loop).

    Em Redis gerenciado cada BRPOP conta como um comando no plano grátis
    (Upstash: 500K/mês). Com timeout de 5s o polling ocioso gastaria ~17K
    comandos/dia; com 30s, ~3K/dia. A latência para pegar um job novo não
    muda: o BRPOP acorda no instante em que o ID chega à fila.
    """
    settings = get_settings()
    r = get_redis()
    popped = await r.brpop(settings.job_queue_key, timeout=timeout_seconds)
    if popped is None:
        return None

    _key, job_id = popped
    job = await get_job(job_id)
    if job is None:
        return None  # estado expirou enquanto estava na fila

    # Job pode ter sido cancelado antes de ser pego.
    if job.status == JobStatus.CANCELLED:
        return None

    if job.can_transition_to(JobStatus.RUNNING):
        job.status = JobStatus.RUNNING
        job.progress.stage = "generating"
        job.progress.total_scenes = len(job.scenes)
        await save_job(job)
    return job


async def mark_done(job: Job, final_asset_id: str) -> None:
    job.status = JobStatus.DONE
    job.final_asset_id = final_asset_id
    job.progress.stage = "done"
    job.progress.percent = 100.0
    await save_job(job)


async def mark_failed(job: Job, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error
    job.progress.stage = "failed"
    await save_job(job)
