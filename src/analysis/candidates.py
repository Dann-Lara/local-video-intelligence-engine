"""Candidate Generator (blueprint secciones 18-24).

Es la etapa GENERATING_CANDIDATES: del `content_map.json` mas el
`normalized_transcript.json` al `clip_candidates.json` con todos los
candidatos a clip descubiertos, completos y puntuados.

Reglas que mandan (y de donde salen):

1. Se trabaja BLOQUE A BLOQUE de nivel 2 (seccion 16), nunca con el texto
   entero. Cada bloque es una llamada a Gemma, y el resultado de cada bloque
   se cachea en `candidates/<bloque>.json` para que un fallo a mitad no
   vuelva a pagar los bloques ya hechos (seccion 51).
2. El prompt pide "Todos los segmentos que funcionen como clips
   independientes" (seccion 18), nunca "dame N clips". El numero de salida es
   el que sea, dentro de los limites; la poda la hace RANKING, no la IA.
3. El envoltorio lo rellena el SOFTWARE (seccion 2.3): `candidate_id`,
   `job_id`, `source.file/sha256` y `generated_by` no salen del modelo.
4. El score se normaliza y se cierra en software (seccion 20): Gemma 3 4B
   Q4_K_M devuelve dimensiones a 0-1, o supera el tope, o el total no cuadra.
   Valores en (0, 1] se tratan como fraccion del tope de la dimension; el
   resto se clamp a [0, tope]; el `total` se recalcula como la suma. Nunca se
   acepta el total del modelo.
5. La integridad (secciones 25/26) se verifica contra el transcript entero:
   un hook con `origin="original"` debe estar pronunciado; si no, se degrada
   a GENERATED_HOOK con `requires_generated_hook=true`.
6. Context expansion (seccion 24): si el candidato corta a media frase
   (`progression.ends_complete=false`), se extiende hasta el fin de un
   parrafo, dentro de [minimum, maximum]. Si tras expandir sigue cortando a
   media frase, el candidato se descarta: una invariante lo rechazaria igual.
7. Idempotencia (seccion 51): si `clip_candidates.json` ya existe y es valido
   y `overwrite=False`, se reutiliza sin llamar al modelo.
8. El contenido del video es DATA, no instrucciones (seccion 61).

La telemetria de `model_runs` (seccion 44) se registra solo si se pasa `repo`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.core import artifacts, config as config_module, integrity, logs, prompts
from src.core.invariants import SCORE_DIMENSION_MAX
from src.llm.lmstudio import (
    LLMFatalError,
    LLMRecoverableError,
    LMStudioClient,
    client_from_config,
)
from src.analysis import ranking

LOGGER = logs.get_logger("candidates")

SCHEMA_VERSION = "1.0"
STAGE = "candidate_detection"
PROMPT_VERSION = prompts.version(STAGE)

# Presupuesto de caracteres del transcript del bloque. Un bloque de nivel 2
# puede llegar a 15 min; por encima del presupuesto se recorta la parte central
# con AVISO, igual que en el content map.
BLOCK_MAX_CHARS = 36_000
# Presupuesto para los items del content map que caen dentro del bloque.
CONTENT_MAP_MAX_CHARS = 12_000

# Codigos HTTP con los que llama.cpp dice "este schema no se puede volver
# gramatica". No son fallos del servidor: hay que degradar a JSON-por-prompt.
_UNSUPPORTED_SCHEMA_STATUS = {400, 422}

_VALUE_PROVIDED = (
    "information",
    "emotion",
    "surprise",
    "entertainment",
    "conflict",
    "curiosity",
    "utility",
)


class CandidateError(RuntimeError):
    """Fallo de la etapa GENERATING_CANDIDATES."""

    code = "candidate_generation_failed"


class CandidateRecoverableError(CandidateError):
    """Reintentable: LM Studio caido, JSON invalido tras reintentos (seccion 58)."""

    code = "candidate_generation_recoverable"


class CandidateFatalError(CandidateError):
    """No reintentable: falta un artefacto previo o la configuracion es invalida."""

    code = "candidate_generation_fatal"


class ModelOutputError(CandidateError):
    """La respuesta de Gemma no es un JSON utilizable (seccion 59)."""

    code = "candidate_invalid_json"


# --- prompt (secciones 18-23, 61) -------------------------------------------


def _format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def _truncate_middle(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.6)
    tail = limit - head
    marker = "\n[... contenido intermedio recortado por presupuesto ...]\n"
    return text[:head].rstrip() + marker + text[-tail:].lstrip(), True


def _block_transcript(paragraphs: list[dict[str, Any]], start: float, end: float) -> str:
    """Parrafos que tocan el bloque, con sus marcas de tiempo.

    Igual que el content map: Gemma ve los tiempos junto al texto para poder
    rellenar `source.start/end` con rangos reales (seccion 62: no inventar).
    """
    lines: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        if float(paragraph.get("end", 0.0)) <= start:
            continue
        if float(paragraph.get("start", end)) >= end:
            continue
        text = paragraph.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        lines.append(f"[{_format_timestamp(paragraph['start'])}-{_format_timestamp(paragraph['end'])}] {text.strip()}")
    return "\n".join(lines)


def _map_items_for_block(map_doc: dict[str, Any], start: float, end: float) -> str:
    """Items del content map que caen dentro del bloque, como lista.-DATA.

    Es el contexto enriquecido que Gemma ya analizo antes: temas, entidades,
    claims, momentos y hooks del tramo. Se evita que el modelo re-analice lo
    que ya decidio, y se le da material para las firmas de diversidad.
    """
    lines: list[str] = []
    global_summary = map_doc.get("global_summary")
    if isinstance(global_summary, str) and global_summary.strip():
        lines.append(f"- resumen global: {global_summary.strip()}")

    def overlaps(item: dict[str, Any]) -> bool:
        time_block = item.get("time")
        if not isinstance(time_block, dict):
            return False
        return float(time_block.get("end", 0.0)) > start and float(time_block.get("start", end)) < end

    for key in ("topics", "entities", "claims"):
        for item in map_doc.get(key, []):
            if not isinstance(item, dict) or not overlaps(item):
                continue
            time_block = item.get("time") or {}
            stamp = (
                f"[{_format_timestamp(time_block.get('start', 0.0))}-"
                f"{_format_timestamp(time_block.get('end', 0.0))}]"
            )
            if key == "topics":
                lines.append(f"- topic: {item.get('label')} {stamp} (signature: {item.get('signature')})")
            elif key == "entities":
                lines.append(f"- entidad: {item.get('name')} ({item.get('type')}) {stamp}")
            else:
                verifiable = item.get("verifiable")
                lines.append(
                    f"- claim: {item.get('statement')} {stamp} (verificable={verifiable})"
                )

    for category in ("potential_hooks", *ranking_categories(map_doc)):
        for item in map_doc.get(category, []):
            if not isinstance(item, dict) or not overlaps(item):
                continue
            time_block = item.get("time") or {}
            stamp = (
                f"[{_format_timestamp(time_block.get('start', 0.0))}-"
                f"{_format_timestamp(time_block.get('end', 0.0))}]"
            )
            if category == "potential_hooks":
                lines.append(
                    f"- hook: {item.get('text')} (tipo={item.get('type')}, "
                    f"origin={item.get('origin')}) {stamp}"
                )
            else:
                summary = str(item.get("summary") or "")
                if summary:
                    lines.append(f"- momento {category}: {summary} {stamp}")
    return "\n".join(lines)


def ranking_categories(map_doc: dict[str, Any]) -> tuple[str, ...]:
    """Categorias de momento presentes en el mapa; se evita importar la lista
    completa solo para enumerarlas aqui."""
    return (
        "stories",
        "questions",
        "answers",
        "conflicts",
        "revelations",
        "surprising_points",
        "emotional_moments",
        "humorous_moments",
        "actionable_points",
        "strong_statements",
        "memorable_phrases",
        "conclusions",
        "calls_to_action",
        "topic_changes",
    )





def build_block_messages(
    block: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    map_doc: dict[str, Any],
    *,
    language: str,
    duration: float,
    blocks_min: float,
    blocks_max: float,
    preferred_min: float,
    preferred_max: float,
    max_chars: int = BLOCK_MAX_CHARS,
    map_max_chars: int = CONTENT_MAP_MAX_CHARS,
) -> tuple[list[dict[str, Any]], bool]:
    """Construye las llamadas al modelo para un bloque. Devuelve (messages, recortado)."""
    transcript, truncated = _truncate_middle(
        _block_transcript(paragraphs, float(block["start"]), float(block["end"])), max_chars
    )
    content_map_text, map_truncated = _truncate_middle(
        _map_items_for_block(map_doc, float(block["start"]), float(block["end"])), map_max_chars
    )
    system = prompts.load("system", stage=STAGE).format(
        min_duration=int(blocks_min),
        max_duration=int(blocks_max),
        preferred_min=int(preferred_min),
        preferred_max=int(preferred_max),
    )
    user = prompts.load("user", stage=STAGE).format(
        language=language,
        duration=f"{duration:.3f}",
        block_id=block["id"],
        block_start=_format_timestamp(block["start"]),
        block_end=_format_timestamp(block["end"]),
        transcript=transcript,
        content_map=content_map_text,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], truncated or map_truncated


# --- parseo (seccion 59) -----------------------------------------------------


def _salvage_candidates(cleaned: str) -> dict[str, Any] | None:
    """Recupera el JSON truncado a mitad de un candidato (max_tokens agotado).

    Gemma responde el array completo y, si se le acaba el presupuesto, corta a
    mitad del ultimo objeto (o peor: con un salto de linea crudo dentro de una
    cadena). En vez de tirar todo el bloque, se parsean de forma incremental los
    candidatos CERrados y se reconstruye `{"candidates": [...]}` con ellos. Si
    no sale nada util, se devuelve None y el llamador decide.
    """
    head = cleaned.find('"candidates"')
    if head == -1:
        return None
    start = cleaned.find("[", head)
    if start == -1:
        return None

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    position = start + 1
    while True:
        while position < len(cleaned) and cleaned[position] in " \t\r\n,[":
            position += 1
        if position >= len(cleaned):
            break
        try:
            candidate, position = decoder.raw_decode(cleaned, position)
        except json.JSONDecodeError:
            break
        if not isinstance(candidate, dict):
            break
        candidates.append(candidate)
    if not candidates:
        return None
    return {"candidates": candidates}


def _extract_json(content: str) -> dict[str, Any]:
    """Convierte la respuesta en un diccionario. Tolerante a fences markdown,
    estricto con el JSON roto: se repara recortando al primer { y ultimo } y, si
    aun asi falla (lista truncada a mitad de un candidato), se rescatan los
    candidatos completos con `_salvage_candidates`."""
    cleaned = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ModelOutputError(f"respuesta sin objeto JSON: {content[:200]!r}") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            salvaged = _salvage_candidates(cleaned[start : end + 1])
            if salvaged is not None:
                return salvaged
            raise ModelOutputError(
                f"JSON invalido aun tras recortar: {exc}\n{content[:300]!r}"
            ) from exc
    if not isinstance(parsed, dict):
        raise ModelOutputError(f"la respuesta no es un objeto: {parsed!r}")
    return parsed


# --- saneamiento en software (secciones 20, 23, 24, 26) ----------------------


def _clamp_time(start: Any, end: Any, duration: float, minimum_gap: float = 1e-6) -> tuple[float, float] | None:
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if duration > 0:
        start_f = max(0.0, min(start_f, duration))
        end_f = max(0.0, min(end_f, duration))
    start_f = max(0.0, start_f)
    end_f = max(0.0, end_f)
    if start_f >= end_f - minimum_gap:
        return None
    return round(start_f, 3), round(end_f, 3)


def _clamp_raw_scores_to_caps(body: dict[str, Any]) -> None:
    """Seccion 20: espejo de `_normalize_score` antes de validar el JSON del LLM.

    El schema `candidate_body` pide dimensiones dentro del tope; el modelo
    responde lo que quiere y el software cierra a la escala. Aqui se clampan las
    dimensiones al tope SIN reinterpretar (0, 1] -> fraccion: esa relectura la
    hace `_normalize_score` al procesar cada candidato. No se toca el `total`
    (el schema lo rechaza), y se conserva el resto de campos tal cual.
    """
    for raw in body.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        score = raw.get("score")
        if not isinstance(score, dict):
            continue
        for key, cap in SCORE_DIMENSION_MAX.items():
            value = score.get(key)
            if isinstance(value, (int, float)):
                score[key] = round(max(0.0, min(float(cap), float(value))), 2)


def _normalize_score(score: dict[str, Any], drops: list[str]) -> dict[str, Any]:
    """Seccion 20: el LLM propone, el software cierra la escala y el total.

    Dimensiones en (0, 1] se interpretan como fraccion del tope (un `hook: 0.8`
    quiere decir 0.8*20=16/20). Fuera de ahi se clamp a [0, tope]. El `total`
    NUNCA se acepta: se recalcula como la suma. Asi un modelo que mezcla
    escalas no pasa la invariante por casualidad.
    """
    normalized: dict[str, Any] = {}
    for key, cap in SCORE_DIMENSION_MAX.items():
        raw = score.get(key)
        if not isinstance(raw, (int, float)):
            drops.append(f"score.{key} no numerico, se fija a 0")
            normalized[key] = 0.0
            continue
        value = float(raw)
        if 0.0 < value <= 1.0:
            value = value * cap
        normalized[key] = round(max(0.0, min(cap, value)), 2)
    normalized["total"] = round(sum(normalized[key] for key in SCORE_DIMENSION_MAX), 2)
    confidence = score.get("confidence")
    if isinstance(confidence, (int, float)):
        normalized["confidence"] = round(max(0.0, min(1.0, float(confidence))), 3)
    return normalized


def _clamp_hook_to_source(hook: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """El tiempo del hook debe caer dentro del clip: el hook es lo PRIMERO que
    se ve/oye. Un hook fuera del rango apuntaria a un instante que no existe."""
    cleaned = dict(hook)
    time_block = hook.get("time")
    clamped = _clamp_time(
        (time_block or {}).get("start"), (time_block or {}).get("end"), end, minimum_gap=0.001
    )
    if clamped is None:
        clamped = (start, min(start + 0.05, end))
    time_block = dict(time_block or {})
    time_block["start"], time_block["end"] = clamped
    cleaned["time"] = time_block
    if cleaned.get("origin") == "generated" and not cleaned.get("requires_generated_hook"):
        cleaned["requires_generated_hook"] = True
    return cleaned


def _expand_to_sentence_end(
    paragraphs: list[dict[str, Any]],
    start: float,
    end: float,
    maximum: float,
) -> float:
    """Seccion 24: extiende el final hasta el cierre del parrafo actual o el
    siguiente, sin pasar de `maximum`. Si no hay parrafo al que anclar, se
    queda igual.

    No ancla nada en `drops`: `drops` es la lista de descartes y un anclaje
    EXITOSO no es un descarte. Se announce por `expansion.reason`, que es donde
    queda auditado.
    """
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        p_start, p_end = float(paragraph.get("start", 0.0)), float(paragraph.get("end", 0.0))
        if p_end <= end - 0.05:
            continue
        if p_start <= end + 0.05:
            extended = min(p_end, start + maximum)
            return round(extended, 3)
    return end


def _first_speech_time(paragraphs: list[dict[str, Any]], start: float, end: float) -> float | None:
    """Primer instante con voz dentro de la ventana, o None si esta muda.

    Se mira el parrafo mas temprano que SOLAPA la ventana, no el primero que
    empieza dentro de ella: un parrafo puede empezar antes del corte y seguir
    hablando cuando empieza el clip, y ese caso no es silencio.
    """
    earliest: float | None = None
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        p_start, p_end = float(paragraph.get("start", 0.0)), float(paragraph.get("end", 0.0))
        if p_end <= start + 0.05 or p_start >= end - 0.05:
            continue
        voice = max(p_start, start)
        if earliest is None or voice < earliest:
            earliest = voice
    return earliest


def _trim_leading_silence(
    paragraphs: list[dict[str, Any]],
    start: float,
    end: float,
    tolerance: float,
) -> float:
    """Seccion 24 (simetrica): recorta el silencio del PRINCIPIO del clip.

    Gemma propone los limites a partir de un bloque de texto, pero el bloque
    empieza donde empieza el parrafo, y el modelo se equivoca de ventana con
    facilidad. El fallo que se ha visto en video real: candidatos que.abrian con
    13,7 s de silencio, porque el corte se ancló al inicio de un parrafo que aun
    no habia hablado. Se renderiza, se pasa el QC tecnico (el MP4 es valido) y el
    QC semantico (el texto de dentro es bueno) y aun asi es un clip que arranca
    13 s en silencio.

    Por eso el corte se ancla a la primera voz, que es lo que el espectador
    percibe como "empieza". Simetrico a `_expand_to_sentence_end`, que hace lo
    mismo por el final. Si la ventana entera esta muda, se deja igual: eso ya lo
    van a descartar el QC y el ranking, y aqui solo se toca lo que se puede
    arreglar sin perder contenido. El recorte se contabiliza en
    `expansion.pre_context_seconds`.
    """
    voice = _first_speech_time(paragraphs, start, end)
    if voice is None:
        return start
    silence = voice - start
    if silence <= tolerance:
        return start
    return round(voice, 3)


# --- cuerpo -> candidato completo --------------------------------------------


def _process_candidate(
    body: dict[str, Any],
    *,
    index: int,
    block_id: str,
    duration: float,
    paragraphs: list[dict[str, Any]],
    full_text: str,
    video_file: str,
    video_sha256: str,
    job_id: str,
    model_run: dict[str, Any],
    blocks_min: float,
    blocks_max: float,
    leading_silence_tolerance: float = 1.5,
    drops: list[str],
) -> dict[str, Any] | None:
    """Convierte un candidato del cuerpo del modelo en un candidato completo.

    Devuelve None si el candidato no se puede salvar: el software descarta,
    no retoca silenciosamente. `drops` acumula los motivos para la telemetria.
    """
    if not isinstance(body, dict):
        drops.append(f"candidate[{index}]: no es un objeto")
        return None

    cand: dict[str, Any] = dict(body)

    # Rango del clip, clavado al video.
    source = cand.get("source")
    if not isinstance(source, dict):
        drops.append(f"candidate[{index}]: sin source")
        return None
    clamped = _clamp_time(source.get("start"), source.get("end"), duration)
    if clamped is None:
        drops.append(f"candidate[{index}]: source sin rango valido tras clamp")
        return None
    start, end = clamped
    proposed_start, proposed_end = start, end

    # Silencio inicial (seccion 24, simetrica del final). Va ANTES del check de
    # duracion a proposito: lo que se recorta no es contenido, es hueco, asi que
    # no debe contar ni para el minimo ni para el maximo.
    start = _trim_leading_silence(paragraphs, start, end, leading_silence_tolerance)
    pre_context = round(start - proposed_start, 3)
    if pre_context > 0:
        remaining = end - start
        if remaining < blocks_min:
            drops.append(
                f"candidate[{index}]: {pre_context:.1f}s de silencio inicial "
                f"y solo quedarian {remaining:.1f}s, por debajo del minimo "
                f"{blocks_min:.0f}s"
            )
            return None

    clipped_length = end - start
    if clipped_length < blocks_min or clipped_length > blocks_max:
        drops.append(
            f"candidate[{index}]: duracion {clipped_length:.1f}s fuera de "
            f"[{blocks_min:.0f}, {blocks_max:.0f}] (seccion 23)"
        )
        return None

    # Ventana original (seccion 24): lo que propuso el modelo ANTES de que el
    # software ajustara los bordes. Si el modelo no la declaro, se reconstruye
    # desde los limites previos al recorte, que es justo lo que hay que auditar.
    original = cand.get("original_window")
    if isinstance(original, dict):
        o_start = float(original.get("start", proposed_start))
        o_end = float(original.get("end", proposed_end))
        original = {"start": round(o_start, 3), "end": round(o_end, 3), "duration": round(max(o_end - o_start, 0.0), 3)}
    else:
        original = {
            "start": proposed_start,
            "end": proposed_end,
            "duration": round(proposed_end - proposed_start, 3),
        }

    source = {
        "file": video_file,
        "sha256": video_sha256,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "boundary_reason": source.get("boundary_reason"),
    }

    # Context expansion (seccion 24): si el modelo dice que corta a media frase,
    # se extiende al fin de parrafo dentro del maximo.
    progression = cand.get("progression")
    ends_complete = (
        isinstance(progression, dict) and progression.get("ends_complete") is not False
    )
    end_anchored = False
    if not ends_complete:
        extended = _expand_to_sentence_end(paragraphs, start, end, blocks_max)
        if extended > end:
            end_anchored = True
            end = extended
            source["end"] = end
            source["duration"] = round(end - start, 3)

    post_context = round(end - proposed_end, 3)
    expanded = end_anchored or pre_context > 0
    if expanded:
        if end_anchored:
            reason = "fin a media frase: anclado al cierre de parrafo (seccion 24)"
        else:
            reason = "silencio inicial: anclado a la primera voz (seccion 24)"
        cand["expansion"] = {
            "expanded": True,
            "pre_context_seconds": pre_context or None,
            "post_context_seconds": post_context or None,
            "reason": reason,
            "rescored": False,
        }
    elif cand.get("expansion") is None:
        cand["expansion"] = {
            "expanded": False,
            "pre_context_seconds": None,
            "post_context_seconds": None,
            "reason": None,
            "rescored": False,
        }

    # Si el modelo digo que corta a media frase y la expansion no lo arreglo,
    # el candidato cae: la invariante y el QC lo rechazarian igual. Se mira
    # `end_anchored` y no `expanded` porque un recorte por silencio inicial no
    # arregla un final a media frase: son bordes distintos.
    if not ends_complete and not end_anchored:
        drops.append(f"candidate[{index}]: progression.ends_complete=false y el fin no se pudo anclar")
        return None

    # Integridad del hook (secciones 25/26): se verifica contra el transcript
    # ENTERO, no solo contra el bloque: lo que importa es que la frase exista.
    hook = cand.get("hook")
    if not isinstance(hook, dict) or not hook.get("text") or not hook.get("origin"):
        drops.append(f"candidate[{index}]: hook sin texto u origin (seccion 19: un clip abre con hook)")
        return None
    hook = _clamp_hook_to_source(hook, start, end)
    cand["hook"] = hook
    integrity_result = integrity.verify_document_hooks({"hook": hook}, full_text)
    cand["hook"] = integrity_result.document["hook"]

    # Si la expansion arreglo un final cortado a media frase, el hecho queda
    # registrado: la invariante rechaza ends_complete=false, y ya no lo es.
    if expanded and progression is not None and isinstance(progression, dict):
        progression = dict(progression)
        progression["ends_complete"] = True
        cand["progression"] = progression

    cand["original_window"] = original
    cand["source"] = source
    cand["content"] = cand.get("content") or {"topic": "unknown", "reason": "no proporcionado"}

    cand["score"] = _normalize_score(cand.get("score") or {}, drops)
    cand["candidate_id"] = f"cand_{block_id}_{index}"
    cand["job_id"] = job_id
    cand["schema_version"] = SCHEMA_VERSION
    cand["generated_by"] = dict(model_run)
    return cand


def _model_run_for(
    *,
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
    input_sha256: str,
    latency_seconds: float,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "seed": None,
        "input_sha256": input_sha256,
        "latency_seconds": round(latency_seconds, 3),
    }


def _level2_blocks(doc: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    windows = doc.get("context_windows", [])
    blocks = [
        window for window in windows if isinstance(window, dict) and window.get("level") == 2
    ]
    if blocks:
        return sorted(blocks, key=lambda window: (float(window["start"]), float(window["end"])))
    # Sin ventanas de nivel 2 (video casi sin voz), un unico bloque sobre todo.
    if duration > 0:
        return [{"id": "l2_global", "level": 2, "start": 0.0, "end": round(duration, 3), "text": ""}]
    return []


# --- API principal -----------------------------------------------------------
# pylama:ignore=CC001


@dataclass
class CandidateSetResult:
    job_id: str | None
    block_count: int
    candidate_count: int
    selected_count: int
    dropped_count: int
    dropped_generation: list[str]
    reused: bool
    path: str | None = None
    sha256: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"bloques     {self.block_count}",
            f"candidatos  {self.candidate_count} descubiertos",
            f"seleccion   {self.selected_count} elegidos, {self.dropped_count} descartados por ranking",
        ]
        if self.reused:
            lines.append("reutilizado clip_candidates.json existente")
        if self.dropped_generation:
            lines.append(f"descartados en generacion: {len(self.dropped_generation)}")
        for warning in self.warnings:
            lines.append(f"AVISO       {warning}")
        return "\n".join(lines)


class _FakeRepoModelLogger:
    def record_model_run(self, **_: Any) -> str:
        return ""


def generate_candidates(
    normalized_path: Path | str,
    content_map_path: Path | str,
    config: dict[str, Any],
    *,
    job_id: str | None = None,
    output_path: Path | str | None = None,
    input_video: str | None = None,
    input_sha256: str | None = None,
    blocks_dir: Path | str | None = None,
    overwrite: bool = True,
    client: LMStudioClient | Any | None = None,
    repo: Any | None = None,
) -> CandidateSetResult:
    """Descubre y puntua los candidatos a clip de un job.

    Lee `normalized_transcript.json` + `content_map.json`, llama a Gemma por
    bloque de nivel 2 y escribe `clip_candidates.json` con el conjunto completo
    y su seleccion de ranking. La telemetria solo se registra si hay `repo`.
    """
    telemetry = repo if repo is not None else _FakeRepoModelLogger()
    normalized = artifacts.read_json(normalized_path, schema_name="normalized_transcript")
    map_doc = artifacts.read_json(content_map_path, schema_name="content_map")

    target = (
        Path(output_path)
        if output_path
        else Path(normalized_path).with_name("clip_candidates.json")
    )
    duration = float(normalized.get("duration") or 0.0)
    language = str(normalized.get("language") or "unknown")
    paragraphs = list(normalized.get("paragraphs", []))
    full_text = str(
        normalized.get("full_text") or " ".join(str(p.get("text", "")) for p in paragraphs)
    )

    video_file = str(input_video) if input_video else ""
    video_sha256 = str(input_sha256) if input_sha256 else str(normalized.get("job_id") or "")

    blocks = _level2_blocks(normalized, duration)
    if not blocks:
        raise CandidateFatalError(
            f"el normalized_transcript {normalized_path} no tiene bloques de nivel 2"
        )

    if not overwrite:
        existing = artifacts.read_or_none(target, schema_name="candidate_set")
        if existing is not None:
            selection = existing.get("selection") or {}
            return CandidateSetResult(
                job_id=job_id,
                block_count=existing.get("block_count", 0),
                candidate_count=len(existing.get("candidates", [])),
                selected_count=len(selection.get("selected", [])),
                dropped_count=len(selection.get("dropped", [])),
                dropped_generation=list(existing.get("drops", [])),
                reused=True,
                path=str(target),
                sha256=artifacts.sha256_of_file(target),
                warnings=list(existing.get("warnings", [])),
            )

    clips_cfg = {
        "minimum": float(config_module.get(config, "clips.minimum", 15.0)),
        "preferred_minimum": float(config_module.get(config, "clips.preferred_minimum", 20.0)),
        "preferred_maximum": float(config_module.get(config, "clips.preferred_maximum", 60.0)),
        "maximum": float(config_module.get(config, "clips.maximum", 90.0)),
        "max_leading_silence": float(
            config_module.get(config, "clips.max_leading_silence", 1.5)
        ),
    }

    content_client = client if client is not None else client_from_config(config)
    model_id = content_client.resolve_model()
    temperature = float(getattr(content_client, "temperature", 0.1))
    max_tokens = getattr(content_client, "max_tokens", None)
    provider = str(config_module.get(config, "llm.provider", "lmstudio"))
    structured_output = str(config_module.get(config, "llm.structured_output", "auto")).lower()
    max_retries = int(config_module.get(config, "llm.max_retries", 3))
    backoff = float(config_module.get(config, "llm.retry_backoff_seconds", 1.0))
    normalized_sha = artifacts.sha256_of_file(normalized_path)

    blocks_root = Path(blocks_dir) if blocks_dir else Path(normalized_path).parent / "candidates"
    blocks_root.mkdir(parents=True, exist_ok=True)

    completed: list[dict[str, Any]] = []
    drop_reasons: list[str] = []
    prompt_truncated_any = False

    for block_index, block in enumerate(blocks):
        block_id = str(block.get("id") or f"l2_{block_index:03d}")
        block_file = blocks_root / f"block_{block_id}.json"
        want_schema = structured_output != "off"

        cached = artifacts.read_or_none(block_file, schema_name="candidate_body")
        block_latency = 0.0
        if cached is not None and not overwrite:
            bodies = list(cached.get("candidates", []))
        else:
            messages, truncated = build_block_messages(
                block,
                paragraphs,
                map_doc,
                language=language,
                duration=duration,
                blocks_min=clips_cfg["minimum"],
                blocks_max=clips_cfg["maximum"],
                preferred_min=clips_cfg["preferred_minimum"],
                preferred_max=clips_cfg["preferred_maximum"],
            )
            prompt_truncated_any = prompt_truncated_any or truncated

            attempts_done = 0
            last_error: Exception | None = None
            body: dict[str, Any] | None = None
            latency_total = 0.0
            while attempts_done < max_retries:
                attempts_done += 1
                started = time.perf_counter()
                try:
                    content = content_client.chat(
                        messages, schema_name="candidate_body" if want_schema else None
                    )
                    latency = time.perf_counter() - started
                    latency_total += latency
                    body = _extract_json(content)
                    if not isinstance(body.get("candidates"), list):
                        raise ModelOutputError("respuesta sin lista 'candidates'")
                    _clamp_raw_scores_to_caps(body)
                    artifacts.validate("candidate_body", body)
                    telemetry.record_model_run(
                        stage="GENERATING_CANDIDATES",
                        model=model_id,
                        provider=provider,
                        job_id=job_id,
                        prompt_version=PROMPT_VERSION,
                        schema_name="candidate_body" if want_schema else None,
                        attempts=attempts_done,
                        succeeded=True,
                        duration_ms=int(latency * 1000),
                    )
                    break
                except LLMFatalError as exc:
                    raise CandidateFatalError(f"error de configuracion del LLM: {exc}") from exc
                except (ModelOutputError, LLMRecoverableError, artifacts.ArtifactInvalidError) as exc:
                    latency = time.perf_counter() - started
                    latency_total += latency
                    last_error = exc
                    telemetry.record_model_run(
                        stage="GENERATING_CANDIDATES",
                        model=model_id,
                        provider=provider,
                        job_id=job_id,
                        prompt_version=PROMPT_VERSION,
                        schema_name="candidate_body" if want_schema else None,
                        attempts=attempts_done,
                        succeeded=False,
                        duration_ms=int(latency * 1000),
                        error_detail=str(exc)[:500],
                    )
                    if (
                        want_schema
                        and structured_output == "auto"
                        and isinstance(exc, LLMRecoverableError)
                        and getattr(exc, "status_code", None) in _UNSUPPORTED_SCHEMA_STATUS
                    ):
                        LOGGER.warning(
                            "degradando a JSON por prompt: el servidor rechazo el schema candidate_body "
                            "(HTTP %s)",
                            getattr(exc, "status_code", None),
                        )
                        want_schema = False
                        attempts_done -= 1
                        continue
                    if attempts_done >= max_retries:
                        break
                    if backoff > 0:
                        time.sleep(backoff)

            if body is None:
                detail = str(last_error) if last_error else "sin detalle"
                raise CandidateRecoverableError(
                    f"Gemma no produjo candidatos validos para el bloque {block_id} "
                    f"tras {max_retries} intento(s): {detail}"
                )

            artifacts.write_json(block_file, body, schema_name="candidate_body", overwrite=True)
            bodies = list(body.get("candidates", []))
            block_latency = latency_total

        model_run = _model_run_for(
            provider=provider,
            model=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
            input_sha256=normalized_sha,
            latency_seconds=block_latency,
        )

        for index, body in enumerate(bodies):
            candidate = _process_candidate(
                body,
                index=index,
                block_id=block_id,
                duration=duration,
                paragraphs=paragraphs,
                full_text=full_text,
                video_file=video_file,
                video_sha256=video_sha256,
                job_id=job_id or str(normalized.get("job_id") or ""),
                model_run=model_run,
                blocks_min=clips_cfg["minimum"],
                blocks_max=clips_cfg["maximum"],
                leading_silence_tolerance=clips_cfg["max_leading_silence"],
                drops=drop_reasons,
            )
            if candidate is not None:
                completed.append(candidate)

    aggregate: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id or str(normalized.get("job_id") or ""),
        "source": {
            "file": video_file,
            "sha256": video_sha256,
            "duration": round(duration, 3),
        },
        "block_count": len(blocks),
        "content_map_sha256": artifacts.sha256_of_file(content_map_path),
        "normalized_transcript_sha256": normalized_sha,
        "candidates": [],
    }

    selection, selection_warnings = ranking.select_candidates(
        {**aggregate, "candidates": completed}, config
    )
    aggregate["selection"] = selection
    warnings = list(selection_warnings)
    if prompt_truncated_any:
        warnings.append(
            f"alguna ventana supero el presupuesto del modelo "
            f"({BLOCK_MAX_CHARS} caracteres) y se recorto la parte central"
        )
    if drop_reasons:
        warnings.append(
            f"{len(drop_reasons)} candidatos descartados en generacion "
            "(fuera de rangos, cortes a media frase, etc.)"
        )
        # Los motivos concretos, no solo el numero. Antes se perdian: el
        # artefacto decia "8 descartados" y no se podia saber por que, asi que
        # cualquier ajuste al prompt era a ciegas. `selection.dropped` es el
        # rastro del ranking; este es el de la generacion.
        aggregate["drops"] = list(drop_reasons)
    if warnings:
        aggregate["warnings"] = warnings
    aggregate["candidates"] = completed

    info = artifacts.write_json(target, aggregate, schema_name="candidate_set", overwrite=overwrite)

    LOGGER.info(
        "candidate_generation_completed",
        extra={
            "blocks": len(blocks),
            "candidates": len(completed),
            "selected": len(selection.get("selected", [])),
            "dropped": len(drop_reasons),
            "path": str(info.path),
        },
    )

    return CandidateSetResult(
        job_id=job_id or str(normalized.get("job_id") or ""),
        block_count=len(blocks),
        candidate_count=len(completed),
        selected_count=len(selection.get("selected", [])),
        dropped_count=len(selection.get("dropped", [])),
        dropped_generation=drop_reasons,
        reused=False,
        path=str(info.path),
        sha256=info.sha256,
        warnings=warnings,
    )