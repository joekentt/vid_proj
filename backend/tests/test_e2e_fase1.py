"""Teste E2E da Fase 1: API -> Redis -> worker -> storage -> API.

Percorre o caminho completo de um job:
  POST /jobs -> estado 'pending' no Redis -> worker dá claim (RUNNING)
  -> clipe sobe pro storage -> mark_done -> GET /jobs/{id} 'done'
  -> Asset resolvível no Redis -> presigned URL baixável.

Pré-requisitos (defaults do docker-compose):
  docker compose up -d redis minio
  cd backend && uvicorn app.main:app &        # ou: docker compose up api

Modos (env E2E_FAKE_WORKER):
  1 (default) — o teste embute um worker fake que consome a fila com o MESMO
      código do worker real (job_queue.claim/mark_done), mas gera um MP4
      minúsculo em vez de rodar o LTX-Video. Valida todo o contrato sem GPU.
  0 — não embute worker: o teste espera um worker de verdade (Colab) conectado
      ao MESMO Redis da API. Use com E2E_TIMEOUT alto (geração na T4 demora):
        E2E_FAKE_WORKER=0 E2E_TIMEOUT=1800 pytest tests/test_e2e_fase1.py -s

Outras envs: API_URL (default http://localhost:8000), REDIS_URL/S3_* (mesmas
da API — o teste inspeciona Redis e storage diretamente para diagnosticar).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import httpx
import pytest

from app.config import get_settings
from app.models import Asset, AssetKind, JobStatus, TERMINAL_STATES
from app.queue import job_queue
from app.queue.redis_client import get_redis
from app.storage import s3_client

API_URL = os.environ.get("API_URL", "http://localhost:8000")
FAKE_WORKER = os.environ.get("E2E_FAKE_WORKER", "1") == "1"
TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "180" if FAKE_WORKER else "1800"))

# Mesmo prefixo usado pelo worker (worker/colab_worker.ipynb, célula 8).
ASSET_KEY_PREFIX = "video_jobs:asset:"

PAYLOAD = {
    "title": "E2E Fase 1",
    "scenes": [
        {
            "id": "s0",
            "order": 0,
            "prompt": "Drone sobrevoa montanhas ao amanhecer, luz dourada",
            "params": {"duration_seconds": 2.0, "width": 512, "height": 320,
                       "num_inference_steps": 20, "seed": 42},
        }
    ],
}


def _tiny_mp4(path: str) -> None:
    """MP4 real de 1s para o worker fake (ffmpeg do PATH ou do imageio-ffmpeg)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        import imageio_ffmpeg  # dev dependency; traz um ffmpeg estático

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=256x160:rate=8",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


async def _register_asset(local_path: str, storage_key: str, kind: AssetKind) -> Asset:
    """Espelha o register_asset do worker: upload + Asset JSON no Redis."""
    settings = get_settings()
    s3_client.upload_file(local_path, storage_key, "video/mp4")
    asset = Asset(id=uuid.uuid4().hex, kind=kind, storage_key=storage_key,
                  size_bytes=os.path.getsize(local_path))
    await get_redis().set(ASSET_KEY_PREFIX + asset.id, asset.model_dump_json(),
                          ex=settings.job_ttl_seconds)
    return asset


async def _fake_worker_once() -> None:
    """Consome UM job pela mesma via do worker real, só trocando a geração."""
    job = None
    deadline = time.monotonic() + TIMEOUT / 2
    while job is None and time.monotonic() < deadline:
        job = await job_queue.claim(timeout_seconds=2)
    assert job is not None, (
        "worker fake não recebeu o job via BRPOP — a API e o teste estão "
        "apontando para o MESMO Redis? Compare REDIS_URL dos dois lados."
    )
    assert job.status == JobStatus.RUNNING, (
        f"claim() deveria ter levado o job a RUNNING, veio '{job.status}'"
    )
    workdir = tempfile.mkdtemp(prefix="e2e-fake-worker-")
    try:
        clip = os.path.join(workdir, "clip.mp4")
        _tiny_mp4(clip)
        for idx, scene in enumerate(sorted(job.scenes, key=lambda s: s.order)):
            asset = await _register_asset(
                clip, f"jobs/{job.id}/scene-{idx}.mp4", AssetKind.VIDEO_CLIP)
            scene.output_asset_id = asset.id
            job.progress.current_scene = idx
            job.progress.stage = "generating"
            await job_queue.save_job(job)
        final = await _register_asset(
            clip, f"jobs/{job.id}/final.mp4", AssetKind.VIDEO_FINAL)
        await job_queue.mark_done(job, final.id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_e2e_fase1():
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        # --- Etapa 1: API de pé -------------------------------------------
        try:
            r = await client.get(f"{API_URL}/health")
        except httpx.ConnectError as exc:
            pytest.fail(f"API inalcançável em {API_URL} ({exc}) — rode "
                        "'uvicorn app.main:app' ou 'docker compose up api'")
        assert r.status_code == 200, f"/health respondeu {r.status_code}"

        # --- Etapa 2: POST /jobs cria e enfileira -------------------------
        r = await client.post(f"{API_URL}/jobs", json=PAYLOAD)
        assert r.status_code == 201, f"POST /jobs falhou: {r.status_code} {r.text}"
        job = r.json()
        job_id = job["id"]
        assert job["status"] == "pending", job
        print(f"\n[etapa 2] job criado: {job_id}")

        # --- Etapa 3: estado visível no Redis (mesmo que a API usa) -------
        raw = await get_redis().get(f"{settings.job_state_prefix}{job_id}")
        assert raw is not None, (
            "POST retornou 201 mas o estado não está no Redis que o teste "
            "enxerga — REDIS_URL do teste difere do da API."
        )
        print(f"[etapa 3] estado no Redis ok ({settings.job_state_prefix}{job_id})")

        # --- Etapa 4: worker consome --------------------------------------
        worker_task = None
        if FAKE_WORKER:
            worker_task = asyncio.create_task(_fake_worker_once())
            print("[etapa 4] worker fake embutido iniciado")
        else:
            print("[etapa 4] aguardando worker REAL (Colab) no mesmo Redis...")

        # --- Etapa 5: polling do GET /jobs/{id} até estado terminal -------
        deadline = time.monotonic() + TIMEOUT
        data = job
        last_stage = None
        while time.monotonic() < deadline:
            r = await client.get(f"{API_URL}/jobs/{job_id}")
            assert r.status_code == 200, f"GET /jobs/{job_id} -> {r.status_code}"
            data = r.json()
            stage = (data["status"], data["progress"]["stage"],
                     data["progress"]["percent"])
            if stage != last_stage:
                print(f"[etapa 5] {data['status']} / {data['progress']['stage']} "
                      f"({data['progress']['percent']}%)")
                last_stage = stage
            if data["status"] in {s.value for s in TERMINAL_STATES}:
                break
            await asyncio.sleep(2)
        if worker_task is not None:
            await worker_task
        assert data["status"] == "done", (
            f"job terminou em '{data['status']}' (erro: {data.get('error')}) "
            f"ou estourou o timeout de {TIMEOUT}s no estado "
            f"'{data['status']}/{data['progress']['stage']}'"
        )
        assert data["final_asset_id"], "DONE sem final_asset_id"
        assert all(s["output_asset_id"] for s in data["scenes"]), (
            "cena sem output_asset_id apesar de DONE")

        # --- Etapa 6: asset resolvível e binário no storage ---------------
        raw = await get_redis().get(ASSET_KEY_PREFIX + data["final_asset_id"])
        assert raw is not None, (
            f"Asset {data['final_asset_id']} não registrado em "
            f"{ASSET_KEY_PREFIX}* no Redis")
        asset = Asset.model_validate_json(raw)
        s3_client.get_s3().head_object(Bucket=settings.s3_bucket,
                                       Key=asset.storage_key)
        url = s3_client.presigned_url(asset.storage_key)
        r = await client.get(url)
        assert r.status_code == 200 and len(r.content) > 0, (
            f"presigned URL não baixou o clipe (HTTP {r.status_code})")
        print(f"[etapa 6] clipe no storage: s3://{settings.s3_bucket}/"
              f"{asset.storage_key} ({len(r.content)} bytes) — presigned OK")
        print(f"[fim] fluxo E2E completo para o job {job_id}")
