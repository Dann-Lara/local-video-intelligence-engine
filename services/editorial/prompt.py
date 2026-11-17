"""Prompt versionado de la etapa editorial.

`PROMPT_VERSION` forma parte de la identidad de una ejecucion: cambiarlo
invalida resultados anteriores (BLUEPRINT 9 y patron de huellas del
proyecto).

El modelo solo recibe la transcripcion: no debe inventar contenido, no debe
calcular timestamps y debe devolver referencias a los segmentos originales
(`segment_id`). El sistema calcula los limites temporales.
"""

from __future__ import annotations

from typing import Dict, List

from schemas.contracts import TranscriptSegment

PROMPT_VERSION = "editorial_v3"

# Senales editoriales que el modelo puede reportar. No son probabilidades.
SIGNAL_NAMES = (
    "hook",
    "curiosity",
    "tension",
    "revelation",
    "clarity",
    "self_contained",
    "boundary_quality",
    "coherence",
)


def _render_segments(
    prefix: str,
    segment_ids: List[str],
    segments_by_id: Dict[str, TranscriptSegment],
) -> str:
    lines: List[str] = []
    for segment_id in segment_ids:
        segment = segments_by_id.get(segment_id)
        if segment is None:
            continue
        lines.append(
            f"{prefix} {segment.segment_id} [{segment.start_seconds:.3f}-"
            f"{segment.end_seconds:.3f}]: {segment.text}"
        )
    return "\n".join(lines)


def build_system_prompt() -> str:
    return (
        "Eres un editor de video que analiza transcripciones para proponer "
        "fragmentos que funcionen como clips independientes. Trabajas "
        "UNICAMENTE con el texto entregado.\n\n"
        "Reglas obligatorias:\n"
        "1. No inventes palabras, hechos, cifras, nombres ni conclusiones que "
        "no esten en el texto. `evidence_text` debe ser una cita literal del "
        "texto de los segment_id referenciados: misma redaccion, mismo orden y "
        "misma puntuacion, sin parafrasear, resumir, omitir ni anadir comillas "
        "o puntos suspensivos.\n"
        "2. No calcules ni inventes timestamps. Devuelve solo referencias a "
        "los segment_id existentes.\n"
        "3. Referencia unicamente segment_id de la seccion CUERPO y deben ser "
        "contiguos (sin saltos). Los segmentos de CONTEXTO son solo para "
        "entender; no pueden formar parte del clip.\n"
        "4. El clip abarca desde el inicio del primer segment_id hasta el fin "
        "del ultimo: elige la cantidad de segmentos contiguos necesaria para "
        "cumplir la duracion objetivo indicada en cada ventana.\n"
        "5. No afirmes nada sobre el tono de voz, las imagenes, la musica ni "
        "lo visual: solo analizas texto.\n"
        "6. No presentes ninguna puntuacion como probabilidad de viralidad.\n"
        "7. Si no hay un fragmento util, devuelve una lista vacia.\n"
        "8. Clasifica la publicidad del fragmento con `ad_classification`:\n"
        "   - `none`: no menciona productos, marcas ni servicios.\n"
        "   - `incidental_mention`: menciona una marca o producto como parte de "
        "la historia, sin promocion, llamada a la accion ni oferta.\n"
        "   - `promotional`: promociona explicitamente un producto, servicio o "
        "marca (descarga, compra, codigo, enlace, sorteo, oferta).\n"
        "   - `sponsored_or_integrated`: tramo patrocinado o anuncio integrado.\n"
        "   - `uncertain`: la evidencia no permite decidir.\n"
        "   Si la clasificacion NO es `none` ni `incidental_mention`, copia en "
        "`ad_evidence` la cita literal que lo demuestra. Para `none` deja "
        "`ad_evidence` vacio. No inventes marcas ni promociones.\n"
        "9. No propongas un clip que empiece o termine dentro de un anuncio o "
        "promocion. Si el CUERPO contiene publicidad, elige solo segmentos "
        "contiguos fuera de ella; si no es posible, devuelve una lista vacia.\n"
        "10. Evalua los limites en `boundary_evaluation`: describe en texto si "
        "el inicio y el final son naturales (frase completa, sin corte a mitad "
        "de idea). No devuelvas timestamps. Usa este objeto:\n"
        '   {"start": "descripcion", "end": "descripcion", '
        '"start_ok": true, "end_ok": true, "notes": "aclaracion o vacio"}\n'
        "11. `editorial_decision` debe ser `accept` solo si la idea esta "
        "completa, los dos limites son validos y `ad_classification` es `none` "
        "o `incidental_mention`. Usa `reject` si el fragmento es publicitario o "
        "no funciona solo. Usa `needs_review` si hay duda de contenido, de "
        "publicidad o de limites. Explica la decision en "
        "`editorial_decision_reason`.\n\n"
        "Devuelve EXCLUSIVAMENTE un objeto JSON con esta forma:\n"
        "{\n"
        '  "candidates": [\n'
        "    {\n"
        '      "segment_ids": ["seg_0001", "seg_0002"],\n'
        '      "title": "titulo editorial breve",\n'
        '      "hook": "gancho sugerido",\n'
        '      "main_idea": "idea principal respaldada por el texto",\n'
        '      "ending": "como cierra el fragmento",\n'
        '      "editorial_reason": "por que funciona como unidad independiente",\n'
        '      "evidence_text": "cita literal tomada del texto",\n'
        '      "context_dependency": "low|medium|high",\n'
        '      "confidence": 0.0,\n'
        '      "ad_classification": "none|incidental_mention|promotional|'
        'sponsored_or_integrated|uncertain",\n'
        '      "ad_evidence": "cita literal de la promocion o vacio",\n'
        '      "editorial_decision": "accept|reject|needs_review",\n'
        '      "editorial_decision_reason": "motivo breve",\n'
        '      "boundary_evaluation": {\n'
        '        "start": "descripcion del inicio",\n'
        '        "end": "descripcion del final",\n'
        '        "start_ok": true,\n'
        '        "end_ok": true,\n'
        '        "notes": "aclaracion o vacio"\n'
        "      },\n"
        '      "signals": [\n'
        '        {"name": "hook", "status": "observed|inferred|unknown", '
        '"evidence": "cita literal o vacio", "note": "aclaracion o vacio"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "No anadas texto fuera del JSON."
    )


def build_window_user_prompt(
    *,
    window_id: str,
    body_ids: List[str],
    context_before_ids: List[str],
    context_after_ids: List[str],
    segments_by_id: Dict[str, TranscriptSegment],
    language: str = "",
    duration_seconds: float = 0.0,
    max_candidates: int = 2,
    clip_min_seconds: float = 0.0,
    clip_target_seconds: float = 0.0,
    clip_max_seconds: float = 0.0,
) -> str:
    parts: List[str] = []
    parts.append(f"Ventana de analisis: {window_id}")
    if language:
        parts.append(f"Idioma detectado: {language}")
    if duration_seconds:
        parts.append(f"Duracion total del video (segundos): {duration_seconds:.3f}")
    parts.append(f"Numero maximo de propuestas para esta ventana: {max_candidates}")
    if clip_min_seconds and clip_max_seconds:
        target = (
            f", preferiblemente cerca de {clip_target_seconds:.0f} s"
            if clip_target_seconds
            else ""
        )
        parts.append(
            f"Duracion objetivo del clip: entre {clip_min_seconds:.0f} y "
            f"{clip_max_seconds:.0f} s{target}. Los rangos [inicio-fin] de cada "
            "segmento son solo para estimar cuanto abarca tu seleccion; no los "
            "devuelvas ni inventes otros."
        )
    parts.append("")

    if context_before_ids:
        parts.append("CONTEXTO ANTERIOR (solo referencia, no elegible):")
        parts.append(_render_segments("[antes]", context_before_ids, segments_by_id))
        parts.append("")

    parts.append("CUERPO (elegible para el clip; usa solo estos segment_id):")
    parts.append(_render_segments("[cuerpo]", body_ids, segments_by_id))
    parts.append("")

    if context_after_ids:
        parts.append("CONTEXTO POSTERIOR (solo referencia, no elegible):")
        parts.append(_render_segments("[despues]", context_after_ids, segments_by_id))
        parts.append("")

    parts.append(
        "Selecciona momentos con contexto suficiente y potencial narrativo "
        "(una idea completa con planteamiento, desarrollo y cierre), no "
        "fragmentos arbitrariamente cortos. Cada propuesta debe referenciar "
        "segment_id contiguos del CUERPO (sin saltos) y copiar `evidence_text` "
        "literalmente de esos segmentos. No propongas cortes dentro de un "
        "anuncio o promocion: si el cuerpo contiene publicidad, usa solo los "
        "segmentos contiguos que quedan fuera de ella."
    )
    return "\n".join(parts)


def build_repair_user_prompt(previous_user_prompt: str, error: str) -> str:
    """Instruccion de reparacion (un intento, trazable).

    Se reenvia el mismo contexto y se anade el error observado para pedir
    JSON valido. No es una reparacion ilimitada: el orquestador acota los
    intentos con `EditorialConfig.max_repair_attempts`.
    """
    return (
        previous_user_prompt
        + "\n\nAVISO: tu respuesta anterior no fue valida y produjo este error:\n"
        + f"{error}\n"
        + "Vuelve a responder EXCLUSIVAMENTE con el objeto JSON indicado, sin "
        "texto adicional ni bloques de codigo."
    )
