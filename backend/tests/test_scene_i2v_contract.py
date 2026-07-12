"""Testes de contrato da Scene para image-to-video (Fase 2).

Validação pura (sem IO): coerência entre `mode` e `init_image_asset_id`,
e limites do `init_image_strength`.
"""
import pytest
from pydantic import ValidationError

from app.models import GenerationMode, Scene, SceneParams


def _scene(**kw) -> Scene:
    return Scene(id="s0", order=0, prompt="um lago ao entardecer", **kw)


def test_default_continua_text_to_video():
    assert _scene().mode == GenerationMode.TEXT_TO_VIDEO


def test_init_image_promove_mode_para_i2v():
    scene = _scene(init_image_asset_id="abc123")
    assert scene.mode == GenerationMode.IMAGE_TO_VIDEO


def test_i2v_explicito_sem_imagem_falha():
    with pytest.raises(ValidationError, match="init_image_asset_id"):
        _scene(mode=GenerationMode.IMAGE_TO_VIDEO)


def test_i2v_explicito_com_imagem_ok():
    scene = _scene(mode=GenerationMode.IMAGE_TO_VIDEO,
                   init_image_asset_id="abc123")
    assert scene.init_image_asset_id == "abc123"


def test_strength_dentro_dos_limites():
    assert SceneParams().init_image_strength == 0.9  # default
    assert SceneParams(init_image_strength=0.0).init_image_strength == 0.0
    assert SceneParams(init_image_strength=1.0).init_image_strength == 1.0
    with pytest.raises(ValidationError):
        SceneParams(init_image_strength=1.5)
    with pytest.raises(ValidationError):
        SceneParams(init_image_strength=-0.1)


def test_roundtrip_json_preserva_i2v():
    """O que a API grava no Redis, o worker lê de volta idêntico."""
    scene = _scene(init_image_asset_id="abc123",
                   params=SceneParams(seed=7, init_image_strength=0.5,
                                      negative_prompt="blur",
                                      duration_seconds=2.0, fps=12,
                                      width=512, height=320))
    clone = Scene.model_validate_json(scene.model_dump_json())
    assert clone == scene
    assert clone.mode == GenerationMode.IMAGE_TO_VIDEO
    assert clone.params.init_image_strength == 0.5
