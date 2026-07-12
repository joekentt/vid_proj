"""Teste E2E da Fase 3: projeto com 3 CENAS encadeadas num vídeo final.

Fluxo completo:
  upload de referência + init image
  -> POST /projects (3 cenas: t2v -fade-> i2v -dissolve-> t2v,
     consistência ip_adapter_keyframe com referência compartilhada)
  -> GET /projects/{id}/storyboard ANTES do render (placeholders/init image)
  -> POST /projects/{id}/render: 1 job POR CENA na fila
  -> worker (fake) consome os 3 jobs; o teste confere que a referência foi
     ESTAMPADA em cada cena (é assim que o worker real decide gerar keyframe)
  -> orquestrador detecta as 3 DONE, compõe com FFmpeg (fade + dissolve)
  -> current_render.status == done, vídeo final baixável, duração coerente
     com os overlaps das transições
  -> storyboard DEPOIS do render (frames reais dos clipes).

Também cobre: CRUD (list/PUT), registry (422 para método só registrado).
Modos fake/real e variáveis: iguais aos testes das Fases 1-2.
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

from app.chaining import ffmpeg_utils
from app.models import AssetKind, GenerationMode, JobStatus
from app.queue import job_queue
from app.storage import asset_store, s3_client

from tests.test_e2e_fase1 import API_URL, FAKE_WORKER, TIMEOUT, _register_asset, _tiny_mp4
from tests.test_e2e_fase2_i2v import _png_bytes

CLIP_SECONDS = 1.0          # duração do clipe do worker fake (_tiny_mp4)
FADE_S, DISSOLVE_S = 0.4, 0.4


async def _upload_png(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post(
        f"{API_URL}/assets/images",
        files={"file": (name, _png_bytes(), "image/png")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _fake_worker_scene_jobs(n: int, reference_id: str) -> None:
    """Consome n jobs de cena única, conferindo a estampa de consistência."""
    workdir = tempfile.mkdtemp(prefix="e2e-p3-")
    try:
        clip = os.path.join(workdir, "clip.mp4")
        _tiny_mp4(clip)
        for _ in range(n):
            job = None
            deadline = time.monotonic() + TIMEOUT / 2
            while job is None and time.monotonic() < deadline:
                job = await job_queue.claim(timeout_seconds=2)
            assert job is not None, "worker fake não recebeu job de cena"
            assert len(job.scenes) == 1, (
                "render de projeto deve enfileirar UM job por cena, "
                f"veio job com {len(job.scenes)} cenas")
            scene = job.scenes[0]
            assert scene.reference_image_asset_id == reference_id, (
                "orquestrador não estampou a referência do projeto na cena")
            assert scene.consistency_method == "ip_adapter_keyframe"
            # Worker real aqui: resolve_init_image -> keyframe IP-Adapter
            # (cena t2v) ou init_image (cena i2v) -> LTX. Fake: clipe mínimo.
            if scene.mode == GenerationMode.IMAGE_TO_VIDEO:
                assert scene.init_image_asset_id, "cena i2v sem init image"
            asset = await _register_asset(
                clip, f"jobs/{job.id}/scene-0.mp4", AssetKind.VIDEO_CLIP)
            scene.output_asset_id = asset.id
            final = await _register_asset(
                clip, f"jobs/{job.id}/final.mp4", AssetKind.VIDEO_FINAL)
            await job_queue.mark_done(job, final.id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_e2e_fase3_projeto_3_cenas():
    async with httpx.AsyncClient(timeout=60) as client:
        # --- Etapa 1: assets de apoio --------------------------------------
        reference = await _upload_png(client, "personagem.png")
        init_img = await _upload_png(client, "cena2-inicio.png")
        print(f"\n[etapa 1] referência {reference['id']} / init {init_img['id']}")

        # --- Etapa 1b: registry rejeita método não implementado ------------
        r = await client.get(f"{API_URL}/registry/consistency-methods")
        assert r.status_code == 200
        assert r.json()["ip_adapter_keyframe"]["status"] == "implemented"
        assert r.json()["controlnet_keyframe"]["status"] == "registered"
        base_scene = {"id": "sX", "order": 0, "prompt": "x"}
        r = await client.post(f"{API_URL}/projects", json={
            "title": "inválido",
            "scenes": [base_scene],
            "reference_image_asset_id": reference["id"],
            "consistency_method": "controlnet_keyframe",
        })
        assert r.status_code == 422, "método só registrado deveria dar 422"
        print("[etapa 1b] registry OK (422 p/ controlnet_keyframe)")

        # --- Etapa 2: cria o projeto com 3 cenas ----------------------------
        params = {"duration_seconds": 1.0, "fps": 12, "width": 512,
                  "height": 320, "num_inference_steps": 15, "seed": 7}
        r = await client.post(f"{API_URL}/projects", json={
            "title": "Curta de 3 cenas",
            "description": "t2v -fade-> i2v -dissolve-> t2v",
            "reference_image_asset_id": reference["id"],
            "consistency_method": "ip_adapter_keyframe",
            "scenes": [
                {"id": "s0", "order": 0,
                 "prompt": "Herói caminha por uma floresta ao amanhecer",
                 "params": params,
                 "transition_to_next": "fade",
                 "transition_duration_seconds": FADE_S},
                {"id": "s1", "order": 1,
                 "prompt": "O herói encontra um lago espelhado",
                 "init_image_asset_id": init_img["id"],
                 "params": params,
                 "transition_to_next": "dissolve",
                 "transition_duration_seconds": DISSOLVE_S},
                {"id": "s2", "order": 2,
                 "prompt": "Close no rosto do herói, vento nos cabelos",
                 "params": params},
            ],
        })
        assert r.status_code == 201, r.text
        project = r.json()
        pid = project["id"]
        print(f"[etapa 2] projeto criado: {pid}")

        # --- Etapa 2b: CRUD básico ------------------------------------------
        r = await client.get(f"{API_URL}/projects")
        assert any(p["id"] == pid for p in r.json()), "projeto fora da listagem"
        body = {k: project[k] for k in
                ("title", "description", "scenes",
                 "reference_image_asset_id", "consistency_method")}
        body["title"] = "Curta de 3 cenas (v2)"
        r = await client.put(f"{API_URL}/projects/{pid}", json=body)
        assert r.status_code == 200 and r.json()["title"].endswith("(v2)")
        print("[etapa 2b] list + PUT OK")

        # --- Etapa 3: storyboard ANTES do render ----------------------------
        r = await client.get(f"{API_URL}/projects/{pid}/storyboard")
        assert r.status_code == 200, r.text
        sb = r.json()
        img = Image.open(io.BytesIO(
            (await client.get(sb["url"])).content))
        assert img.size[0] == 3 * 384, f"grade deveria ter 3 colunas: {img.size}"
        print(f"[etapa 3] storyboard pré-render: {img.size[0]}x{img.size[1]} px")

        # --- Etapa 4: render ------------------------------------------------
        r = await client.post(f"{API_URL}/projects/{pid}/render",
                              json={"quality": "final"})
        assert r.status_code == 202, r.text
        render = r.json()["current_render"]
        assert render["status"] == "rendering"
        assert len(render["scene_jobs"]) == 3, "esperado 1 job por cena"
        # segundo render simultâneo deve ser rejeitado
        r = await client.post(f"{API_URL}/projects/{pid}/render",
                              json={"quality": "final"})
        assert r.status_code == 409
        print(f"[etapa 4] render {render['id']}: 3 jobs enfileirados, 409 p/ duplicado")

        # --- Etapa 5: worker consome as 3 cenas ------------------------------
        worker_task = None
        if FAKE_WORKER:
            worker_task = asyncio.create_task(
                _fake_worker_scene_jobs(3, reference["id"]))
            print("[etapa 5] worker fake consumindo 3 jobs de cena")
        else:
            print("[etapa 5] aguardando worker REAL no Colab (3 cenas + keyframes)...")

        # --- Etapa 6: orquestrador detecta o fim e compõe --------------------
        deadline = time.monotonic() + TIMEOUT
        last = None
        while time.monotonic() < deadline:
            r = await client.get(f"{API_URL}/projects/{pid}")
            render = r.json()["current_render"]
            snap = (render["status"], render["scenes_done"])
            if snap != last:
                print(f"[etapa 6] {render['status']} "
                      f"({render['scenes_done']}/{render['total_scenes']} cenas)")
                last = snap
            if render["status"] in ("done", "failed"):
                break
            await asyncio.sleep(2)
        if worker_task is not None:
            await worker_task
        assert render["status"] == "done", f"render falhou: {render.get('error')}"
        assert render["final_asset_id"]

        # --- Etapa 7: vídeo final coerente com as transições ------------------
        final = await asset_store.get_asset(render["final_asset_id"])
        assert final is not None and final.kind == AssetKind.VIDEO_FINAL
        r = await client.get(s3_client.presigned_url(final.storage_key))
        assert r.status_code == 200 and len(r.content) > 0
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(r.content)
            final_path = f.name
        try:
            dur = ffmpeg_utils.duration_seconds(final_path)
        finally:
            os.unlink(final_path)
        expected = 3 * CLIP_SECONDS - FADE_S - DISSOLVE_S  # xfade sobrepõe
        assert abs(dur - expected) < 0.5, (
            f"duração {dur:.2f}s difere do esperado {expected:.2f}s "
            "(3 clipes de 1s com fade 0.4s + dissolve 0.4s)")
        print(f"[etapa 7] final: {len(r.content)} bytes, {dur:.2f}s "
              f"(esperado ~{expected:.2f}s) — transições aplicadas")

        # --- Etapa 8: storyboard DEPOIS do render (frames reais) -------------
        r = await client.get(f"{API_URL}/projects/{pid}/storyboard")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(
            (await client.get(r.json()["url"])).content))
        assert img.size[0] == 3 * 384
        print(f"[etapa 8] storyboard pós-render regenerado — fluxo Fase 3 completo")
