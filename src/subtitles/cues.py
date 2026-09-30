"""Cues de subtitulo para un clip (blueprint seccion 31).

El render necesita saber QUE palabras del transcript caen dentro de la ventana
[start, end] del clip y como agruparlas en pantalla. Este modulo hace esa
operacion pura: toma el transcript inmutable (timestamps absolutos por palabra,
seccion 14) y devuelve los cues ya recortados a la ventana y con tiempos
relativos al inicio del clip.

Reglas de la seccion 31 aplicadas aqui:

- Maximo `max_chars_per_line` caracteres y `max_words_per_line` palabras por
  linea; la palabra que no cabe se baja a la siguiente linea.
- El cartel tiene `max_lines` lineas como maximo; nunca se llena la pantalla.
- Cada cue dura entre `min_duration_ms` y `max_duration_ms`. Un cue demasiado
  corto intenta absorber la siguiente; uno demasiado largo se parte al cerrar
  el grupo antes de la palabra que lo excederia.

Todos los limites vienen de la config (subtitles.*) o del edit_plan; aqui solo
entran como parametros para que la funcion sea pura y testeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Lineas separadas con "\n"; la capa ASS las convierte en \N.
LINE_BREAK = "\n"
_EPSILON = 1e-6


@dataclass(frozen=True)
class SubtitleCue:
    """Un cartel: texto y ventana de tiempo, en segundos relativos al clip."""

    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, float | str]:
        return {"start": self.start, "end": self.end, "text": self.text}


def _word_window(word: dict[str, Any], clip_start: float, clip_end: float) -> dict[str, Any] | None:
    """Recorta una palabra absoluta a la ventana del clip.

    Devuelve la palabra con tiempos RELATIVOS al clip, o None si no toca la
    ventana. Una palabra a caballo del borde se recorta, no se descarta: la
    seccion 33 exige sincronizacion y un "que" que empieza 10 ms antes del
    inicio del clip se tiene que ver.
    """
    w_start = float(word.get("start", 0.0))
    w_end = float(word.get("end", w_start))
    if w_end <= clip_start + _EPSILON or w_start >= clip_end - _EPSILON:
        return None
    text = str(word.get("word", "")).strip()
    if not text:
        return None
    return {
        "text": text,
        "start": round(max(w_start, clip_start) - clip_start, 3),
        "end": round(min(w_end, clip_end) - clip_start, 3),
    }


def _collect_words(
    transcript: dict[str, Any], clip_start: float, clip_end: float
) -> list[dict[str, Any]]:
    """Palabras del transcript que tocan la ventana, en orden cronologico.

    Fast path: si el segmento termina antes de la ventana o empieza despues se
    salta entero, que es lo que hace que el sistema aguante videos de horas sin
    construir listas enormes para cada clip.
    """
    words: list[dict[str, Any]] = []
    for segment in transcript.get("segments", []) or []:
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", seg_start))
        if seg_end <= clip_start or seg_start >= clip_end:
            continue
        for word in segment.get("words", []) or []:
            clipped = _word_window(word, clip_start, clip_end)
            if clipped is not None:
                words.append(clipped)
    return words


def _wrap(
    group: list[dict[str, Any]],
    *,
    max_words_per_line: int,
    max_chars_per_line: int,
) -> list[list[dict[str, Any]]]:
    """Divide un grupo de palabras en lineas que respetan los maximos."""
    if not group:
        return []
    lines: list[list[dict[str, Any]]] = [[]]
    for word in group:
        line = lines[-1]
        chars = sum(len(w["text"]) for w in line) + len(line)
        word_fits = len(line) < max_words_per_line and chars + len(word["text"]) <= max_chars_per_line
        if line and not word_fits:
            lines.append([word])
        else:
            line.append(word)
    return lines


def _pack_groups(
    words: list[dict[str, Any]],
    *,
    max_words_per_line: int,
    max_chars_per_line: int,
    max_lines: int,
    max_duration_s: float,
) -> list[list[dict[str, Any]]]:
    """Agrupa palabras en cues, cerrando el grupo cuando ya no caben."""

    def lines_of(candidate: list[dict[str, Any]]) -> int:
        return len(_wrap(candidate, max_words_per_line=max_words_per_line, max_chars_per_line=max_chars_per_line))

    groups: list[list[dict[str, Any]]] = []
    for word in words:
        if not groups:
            groups.append([word])
            continue
        group = groups[-1]
        start = group[0]["start"]
        if word["end"] - start > max_duration_s + _EPSILON:
            groups.append([word])
            continue
        if lines_of(group + [word]) > max_lines:
            groups.append([word])
            continue
        group.append(word)
    return groups


def _merge_short_cues(
    groups: list[list[dict[str, Any]]],
    *,
    min_duration_s: float,
    max_duration_s: float,
) -> list[list[dict[str, Any]]]:
    """Une cues que quedan por debajo de la duracion minima con la siguiente.

    Seccion 31: un cartel de 200 ms parpadea y no se lee. Si el vecino existe y
    la union no supera `max_duration_ms`, se fusionan; si no, se deja como esta
    (forzarlo romperia la sincronizacion de seccion 33).
    """
    merged: list[list[dict[str, Any]]] = []
    for group in groups:
        duration = group[-1]["end"] - group[0]["start"]
        if (
            merged
            and duration < min_duration_s - _EPSILON
            and group[-1]["end"] - merged[-1][0]["start"] <= max_duration_s + _EPSILON
        ):
            merged[-1].extend(group)
        else:
            merged.append(group)
    return merged


def _cue_text(group: list[dict[str, Any]], *, max_words_per_line: int, max_chars_per_line: int) -> str:
    lines = _wrap(group, max_words_per_line=max_words_per_line, max_chars_per_line=max_chars_per_line)
    return LINE_BREAK.join(" ".join(w["text"] for w in line) for line in lines)


def build_cues(
    transcript: dict[str, Any],
    clip_start: float,
    clip_end: float,
    *,
    max_words_per_line: int = 4,
    max_lines: int = 2,
    max_chars_per_line: int = 22,
    min_duration_ms: int = 800,
    max_duration_ms: int = 4000,
) -> list[SubtitleCue]:
    """Cues de subtitulo para la ventana [clip_start, clip_end).

    La ventana se interpreta en segundos absolutos (como vienen los timestamps
    del transcript); los cues devueltos son relativos al inicio del clip, que es
    como los espera el compositor.
    """
    if max_words_per_line < 1 or max_lines < 1 or max_chars_per_line < 1:
        raise ValueError("los limites de subtitulos tienen que ser >= 1")
    if clip_end <= clip_start:
        return []

    words = _collect_words(transcript, clip_start, clip_end)
    if not words:
        return []

    min_duration_s = min_duration_ms / 1000.0
    max_duration_s = max_duration_ms / 1000.0

    groups = _pack_groups(
        words,
        max_words_per_line=max_words_per_line,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        max_duration_s=max_duration_s,
    )
    groups = _merge_short_cues(groups, min_duration_s=min_duration_s, max_duration_s=max_duration_s)

    duration = round(clip_end - clip_start, 3)
    cues: list[SubtitleCue] = []
    for group in groups:
        text = _cue_text(group, max_words_per_line=max_words_per_line, max_chars_per_line=max_chars_per_line)
        if not text:
            continue
        start = max(0.0, group[0]["start"])
        end = min(duration, group[-1]["end"])
        if end <= start:
            continue
        cues.append(SubtitleCue(start=start, end=end, text=text))
    return cues