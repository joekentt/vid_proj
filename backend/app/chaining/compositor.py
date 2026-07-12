"""Concatenação dos clipes das cenas com transições (FFmpeg).

Estratégia: normaliza cada clipe para a MESMA resolução/fps (o fallback de
OOM do worker pode ter reduzido uma cena; xfade exige streams idênticos) e
então funde os clipes DOIS A DOIS, da esquerda para a direita:

  cut      -> filtro concat (corte seco)
  fade     -> xfade transition=fadeblack (passa pelo preto)
  dissolve -> xfade transition=fade      (crossfade entre os clipes)

Fundir aos pares re-encoda N-1 vezes, mas mantém o grafo de filtros trivial
e o offset do xfade exato (duração do acumulado - duração da transição) —
para dezenas de clipes curtos o custo é irrelevante perto da geração.
"""
from __future__ import annotations

import os
import tempfile

from app.chaining import ffmpeg_utils
from app.models import TransitionType

_ENCODE = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"]

_XFADE_BY_TRANSITION = {
    TransitionType.FADE: "fadeblack",
    TransitionType.DISSOLVE: "fade",
}


def _normalize(src: str, dst: str, width: int, height: int, fps: int) -> None:
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
    ffmpeg_utils.run(["-i", src, "-vf", vf, *_ENCODE, dst],
                     f"normalizar {os.path.basename(src)}")


def _concat_pair(a: str, b: str, out: str) -> None:
    ffmpeg_utils.run(
        ["-i", a, "-i", b,
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]",
         "-map", "[out]", *_ENCODE, out],
        "concat (cut)")


def _xfade_pair(a: str, b: str, out: str, kind: str, duration: float) -> None:
    dur_a = ffmpeg_utils.duration_seconds(a)
    # O clipe seguinte precisa ser mais longo que a transição.
    duration = min(duration, max(0.1, ffmpeg_utils.duration_seconds(b) - 0.1),
                   max(0.1, dur_a - 0.1))
    offset = max(0.0, dur_a - duration)
    ffmpeg_utils.run(
        ["-i", a, "-i", b,
         "-filter_complex",
         f"[0:v][1:v]xfade=transition={kind}:duration={duration:.3f}:"
         f"offset={offset:.3f}[out]",
         "-map", "[out]", *_ENCODE, out],
        f"xfade {kind}")


def compose(clips: list[str],
            transitions: list[tuple[TransitionType, float]],
            out_path: str, width: int, height: int, fps: int) -> None:
    """Junta os clipes em `out_path`.

    `transitions[i]` = (tipo, duração) aplicada ENTRE clips[i] e clips[i+1]
    (vem de Scene.transition_to_next/transition_duration_seconds).
    """
    assert len(transitions) == len(clips) - 1, "1 transição entre cada par"
    with tempfile.TemporaryDirectory(prefix="compose-") as tmp:
        norm = []
        for i, c in enumerate(clips):
            n = os.path.join(tmp, f"norm-{i}.mp4")
            _normalize(c, n, width, height, fps)
            norm.append(n)

        acc = norm[0]
        for i, (kind, dur) in enumerate(transitions):
            step = os.path.join(tmp, f"acc-{i}.mp4")
            if kind == TransitionType.CUT:
                _concat_pair(acc, norm[i + 1], step)
            else:
                _xfade_pair(acc, norm[i + 1], step,
                            _XFADE_BY_TRANSITION[kind], dur)
            acc = step

        # Passe final para garantir moov/encode consistente no destino.
        ffmpeg_utils.run(["-i", acc, "-c", "copy", out_path], "mover resultado")
