"""Rotas de Jobs — a interface HTTP que o frontend consome.

POST   /jobs          cria um job e enfileira
GET    /jobs/{id}     lê estado/progresso (frontend faz polling)
POST   /jobs/{id}/cancel
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.models import Job, JobCreateRequest, JobStatus
from app.queue import job_queue

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=Job, status_code=201)
async def create_job(req: JobCreateRequest) -> Job:
    """Cria um job a partir das cenas e o coloca na fila."""
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
