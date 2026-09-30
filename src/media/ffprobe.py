"""Analisis de media con FFprobe (blueprint seccion 13).

Extrae duracion, resolucion, fps, codec, bitrate, streams de audio, sample
rate, canales, tamano y aspect ratio.

Dos detalles que no son obvios y que cuestan un rato si se ignoran:

1. `r_frame_rate` viene como fraccion textual ("30000/1001"). Parsearla con
   `float()` directo falla en cuanto hay frame rate no entero, que es lo normal
   en material de camara.

2. Los telefonos guardan el video con matriz de rotacion. Un "1080x1920" en el
   contenedor puede mostrarse en horizontal. ffprobe lo expone en el stream
   `side_data_list` / en tags como `rotate`. Omitirlo hace que el recorte 9:16
   salga girado, y el sintoma aparece tres etapas mas tarde, en el render.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from src.media import ffmpeg


class ProbeError(RuntimeError):
    """ffprobe no pudo leer el fichero, o no es multimedia reconocible."""


@dataclass(frozen=True)
class AudioStream:
    codec: str | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    bitrate: int | None
    duration: float | None


@dataclass(frozen=True)
class VideoStream:
    codec: str | None
    width: int
    height: int
    fps: float | None
    bitrate: int | None
    pix_fmt: str | None
    rotation: int

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def display_width(self) -> int:
        """Ancho tal y como se ve, ya aplicado el giro de camara."""
        return self.height if self.rotation % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation % 180 == 90 else self.height

    @property
    def is_display_portrait(self) -> bool:
        return self.display_height > self.display_width


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    size_bytes: int
    format_name: str | None
    duration: float | None
    bitrate: int | None
    video: VideoStream | None
    audio: AudioStream | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def aspect_ratio(self) -> str | None:
        if not self.video:
            return None
        return aspect_ratio(self.video.display_width, self.video.display_height)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload.pop("raw", None)
        return payload

    def summary(self) -> str:
        if not self.video:
            return f"{self.path.name}: sin stream de video"
        parts = [
            self.path.name,
            f"{self.video.display_width}x{self.video.display_height}",
            str(self.aspect_ratio),
            f"{self.video.fps:.2f}fps" if self.video.fps else "fps?",
            f"{self.duration:.1f}s" if self.duration else "duracion?",
        ]
        if self.audio:
            rate = self.audio.sample_rate or "?"
            parts.append(f"audio {self.audio.codec} {rate}Hz {self.audio.channels}ch")
        else:
            parts.append("SIN AUDIO")
        if self.video.rotation % 360:
            parts.append(f"rotacion {self.video.rotation}")
        return "  ".join(parts)


def aspect_ratio(width: int, height: int) -> str | None:
    """Reduce a la forma mas simple: 1920x1080 -> '16:9'."""
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def parse_rate(value: str | None) -> float | None:
    """'30000/1001' -> 29.97. Devuelve None si no es interpretable.

    Se rechazan los valores <= 0 en AMBAS ramas. Un frame rate de 0 o negativo
    no es "raro", es basura: hay contenedores que lo traen y acabaria
    presentandose como un video valido con fps 0, que revienta mas tarde al
    calcular timecodes.
    """
    if not value or value in {"0/0", "N/A"}:
        return None
    for parse in (
        lambda: float(Fraction(value)),
        lambda: float(value),
    ):
        try:
            rate = parse()
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return None


def parse_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rotation_of(stream: dict[str, Any]) -> int:
    """Giro declarado por el contenedor, normalizado a 0..359.

    Hay DOS convenciones opuestas en el mundo real y confundirlas produce un
    clip vertical del reves, que es un fallo caro y dificil de atribuir:

    - `side_data_list[].rotation` (Display Matrix). Viene ya en la convencion
      que hay que aplicar: los angulos en grados. `90` significa 90 grados, y no
      hay que negarlo.
    - `tags.rotate` (campo heredado, de telefonos antiguos). Viene con el signo
      invertido respecto al anterior: un `-90` ahi equivale a un `90` en la
      matriz.

    Se prioriza la matriz de display porque es la que respeta el contenedor; el
    tag es un resto. Solo importa si el giro es multiple de 90, que es lo unico
    que cambia las dimensiones; la direccion exacta solo afecta al recorte, y
    para eso hay mas info en el stream.
    """
    for entry in stream.get("side_data_list") or []:
        if "rotation" in entry:
            degrees = parse_float(entry["rotation"])
            if degrees is not None:
                return int(round(degrees)) % 360

    tags = stream.get("tags") or {}
    for key in ("rotate", "Rotate"):
        raw = tags.get(key)
        if raw is not None:
            degrees = parse_float(raw)
            if degrees is not None:
                return int(round(-degrees)) % 360

    return 0


def _audio_stream(stream: dict[str, Any]) -> AudioStream:
    return AudioStream(
        codec=stream.get("codec_name"),
        sample_rate=parse_int(stream.get("sample_rate")),
        channels=parse_int(stream.get("channels")),
        channel_layout=stream.get("channel_layout"),
        bitrate=parse_int(stream.get("bit_rate")),
        duration=parse_float(stream.get("duration")),
    )


def _video_stream(stream: dict[str, Any]) -> VideoStream:
    width = parse_int(stream.get("width")) or 0
    height = parse_int(stream.get("height")) or 0
    return VideoStream(
        codec=stream.get("codec_name"),
        width=width,
        height=height,
        fps=parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate")),
        bitrate=parse_int(stream.get("bit_rate")),
        pix_fmt=stream.get("pix_fmt"),
        rotation=_rotation_of(stream),
    )


def _duration_of(payload: dict[str, Any], video: VideoStream | None, audio: AudioStream | None) -> float | None:
    """Duracion del contenedor, con respaldo en los streams.

    Algunos MP4 no traen duracion a nivel de formato; en ese caso se usa la del
    stream de video, y si tampoco la hay, se estima con frames y fps.
    """
    container = parse_float(payload.get("format", {}).get("duration"))
    if container and container > 0:
        return container
    for stream in (video, audio):
        if stream and stream.duration and stream.duration > 0:
            return stream.duration
    if video and video.fps:
        stream = next(
            (s for s in payload.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        frames = parse_int((stream or {}).get("nb_frames"))
        if frames and frames > 0:
            return frames / video.fps
    return None


def probe_raw(path: Path | str) -> dict[str, Any]:
    """Salida JSON de ffprobe.

    Traduce el fallo del proceso a `ProbeError`. Sin esta traduccion, un
    fichero corrupto escapaba como `CommandFailedError` y se colaba hasta
    `is_valid_video()`, que solo captura `ProbeError`: el mensaje que veria el
    usuario seria un volcado de stderr de ffprobe en vez de "esto no es un
    video".
    """
    try:
        result = ffmpeg.run(
            [
                ffmpeg.require("ffprobe"),
                "-v", "error",
                "-show_format",
                "-show_streams",
                "-of", "json",
                str(path),
            ]
        )
    except ffmpeg.CommandFailedError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        raise ProbeError(
            f"ffprobe no pudo leer {Path(path).name}: "
            f"{detail[-1] if detail else 'contenedor o stream invalido'}"
        ) from exc

    try:
        payload = result.json()
    except ValueError as exc:
        raise ProbeError(f"ffprobe no devolvio JSON para {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"ffprobe devolvio una estructura inesperada para {path}")
    return payload


def probe(path: Path | str) -> MediaInfo:
    """Analiza un fichero multimedia. Lanza ProbeError si no es legible."""
    target = Path(path)
    if not target.is_file():
        raise ProbeError(f"no existe el fichero: {target}")

    payload = probe_raw(target)
    streams = payload.get("streams") or []
    if not streams:
        raise ProbeError(f"ffprobe no encontro ningun stream en {target.name}")

    video_raw = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_raw = next((s for s in streams if s.get("codec_type") == "audio"), None)

    video = _video_stream(video_raw) if video_raw else None
    audio = _audio_stream(audio_raw) if audio_raw else None

    return MediaInfo(
        path=target,
        size_bytes=target.stat().st_size,
        format_name=payload.get("format", {}).get("format_name"),
        duration=_duration_of(payload, video, audio),
        bitrate=parse_int(payload.get("format", {}).get("bit_rate")),
        video=video,
        audio=audio,
        raw=payload,
    )


def is_valid_video(path: Path | str) -> tuple[bool, str]:
    """Comprobacion de formato para el paso 6 de la seccion 12."""
    try:
        info = probe(path)
    except ProbeError as exc:
        return False, str(exc)

    if not info.has_video:
        return False, f"{Path(path).name}: no contiene un stream de video"
    if not info.video or info.video.width <= 0 or info.video.height <= 0:
        return False, f"{Path(path).name}: resolucion invalida"
    if not info.has_audio:
        return False, (
            f"{Path(path).name}: no tiene audio. El pipeline transcribe la voz, "
            "asi que un video mudo no se puede procesar"
        )
    return True, info.summary()


__all__ = [
    "AudioStream",
    "MediaInfo",
    "ProbeError",
    "VideoStream",
    "aspect_ratio",
    "is_valid_video",
    "parse_float",
    "parse_int",
    "parse_rate",
    "probe",
    "probe_raw",
]
