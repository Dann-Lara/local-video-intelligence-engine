"""Invariantes semanticas que JSON Schema no puede expresar (secciones 20, 23, 63).

El schema garantiza ESTRUCTURA (tipos, campos obligatorios, topes por campo).
Este modulo garantiza COHERENCIA entre campos, que draft 2020-12 no soporta:

    "inicio < final"                        -> checks de rango temporal
    "duración válida"                       -> end - start == duration
    "scores correctos / pesos correctos"    -> total == suma de dimensiones
    "no cortar a media frase"               -> progresion.ends_complete

Regla: la IA propone, el software valida. Ninguna invariante se delega al LLM.
"""

from __future__ import annotations

from typing import Any, Callable

# Seccion 20: topes por dimension. El total no se declara aqui, se deriva.
SCORE_DIMENSION_MAX = {
    "hook": 20,
    "curiosity": 15,
    "value": 15,
    "emotion": 10,
    "narrative": 10,
    "payoff": 10,
    "shareability": 10,
    "standalone": 5,
    "visual": 5,
}
SCORE_MAX_TOTAL = 100  # = sum(SCORE_DIMENSION_MAX.values())

# Seccion 23. Configurables; aqui los valores por defecto del blueprint.
DURATION_LIMITS = {
    "minimum": 15.0,
    "preferred_minimum": 20.0,
    "preferred_maximum": 60.0,
    "maximum": 90.0,
}

# Tolerancia de un frame a 30 fps para las comparaciones de duracion.
DURATION_TOLERANCE = 0.05

Rule = Callable[[dict[str, Any]], list[str]]


def _time_range_errors(block: Any, label: str) -> list[str]:
    if not isinstance(block, dict):
        return []
    start = block.get("start")
    end = block.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return []
    errors = []
    if start >= end:
        errors.append(f"{label}: start ({start}) debe ser menor que end ({end})")
    if start < 0:
        errors.append(f"{label}: start ({start}) no puede ser negativo")
    return errors


def _duration_errors(block: dict[str, Any], label: str) -> list[str]:
    errors = _time_range_errors(block, label)
    start, end = block.get("start"), block.get("end")
    duration = block.get("duration")
    if not isinstance(duration, (int, float)):
        return errors
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        if start < end and abs((end - start) - duration) > DURATION_TOLERANCE:
            errors.append(
                f"{label}: duration ({duration}) no coincide con end - start ({end - start:.3f})"
            )
    return errors


def _check_transcript(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    segments = doc.get("segments", [])
    previous_end = 0.0
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        errors += _duration_errors(segment, label)
        start, end = segment.get("start"), segment.get("end")
        if isinstance(start, (int, float)) and start < previous_end - DURATION_TOLERANCE:
            errors.append(
                f"{label}: start ({start}) retrocede respecto al segmento previo ({previous_end})"
            )
        if isinstance(end, (int, float)):
            previous_end = end
        for word_index, word in enumerate(segment.get("words", [])):
            word_label = f"{label}.words[{word_index}]"
            errors += _time_range_errors(word, word_label)
            text = word.get("word")
            if isinstance(text, str) and text != text.strip():
                errors.append(f"{word_label}: 'word' tiene espacios sobrantes")
    duration = doc.get("duration")
    # Solo se contrasta si la duracion declarada aporta informacion. Un backend
    # puede devolver duration=0 cuando no la conoce (seccion 62: unknown, no
    # cero de mentira), y en ese caso TODO segmento pareceria estar fuera de
    # rango. Normalizar deduce la duracion real a partir del ultimo segmento.
    if isinstance(duration, (int, float)) and duration > 0 and segments:
        last_end = segments[-1].get("end")
        if isinstance(last_end, (int, float)) and last_end - duration > 1.0:
            errors.append(
                f"segments: el ultimo segmento termina en {last_end}, despues de la duracion declarada {duration}"
            )
    return errors


def _check_candidate(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += _duration_errors(doc.get("source", {}), "source")

    score = doc.get("score", {})
    if isinstance(score, dict):
        dimensions = {
            key: value
            for key, value in score.items()
            if key in SCORE_DIMENSION_MAX and isinstance(value, (int, float))
        }
        total = score.get("total")
        if len(dimensions) == len(SCORE_DIMENSION_MAX) and isinstance(total, (int, float)):
            computed = sum(dimensions.values())
            if abs(computed - total) > 0.5:
                errors.append(
                    f"score: total ({total}) no es la suma de las dimensiones ({computed})"
                )
        for key, value in dimensions.items():
            if value > SCORE_DIMENSION_MAX[key]:
                errors.append(
                    f"score.{key}: {value} excede el maximo {SCORE_DIMENSION_MAX[key]}"
                )

    progression = doc.get("progression", {})
    if isinstance(progression, dict) and progression.get("ends_complete") is False:
        errors.append("progression.ends_complete es false: el clip corta a media frase")

    hook = doc.get("hook", {})
    if isinstance(hook, dict) and hook.get("origin") == "generated" and not hook.get(
        "requires_generated_hook"
    ):
        errors.append(
            "hook: un hook GENERATED_HOOK debe llevar requires_generated_hook=true (seccion 26)"
        )
    return errors


def _check_edit_plan(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += _duration_errors(doc.get("source", {}), "source")

    output = doc.get("output", {})
    if isinstance(output, dict):
        width, height = output.get("width"), output.get("height")
        if isinstance(width, int) and isinstance(height, int) and height <= width:
            errors.append(
                f"output: {width}x{height} no es vertical (seccion 30 exige 1080x1920)"
            )

    hook = doc.get("hook", {})
    if isinstance(hook, dict) and hook.get("origin") == "generated" and not hook.get(
        "requires_generated_hook"
    ):
        errors.append(
            "hook: hook generado sin requires_generated_hook=true; no puede presentarse "
            "como texto pronunciado (seccion 25/26)"
        )

    subtitles = doc.get("subtitles", {})
    if isinstance(subtitles, dict) and subtitles.get("enabled"):
        max_lines = subtitles.get("max_lines")
        if isinstance(max_lines, int) and max_lines > 3:
            errors.append(
                f"subtitles.max_lines ({max_lines}): no llenar la pantalla de texto (seccion 31)"
            )
    return errors


def _check_candidate_set(doc: dict[str, Any]) -> list[str]:
    """El conjunto candidato a candidato: cada uno cumple `_check_candidate`, y
    la seleccion solo referencia candidatos presentes en el conjunto."""
    errors: list[str] = []
    candidates = doc.get("candidates", [])
    if not isinstance(candidates, list):
        return errors
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        for message in _check_candidate(candidate):
            errors.append(f"candidates[{index}]: {message}")

    selection = doc.get("selection")
    if isinstance(selection, dict):
        ids = {str(candidate.get("candidate_id")) for candidate in candidates if isinstance(candidate, dict)}
        for index, candidate_id in enumerate(selection.get("selected", [])):
            if candidate_id not in ids:
                errors.append(f"selection.selected[{index}]: '{candidate_id}' no existe entre los candidatos")
        for index, dropped in enumerate(selection.get("dropped", [])):
            if isinstance(dropped, dict) and dropped.get("candidate_id") not in ids:
                errors.append(
                    f"selection.dropped[{index}]: '{dropped.get('candidate_id')}' no existe entre los candidatos"
                )
    return errors


def _check_clip_record(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += _duration_errors(doc.get("source", {}), "source")
    for index, snapshot in enumerate(doc.get("performance", [])):
        if not isinstance(snapshot, dict):
            continue
        label = f"performance[{index}]"
        platform = snapshot.get("platform")
        if not isinstance(platform, str) or not platform:
            errors.append(f"{label}.platform vacio")
        metrics = snapshot.get("metrics", {})
        if isinstance(metrics, dict):
            engagement = metrics.get("engagement", {})
            if isinstance(engagement, dict):
                for key in ("likes", "comments", "shares", "saves"):
                    if key in engagement and engagement[key] == 0:
                        errors.append(
                            f"{label}.metrics.engagement.{key} es 0: usar null para "
                            "'no proporcionado' (seccion 38)"
                        )
    qc = doc.get("qc", {})
    if isinstance(qc, dict) and qc.get("passed") is True:
        problems = qc.get("problems", [])
        if problems:
            errors.append("qc.passed es true pero qc.problems no esta vacio")
    return errors


# Seccion 17: categorias de momento del content map. Se comparte con el modulo
# de generacion (src/llm/content_map.py) para que la lista viva en un solo sitio.
CONTENT_MAP_MOMENT_CATEGORIES = (
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


def _check_content_map(doc: dict[str, Any]) -> list[str]:
    """Seccion 17 + 62: ademas de rangos coherentes, TODOS los times tienen que
    caer dentro del video. Un moment fuera de [0, duration] es un moment que
    apunta a algo que no existe: o una alucinacion temporal, o que Gemma no supo
    leer los timecodes. La capa de generacion lo clampa antes de escribir, pero
    si algo invalido llega aqui (por ejemplo, un artefacto editado a mano) la
    invariante tiene que delatarlo."""
    errors: list[str] = []
    duration = doc.get("duration")
    has_duration = isinstance(duration, (int, float)) and duration > 0

    def _bound(label: str, block: Any) -> None:
        if not isinstance(block, dict):
            return
        errors.extend(_time_range_errors(block, label))
        end = block.get("end")
        if has_duration and isinstance(end, (int, float)) and end > duration + DURATION_TOLERANCE:
            errors.append(f"{label}: end ({end}) supera la duracion del video ({duration})")

    for key in CONTENT_MAP_MOMENT_CATEGORIES:
        for index, moment in enumerate(doc.get(key, [])):
            if not isinstance(moment, dict):
                continue
            label = f"{key}[{index}]"
            _bound(label, moment.get("time", {}))
            for quote_index, quote in enumerate(moment.get("quotes", [])):
                if isinstance(quote, dict):
                    _bound(f"{label}.quotes[{quote_index}]", quote.get("time", {}))

    for index, claim in enumerate(doc.get("claims", [])):
        if not isinstance(claim, dict):
            continue
        label = f"claims[{index}]"
        _bound(label, claim.get("time", {}))
        for evidence_index, evidence in enumerate(claim.get("evidence", [])):
            if isinstance(evidence, dict):
                _bound(f"{label}.evidence[{evidence_index}]", evidence.get("time", {}))

    for index, topic in enumerate(doc.get("topics", [])):
        if isinstance(topic, dict):
            _bound(f"topics[{index}]", topic.get("time", {}))

    for index, entity in enumerate(doc.get("entities", [])):
        if isinstance(entity, dict):
            _bound(f"entities[{index}]", entity.get("time", {}))

    for index, hook in enumerate(doc.get("potential_hooks", [])):
        if not isinstance(hook, dict):
            continue
        label = f"potential_hooks[{index}]"
        _bound(label, hook.get("time", {}))
        if hook.get("origin") == "generated" and not hook.get("requires_generated_hook"):
            errors.append(f"{label}: hook generado sin requires_generated_hook=true")
    return errors


def _check_performance_snapshot(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if doc.get("age_hours") is not None and doc.get("captured_at") is None:
        errors.append("age_hours sin captured_at no es trazable")
    return errors


# Seccion 16: ventanas de nivel 1. No es un requisito cuando el material
# completo es mas corto que la ventana minima.
CONTEXT_WINDOW_LEVEL_1 = (30.0, 120.0)


def _check_normalized_transcript(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for index, paragraph in enumerate(doc.get("paragraphs", [])):
        errors += _duration_errors(paragraph, f"paragraphs[{index}]")
    total_duration = doc.get("duration")
    level_1_applicable = not isinstance(total_duration, (int, float)) or (
        total_duration >= CONTEXT_WINDOW_LEVEL_1[0]
    )
    for index, window in enumerate(doc.get("context_windows", [])):
        if not isinstance(window, dict):
            continue
        label = f"context_windows[{index}]"
        errors += _time_range_errors(window, label)
        if window.get("level") != 1 or not level_1_applicable:
            continue
        length = window.get("end", 0) - window.get("start", 0)
        low, high = CONTEXT_WINDOW_LEVEL_1
        if not (low <= length <= high):
            errors.append(
                f"{label}: nivel 1 dura {length:.0f}s, seccion 16 pide {low:.0f}-{high:.0f}s"
            )
    return errors


RULES: dict[str, Rule] = {
    "transcript": _check_transcript,
    "normalized_transcript": _check_normalized_transcript,
    "content_map": _check_content_map,
    "candidate": _check_candidate,
    "candidate_set": _check_candidate_set,
    "edit_plan": _check_edit_plan,
    "clip_record": _check_clip_record,
    "performance_snapshot": _check_performance_snapshot,
}


def invariant_errors(schema_name: str, document: Any) -> list[str]:
    """Devuelve los fallos de coherencia. Vacio = coherente."""
    rule = RULES.get(schema_name)
    if rule is None or not isinstance(document, dict):
        return []
    return rule(document)


def is_invariant(schema_name: str, document: Any) -> bool:
    return not invariant_errors(schema_name, document)


def check_duration_within_limits(
    duration: float, limits: dict[str, float] | None = None
) -> list[str]:
    """Seccion 23: fuera de [minimum, maximum] es invalido.

    Estar fuera de [preferred_minimum, preferred_maximum] NO es un error: el
    sistema puede seleccionar un clip mas largo si la estructura lo justifica.
    """
    limits = {**DURATION_LIMITS, **(limits or {})}
    if duration < limits["minimum"]:
        return [f"duration {duration:.2f}s por debajo del minimo {limits['minimum']}s"]
    if duration > limits["maximum"]:
        return [f"duration {duration:.2f}s por encima del maximo {limits['maximum']}s"]
    return []
