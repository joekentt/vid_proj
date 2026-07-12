"""Orquestração do render de um projeto.

Como o backend sabe que todas as cenas terminaram:

1. `start_render` enfileira UM Job por cena (o worker já sabe processar jobs
   de cena única — nada muda lá) e grava o mapa scene_id -> job_id no
   ProjectRender. A referência de consistência do projeto é ESTAMPADA em
   cada cena aqui, para o worker nunca precisar conhecer o Project.
2. Um finalizador (asyncio.Task no processo da API) faz polling dos estados
   dos jobs no Redis a cada poucos segundos — a mesma fonte de verdade que o
   GET /jobs/{id} usa. Push seria possível (pub/sub), mas polling barato no
   Redis mantém o worker 100% desacoplado e sobrevive a worker que morre no
   meio (o timeout do render captura).
3. Todas DONE  -> status COMPOSITING: baixa os clipes, roda o FFmpeg
   (compositor, em thread para não travar o event loop), sobe o final.
   Alguma FAILED/CANCELLED -> render FAILED com o erro da cena culpada.

Se o processo da API reiniciar no meio, a Task morre mas o estado persiste
no Redis: `ensure_finalizer` (chamada no GET do projeto) re-anexa um
finalizador a qualquer render órfão em andamento — o polling é idempotente.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import tempfile
import uuid

from app.chaining import compositor
from app.models import (
    Asset,
    AssetKind,
    Job,
    JobStatus,
    Project,
    ProjectRender,
    RenderQuality,
    RenderStatus,
)
from app.queue import job_queue
from app.storage import asset_store, project_store, s3_client

log = logging.getLogger("orchestrator")

POLL_SECONDS = 3.0
RENDER_TIMEOUT_SECONDS = float(os.environ.get("RENDER_TIMEOUT_SECONDS", 3600 * 4))

# Finalizadores vivos NESTE processo: render_id -> Task.
_finalizers: dict[str, asyncio.Task] = {}


def _stamp_scene(project: Project, scene, quality: RenderQuality):
    """Cópia da cena pronta para virar job independente."""
    s = copy.deepcopy(scene)
    if project.reference_image_asset_id and project.consistency_method != "none":
        s.reference_image_asset_id = project.reference_image_asset_id
        s.consistency_method = project.consistency_method
    if quality == RenderQuality.PREVIEW:
        s.params.num_inference_steps = min(s.params.num_inference_steps, 12)
        s.params.width = max(256, s.params.width // 2)
        s.params.height = max(256, s.params.height // 2)
    return s


async def start_render(project: Project, quality: RenderQuality) -> Project:
    """Enfileira todas as cenas e liga o finalizador. Persiste e retorna."""
    render = ProjectRender(quality=quality,
                           total_scenes=len(project.scenes))
    for scene in project.ordered_scenes:
        job = Job(
            id=uuid.uuid4().hex,
            title=f"{project.title} — cena {scene.id}",
            scenes=[_stamp_scene(project, scene, quality)],
        )
        await job_queue.enqueue(job)
        render.scene_jobs[scene.id] = job.id
    project.current_render = render
    await project_store.save_project(project)
    ensure_finalizer(project.id, render.id)
    log.info("render %s do projeto %s: %d cenas enfileiradas",
             render.id, project.id, render.total_scenes)
    return project


def ensure_finalizer(project_id: str, render_id: str) -> None:
    """Garante um finalizador vivo para o render (idempotente; re-anexa
    renders órfãos depois de um restart da API)."""
    task = _finalizers.get(render_id)
    if task is not None and not task.done():
        return
    _finalizers[render_id] = asyncio.create_task(
        _finalize_when_done(project_id, render_id))


async def _set_failed(project: Project, render: ProjectRender, error: str):
    render.status = RenderStatus.FAILED
    render.error = error
    project.current_render = render
    await project_store.save_project(project)
    log.warning("render %s FAILED: %s", render.id, error)


async def _finalize_when_done(project_id: str, render_id: str) -> None:
    try:
        deadline = asyncio.get_event_loop().time() + RENDER_TIMEOUT_SECONDS
        while True:
            project = await project_store.get_project(project_id)
            if project is None or project.current_render is None \
                    or project.current_render.id != render_id:
                return  # projeto apagado ou render substituído: nada a fazer
            render = project.current_render

            jobs: dict[str, Job | None] = {}
            for scene_id, job_id in render.scene_jobs.items():
                jobs[scene_id] = await job_queue.get_job(job_id)

            failed = [(sid, j) for sid, j in jobs.items()
                      if j is None or j.status in
                      (JobStatus.FAILED, JobStatus.CANCELLED)]
            if failed:
                sid, j = failed[0]
                reason = "job expirou no Redis" if j is None else \
                    (j.error or f"job {j.status.value}")
                await _set_failed(project, render,
                                  f"cena '{sid}': {reason}")
                return

            done = sum(1 for j in jobs.values()
                       if j and j.status == JobStatus.DONE)
            if done != render.scenes_done:
                render.scenes_done = done
                project.current_render = render
                await project_store.save_project(project)

            if done == render.total_scenes:
                break
            if asyncio.get_event_loop().time() > deadline:
                await _set_failed(
                    project, render,
                    f"timeout: {done}/{render.total_scenes} cenas em "
                    f"{RENDER_TIMEOUT_SECONDS:.0f}s — worker conectado?")
                return
            await asyncio.sleep(POLL_SECONDS)

        # ---- todas as cenas prontas: compor -----------------------------
        render.status = RenderStatus.COMPOSITING
        project.current_render = render
        await project_store.save_project(project)
        try:
            final_asset = await _composite(project, render, jobs)
        except Exception as exc:
            await _set_failed(project, render,
                              f"composição falhou: {exc}")
            return
        render.status = RenderStatus.DONE
        render.final_asset_id = final_asset.id
        project.current_render = render
        await project_store.save_project(project)
        log.info("render %s DONE — asset %s", render.id, final_asset.id)
    except Exception:  # nunca deixar o finalizador morrer calado
        log.exception("finalizador do render %s morreu", render_id)
    finally:
        _finalizers.pop(render_id, None)


async def _composite(project: Project, render: ProjectRender,
                     jobs: dict[str, Job]) -> Asset:
    """Baixa os clipes, concatena com transições e sobe o vídeo final."""
    scenes = project.ordered_scenes
    with tempfile.TemporaryDirectory(prefix=f"render-{render.id}-") as tmp:
        clips: list[str] = []
        for i, scene in enumerate(scenes):
            job = jobs[scene.id]
            clip_asset = await asset_store.get_asset(job.final_asset_id)
            if clip_asset is None:
                raise RuntimeError(
                    f"asset do clipe da cena '{scene.id}' expirou")
            path = os.path.join(tmp, f"clip-{i}.mp4")
            with open(path, "wb") as f:
                f.write(s3_client.download_bytes(clip_asset.storage_key))
            clips.append(path)

        transitions = [
            (s.transition_to_next, s.transition_duration_seconds)
            for s in scenes[:-1]
        ]
        first = scenes[0].params
        divisor = 2 if render.quality == RenderQuality.PREVIEW else 1
        out = os.path.join(tmp, "final.mp4")
        await asyncio.to_thread(
            compositor.compose, clips, transitions, out,
            max(256, first.width // divisor),
            max(256, first.height // divisor),
            first.fps,
        )

        storage_key = f"projects/{project.id}/renders/{render.id}/final.mp4"
        s3_client.upload_file(out, storage_key, "video/mp4")
        asset = Asset(
            id=uuid.uuid4().hex,
            kind=AssetKind.VIDEO_FINAL,
            storage_key=storage_key,
            size_bytes=os.path.getsize(out),
        )
        await asset_store.save_asset(asset)
        return asset
