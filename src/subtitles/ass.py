"""Escritura de subtitulos ASS (blueprint seccion 31).

El camino rapido del render (`subtitles.renderer: ffmpeg_libass`) quema los
subtitulos con libass dentro de FFmpeg, y libass lee ASS. Este modulo convierte
los cues de `cues.py` a un fichero ASS valido.

El estilo vive en configuracion, no en el codigo (seccion 31): aqui solo se
mapean tres decisiones que tienen que ser consistentes con el compositor de
Remotion:

- La posicion del cartel (lower_center, center, upper_center, lower_third,
  burned_in) se traduce al Alignment de ASS.
- El tamano de letra se escala con el ancho de salida (PlayResX), no es un
  numero magico.
- Los textos con una linea extra (separada por "\n") se convierten en el
  codigo de salto de linea de ASS (una barra invertida y una N).

Los timestamps de ASS son relativos al inicio del clip, igual que los cues.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.subtitles.cues import LINE_BREAK, SubtitleCue

# Position -> Alignment de ASS (1=solo abajo izquierda, 2=centro abajo, 5=centro).
_ALIGNMENT = {
    "lower_center": 2,
    "center": 5,
    "upper_center": 8,
    "lower_third": 2,
    "burned_in": 2,
}
_DEFAULT_ALIGNMENT = 2

# Margen vertical de seguridad del cartel, en px del PlayResY (zona segura para
# moviles, seccion 31). Los margenes laterales se dejan a 0 porque el texto en
# ASS se centra por el Alignment, no por los margenes.
_MARGIN_V = 220


def _position_alignment(position: str | None) -> int:
    return _ALIGNMENT.get(str(position or "lower_center"), _DEFAULT_ALIGNMENT)


def _escape(text: str) -> str:
    """Escapa caracteres de control de ASS dentro del campo de texto."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _ass_ts(seconds: float) -> str:
    """`0:00:01.25` -> `0:00:01.25` con centesimas, como espera ASS."""
    tenths = int(round(max(seconds, 0.0) * 100))
    hours, rest = divmod(tenths, 360000)
    minutes, rest = divmod(rest, 6000)
    whole, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def _event(cue: SubtitleCue, *, alignment: int) -> str:
    body = _escape(cue.text).replace(LINE_BREAK, r"\N")
    return (
        f"Dialogue: 0,{_ass_ts(cue.start)},{_ass_ts(cue.end)},"
        f"Default,,0,0,0,,{body}"
    )


def render_ass(
    cues: Sequence[SubtitleCue],
    *,
    width: int,
    height: int,
    position: str = "lower_center",
) -> str:
    """Texto ASS completo para los cues, en la resolucion de salida."""
    if not cues:
        return "[Script Info]\nScriptType: v4.00+\n"

    font_size = max(40, round(width * 0.05))
    alignment = _position_alignment(position)
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
            " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
            " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
            " Alignment, MarginL, MarginR, MarginV, Encoding",
            (
                "Style: Default,Arial,{font},&H00FFFFFF,&H000000FF,&H00000000,"
                "&H96000000,-1,0,0,0,100,100,0,0,1,3,1,{align},0,0,{margin},1"
            ).format(font=font_size, align=alignment, margin=_MARGIN_V),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    events = "\n".join(_event(cue, alignment=alignment) for cue in cues)
    return f"{header}\n{events}\n"


def write_ass(
    cues: Sequence[SubtitleCue],
    path: Path | str,
    *,
    width: int,
    height: int,
    position: str = "lower_center",
) -> Path:
    """Escribe el ASS en disco y devuelve la ruta."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_ass(cues, width=width, height=height, position=position), encoding="utf-8")
    return target


def ass_style(config: dict[str, Any]) -> dict[str, Any]:
    """Bloque de estilo que el render lee de la config; centraliza el mapeo."""
    return {
        "enabled": bool(config.get("enabled", True)),
        "position": str(config.get("position", "lower_center")),
    }