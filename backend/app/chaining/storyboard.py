"""Storyboard: grade com o primeiro frame de cada cena para revisão.

Cada tile mostra a melhor fonte disponível para a cena, nesta ordem:
  1. primeiro frame do clipe já renderizado (render mais recente);
  2. a init_image da cena (image-to-video);
  3. placeholder com o prompt (cena ainda não renderizada).

Assim o storyboard funciona ANTES do render (prompts + init images) e fica
fiel DEPOIS (frames reais) — o usuário revisa, ajusta cenas e re-renderiza.
"""
from __future__ import annotations

import io
import os
import tempfile
import textwrap
import uuid

from PIL import Image, ImageDraw

from app.chaining import ffmpeg_utils
from app.models import Asset, AssetKind, JobStatus, Project
from app.queue import job_queue
from app.storage import asset_store, s3_client

TILE_W, TILE_H = 384, 216   # 16:9
CAPTION_H = 40
COLS = 3


async def _scene_frame(project: Project, scene) -> Image.Image | None:
    """Melhor imagem disponível para a cena, ou None (vira placeholder)."""
    render = project.current_render
    if render and scene.id in render.scene_jobs:
        job = await job_queue.get_job(render.scene_jobs[scene.id])
        if job and job.status == JobStatus.DONE and job.final_asset_id:
            clip_asset = await asset_store.get_asset(job.final_asset_id)
            if clip_asset:
                with tempfile.TemporaryDirectory(prefix="sb-") as tmp:
                    clip = os.path.join(tmp, "clip.mp4")
                    with open(clip, "wb") as f:
                        f.write(s3_client.download_bytes(clip_asset.storage_key))
                    frame = os.path.join(tmp, "frame.png")
                    ffmpeg_utils.extract_first_frame(clip, frame)
                    return Image.open(frame).convert("RGB")
    if scene.init_image_asset_id:
        asset = await asset_store.get_asset(scene.init_image_asset_id)
        if asset:
            data = s3_client.download_bytes(asset.storage_key)
            return Image.open(io.BytesIO(data)).convert("RGB")
    return None


def _tile(frame: Image.Image | None, scene, index: int) -> Image.Image:
    tile = Image.new("RGB", (TILE_W, TILE_H + CAPTION_H), (24, 24, 28))
    if frame is not None:
        frame = frame.copy()
        frame.thumbnail((TILE_W, TILE_H))
        tile.paste(frame, ((TILE_W - frame.width) // 2,
                           (TILE_H - frame.height) // 2))
        caption = f"{index + 1}. {scene.id} — {scene.mode.value}"
    else:
        draw = ImageDraw.Draw(tile)
        wrapped = textwrap.fill(scene.prompt, width=48)[:200]
        draw.multiline_text((12, 16), wrapped, fill=(200, 200, 210))
        caption = f"{index + 1}. {scene.id} — não renderizada"
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, TILE_H, TILE_W, TILE_H + CAPTION_H], fill=(12, 12, 16))
    draw.text((12, TILE_H + 12), caption[:60], fill=(230, 230, 240))
    return tile


async def build_storyboard(project: Project) -> Asset:
    """Monta a grade, sobe como Asset (kind=image) e o retorna."""
    scenes = project.ordered_scenes
    cols = min(COLS, len(scenes))
    rows = (len(scenes) + cols - 1) // cols
    grid = Image.new("RGB", (cols * TILE_W, rows * (TILE_H + CAPTION_H)),
                     (8, 8, 10))
    for i, scene in enumerate(scenes):
        frame = await _scene_frame(project, scene)
        grid.paste(_tile(frame, scene, i),
                   ((i % cols) * TILE_W, (i // cols) * (TILE_H + CAPTION_H)))

    buf = io.BytesIO()
    grid.save(buf, format="PNG")
    storage_key = f"projects/{project.id}/storyboard.png"
    s3_client.upload_bytes(buf.getvalue(), storage_key, "image/png")
    asset = Asset(
        id=uuid.uuid4().hex,
        kind=AssetKind.IMAGE,
        storage_key=storage_key,
        mime_type="image/png",
        size_bytes=buf.getbuffer().nbytes,
        width=grid.width,
        height=grid.height,
        url=s3_client.presigned_url(storage_key),
    )
    await asset_store.save_asset(asset)
    return asset
