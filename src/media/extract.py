"""Extraccion de audio (blueprint secciones 12 y 32).

Whisper espera 16 kHz mono PCM. Se extrae una vez, al inicio, y todas las
etapas siguientes (Whisper, y el analisis de nivel de audio) leen ese WAV.

Parametros que estan fijados por el modelo y no son negociables:

    -ar 16000   frecuencia de muestreo que espera faster-whisper
    -ac 1       mono: el modelo es mono y multicanal solo desperdicia VRAM
    -c:a pcm_s16le
                PCM sin comprimir. Un MP3 a 128 kbps para 10 minutos de voz
                discourse cabe de sobra, pero medir volumen con audio con
                perdida es medir la compresion, no la voz.

Se normaliza a -14 LUFS en el render final (seccion 32), NO aqui. Normalizar
antes de transcribir descarta informacion de rango dinamico que Whisper
aprovecha, y el objetivo del pipeline es entender el contenido, no masterizar
el audio en la ingesta.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.media import ffmpeg, ffprobe

WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
PCM_CODEC = "pcm_s16le"


class ExtractionError(RuntimeError):
    """No se pudo extraer audio del fichero."""


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    sample_rate: int
    channels: int
    codec: str | None
    duration: float | None
    size_bytes: int

    @property
    def is_whisper_ready(self) -> bool:
        return (
            self.sample_rate == WHISPER_SAMPLE_RATE
            and self.channels == WHISPER_CHANNELS
        )

    def summary(self) -> str:
        state = "listo para Whisper" if self.is_whisper_ready else "NO es lo que espera Whisper"
        return (
            f"{self.path.name}: {self.sample_rate} Hz, {self.channels} ch, "
            f"{self.codec}, {self.duration:.1f}s ({state})"
            if self.duration
            else f"{self.path.name}: {self.sample_rate} Hz, {self.channels} ch, {state}"
        )


def _settings(config: dict[str, Any] | None) -> tuple[int, int]:
    if not config:
        return WHISPER_SAMPLE_RATE, WHISPER_CHANNELS
    from src.core import config as config_module

    rate = config_module.get(config, "whisper.audio_sample_rate", WHISPER_SAMPLE_RATE)
    channels = config_module.get(config, "whisper.audio_channels", WHISPER_CHANNELS)
    return int(rate or WHISPER_SAMPLE_RATE), int(channels or WHISPER_CHANNELS)


def extract_audio(
    source: Path,
    destination: Path,
    config: dict[str, Any] | None = None,
    *,
    overwrite: bool = False,
) -> AudioInfo:
    """Extrae el audio a WAV mono listo para Whisper."""
    if not source.is_file():
        raise ExtractionError(f"no existe el origen: {source}")

    try:
        info = ffprobe.probe(source)
    except ffprobe.ProbeError as exc:
        raise ExtractionError(f"no se pudo analizar {source.name}: {exc}") from exc

    if not info.has_audio:
        raise ExtractionError(
            f"{source.name} no tiene stream de audio. El pipeline transcribe la voz, "
            "asi que un video mudo no se puede procesar."
        )

    if destination.exists() and not overwrite:
        existing = _inspect(destination)
        if existing is not None:
            return existing
        # El destino existe pero no vale (0 bytes, o un WAV que ffprobe no lee).
        # Hay que rehacerlo, asi que se permite sobrescribir. Sin esto, el
        # `-n` de mas abajo haria que ffmpeg se negara y el error que apareceria
        # ("ffmpeg termino sin error pero audio.wav esta vacio") no explicaria
        # la causa real: que el fichero estaba ahi de antes.
        replace = True
    else:
        replace = bool(overwrite)

    destination.parent.mkdir(parents=True, exist_ok=True)
    sample_rate, channels = _settings(config)

    argv = [
        ffmpeg.require("ffmpeg"),
        "-y" if replace else "-n",
        "-loglevel", "error",
        "-i", str(source),
        "-vn",                      # descarta video
        "-map", "0:a:0",            # primer stream de audio
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-c:a", PCM_CODEC,
        str(destination),
    ]

    try:
        ffmpeg.run(argv)
    except ffmpeg.CommandFailedError as exc:
        raise ExtractionError(
            f"ffmpeg no pudo extraer el audio de {source.name}: {exc}"
        ) from exc

    if not destination.is_file() or destination.stat().st_size == 0:
        raise ExtractionError(
            f"ffmpeg termino sin error pero {destination.name} esta vacio o no existe"
        )

    result = _inspect(destination)
    if result is None:
        raise ExtractionError(f"el audio extraido en {destination} no es legible")
    return result


def _inspect(path: Path) -> AudioInfo | None:
    try:
        info = ffprobe.probe(path)
    except ffprobe.ProbeError:
        return None
    if not info.audio:
        return None
    return AudioInfo(
        path=path,
        sample_rate=info.audio.sample_rate or 0,
        channels=info.audio.channels or 0,
        codec=info.audio.codec,
        duration=info.duration,
        size_bytes=path.stat().st_size,
    )


def extract_thumbnail(
    source: Path,
    destination: Path,
    *,
    at_seconds: float = 1.0,
    overwrite: bool = False,
) -> Path | None:
    """Extrae un fotograma para inspeccion visual o QC posterior.

    Devuelve None si no se pudo: una miniatura ausente no debe tumbar la ingesta.
    """
    if destination.exists() and not overwrite:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg.run(
            [
                ffmpeg.require("ffmpeg"),
                "-y" if overwrite else "-n",
                "-loglevel", "error",
                "-ss", str(at_seconds),
                "-i", str(source),
                "-frames:v", "1",
                "-q:v", "3",
                str(destination),
            ]
        )
    except ffmpeg.CommandFailedError:
        return None
    return destination if destination.is_file() else None
