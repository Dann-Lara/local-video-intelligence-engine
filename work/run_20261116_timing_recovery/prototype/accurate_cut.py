"""Corte con precision de frame (modulo aislado, solo para prototipo/recuperacion).

No modifica el pipeline compartido. Implementa el corte hibrido demostrado en
`diagnosis/`:

1. `-ss <start - pre_roll>` ANTES de `-i`: seek rapido al keyframe anterior.
2. `-ss <pre_roll>` DESPUES de `-i`: descarte exacto de frames hasta el
   timestamp solicitado (recodificando video, porque un corte por copia solo
   puede empezar en un keyframe).
3. `-t <duracion>`: duracion editorial exacta.
4. Video recodificado (libx264, crf 14, yuv420p, 30000/1001 heredado).
5. Audio COPIADO (`-c:a copy`): conserva el bitstream AAC original y evita una
   generacion extra de recodificacion; el offset medido es ~3 ms (por debajo
   del umbral perceptible y muy por dentro de la tolerancia de un frame).

La firma imita `services.rendering.renderer.render_clip` para poder inyectarse
en `services.delivery.pipeline` en tiempo de ejecucion (monkeypatch aislado).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from services.rendering.errors import (
    RenderingOutputError,
    RenderingSourceError,
    RenderingToolError,
    RenderingValidationError,
)
from services.rendering.renderer import (
    DEFAULT_FFMPEG,
    DEFAULT_FFPROBE,
    DEFAULT_TIMEOUT,
    _run,
    _stream_of,
    probe,
)

PRE_ROLL_SECONDS = 10.0


def accurate_cut_command(
    *,
    ffmpeg: str,
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    pre_roll: float = PRE_ROLL_SECONDS,
) -> Dict[str, Any]:
    """Devuelve (sin ejecutar) el comando exacto y los valores de seek."""
    start = float(start_seconds)
    end = float(end_seconds)
    duration = end - start
    # Seek de entrada: keyframe anterior a `start`. Seek de salida: descarte
    # exacto desde ese keyframe hasta `start`.
    pre = max(0.0, start - pre_roll)
    offset = start - pre
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        f"{pre:.6f}",
        "-i",
        str(source_path),
        "-ss",
        f"{offset:.6f}",
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return {
        "command": cmd,
        "pre_roll_seconds": pre,
        "output_seek_seconds": offset,
        "requested_start_seconds": start,
        "requested_end_seconds": end,
        "requested_duration_seconds": duration,
    }


def render_clip_frame_accurate(
    *,
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    ffmpeg: str = DEFAULT_FFMPEG,
    ffprobe: str = DEFAULT_FFPROBE,
    timeout: float = DEFAULT_TIMEOUT,
    pre_roll: float = PRE_ROLL_SECONDS,
) -> Dict[str, Any]:
    """Corta [start, end) con precision de frame y devuelve metadatos reales."""
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not source_path.exists() or source_path.stat().st_size == 0:
        raise RenderingSourceError(
            f"Video fuente inexistente o vacio: {source_path}",
            details={"source_path": str(source_path)},
        )

    start = float(start_seconds)
    end = float(end_seconds)
    if not (start >= 0 and end > start):
        raise RenderingValidationError(
            f"Rango invalido: start={start} end={end}",
            details={"start_seconds": start, "end_seconds": end},
        )

    source_probe = probe(source_path, ffprobe=ffprobe, timeout=timeout)
    source_duration = float(
        source_probe.get("format", {}).get("duration", 0.0) or 0.0
    )
    if source_duration > 0 and end > source_duration + 0.01:
        raise RenderingValidationError(
            f"end_seconds {end} excede la duracion de la fuente {source_duration}",
            details={"end_seconds": end, "source_duration": source_duration},
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plan = accurate_cut_command(
        ffmpeg=ffmpeg,
        source_path=source_path,
        start_seconds=start,
        end_seconds=end,
        output_path=output_path,
        pre_roll=pre_roll,
    )
    cmd = plan["command"]

    proc = _run(cmd, timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RenderingToolError(
            f"FFmpeg fallo cortando {output_path}: {err[-2000:]}",
            details={"command": cmd},
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderingOutputError(
            f"Salida no generada o vacia: {output_path}",
            details={"output_path": str(output_path)},
        )

    out_probe = probe(output_path, ffprobe=ffprobe, timeout=timeout)
    v = _stream_of(out_probe, "video")
    a = _stream_of(out_probe, "audio")
    if v is None:
        raise RenderingOutputError(
            f"El clip no tiene stream de video: {output_path}",
            details={"output_path": str(output_path)},
        )

    out_duration = float(out_probe.get("format", {}).get("duration", 0.0) or 0.0)
    duration = end - start
    return {
        "output_path": str(output_path),
        "requested_start_seconds": start,
        "requested_end_seconds": end,
        "requested_duration_seconds": duration,
        "actual_duration_seconds": out_duration,
        "duration_delta_seconds": out_duration - duration,
        "size_bytes": output_path.stat().st_size,
        "width": v.get("width"),
        "height": v.get("height"),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "container": out_probe.get("format", {}).get("format_name"),
        "cut_method": "frame_accurate_hybrid_decode_trim_reencode_video_audio_copy",
        "pre_roll_seconds": plan["pre_roll_seconds"],
        "output_seek_seconds": plan["output_seek_seconds"],
        "ffmpeg_command": plan["command"],
    }
