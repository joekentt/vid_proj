"""Registry de métodos de consistência entre cenas (personagem/estilo).

Catálogo declarativo do que o worker sabe (ou saberá) fazer com a imagem de
referência do projeto. A API valida `Project.consistency_method` contra este
registry; o frontend pode listá-lo em GET /registry/consistency-methods.

Trade-off na T4 (16 GB, sem bf16), resumido nos verbetes abaixo:

- IP-Adapter injeta a IDENTIDADE/ESTILO da referência via cross-attention no
  gerador de keyframe (SD 1.5): leve (~2.5 GB extras com offload), não exige
  preparo por cena e resolve exatamente o problema "mesmo personagem em toda
  cena". Como o diffusers não tem IP-Adapter para o LTX-Video, aplicamos no
  KEYFRAME: SD1.5+IP-Adapter gera o primeiro frame fiel à referência e o
  LTX image-to-video (Fase 2) anima esse frame. Implementado primeiro.

- ControlNet condiciona ESTRUTURA ESPACIAL (pose/profundidade/bordas), não
  identidade: exige um mapa de controle por cena (estimador de pose/depth =
  mais um modelo na VRAM) e, sozinho, não mantém o rosto/estilo — resolve
  outro problema (enquadramento/pose). Mais pesado e menos direto para este
  caso de uso; fica registrado para quando houver cenas com pose dirigida.
"""
from __future__ import annotations

CONSISTENCY_METHODS: dict[str, dict] = {
    "none": {
        "status": "implemented",
        "label": "Sem consistência",
        "description": "Cada cena é gerada de forma independente.",
        "extra_vram_gb": 0.0,
    },
    "ip_adapter_keyframe": {
        "status": "implemented",
        "label": "IP-Adapter (keyframe)",
        "description": (
            "SD 1.5 + IP-Adapter gera um keyframe fiel à imagem de referência "
            "(identidade/estilo) para cada cena sem init_image próprio; o "
            "LTX image-to-video anima o keyframe. Cabe na T4 com cpu offload."
        ),
        "models": [
            "stable-diffusion-v1-5/stable-diffusion-v1-5",
            "h94/IP-Adapter (ip-adapter_sd15.bin)",
        ],
        "extra_vram_gb": 2.5,
    },
    "controlnet_keyframe": {
        "status": "registered",  # não implementado — ver trade-off no docstring
        "label": "ControlNet (keyframe)",
        "description": (
            "Controle espacial (pose/depth/canny) do keyframe a partir de um "
            "mapa derivado da referência. Não preserva identidade sozinho e "
            "custa um estimador extra na VRAM; planejado para cenas com pose "
            "dirigida, idealmente combinado com o IP-Adapter."
        ),
        "models": [
            "lllyasviel/control_v11p_sd15_openpose (a definir)",
        ],
        "extra_vram_gb": 4.0,
    },
}


def implemented_methods() -> set[str]:
    return {k for k, v in CONSISTENCY_METHODS.items()
            if v["status"] == "implemented"}
