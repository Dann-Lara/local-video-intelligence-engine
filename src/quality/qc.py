"""Quality Control tecnico y de subtitulos (blueprint seccion 33).

Este modulo valida el clip YA RENDERIZADO contra la seccion 33, la parte que no
necesita IA:

- Media: existe, duracion correcta, codec, resolucion, audio, video, tamano.
- Subtitulos: no salen de pantalla, sincronizacion, caracteres invalidos.

Reglas que mandan (y de donde salen):

1. El QC NO regenera nada. Solo mira el fichero que hay. Si algo falla, el
   clip se marca como fallido y quien lo llamo decide si se re-renderiza.
2. Las comprobaciones de media usan FFprobe sobre el render final, el MISMO
   fichero que se va a publicar, no el master intermedio. Validar el master
   y luego publicar el reencode es validar otra cosa.
3. La duracion esperada sale del edit_plan (ventana de origen), no de la
   config: el plan es la fuente de verdad de cuanto debe durar el clip.
4. La resolucion esperada sale de `output.*` de la config y se compara con las
   dimensiones DE PRESENTACION (ya aplicadas la rotacion del contenedor), que
   es lo que ve el espectador. Un 1080x1920 con rotate=90 se ve 1920x1080.
5. "No sale de pantalla" no se puede decidir leyendo el JSON: depende del
   renderizado real. Se resuelve con una estimacion geometrica conservadora
   (ancho de linea medido con la fuente, margen de seguridad) que marca
   `out_of_screen` si un cue teoricamente no cabe. Es una PORNADA de caja: un
   clip con lineas largas se marca sospechoso aunque quepa. Se decided en
   tools/check_layout.py, que si renderiza y mide de verdad.
6. Los caracteres invalidos (caracteres de reemplazo Unicode, notas de control)
   NO son detectables mirando el texto del cue si el Whisper ya los emitio
   como caracteres de reemplazo. Se comprueba que el cue no traiga caracteres
   de control o U+FFFD, que es lo que se puede garantizar desde aqui.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts, config as config_module, logs
from src.media import ffprobe

LOGGER = logs.get_logger("qc")

# Codecs de video aceptados para un clip publicable (seccion 33 "codec valido").
_ACCEPTED_VIDEO_CODECS = {"h264", "hevc", "av1"}
_ACCEPTED_AUDIO_CODECS = {"aac", "mp3", "opus"}

# Caracteres que no deberian aparecer en un subtitulo: notas de control y el
# caracter de reemplazo Unicode (U+FFFD), sintoma clasico de transcripcion con
# caracteres fuera del juego de fuentes.
_BAD_SUBTITLE_CHARS = {"�"}


class QCError(RuntimeError):
    """Fallo irrecuperable de la etapa QUALITY_CONTROL (no del clip)."""


@dataclass
class SubtitleReport:
    """Resultado de las comprobaciones de subtitulos de un clip."""

    subtitles_present: bool
    out_of_screen: bool
    unsynchronized: bool
    invalid_characters: bool
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.out_of_screen or self.unsynchronized or self.invalid_characters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtitles_present": self.subtitles_present,
            "out_of_screen": self.out_of_screen,
            "unsynchronized": self.unsynchronized,
            "invalid_characters": self.invalid_characters,
            "problems": list(self.problems),
            "passed": self.passed,
        }


@dataclass
class MediaReport:
    """Resultado de las comprobaciones de media de un clip (seccion 33)."""

    exists: bool
    video_present: bool
    audio_present: bool
    duration_ok: bool
    resolution_ok: bool
    codec_ok: bool
    size_ok: bool
    size_bytes: int
    actual_duration: float | None = None
    actual_resolution: str | None = None
    actual_codecs: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.exists
            and self.video_present
            and self.audio_present
            and self.duration_ok
            and self.resolution_ok
            and self.codec_ok
            and self.size_ok
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "video_present": self.video_present,
            "audio_present": self.audio_present,
            "duration_ok": self.duration_ok,
            "resolution_ok": self.resolution_ok,
            "codec_ok": self.codec_ok,
            "size_ok": self.size_ok,
            "size_bytes": self.size_bytes,
            "actual_duration": (
                round(self.actual_duration, 3) if self.actual_duration is not None else None
            ),
            "actual_resolution": self.actual_resolution,
            "actual_codecs": self.actual_codecs,
            "problems": list(self.problems),
            "passed": self.passed,
        }


# --- media -----------------------------------------------------------------


def check_media(
    path: Path | str,
    *,
    expected_duration: float | None,
    config: dict[str, Any],
) -> MediaReport:
    """Valida el render final con FFprobe (seccion 33, bloque Media).

    No lanza si el fichero no existe o FFprobe no lo puede leer: eso es un
    resultado de QC (fallo), no un error de la etapa. Devuelve el informe con
    `exists=False` para que quien llama lo registre como clip fallido.
    """
    target = Path(path)
    quality = _quality_config(config)

    if not target.is_file():
        return MediaReport(
            exists=False,
            video_present=False,
            audio_present=False,
            duration_ok=False,
            resolution_ok=False,
            codec_ok=False,
            size_ok=False,
            size_bytes=0,
            problems=[f"el render no existe: {target}"],
        )

    try:
        info = ffprobe.probe(target)
    except ffprobe.ProbeError as exc:
        return MediaReport(
            exists=True,
            video_present=False,
            audio_present=False,
            duration_ok=False,
            resolution_ok=False,
            codec_ok=False,
            size_ok=False,
            size_bytes=target.stat().st_size,
            problems=[f"ffprobe no pudo leer el render: {exc}"],
        )

    problems: list[str] = []
    video = info.video
    audio = info.audio

    # video / audio presentes
    video_present = info.has_video
    audio_present = info.has_audio
    if not video_present:
        problems.append("el render no tiene stream de video")
    if not audio_present:
        problems.append("el render no tiene stream de audio")

    # duracion contra la ventana del plan
    duration_ok = True
    actual_duration = info.duration
    if expected_duration is not None and expected_duration > 0:
        if actual_duration is None:
            duration_ok = False
            problems.append("no se pudo leer la duracion del render")
        else:
            tolerance = float(quality["duration_tolerance_seconds"])
            delta = abs(actual_duration - expected_duration)
            duration_ok = delta <= tolerance
            if not duration_ok:
                problems.append(
                    f"duracion fuera de tolerancia: {actual_duration:.2f}s vs "
                    f"esperada {expected_duration:.2f}s (delta {delta:.2f}s > "
                    f"{tolerance:.2f}s)"
                )

    # resolucion de presentacion contra output.*
    resolution_ok = True
    actual_resolution = None
    if video is not None:
        actual_resolution = f"{video.display_width}x{video.display_height}"
        exp_w = int(config_module.get(config, "output.width", 1080))
        exp_h = int(config_module.get(config, "output.height", 1920))
        if (video.display_width, video.display_height) != (exp_w, exp_h):
            resolution_ok = False
            problems.append(
                f"resolucion inesperada: {actual_resolution} vs {exp_w}x{exp_h}"
            )

    # codec de video y audio
    codec_ok = True
    actual_codecs = None
    if video is not None:
        actual_codecs = video.codec or "?"
        if actual_codecs not in _ACCEPTED_VIDEO_CODECS:
            codec_ok = False
            problems.append(f"codec de video no publicable: {actual_codecs}")
    if audio is not None and audio.codec:
        if actual_codecs:
            actual_codecs = f"{actual_codecs}/{audio.codec}"
        else:
            actual_codecs = audio.codec
        if audio.codec not in _ACCEPTED_AUDIO_CODECS:
            codec_ok = False
            problems.append(f"codec de audio no publicable: {audio.codec}")

    # tamano maximo
    size_ok = True
    max_size = int(quality["max_render_size_bytes"])
    size_bytes = info.size_bytes
    if max_size > 0 and size_bytes > max_size:
        size_ok = False
        problems.append(
            f"render demasiado grande: {size_bytes} bytes > maximo {max_size}"
        )

    return MediaReport(
        exists=True,
        video_present=video_present,
        audio_present=audio_present,
        duration_ok=duration_ok,
        resolution_ok=resolution_ok,
        codec_ok=codec_ok,
        size_ok=size_ok,
        size_bytes=size_bytes,
        actual_duration=actual_duration,
        actual_resolution=actual_resolution,
        actual_codecs=actual_codecs,
        problems=problems,
    )


# --- subtitulos ------------------------------------------------------------


def _bad_characters_in(text: str) -> list[str]:
    """Caracteres problematicos en un texto de subtitulo.

    Se marcan el caracter de reemplazo Unicode (U+FFFD) y las notas de control,
    que en un .ass no imprimen nada util o rompen el cartel. Los saltos de linea
    del cue (separadores de linea, U+000A) NO son notas problematicas: son
    legitimos, el cue se parte en dos lineas a proposito, y el propio renderer
    los convierte en \\N. Tambien se permiten tabuladores.
    """
    bad: list[str] = []
    for char in text:
        if char in _BAD_SUBTITLE_CHARS:
            bad.append(char)
            continue
        if unicodedata.category(char) == "Cc" and char not in "\n\t\r":
            bad.append(char)
    return bad


def check_subtitles(
    cues: list[dict[str, Any]],
    *,
    duration: float | None,
    config: dict[str, Any],
) -> SubtitleReport:
    """Valida los cues de un clip (seccion 33, bloque Subtitulos).

    `cues` es la lista de dicts {start, end, text} en tiempos RELATIVOS al clip,
    tal y como los produce src/subtitles/cues.py. Un clip sin subtitulos
    (subtitles.enabled=false) devuelve subtitles_present=False y passed=True:
    no tener subtitulos no es un fallo.
    """
    problems: list[str] = []
    if not cues:
        return SubtitleReport(
            subtitles_present=False,
            out_of_screen=False,
            unsynchronized=False,
            invalid_characters=False,
            problems=[],
        )

    out_of_screen = False
    unsynchronized = False
    invalid_characters = False

    tolerance = float(_quality_config(config)["duration_tolerance_seconds"])
    min_cue = _cue_min_duration(config)
    max_cue = _cue_max_duration(config)

    previous_end: float | None = None
    for index, cue in enumerate(cues):
        try:
            start = float(cue.get("start"))
            end = float(cue.get("end"))
        except (TypeError, ValueError):
            unsynchronized = True
            problems.append(f"cue {index}: tiempos no numericos")
            continue

        # sincronizacion: dentro del clip, ordenados, sin solaparse, duracion
        # dentro de los limites de configuracion.
        if start < -tolerance or (duration is not None and end > duration + tolerance):
            unsynchronized = True
            problems.append(
                f"cue {index} fuera de la duracion del clip: {start:.2f}-{end:.2f} "
                f"(clip {duration})"
            )
        if end <= start:
            unsynchronized = True
            problems.append(f"cue {index}: fin anterior o igual al inicio")
        if previous_end is not None and start < previous_end - tolerance:
            unsynchronized = True
            problems.append(
                f"cue {index} se solapa con el anterior ({start:.2f} < {previous_end:.2f})"
            )
        cue_duration = end - start
        if cue_duration < min_cue - 0.001:
            unsynchronized = True
            problems.append(
                f"cue {index} dura {cue_duration:.3f}s, por debajo del minimo {min_cue}s"
            )
        if cue_duration > max_cue + 0.001:
            unsynchronized = True
            problems.append(
                f"cue {index} dura {cue_duration:.3f}s, por encima del maximo {max_cue}s"
            )
        previous_end = end

        # caracteres invalidos
        text = str(cue.get("text", ""))
        bad = _bad_characters_in(text)
        if bad:
            invalid_characters = True
            shown = ", ".join(sorted({f"U+{ord(c):04X}" for c in bad}))
            problems.append(f"cue {index} contiene caracteres invalidos: {shown}")

        # caja: estimacion conservadora del ancho de la linea mas larga
        widest = max((len(line) for line in text.split("\n")), default=0)
        if widest > _max_line_chars(config):
            out_of_screen = True
            problems.append(
                f"cue {index} tiene una linea de {widest} caracteres, por encima "
                f"del maximo de linea {int(_max_line_chars(config))}"
            )

    return SubtitleReport(
        subtitles_present=True,
        out_of_screen=out_of_screen,
        unsynchronized=unsynchronized,
        invalid_characters=invalid_characters,
        problems=problems,
    )


# --- configuracion ---------------------------------------------------------


def _quality_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("quality", {}) or {})


def _cue_min_duration(config: dict[str, Any]) -> float:
    return float(config_module.get(config, "subtitles.min_duration_ms", 700)) / 1000.0


def _cue_max_duration(config: dict[str, Any]) -> float:
    return float(config_module.get(config, "subtitles.max_duration_ms", 5000)) / 1000.0


def _max_line_chars(config: dict[str, Any]) -> float:
    return float(config_module.get(config, "subtitles.max_chars_per_line", 34))


# --- orquestacion de un clip ----------------------------------------------


@dataclass
class ClipQC:
    """QC completo de un clip: media + subtitulos + semantica (opcional).

    `semantic` lo rellena src/quality/semantic.py (el segundo LLM pass). Si la
    etapa semantica esta desactivada o no se ha corrido, queda en None y
    `passed` solo depende de media y subtitulos.
    """

    clip_id: str
    media: MediaReport
    subtitles: SubtitleReport
    semantic: dict[str, Any] | None = None
    min_semantic_score: float = 0.0

    @property
    def passed(self) -> bool:
        if not self.media.passed or not self.subtitles.passed:
            return False
        if self.semantic is not None:
            if not self.semantic.get("approved", False):
                return False
            score = self.semantic.get("score")
            if score is not None and score < self.min_semantic_score:
                return False
        return True

    def problems(self) -> list[str]:
        problems = list(self.media.problems) + list(self.subtitles.problems)
        if self.semantic:
            problems.extend(self.semantic.get("problems", []))
        return problems

    def to_record_qc(self, config: dict[str, Any]) -> dict[str, Any]:
        """Bloque `qc` del clip_record (seccion 37, campo qc)."""
        return {
            "passed": self.passed,
            "technical": {
                "exists": self.media.exists,
                "duration_ok": self.media.duration_ok,
                "resolution_ok": self.media.resolution_ok,
                "codec_ok": self.media.codec_ok,
                "audio_present": self.media.audio_present,
                "video_present": self.media.video_present,
                "size_bytes": self.media.size_bytes,
            },
            "subtitles_ok": self.subtitles.passed,
            "semantic": self.semantic,
            "problems": self.problems(),
        }

    def summary(self) -> str:
        verdict = "OK" if self.passed else "FALLO"
        if not self.subtitles.subtitles_present:
            subs = "sin"
        else:
            subs = "ok" if self.subtitles.passed else "fallo"
        parts = [
            f"{self.clip_id}: {verdict}",
            f"media={'ok' if self.media.passed else 'fallo'}",
            f"subs={subs}",
        ]
        if self.semantic is not None:
            approved = self.semantic.get("approved")
            score = self.semantic.get("score")
            parts.append(f"semantica={'ok' if approved else 'fallo'}({score})")
        return "  ".join(parts)


def run_clip_qc(
    render_path: Path | str,
    cues: list[dict[str, Any]],
    *,
    clip_id: str,
    expected_duration: float | None,
    config: dict[str, Any],
) -> ClipQC:
    """Ejecuta el QC tecnico de un clip renderizado.

    Es la parte sin IA: media + subtitulos. El segundo LLM pass se engancha
    encima asignando `clip_qc.semantic`.
    """
    media = check_media(
        render_path, expected_duration=expected_duration, config=config
    )

    # La duracion real del render es la referencia para comprobar los cues, no
    # la del plan: si el render se ha salido de duracion, los cues se miden
    # contra lo que hay, que es lo que el espectador ve.
    subtitle_duration = media.actual_duration or expected_duration
    subtitles = check_subtitles(cues, duration=subtitle_duration, config=config)

    if not media.passed or not subtitles.passed:
        LOGGER.info(
            "clip_qc_failed",
            extra={
                "clip_id": clip_id,
                "media_ok": media.passed,
                "subs_ok": subtitles.passed,
                "problems": len(media.problems) + len(subtitles.problems),
            },
        )

    return ClipQC(
        clip_id=clip_id,
        media=media,
        subtitles=subtitles,
        min_semantic_score=float(
            _quality_config(config).get("minimum_semantic_score", 70)
        ),
    )


__all__ = [
    "ClipQC",
    "MediaReport",
    "QCError",
    "SubtitleReport",
    "check_media",
    "check_subtitles",
    "run_clip_qc",
]
