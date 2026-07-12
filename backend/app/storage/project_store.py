"""Persistência de Projects no Redis (mesmo padrão do asset_store).

Além da chave por projeto, mantém um índice (set) com os IDs para o GET de
listagem — Redis não tem "SELECT *".
"""
from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.models import Project
from app.queue.redis_client import get_redis

PROJECT_KEY_PREFIX = "video_jobs:project:"
PROJECT_INDEX_KEY = "video_jobs:projects"


async def save_project(project: Project) -> None:
    project.touch()
    r = get_redis()
    ttl = get_settings().job_ttl_seconds
    await r.set(PROJECT_KEY_PREFIX + project.id,
                project.model_dump_json(), ex=ttl)
    await r.sadd(PROJECT_INDEX_KEY, project.id)


async def get_project(project_id: str) -> Optional[Project]:
    raw = await get_redis().get(PROJECT_KEY_PREFIX + project_id)
    if raw is None:
        return None
    return Project.model_validate_json(raw)


async def delete_project(project_id: str) -> bool:
    r = get_redis()
    removed = await r.delete(PROJECT_KEY_PREFIX + project_id)
    await r.srem(PROJECT_INDEX_KEY, project_id)
    return removed > 0


async def list_projects() -> list[Project]:
    r = get_redis()
    ids = await r.smembers(PROJECT_INDEX_KEY)
    projects: list[Project] = []
    for pid in ids:
        p = await get_project(pid)
        if p is None:  # expirou: limpa o índice de carona
            await r.srem(PROJECT_INDEX_KEY, pid)
        else:
            projects.append(p)
    return sorted(projects, key=lambda p: p.created_at, reverse=True)
