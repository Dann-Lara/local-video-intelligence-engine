"""Renderizado minimo de clips (Fase 5, primera version).

Objetivo de esta fase: obtener los primeros MP4 reales a partir de propuestas
editoriales aceptadas, validando el resultado audiovisual de principio a fin.

Decisiones (minimas y explicitas):

- Se usa FFmpeg con **argumentos estructurados** (lista, `shell=False`), igual
  que `services/transcription/orchestrator.py`. Nunca se interpola el nombre
  del archivo en una cadena de shell.
- El corte es preciso a nivel de frame: se hace un seek rapido a un keyframe
  anterior (`-ss` antes de `-i`) y luego se descarta el pre-roll exacto con
  `-ss` despues de `-i`. El video se recodifica (libx264, CRF 14) porque un
  corte por copia solo puede empezar en keyframe; el audio se copia sin
  recodificar para conservar el bitstream AAC original.
- No se generan subtitulos, efectos ni animaciones en esta fase: no estan
  implementados y no deben inventarse.
- La salida va a una carpeta nueva dentro de `work/`, nunca sobre el original.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import (
    RenderingOutputError,
    RenderingSourceError,
    RenderingToolError,
    RenderingValidationError,
)

DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"
DEFAULT_TIMEOUT = 600.0

# Margen de decodificacion anterior al inicio editorial. Debe ser mayor que el
# intervalo maximo de keyframes de la fuente (en el video de referencia: 3.7 s).
PRE_ROLL_SECONDS = 10.0


def _run(cmd, timeout: float):
    """Ejecuta un proceso externo con argumentos estructurados (sin shell)."""
    try:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=False,
        )
    except FileNotFoundError as exc:
        raise RenderingToolError(f"Ejecutable no encontrado: {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderingToolError(
            f"Timeout ejecutando {cmd[0]} tras {timeout}s"
        ) from exc


def probe(path: Path, *, ffprobe: str = DEFAULT_FFPROBE, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Devuelve el JSON de FFprobe para el archivo dado."""
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = _run(cmd, timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RenderingToolError(f"FFprobe fallo sobre {path}: {err}")
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RenderingToolError(f"FFprobe devolvio JSON invalido para {path}: {exc}") from exc


def _stream_of(data: Dict[str, Any], codec_type: str) -> Optional[Dict[str, Any]]:
    for stream in data.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def build_cut_plan(
    *,
    ffmpeg: str,
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    pre_roll: float = PRE_ROLL_SECONDS,
) -> Dict[str, Any]:
    """Construye (sin ejecutar) el plan de corte preciso.

    El `-ss` de entrada solo posiciona en un keyframe anterior; el `-ss` de
    salida descarta los frames intermedios ya decodificados. Se devuelve el
    comando y los valores reales de seek para trazabilidad.
    """
    start = float(start_seconds)
    end = float(end_seconds)
    roll = max(0.0, min(float(pre_roll), start))
    input_seek = start - roll
    duration = end - start

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        f"{input_seek:.6f}",
        "-i",
        str(source_path),
        "-ss",
        f"{roll:.6f}",
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
        "pre_roll_seconds": roll,
        "input_seek_seconds": input_seek,
        "output_seek_seconds": roll,
        "requested_start_seconds": start,
        "requested_end_seconds": end,
        "requested_duration_seconds": duration,
    }


def render_clip(
    *,
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
    output_path: Path,
    ffmpeg: str = DEFAULT_FFMPEG,
    ffprobe: str = DEFAULT_FFPROBE,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Renderiza un clip [start_seconds, end_seconds) a `output_path`.

    Usa el corte preciso descrito en `build_cut_plan`. Valida tiempos contra la
    duracion real de la fuente y verifica el resultado con FFprobe. Devuelve un
    dict con los metadatos reales y la trazabilidad del corte.
    """
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
    source_duration = float(source_probe.get("format", {}).get("duration", 0.0) or 0.0)
    if source_duration > 0 and end > source_duration + 0.01:
        raise RenderingValidationError(
            f"end_seconds {end} excede la duracion de la fuente {source_duration}",
            details={"end_seconds": end, "source_duration": source_duration},
        )

    duration = end - start
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plan = build_cut_plan(
        ffmpeg=ffmpeg,
        source_path=source_path,
        start_seconds=start,
        end_seconds=end,
        output_path=output_path,
    )
    cmd = plan["command"]
    proc = _run(cmd, timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RenderingToolError(
            f"FFmpeg fallo cortando {output_path}: {err}",
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
        "input_seek_seconds": plan["input_seek_seconds"],
        "output_seek_seconds": plan["output_seek_seconds"],
        "ffmpeg_command": cmd,
    }


# ---------------------------------------------------------------------------
# Version vertical 9:16 (Fase 5B)
# ---------------------------------------------------------------------------

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920


def render_vertical(
    *,
    source_path: Path,
    output_path: Path,
    ffmpeg: str = DEFAULT_FFMPEG,
    ffprobe: str = DEFAULT_FFPROBE,
    timeout: float = DEFAULT_TIMEOUT,
    width: int = SHORTS_WIDTH,
    height: int = SHORTS_HEIGHT,
) -> Dict[str, Any]:
    """Convierte un clip a un lienzo vertical 9:16 sin perder contenido.

    Estrategia de encuadre (elegida porque NO se puede verificar visualmente un
    recorte y el blueprint prohibe el recorte central ciego):

    - Fondo: el MISMO video escalado para cubrir todo el lienzo y desenfocado
      (rellena el espacio sin introducir material ajeno).
    - Primer plano: el video COMPLETO escalado para caber dentro del lienzo
      (contain), centrado. Se conserva el 100% del contenido visual original;
      no se recorta ninguna zona susceptible de contener sujeto/texto.

    Limitacion explicita: no se afirma que el encuadre sea optimo; es la opcion
    segura hasta que exista un reframing verificable visualmente.

    Requiere recodificacion de video (cambia la geometria). El audio se copia
    sin recodificar para preservar sincronizacion exacta.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not source_path.exists() or source_path.stat().st_size == 0:
        raise RenderingSourceError(
            f"Clip fuente inexistente o vacio: {source_path}",
            details={"source_path": str(source_path)},
        )

    source_probe = probe(source_path, ffprobe=ffprobe, timeout=timeout)
    src_v = _stream_of(source_probe, "video")
    src_a = _stream_of(source_probe, "audio")
    if src_v is None:
        raise RenderingSourceError(
            f"El clip fuente no tiene stream de video: {source_path}",
            details={"source_path": str(source_path)},
        )

    out_w = int(width)
    out_h = int(height)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Fondo desenfocado que cubre el lienzo; primer plano completo (contain).
    filter_complex = (
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},gblur=sigma=20,eq=brightness=-0.05[bg];"
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]"
    )

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = _run(cmd, timeout)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RenderingToolError(
            f"FFmpeg fallo creando la version vertical {output_path}: {err}",
            details={"command": cmd},
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RenderingOutputError(
            f"Salida vertical no generada o vacia: {output_path}",
            details={"output_path": str(output_path)},
        )

    out_probe = probe(output_path, ffprobe=ffprobe, timeout=timeout)
    v = _stream_of(out_probe, "video")
    a = _stream_of(out_probe, "audio")
    if v is None:
        raise RenderingOutputError(
            f"La version vertical no tiene stream de video: {output_path}",
            details={"output_path": str(output_path)},
        )

    out_duration = float(out_probe.get("format", {}).get("duration", 0.0) or 0.0)
    return {
        "output_path": str(output_path),
        "source_path": str(source_path),
        "source_width": src_v.get("width"),
        "source_height": src_v.get("height"),
        "source_duration_seconds": float(
            source_probe.get("format", {}).get("duration", 0.0) or 0.0
        ),
        "actual_duration_seconds": out_duration,
        "size_bytes": output_path.stat().st_size,
        "width": v.get("width"),
        "height": v.get("height"),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name") if a else None,
        "has_audio": a is not None,
        "source_had_audio": src_a is not None,
        "framing": "blurred_background_fit",
        "container": out_probe.get("format", {}).get("format_name"),
    }
