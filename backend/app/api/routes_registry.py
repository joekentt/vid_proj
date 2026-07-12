"""Rota do model registry — o frontend lista o que o worker sabe fazer."""
from __future__ import annotations

from fastapi import APIRouter

from app import registry

router = APIRouter(prefix="/registry", tags=["meta"])


@router.get("/consistency-methods")
async def consistency_methods() -> dict[str, dict]:
    return registry.CONSISTENCY_METHODS
