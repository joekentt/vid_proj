"""Rotas de Projects (storyboard multi-cena) — Fase 3.

POST   /projects                    cria
GET    /projects                    lista
GET    /projects/{id}               lê (re-anexa finalizador órfão se preciso)
PUT    /projects/{id}               substitui título/cenas/referência
DELETE /projects/{id}
POST   /projects/{id}/render        enfileira as cenas e orquestra o final
GET    /projects/{id}/storyboard    grade com o 1º frame de cada cena
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import registry
from app.chaining import orchestrator, storyboard
from app.models import (
    Asset,
    AssetKind,
    Project,
    ProjectCreateRequest,
    RENDER_TERMINAL,
    RenderQuality,
)
from app.api.routes_jobs import _validate_init_image
from app.storage import asset_store, project_store

router = APIRouter(prefix="/projects", tags=["projects"])


async def _validate_request(req: ProjectCreateRequest) -> None:
    if req.consistency_method not in registry.CONSISTENCY_METHODS:
        raise HTTPException(
            status_code=422,
            detail=f"consistency_method '{req.consistency_method}' não existe "
                   f"no registry; opções: {sorted(registry.CONSISTENCY_METHODS)}")
    if req.consistency_method not in registry.implemented_methods():
        raise HTTPException(
            status_code=422,
            detail=f"consistency_method '{req.consistency_method}' está "
                   "registrado mas ainda não implementado "
                   "(GET /registry/consistency-methods)")
    if req.consistency_method != "none" and not req.reference_image_asset_id:
        raise HTTPException(
            status_code=422,
            detail="consistency_method exige reference_image_asset_id")
    if req.reference_image_asset_id:
        ref = await asset_store.get_asset(req.reference_image_asset_id)
        if ref is None:
            raise HTTPException(
                status_code=422,
                detail=f"reference_image_asset_id "
                       f"'{req.reference_image_asset_id}' não existe/expirou")
        if ref.kind != AssetKind.IMAGE:
            raise HTTPException(
                status_code=422,
                detail=f"asset de referência é '{ref.kind.value}', "
                       "esperado 'image'")
    for scene in req.scenes:
        await _validate_init_image(scene)


def _rendering(project: Project) -> bool:
    return (project.current_render is not None
            and project.current_render.status not in RENDER_TERMINAL)


async def _get_or_404(project_id: str) -> Project:
    project = await project_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("", response_model=Project, status_code=201)
async def create_project(req: ProjectCreateRequest) -> Project:
    await _validate_request(req)
    project = Project(id=uuid.uuid4().hex, **req.model_dump())
    await project_store.save_project(project)
    return project


@router.get("", response_model=list[Project])
async def list_projects() -> list[Project]:
    return await project_store.list_projects()


@router.get("/{project_id}", response_model=Project)
async def read_project(project_id: str) -> Project:
    project = await _get_or_404(project_id)
    # Auto-cura: se a API reiniciou no meio de um render, o estado persiste
    # no Redis mas a Task morreu — re-anexa o finalizador.
    if _rendering(project):
        orchestrator.ensure_finalizer(project.id, project.current_render.id)
    return project


@router.put("/{project_id}", response_model=Project)
async def update_project(project_id: str, req: ProjectCreateRequest) -> Project:
    project = await _get_or_404(project_id)
    if _rendering(project):
        raise HTTPException(
            status_code=409,
            detail="projeto está renderizando; aguarde ou cancele os jobs")
    await _validate_request(req)
    # Reconstrói (model_copy(update=...) não re-validaria as cenas).
    updated = Project(
        id=project.id,
        created_at=project.created_at,
        current_render=project.current_render,
        **req.model_dump(),
    )
    await project_store.save_project(updated)
    return updated


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str) -> None:
    project = await _get_or_404(project_id)
    if _rendering(project):
        raise HTTPException(status_code=409,
                            detail="projeto está renderizando")
    await project_store.delete_project(project_id)


class RenderRequest(BaseModel):
    quality: RenderQuality = RenderQuality.FINAL


@router.post("/{project_id}/render", response_model=Project, status_code=202)
async def render_project(project_id: str, req: RenderRequest) -> Project:
    """Enfileira cada cena como um job e orquestra o vídeo final."""
    project = await _get_or_404(project_id)
    if _rendering(project):
        raise HTTPException(
            status_code=409,
            detail=f"render '{project.current_render.id}' ainda em "
                   f"'{project.current_render.status.value}'")
    return await orchestrator.start_render(project, req.quality)


@router.get("/{project_id}/storyboard", response_model=Asset)
async def get_storyboard(project_id: str) -> Asset:
    """Gera a grade de revisão na hora (frames reais quando existirem)."""
    project = await _get_or_404(project_id)
    return await storyboard.build_storyboard(project)
