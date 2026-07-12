"""Schemas compartilhados do AI Video Studio.

Este pacote é o "contrato" do sistema: frontend e worker programam contra
estes tipos. Manter retrocompatibilidade aqui é o que evita quebrar as pontas.
"""
from .asset import Asset, AssetKind
from .job import (
    Job,
    JobCreateRequest,
    JobProgress,
    JobStatus,
    TERMINAL_STATES,
)
from .project import (
    Project,
    ProjectCreateRequest,
    ProjectRender,
    RENDER_TERMINAL,
    RenderQuality,
    RenderStatus,
)
from .scene import (
    GenerationMode,
    Scene,
    SceneParams,
    TransitionType,
)

__all__ = [
    "Asset",
    "AssetKind",
    "Job",
    "JobCreateRequest",
    "JobProgress",
    "JobStatus",
    "TERMINAL_STATES",
    "Project",
    "ProjectCreateRequest",
    "ProjectRender",
    "RENDER_TERMINAL",
    "RenderQuality",
    "RenderStatus",
    "GenerationMode",
    "Scene",
    "SceneParams",
    "TransitionType",
]
