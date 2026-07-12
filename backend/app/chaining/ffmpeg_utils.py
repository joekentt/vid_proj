"""Acesso ao ffmpeg no backend.

No container da API o ffmpeg vem do apt (Dockerfile). Em dev local, cai no
binário estático do imageio-ffmpeg (dependência de dev). ffprobe nem sempre
existe (o imageio-ffmpeg não o traz), então a duração é lida do stderr do
próprio ffmpeg.
"""
from __future__ import annotations

import re
import shutil
import subprocess

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


def get_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg não encontrado no PATH e imageio-ffmpeg não instalado"
        ) from exc


def run(args: list[str], action: str) -> subprocess.CompletedProcess:
    """Roda ffmpeg com os args dados; erro vira RuntimeError legível."""
    cmd = [get_ffmpeg(), "-y", "-v", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou ({action}): {result.stderr[-800:]}")
    return result


def duration_seconds(path: str) -> float:
    """Duração de um vídeo, parseada do banner do ffmpeg (sem ffprobe)."""
    result = subprocess.run([get_ffmpeg(), "-i", path],
                            capture_output=True, text=True)
    m = _DURATION_RE.search(result.stderr)
    if m is None:
        raise RuntimeError(f"não consegui ler a duração de {path}")
    h, mnt, sec = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(sec)


def extract_first_frame(video_path: str, image_path: str) -> None:
    """Primeiro frame de um clipe como imagem (storyboard)."""
    run(["-i", video_path, "-frames:v", "1", image_path],
        f"extrair frame de {video_path}")
