"""Teste E2E da Fase 2: vídeo gerado A PARTIR DE UMA IMAGEM.

Caminho completo do image-to-video:
  POST /assets/images (upload PNG) -> Asset kind=image
  -> POST /jobs com scene.init_image_asset_id -> mode vira image_to_video
  -> worker resolve o asset, baixa a imagem do storage e gera
  -> done com final_asset_id -> presigned URL baixável.

Mesmos modos do teste da Fase 1 (E2E_FAKE_WORKER=1 default / 0 para o worker
real no Colab). O worker fake daqui espelha o caminho i2v do notebook —
resolve o asset via asset_store, valida o kind e BAIXA a imagem do storage —
só trocando o LTX por um MP4 mínimo. No modo real, o clipe sai do
LTXImageToVideoPipeline de verdade.

Também cobre as validações da API: content-type não suportado (415) e
init_image_asset_id inexistente (422).
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
import time

import httpx
import pytest
from PIL import Image

from app.models import AssetKind, GenerationMode, JobStatus, TERMINAL_STATES
from app.queue import job_queue
from app.storage import asset_store, s3_client

from tests.test_e2e_fase1 import API_URL, FAKE_WORKER, TIMEOUT, _register_asset, _tiny_mp4


def _png_bytes(width: int = 512, height: int = 320) -> bytes:
    """PNG de teste: gradiente horizontal (conteúdo determinístico)."""
    img = Image.new("RGB", (width, height))
    img.putdata([(int(255 * x / width), 64, 128) for _ in range(height)
                 for x in range(width)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _fake_worker_i2v_once() -> None:
    """Espelha o caminho i2v do worker real (menos a GPU)."""
    job = None
    deadline = time.monotonic() + TIMEOUT / 2
    while job is None and time.monotonic() < deadline:
        job = await job_queue.claim(timeout_seconds=2)
    assert job is not None, "worker fake não recebeu o job — mesmo Redis nas duas pontas?"

    scene = job.scenes[0]
    assert scene.mode == GenerationMode.IMAGE_TO_VIDEO, (
        f"a fila entregou mode '{scene.mode}' — o validator deveria ter "
        "promovido para image_to_video")

    # Exatamente o que o load_init_image do notebook faz:
    asset = await asset_store.get_asset(scene.init_image_asset_id)
    assert asset is not None, "init_image_asset_id não resolve no Redis"
    assert asset.kind == AssetKind.IMAGE
    data = s3_client.download_bytes(asset.storage_key)
    init_image = Image.open(io.BytesIO(data)).convert("RGB")
    assert init_image.size == (512, 320), "imagem baixada difere da enviada"
    # No worker real, aqui entraria: pipe_i2v(image=init_image, prompt=..., strength=...)

    workdir = tempfile.mkdtemp(prefix="e2e-i2v-")
    try:
        clip = os.path.join(workdir, "clip.mp4")
        _tiny_mp4(clip)
        clip_asset = await _register_asset(
            clip, f"jobs/{job.id}/scene-0.mp4", AssetKind.VIDEO_CLIP)
        scene.output_asset_id = clip_asset.id
        await job_queue.save_job(job)
        final = await _register_asset(
            clip, f"jobs/{job.id}/final.mp4", AssetKind.VIDEO_FINAL)
        await job_queue.mark_done(job, final.id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_e2e_fase2_image_to_video():
    async with httpx.AsyncClient(timeout=30) as client:
        # --- Etapa 1: upload da imagem -------------------------------------
        r = await client.post(
            f"{API_URL}/assets/images",
            files={"file": ("gradiente.png", _png_bytes(), "image/png")},
        )
        assert r.status_code == 201, f"upload falhou: {r.status_code} {r.text}"
        image_asset = r.json()
        assert image_asset["kind"] == "image"
        assert image_asset["url"], "upload deveria devolver presigned URL de preview"
        print(f"\n[etapa 1] imagem no storage: {image_asset['storage_key']} "
              f"(asset {image_asset['id']})")

        # --- Etapa 1b: validações da API rejeitam entradas ruins -----------
        r = await client.post(
            f"{API_URL}/assets/images",
            files={"file": ("nota.txt", b"nao sou imagem", "text/plain")},
        )
        assert r.status_code == 415, f"esperado 415 para text/plain, veio {r.status_code}"
        r = await client.post(f"{API_URL}/jobs", json={
            "title": "asset fantasma",
            "scenes": [{"id": "s0", "order": 0, "prompt": "x",
                        "init_image_asset_id": "nao-existe"}],
        })
        assert r.status_code == 422, f"esperado 422 para asset inexistente, veio {r.status_code}"
        print("[etapa 1b] 415 p/ não-imagem e 422 p/ asset inexistente OK")

        # --- Etapa 2: job i2v com todos os parâmetros expostos -------------
        r = await client.post(f"{API_URL}/jobs", json={
            "title": "E2E Fase 2 — image-to-video",
            "scenes": [{
                "id": "s0",
                "order": 0,
                "prompt": "A câmera afasta lentamente revelando o gradiente virando um pôr do sol",
                "init_image_asset_id": image_asset["id"],
                "params": {
                    "seed": 42,
                    "negative_prompt": "blurry, distorted",
                    "duration_seconds": 2.0,
                    "fps": 12,
                    "width": 512,
                    "height": 320,
                    "init_image_strength": 0.85,
                    "num_inference_steps": 20,
                },
            }],
        })
        assert r.status_code == 201, f"POST /jobs i2v falhou: {r.status_code} {r.text}"
        job = r.json()
        job_id = job["id"]
        assert job["scenes"][0]["mode"] == "image_to_video", (
            "mode deveria ter sido promovido a image_to_video pelo validator")
        assert job["scenes"][0]["params"]["init_image_strength"] == 0.85
        print(f"[etapa 2] job i2v criado: {job_id}")

        # --- Etapa 3: worker consome ----------------------------------------
        worker_task = None
        if FAKE_WORKER:
            worker_task = asyncio.create_task(_fake_worker_i2v_once())
            print("[etapa 3] worker fake i2v iniciado")
        else:
            print("[etapa 3] aguardando worker REAL (Colab) gerar do LTX i2v...")

        # --- Etapa 4: polling até terminal ----------------------------------
        deadline = time.monotonic() + TIMEOUT
        data = job
        while time.monotonic() < deadline:
            r = await client.get(f"{API_URL}/jobs/{job_id}")
            data = r.json()
            if data["status"] in {s.value for s in TERMINAL_STATES}:
                break
            await asyncio.sleep(2)
        if worker_task is not None:
            await worker_task
        assert data["status"] == "done", (
            f"job i2v terminou em '{data['status']}' (erro: {data.get('error')})")
        assert data["scenes"][0]["output_asset_id"]
        print(f"[etapa 4] done — clipe da cena: {data['scenes'][0]['output_asset_id']}")

        # --- Etapa 5: vídeo final baixável ----------------------------------
        final = await asset_store.get_asset(data["final_asset_id"])
        assert final is not None and final.kind == AssetKind.VIDEO_FINAL
        r = await client.get(s3_client.presigned_url(final.storage_key))
        assert r.status_code == 200 and len(r.content) > 0
        print(f"[etapa 5] vídeo final ({len(r.content)} bytes) baixado da "
              f"presigned URL — fluxo i2v completo")
