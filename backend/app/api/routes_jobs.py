"""Rotas de Jobs — a interface HTTP que o frontend consome.

POST   /jobs          cria um job e enfileira
GET    /jobs/{id}     lê estado/progresso (frontend faz polling)
POST   /jobs/{id}/cancel
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.models import AssetKind, Job, JobCreateRequest, JobStatus, Scene
from app.queue import job_queue
from app.storage import asset_store

router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _validate_init_image(scene: Scene) -> None:
    """Garante que o init_image_asset_id aponta para um Asset de imagem vivo.

    Validação com IO fica aqui (o validator do Pydantic cuida só da coerência
    interna da cena). Falhar na criação é barato; falhar no worker desperdiça
    tempo de GPU.
    """
    if scene.init_image_asset_id is None:
        return
    asset = await asset_store.get_asset(scene.init_image_asset_id)
    if asset is None:
        raise HTTPException(
            status_code=422,
            detail=f"cena '{scene.id}': init_image_asset_id "
                   f"'{scene.init_image_asset_id}' não existe ou expirou — "
                   "faça upload em POST /assets/images",
        )
    if asset.kind != AssetKind.IMAGE:
        raise HTTPException(
            status_code=422,
            detail=f"cena '{scene.id}': asset '{asset.id}' é "
                   f"'{asset.kind.value}', esperado 'image'",
        )


@router.post("", response_model=Job, status_code=201)
async def create_job(req: JobCreateRequest) -> Job:
    """Cria um job a partir das cenas e o coloca na fila."""
    for scene in req.scenes:
        await _validate_init_image(scene)
    job_id = uuid.uuid4().hex
    # Reordena as cenas pelo campo `order` para garantir consistência.
    scenes = sorted(req.scenes, key=lambda s: s.order)
    job = Job(id=job_id, title=req.title, scenes=scenes)
    await job_queue.enqueue(job)
    return job


@router.get("/{job_id}", response_model=Job)
async def read_job(job_id: str) -> Job:
    job = await job_queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    job = await job_queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.can_transition_to(JobStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job in state '{job.status.value}'",
        )
    job.status = JobStatus.CANCELLED
    job.progress.stage = "cancelled"
    await job_queue.save_job(job)
    return job
