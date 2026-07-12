"""Schemas de Cena — o contrato central que frontend e worker compartilham.

Uma Scene é a unidade mínima de geração: um prompt + parâmetros que produzem
um clipe de vídeo. Vários Scenes encadeados formam um vídeo final.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator


class GenerationMode(str, Enum):
    """Como a cena é gerada."""
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"


class TransitionType(str, Enum):
    """Transição aplicada ao encadear esta cena com a seguinte (Fase 3)."""
    CUT = "cut"          # corte seco
    FADE = "fade"        # fade to/from black
    DISSOLVE = "dissolve"  # crossfade entre clipes


class SceneParams(BaseModel):
    """Parâmetros avançados de geração. Defaults seguros para uma GPU T4."""
    seed: Optional[int] = Field(
        default=None,
        description="Seed para reprodutibilidade. None = aleatório.",
    )
    negative_prompt: Optional[str] = Field(
        default=None,
        description="O que evitar na geração (artefatos, distorções, etc.).",
    )
    duration_seconds: float = Field(
        default=4.0, ge=1.0, le=10.0,
        description="Duração do clipe. Limitado a 10s para caber na T4.",
    )
    fps: int = Field(default=24, ge=8, le=30)
    width: int = Field(default=768, ge=256, le=1280)
    height: int = Field(default=512, ge=256, le=1280)
    guidance_scale: float = Field(
        default=3.0, ge=1.0, le=15.0,
        description="Quão fiel ao prompt. Mais alto = mais aderente, menos criativo.",
    )
    num_inference_steps: int = Field(
        default=40, ge=10, le=100,
        description="Passos de denoising. Mais = melhor qualidade, mais lento.",
    )
    init_image_strength: float = Field(
        default=0.9, ge=0.0, le=1.0,
        description=(
            "Só para image-to-video: quanto a imagem inicial 'segura' o vídeo. "
            "1.0 = primeiro frame idêntico à imagem; valores menores dão mais "
            "liberdade ao modelo."
        ),
    )


class Scene(BaseModel):
    """Uma cena individual dentro de um job de geração."""
    id: str = Field(description="ID único da cena dentro do job.")
    order: int = Field(ge=0, description="Posição na sequência final.")
    mode: GenerationMode = GenerationMode.TEXT_TO_VIDEO
    prompt: str = Field(min_length=1, max_length=2000)

    # Para image-to-video (Fase 2): Asset (kind=image) já no storage.
    # A API valida existência e tipo na criação do job; o worker baixa
    # a imagem pelo storage_key do Asset.
    init_image_asset_id: Optional[str] = Field(
        default=None,
        description="ID de um Asset de imagem para usar como frame inicial.",
    )

    # Para consistência de personagem entre cenas (Fase 3).
    reference_image_url: Optional[HttpUrl] = None

    params: SceneParams = Field(default_factory=SceneParams)
    transition_to_next: TransitionType = TransitionType.CUT

    # Preenchido pelo worker quando o clipe fica pronto.
    output_asset_id: Optional[str] = None

    @model_validator(mode="after")
    def _sync_mode_with_init_image(self) -> "Scene":
        """Mantém `mode` coerente com a presença da imagem inicial.

        Enviar só o init_image_asset_id já basta: o modo vira
        IMAGE_TO_VIDEO automaticamente. Pedir IMAGE_TO_VIDEO sem imagem
        é erro de contrato e falha na validação (422 na API).
        """
        if self.init_image_asset_id and self.mode == GenerationMode.TEXT_TO_VIDEO:
            self.mode = GenerationMode.IMAGE_TO_VIDEO
        if self.mode == GenerationMode.IMAGE_TO_VIDEO and not self.init_image_asset_id:
            raise ValueError(
                "cena image_to_video exige init_image_asset_id "
                "(faça upload em POST /assets/images e use o id retornado)"
            )
        return self
