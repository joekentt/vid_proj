"""Rotas de Assets — upload e consulta de arquivos no storage.

POST  /assets/images   sobe uma imagem (multipart) e devolve o Asset;
                       o id retornado vai em Scene.init_image_asset_id.
GET   /assets/{id}     lê o Asset com presigned URL renovada.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, UploadFile

from app.models import Asset, AssetKind
from app.storage import asset_store, s3_client

router = APIRouter(prefix="/assets", tags=["assets"])

# Formatos que o pipeline aceita (PIL abre todos).
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB é generoso para um frame inicial


@router.post("/images", response_model=Asset, status_code=201)
async def upload_image(file: UploadFile) -> Asset:
    """Recebe uma imagem e a registra como Asset (kind=image)."""
    ext = ALLOWED_IMAGE_TYPES.get(file.content_type or "")
    if ext is None:
        raise HTTPException(
            status_code=415,
            detail=f"content-type '{file.content_type}' não suportado; "
                   f"use {sorted(ALLOWED_IMAGE_TYPES)}",
        )
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"imagem com {len(data)} bytes excede o máximo de "
                   f"{MAX_IMAGE_BYTES} ({MAX_IMAGE_BYTES // 2**20} MB)",
        )

    asset_id = uuid.uuid4().hex
    storage_key = f"uploads/{asset_id}{ext}"
    s3_client.upload_bytes(data, storage_key, file.content_type)
    asset = Asset(
        id=asset_id,
        kind=AssetKind.IMAGE,
        storage_key=storage_key,
        mime_type=file.content_type,
        size_bytes=len(data),
        url=s3_client.presigned_url(storage_key),
    )
    await asset_store.save_asset(asset)
    return asset


@router.get("/{asset_id}", response_model=Asset)
async def read_asset(asset_id: str) -> Asset:
    asset = await asset_store.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset.url = s3_client.presigned_url(asset.storage_key)
    return asset
