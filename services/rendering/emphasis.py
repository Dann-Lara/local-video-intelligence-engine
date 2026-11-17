"""Rotulos de enfasis en ASS (mismo estilo del MVP aprobado).

Cada evento debe ser una frase LITERAL de la transcripcion del clip. La
posicion dentro del segmento es proporcional (no hay word timestamps). El
codigo valida literalidad, rango y ausencia de colision con tarjetas antes de
escribir el ASS.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

WIDTH = 1080
HEIGHT = 1920
POS_X = 540
POS_Y = 1010

_HEADER = (
    "[Script Info]\n"
    "; Rotulos de enfasis derivados de la transcripcion del clip.\n"
    "; Textos literales; sin word_timestamps, la posicion es proporcional.\n"
    "ScriptType: v4.00+\n"
    "WrapStyle: 2\n"
    "ScaledBorderAndShadow: yes\n"
    "YCbCr Matrix: TV.709\n"
    f"PlayResX: {WIDTH}\n"
    f"PlayResY: {HEIGHT}\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: MotionTypo,Arial Black,84,&H00FFFFFF,&H00FFFFFF,&H00101010,"
    "&H96000000,-1,0,0,0,100,100,1,0,1,6,4,5,70,70,80,1\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
)


def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def validate_emphasis(
    events: Sequence[Dict[str, Any]],
    segments_by_id: Dict[str, Dict[str, Any]],
    *,
    duration: float,
    cards: Iterable[Dict[str, Any]] = (),
) -> List[str]:
    errors: List[str] = []
    card_windows = [
        (float(c["enter_start_seconds"]), float(c["exit_end_seconds"]))
        for c in cards
    ]
    for event in events:
        text = str(event.get("text", "")).strip()
        start = float(event.get("start", -1))
        end = float(event.get("end", -1))
        refs = list(event.get("segment_ids") or [])
        if not text:
            errors.append("Rotulo sin texto.")
            continue
        if not (0.0 <= start < end <= duration):
            errors.append(f"Rotulo fuera de rango: {text!r} ({start}-{end}).")
        for c0, c1 in card_windows:
            if start < c1 and end > c0:
                errors.append(f"Rotulo {text!r} solapa una tarjeta ({c0}-{c1}).")
        if not refs:
            errors.append(f"Rotulo sin segmento de evidencia: {text!r}.")
            continue
        joined = " ".join(
            _norm(segments_by_id.get(ref, {}).get("text", "")) for ref in refs
        )
        words = [w for w in _norm(text).split() if w]
        if not words or not all(w in joined for w in words):
            errors.append(f"Rotulo no literal en {refs}: {text!r}.")
    return errors


def build_emphasis_ass(events: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for event in events:
        lines.append(
            f"Dialogue: 1,{ass_time(float(event['start']))},"
            f"{ass_time(float(event['end']))},MotionTypo,,0,0,0,,"
            "{\\an5\\pos(540,1010)\\fscx45\\fscy45\\fad(120,140)"
            "\\t(0,360,0.6\\fscx100\\fscy100)\\t(360,1450,0.55\\fscx103\\fscy103)}"
            f"{event['text']}\n"
        )
    return _HEADER + "".join(lines)


__all__ = ["ass_time", "build_emphasis_ass", "validate_emphasis"]
