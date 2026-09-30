"""Normalizacion del transcript (blueprint seccion 15) y ventanas de contexto (16).

Produce `normalized_transcript.json`, una SEGUNDA representacion derivada de
`transcript.json`. Regla que manda sobre todas las demas: el original no se
modifica nunca. Si el transcript esta mal, se regenera; si el normalizado esta
mal, se regenera sin volver a transcribir 40 minutos de audio.

Lo que hace, en el orden que pide la seccion 15:

* limpieza de espacios
* union logica de frases
* deteccion de pausas
* parrafos
* ventanas de contexto
* timestamps originales

Dos decisiones que merecen justificacion:

**1. Por que NO se usa un segmentador externo.** spaCy o nltk darian mejores
puntos de frase, pero anaden una dependencia pesada, un modelo que descargar y una
fuente de fallo mas (seccion 73: "unicamente dependencias necesarias"). Con los
timestamps por palabra de Whisper, un segmentador de frases no aporta lo
suficiente. La heuristica de puntuacion es transparente, no tiene modelo que
reentrenar y su fallo es visible.

**2. Por que los parrafos cortan por tiempo y no por significado.** La unidad
semantica la dara Gemma en la seccion 17 (cambios de tema). Aqui el corte es por
duracion y por pausa, que es lo unico que se puede decidir sin modelo. Se deja
constante para que el content map pueda reagrupar despues sin romper los
timestamps.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts, logs, schemas

LOGGER = logs.get_logger("normalization")

SCHEMA_VERSION = "1.0"

# Un parrafo se cierra al superar esta duracion...
PARAGRAPH_TARGET_SECONDS = 90.0
# ...o antes, si hay una pausa larga. Cerrar en la pausa deja el corte en un
# silencio, que es justo donde un editor cortaria.
PARAGRAPH_SPLIT_ON_PAUSE_SECONDS = 1.2
PARAGRAPH_MIN_SECONDS = 8.0

# Pausa a partir de la cual se considera que el hablante se para. 500 ms es
# una respiracion, 1.2 s ya es un cambio de idea.
PAUSE_MIN_SECONDS = 0.8

# Ventanas de contexto (seccion 16).
LEVEL_1_MIN_SECONDS = 30
LEVEL_1_MAX_SECONDS = 120
LEVEL_2_MIN_SECONDS = 300
LEVEL_2_MAX_SECONDS = 900

# Se solapan para que una idea que cae justo en la frontera no se parta en dos.
LEVEL_1_OVERLAP_SECONDS = 10.0
LEVEL_2_OVERLAP_SECONDS = 30.0

# Puntuacion que cierra frase. Se USA LA PUNTUACION ORIGINAL de Whisper, que ya
# es correcta en el 95% de los casos; no se "corrige" texto durante la
# normalizacion, porque el normalizado es derivado y se puede regenerar.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*\s*$")
_WHITESPACE = re.compile(r"[ \t ]+")
_NEWLINES = re.compile(r"\s*\n\s*")


class NormalizationError(RuntimeError):
    """El transcript no se puede normalizar."""


# --- Limpieza (seccion 15, "limpieza de espacios") ---------------------------

def clean_text(value: str) -> str:
    """Colapsa espacios y normaliza a NFC.

    Whisper devuelve texto con espacios dobles, espacios al final del segmento y
    a veces NBSP (\xa0) o espacios finos (\u2009) cuando el hablante arrastra las
    palabras. Todo eso es formato, no contenido, y se normaliza en el derivado.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " ")
    text = _NEWLINES.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def _round(value: float) -> float:
    return round(max(float(value), 0.0), 3)


# --- Union logica de frases (seccion 15) -------------------------------------

def _distribute_ids(segment_ids: tuple[int, ...], weights: list[int]) -> list[list[int]]:
    """Reparte los segmentos de un parrafo entre sus frases, por longitud.

    Sin esto, cada frase del parrafo lleva la lista completa de segmentos y la
    trazabilidad "de donde salio esta frase" no dice nada. El reparto es
    proporcional al numero de caracteres: los segmentos de Whisper cubren
    Estonia prosa continua, asi que la longitud es una aproximacion razonable
    de donde cae la frontera de cada frase.
    """
    if not segment_ids:
        return [[] for _ in weights]

    total = sum(weights) or 1
    distributed: list[list[int]] = []
    cursor = 0
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            cut = len(segment_ids)
        else:
            cut = cursor + max(1, round(len(segment_ids) * weight / total))
            cut = min(cut, len(segment_ids))
            # Si dos frases cortas se comen el mismo segmento, se cede uno.
            if cut <= cursor and cursor < len(segment_ids):
                cut = cursor + 1
        distributed.append(list(segment_ids[cursor:cut]))
        cursor = cut
    return distributed


def split_sentences(text: str, start: float, end: float, segment_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    """Parte un parrafo en frases, repartiendo el tiempo de forma proporcional.

    El tiempo se reparte por longitud porque solo se dispone del intervalo del
    parrafo completo. Es una aproximacion: una frase larga con una pausa dentro
    recibe un poco de tiempo de la pausa. Se prefiere a marcar todas las frases
    con el mismo intervalo, que daria ventanas solapadas e imposibles de recortar.
    """
    text = clean_text(text)
    if not text:
        return []

    parts = [part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]
    if not parts:
        return []

    lengths = [len(part) for part in parts]
    total_chars = sum(lengths) or 1
    span = max(end - start, 0.0)
    ids_per_sentence = _distribute_ids(segment_ids, lengths)

    sentences: list[dict[str, Any]] = []
    cursor = start
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            sentence_end = end
        else:
            sentence_end = cursor + span * (lengths[index] / total_chars)
        sentences.append(
            {
                "text": part,
                "start": _round(cursor),
                "end": _round(max(sentence_end, cursor)),
                "segment_ids": ids_per_sentence[index],
            }
        )
        cursor = sentence_end
    return sentences


# Corte de frase conservando el signo, para poder recomponer el texto exacto.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])(?=\s)")


# --- Pausas (seccion 15, "deteccion de pausas") ------------------------------

@dataclass(frozen=True)
class Pause:
    start: float
    end: float
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_pauses(segments: list[dict[str, Any]], *, min_seconds: float = PAUSE_MIN_SECONDS) -> list[Pause]:
    """Silencios entre el fin de un segmento y el inicio del siguiente.

    Se ignoran los solapes: Whisper a veces solapa segmentos unos centesimas y
    un "pause" de -0.03 s no es una pausa.
    """
    pauses: list[Pause] = []
    for previous, current in zip(segments, segments[1:]):
        gap = float(current["start"]) - float(previous["end"])
        if gap >= min_seconds:
            pauses.append(
                Pause(start=_round(previous["end"]), end=_round(current["start"]), duration=_round(gap))
            )
    return pauses


# --- Parrafos (seccion 15) ---------------------------------------------------

def build_paragraphs(
    segments: list[dict[str, Any]],
    *,
    target_seconds: float = PARAGRAPH_TARGET_SECONDS,
    split_on_pause_seconds: float = PARAGRAPH_SPLIT_ON_PAUSE_SECONDS,
    min_seconds: float = PARAGRAPH_MIN_SECONDS,
) -> list[dict[str, Any]]:
    """Agrupa segmentos en parrafos de lectura.

    Regla: se cierra el parrafo al superar `target_seconds`, o antes si hay una
    pausa de al menos `split_on_pause_seconds`. Un parrafo muy corto se fusiona
    con el siguiente, porque un parrafo de 3 s no aporta nada al modelo.
    """
    if not segments:
        return []

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for index, segment in enumerate(segments):
        current.append(segment)
        span = float(segment["end"]) - float(current[0]["start"])
        reached_target = span >= target_seconds

        following = segments[index + 1] if index + 1 < len(segments) else None
        gap = float(following["start"]) - float(segment["end"]) if following else None
        reached_pause = gap is not None and gap >= split_on_pause_seconds

        if (reached_target or reached_pause) and following is not None:
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    # Fusiona los grupos demasiado cortos con el siguiente.
    merged: list[list[dict[str, Any]]] = []
    for group in groups:
        span = float(group[-1]["end"]) - float(group[0]["start"])
        if merged and span < min_seconds:
            merged[-1].extend(group)
        else:
            merged.append(group)

    paragraphs: list[dict[str, Any]] = []
    for identifier, group in enumerate(merged):
        text = clean_text(" ".join(str(segment.get("text", "")) for segment in group))
        if not text:
            continue
        start = _round(group[0]["start"])
        end = _round(group[-1]["end"])
        segment_ids = tuple(
            sorted({int(segment["id"]) for segment in group if isinstance(segment.get("id"), int)})
        )
        paragraphs.append(
            {
                "id": len(paragraphs),
                "text": text,
                "start": start,
                "end": end,
                "sentences": split_sentences(text, start, end, segment_ids),
                "source": {"start": start, "end": end, "segment_ids": list(segment_ids)},
            }
        )
    return paragraphs


# --- Ventanas de contexto (seccion 16) --------------------------------------

@dataclass(frozen=True)
class _WindowSpec:
    identifier: str
    level: int
    start: float
    end: float
    parent_id: str | None
    child_ids: list[str] = field(default_factory=list)


def _chunk(duration: float, minimum: float, maximum: float, overlap: float) -> list[tuple[float, float]]:
    """Parte [0, duration] en ventanas de entre `minimum` y `maximum` segundos.

    Si el video entero cabe en una ventana, se devuelve una sola. Si no cabe ni
    en `maximum`, se devuelve tambien una sola: partirla en trozos de
    `minimum` produciria miles de ventanas para un video largo, y lo que
    interesa es que la ventana que se mande a Gemma quepa en su contexto.
    """
    if duration <= maximum:
        return [(0.0, _round(duration))]

    step = max(maximum - overlap, minimum)
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + maximum, duration)
        windows.append((_round(start), _round(end)))
        if end >= duration:
            break
        start += step

    # La cola del video rara vez mide un multiplo exacto de `step`, y la ultima
    # ventana acaba dando menos de `minimum` (18 s y pico en un video de 20 min
    # con minimo de 30). Emitirla tal cual viola la seccion 16 (nivel 1: 30-120 s)
    # y rechaza el artefacto entero. Se re-ancla hacia atras esa ventana hasta
    # cubrir como minimo `minimum` segundos, manteniendo el solape con la previa.
    if len(windows) > 1:
        last_start, last_end = windows[-1]
        if last_end - last_start < minimum:
            anchor = max(0.0, last_end - minimum)
            if anchor > windows[-2][0]:
                windows[-1] = (_round(anchor), _round(last_end))
    return windows


def _text_between(paragraphs: list[dict[str, Any]], start: float, end: float) -> str:
    """Texto de los parrafos que tocan la ventana, recortado al rango.

    Se incluyen los parrafos que SOLAPAN la ventana, no solo los contenidos: si
    una idea empieza antes del corte y sigue dentro, mandarla a medias
    hace que Gemma la rechace.
    """
    pieces: list[str] = []
    for paragraph in paragraphs:
        if float(paragraph["end"]) <= start:
            continue
        if float(paragraph["start"]) >= end:
            break
        pieces.append(str(paragraph["text"]))
    return clean_text(" ".join(pieces))


def build_context_windows(paragraphs: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Niveles 1, 2 y 3 de la seccion 16, con la jerarquia montada.

    Nivel 3 (el video completo) es siempre el resumen global, y es padre de las
    ventanas de nivel 2. Si no hay nada de nivel 2, es padre de las de nivel 1.
    """
    if not paragraphs:
        return []

    specs: list[_WindowSpec] = []

    for index, (start, end) in enumerate(
        _chunk(duration, LEVEL_1_MIN_SECONDS, LEVEL_1_MAX_SECONDS, LEVEL_1_OVERLAP_SECONDS)
    ):
        specs.append(_WindowSpec(identifier=f"l1_{index:03d}", level=1, start=start, end=end, parent_id=None))

    for index, (start, end) in enumerate(
        _chunk(duration, LEVEL_2_MIN_SECONDS, LEVEL_2_MAX_SECONDS, LEVEL_2_OVERLAP_SECONDS)
    ):
        specs.append(_WindowSpec(identifier=f"l2_{index:03d}", level=2, start=start, end=end, parent_id=None))

    global_id = "l3_global"
    specs.append(
        _WindowSpec(identifier=global_id, level=3, start=0.0, end=_round(duration), parent_id=None)
    )

    def at_level(level: int) -> list[_WindowSpec]:
        return sorted(
            (spec for spec in specs if spec.level == level),
            key=lambda spec: (spec.start, spec.end),
        )

    # El padre es la ventana del nivel superior que CONTIENE entera a la hija.
    # La relacion se guarda en diccionarios en vez de mutar los dataclasses,
    # que son frozen a proposito: una ventana no cambia una vez construida.
    parent_of: dict[str, str | None] = {global_id: None}
    children_of: dict[str, list[str]] = {spec.identifier: [] for spec in specs}

    for level in (1, 2):
        for child in at_level(level):
            enclosing = [
                spec
                for spec in at_level(level + 1)
                if spec.start <= child.start and spec.end >= child.end
            ]
            if enclosing:
                parent_id = enclosing[0].identifier
            else:
                # Pasa con los solapes en los bordes del video: ninguna ventana
                # del nivel superior contiene a esta entera. Se cuelga del
                # resumen global, que siempre contiene todo.
                parent_id = global_id
            parent_of[child.identifier] = parent_id
            children_of[parent_id].append(child.identifier)

    return [
        {
            "id": spec.identifier,
            "level": spec.level,
            "start": spec.start,
            "end": spec.end,
            "text": _text_between(paragraphs, spec.start, spec.end),
            "parent_id": parent_of.get(spec.identifier),
            "child_ids": children_of.get(spec.identifier, []),
        }
        for spec in specs
    ]


# --- API principal -----------------------------------------------------------


@dataclass
class NormalizationResult:
    job_id: str | None
    language: str
    duration: float
    segment_count: int
    paragraph_count: int
    sentence_count: int
    pause_count: int
    window_count: int
    reused: bool
    path: str | None = None
    sha256: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"parrafos    {self.paragraph_count} ({self.sentence_count} frases)",
            f"pausas      {self.pause_count}",
            f"ventanas    {self.window_count} (jerarquicas, seccion 16)",
        ]
        if self.reused:
            lines.append("reutilizado normalized_transcript.json existente")
        for warning in self.warnings:
            lines.append(f"AVISO       {warning}")
        return "\n".join(lines)


def normalize(
    transcript_path: Path | str,
    *,
    job_id: str | None = None,
    output_path: Path | str | None = None,
    overwrite: bool = False,
) -> NormalizationResult:
    """Construye `normalized_transcript.json` a partir de `transcript.json`.

    Es una operacion PURA: lee el original, escribe el derivado y no toca el
    original. Por eso es barato repetirla mientras no cambie el transcript.
    """
    source = Path(transcript_path)
    document = artifacts.read_json(source, schema_name="transcript")

    target = Path(output_path) if output_path else source.with_name("normalized_transcript.json")
    if target.is_file() and not overwrite:
        existing = artifacts.read_or_none(target, schema_name="normalized_transcript")
        if existing is not None:
            LOGGER.info("normalized_transcript ya existe, se reutiliza", extra={"path": str(target)})
            # Se pasa el SHA tambien al reutilizar: sin el, el upsert de
            # artifacts en la base BORRA el hash guardado la primera vez.
            return _result_from(
                existing, job_id, reused=True, path=str(target), sha256=artifacts.sha256_of_file(target)
            )

    segments = list(document.get("segments", []))
    duration = float(document.get("duration", 0.0))

    # Si Whisper no dio duracion (o dio 0), la duracion util es la del ultimo
    # segmento. Sin esto, un video largo se convertiria en ventanas de 0 s.
    if duration <= 0 and segments:
        duration = _round(max(float(segment["end"]) for segment in segments))

    paragraphs = build_paragraphs(segments)
    pauses = detect_pauses(segments)
    windows = build_context_windows(paragraphs, duration)
    full_text = clean_text(" ".join(str(segment.get("text", "")) for segment in segments))

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id if job_id is not None else document.get("job_id"),
        "derived_from": {
            "file": source.name,
            "sha256": artifacts.sha256_of_file(source),
            "schema_version": str(document.get("schema_version", SCHEMA_VERSION)),
        },
        "language": str(document.get("language", "unknown")),
        "duration": _round(duration),
        "paragraphs": paragraphs,
        "pauses": [pause.to_dict() for pause in pauses],
        "context_windows": windows,
        "full_text": full_text,
    }

    warnings = _warn_about(result, segments)
    written = artifacts.write_json(
        target, result, schema_name="normalized_transcript", overwrite=True
    )

    LOGGER.info(
        "normalizacion completada",
        extra={
            "paragraphs": len(paragraphs),
            "pauses": len(pauses),
            "windows": len(windows),
            "path": str(target),
        },
    )

    return _result_from(result, job_id, reused=False, path=str(written.path), sha256=written.sha256, warnings=warnings)


def _result_from(
    document: dict[str, Any],
    job_id: str | None,
    *,
    reused: bool,
    path: str | None = None,
    sha256: str | None = None,
    warnings: list[str] | None = None,
) -> NormalizationResult:
    paragraphs = document.get("paragraphs", [])
    return NormalizationResult(
        job_id=document.get("job_id", job_id),
        language=str(document.get("language", "unknown")),
        duration=float(document.get("duration", 0.0)),
        segment_count=sum(len(paragraph.get("source", {}).get("segment_ids", [])) for paragraph in paragraphs),
        paragraph_count=len(paragraphs),
        sentence_count=sum(len(paragraph.get("sentences", [])) for paragraph in paragraphs),
        pause_count=len(document.get("pauses", [])),
        window_count=len(document.get("context_windows", [])),
        reused=reused,
        path=path,
        sha256=sha256,
        warnings=warnings or [],
    )


def _warn_about(document: dict[str, Any], segments: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not segments:
        warnings.append(
            "el transcript no tiene segmentos; normalized_transcript.json queda vacio. "
            "Regenera el transcript si el video si tiene voz (seccion 14: es inmutable)."
        )
        return warnings

    duration = document["duration"]
    windows = document.get("context_windows", [])
    level_3 = [window for window in windows if window["level"] == 3]
    if level_3 and not level_3[0]["text"]:
        warnings.append(
            "la ventana global (nivel 3) salio vacia. No habra resumen global que mandar "
            "a Gemma (seccion 16)."
        )
    if duration > LEVEL_2_MAX_SECONDS:
        warnings.append(
            f"el video dura {duration / 60:.1f} min y se ha dividido en "
            f"{len([w for w in windows if w['level'] == 2])} bloques de nivel 2. "
            "La etapa de analisis trabajara bloque a bloque, no con el texto entero."
        )
    return warnings


def load_normalized(path: Path | str) -> dict[str, Any]:
    return artifacts.read_json(path, schema_name="normalized_transcript")


def is_valid(path: Path | str) -> bool:
    target = Path(path)
    if not target.is_file():
        return False
    try:
        return schemas.is_valid("normalized_transcript", artifacts.read_json(target))
    except artifacts.ArtifactError:
        return False
