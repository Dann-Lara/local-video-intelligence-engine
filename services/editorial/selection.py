"""Deduplicacion, ordenamiento y acotado de candidatos.

Determinista, independiente del orden de entrada y auditable:

- La relacion de duplicidad `is_duplicate` combina el solapamiento temporal
  con una senal textual calculada SOLO sobre el texto editorial (titulo,
  hook, idea, cierre y razon). La evidencia literal se excluye de la
  similitud editorial a proposito: comparte palabras de la fuente y, si se
  incluyera siempre, inflaria la similitud y fusionaria propuestas distintas
  que solo comparten el material citado.
- Un intervalo IDENTICO no basta por si solo para declarar duplicidad: dos
  enfoques editoriales distintos pueden cubrir el mismo momento. Para
  candidatos con el mismo intervalo se admite, ademas de la similitud
  editorial, la similitud de la EVIDENCIA LITERAL como segunda senal. Si
  ninguna supera el umbral, se conservan ambos (politica conservadora B1).
- La deduplicacion recorre los candidatos en orden canonico (mejor primero,
  segun `_comparison_key`) y descarta un candidato UNICAMENTE si es
  duplicado directo de un sobreviviente ya conservado. No se usan
  componentes conexos ni cadenas transitivas: el ganador registrado es
  siempre un duplicado directo y un sobreviviente. Asi el resultado es
  independiente del orden de entrada y una cadena no transitiva (A~B, B~C,
  A!~C) con A>B>C conserva A y C, descartando solo B.
- La preferencia (`_comparison_key`) NO usa la confianza del modelo como
  medida de calidad. Prioriza: (1) validez verificable, (2) cantidad de
  campos editoriales poblados, (3) confianza como desempate, (4) inicio
  ascendente y (5) `clip_id` (desempate total y estable). No es una
  prediccion de viralidad.

`rank_candidates` sigue siendo una PRIORIDAD DE REVISION (no calidad). Los
descartes se registran con el contrato existente (`reason`, `detail`,
`clip_id`) y el ganador acumula `superseded_candidate_ids`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from schemas.contracts import ReviewStatus
from schemas.editorial import (
    AdClassification,
    EditorialCandidate,
    EditorialDecision,
)

_WORD = re.compile(r"[\wáéíóúüñ]+", re.IGNORECASE)

# Orden de preferencia editorial para deduplicacion: aceptado primero, luego
# revision, luego rechazado. `reject` no se elimina del artefacto (es
# trazabilidad), pero nunca gana un duplicado frente a un aceptado.
_DECISION_PRIORITY = {
    EditorialDecision.ACCEPT.value: 0,
    EditorialDecision.NEEDS_REVIEW.value: 1,
    EditorialDecision.REJECT.value: 2,
}

# Clasificaciones publicitarias que permiten render (junto con `accept`).
RENDER_SAFE_AD_CLASSIFICATIONS = (
    AdClassification.NONE.value,
    AdClassification.INCIDENTAL_MENTION.value,
)

# Campos editoriales que cuentan para la completitud verificable. La
# evidencia NO cuenta como "completitud editorial": es material citado.
_EDITORIAL_FIELDS = ("hook", "main_idea", "ending", "editorial_reason")

# Tolerancia para considerar que dos candidatos cubren el mismo intervalo.
# Los limites los deriva el sistema de los segmentos, por lo que un mismo
# conjunto de segmentos produce valores identicos.
_SPAN_TOLERANCE = 0.01


def _words(text: str) -> set:
    return {match.group(0).lower() for match in _WORD.finditer(text or "")}


def text_similarity(a: str, b: str) -> float:
    """Jaccard entre conjuntos de palabras de dos textos.

    Es una heuristica LEXICA, no una medida semantica fiable: solo sirve
    como senal acotada dentro de la politica de duplicidad.
    """
    wa, wb = _words(a), _words(b)
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def overlap_ratio(c1: EditorialCandidate, c2: EditorialCandidate) -> float:
    """Interseccion temporal sobre el menor de los dos clips."""
    start = max(c1.proposal.start_seconds, c2.proposal.start_seconds)
    end = min(c1.proposal.end_seconds, c2.proposal.end_seconds)
    inter = end - start
    if inter <= 0:
        return 0.0
    shortest = min(c1.duration_seconds, c2.duration_seconds)
    if shortest <= 0:
        return 0.0
    return inter / shortest


def _editorial_text(candidate: EditorialCandidate) -> str:
    """Texto editorial del candidato (excluye la evidencia literal)."""
    parts = [candidate.proposal.title]
    for name in _EDITORIAL_FIELDS:
        value = getattr(candidate.proposal, name, "")
        if isinstance(value, str):
            parts.append(value)
    return " ".join(part for part in parts if part)


def _editorial_similarity(a: EditorialCandidate, b: EditorialCandidate) -> float:
    return text_similarity(_editorial_text(a), _editorial_text(b))


def _evidence_similarity(a: EditorialCandidate, b: EditorialCandidate) -> float:
    """Jaccard sobre la EVIDENCIA literal (segunda senal para mismo intervalo)."""
    return text_similarity(a.evidence_text or "", b.evidence_text or "")


def _same_source_span(a: EditorialCandidate, b: EditorialCandidate) -> bool:
    """True si ambos candidatos cubren exactamente el mismo intervalo."""
    return (
        abs(a.proposal.start_seconds - b.proposal.start_seconds) <= _SPAN_TOLERANCE
        and abs(a.proposal.end_seconds - b.proposal.end_seconds) <= _SPAN_TOLERANCE
    )


def is_duplicate(
    a: EditorialCandidate,
    b: EditorialCandidate,
    *,
    overlap_ratio_threshold: float,
    text_similarity_threshold: float,
) -> bool:
    """Decide si dos candidatos describen el mismo clip.

    Reglas (todas requieren solapamiento temporal suficiente):

    1. Texto editorial suficientemente similar -> duplicado claro.
    2. Mismo intervalo Y evidencia literal suficientemente similar ->
       parafrasis del mismo momento respaldada por la fuente.

    El intervalo identico NO es condicion suficiente por si solo: dos
    enfoques editoriales distintos sobre el mismo momento se conservan si ni
    el texto editorial ni la evidencia permiten afirmar que son duplicados.
    Ante la duda, la funcion devuelve False: se conservan ambos.
    """
    if a.clip_id == b.clip_id:
        return False
    if overlap_ratio(a, b) < overlap_ratio_threshold:
        return False
    if _editorial_similarity(a, b) >= text_similarity_threshold:
        return True
    if _same_source_span(a, b) and _evidence_similarity(a, b) >= text_similarity_threshold:
        return True
    return False


# ---------------------------------------------------------------------------
# Preferencia (P2)
# ---------------------------------------------------------------------------

def _is_editorially_valid(candidate: EditorialCandidate) -> bool:
    """Validez verificable minima de una propuesta ya construida."""
    proposal = candidate.proposal
    try:
        if not isinstance(candidate.evidence_text, str) or not candidate.evidence_text.strip():
            return False
        if float(proposal.start_seconds) < 0:
            return False
        if float(proposal.end_seconds) <= float(proposal.start_seconds):
            return False
        if not proposal.transcript_refs:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _editorial_completeness(candidate: EditorialCandidate) -> int:
    """Numero de campos editoriales poblados (0..4).

    Es un conteo FACTICO de informacion verificable, no una puntuacion de
    calidad ni una probabilidad de exito.
    """
    proposal = candidate.proposal
    count = 0
    for name in _EDITORIAL_FIELDS:
        value = getattr(proposal, name, "")
        if isinstance(value, str) and value.strip():
            count += 1
    return count


def content_render_blockers(candidate: EditorialCandidate) -> List[str]:
    """Motivos por los que un candidato NO debe renderizarse.

    Solo evalua contenido editorial y publicidad; NO exige aprobacion humana.
    Un resultado vacio significa "puede renderizarse como borrador", no
    "aprobado para publicar".
    """
    blockers: List[str] = []
    if candidate.editorial_decision != EditorialDecision.ACCEPT.value:
        blockers.append(f"editorial_decision={candidate.editorial_decision}")
    if candidate.ad_classification not in RENDER_SAFE_AD_CLASSIFICATIONS:
        blockers.append(f"ad_classification={candidate.ad_classification}")
    if not _is_editorially_valid(candidate):
        blockers.append("propuesta_sin_evidencia_o_tiempos_invalidos")
    start_ok = candidate.boundary_evaluation.get("start_ok")
    end_ok = candidate.boundary_evaluation.get("end_ok")
    if start_ok is not True or end_ok is not True:
        blockers.append("limites_no_validados")
    return blockers


def is_content_eligible(candidate: EditorialCandidate) -> bool:
    """True si el contenido puede pasar a render (sin contar aprobacion)."""
    return not content_render_blockers(candidate)


def render_blockers(candidate: EditorialCandidate) -> List[str]:
    """Motivos completos para no renderizar, incluida la aprobacion humana."""
    blockers = content_render_blockers(candidate)
    if candidate.proposal.review_status != ReviewStatus.APPROVED:
        blockers.append(f"review_status={candidate.proposal.review_status.value}")
    return blockers


def is_render_eligible(candidate: EditorialCandidate) -> bool:
    """True solo si el contenido es elegible Y existe aprobacion humana.

    La aprobacion humana es un requisito independiente (AGENTS.md, principio
    5): ningun filtro automatico la sustituye.
    """
    return not render_blockers(candidate)


def _comparison_key(candidate: EditorialCandidate):
    """Clave de orden: menor = preferible.

    (1) validos primero, (2) decision editorial (accept < needs_review <
    reject), (3) mas completo primero, (4) mayor confianza solo como
    desempate, (5) inicio mas temprano, (6) clip_id estable.
    """
    return (
        0 if _is_editorially_valid(candidate) else 1,
        _DECISION_PRIORITY.get(candidate.editorial_decision, 3),
        -_editorial_completeness(candidate),
        -float(candidate.proposal.confidence),
        float(candidate.proposal.start_seconds),
        candidate.clip_id,
    )


# ---------------------------------------------------------------------------
# Deduplicacion (P1)
# ---------------------------------------------------------------------------

def deduplicate(
    candidates: Sequence[EditorialCandidate],
    *,
    overlap_ratio_threshold: float,
    text_similarity_threshold: float,
) -> Tuple[List[EditorialCandidate], List[Dict[str, object]]]:
    """Elimina duplicados de forma independiente del orden de entrada.

    Recorre los candidatos en orden canonico (mejor primero, segun
    `_comparison_key`) y descarta un candidato UNICAMENTE si es duplicado
    directo de un sobreviviente ya conservado. No se usan componentes conexos
    ni cadenas transitivas: el ganador registrado es siempre un
    sobreviviente y un duplicado directo del descartado.

    Devuelve (conservados ordenados por preferencia, descartes ordenados por
    clip_id).
    """
    # Orden canonico: mejor primero. La misma entrada produce el mismo
    # orden, por lo que el resultado no depende del orden original.
    items = sorted(candidates, key=_comparison_key)

    def _duplicates(x: EditorialCandidate, y: EditorialCandidate) -> bool:
        return is_duplicate(
            x,
            y,
            overlap_ratio_threshold=overlap_ratio_threshold,
            text_similarity_threshold=text_similarity_threshold,
        )

    survivors: List[EditorialCandidate] = []
    removed: List[Dict[str, object]] = []
    for candidate in items:
        dominator = None
        for kept in survivors:
            if _duplicates(kept, candidate):
                dominator = kept
                break
        if dominator is None:
            survivors.append(candidate)
            continue
        dominator.superseded_candidate_ids.append(candidate.clip_id)
        removed.append(
            {
                "reason": "duplicate",
                "detail": f"{candidate.clip_id} reemplazado por {dominator.clip_id}",
                "clip_id": candidate.clip_id,
            }
        )

    for survivor in survivors:
        survivor.superseded_candidate_ids = sorted(
            set(survivor.superseded_candidate_ids)
        )
    survivors.sort(key=_comparison_key)
    removed.sort(key=lambda item: str(item["clip_id"]))
    return survivors, removed


# ---------------------------------------------------------------------------
# Ordenamiento y limite
# ---------------------------------------------------------------------------

def rank_candidates(candidates: Sequence[EditorialCandidate]) -> List[EditorialCandidate]:
    """Asigna `rank` (prioridad de revision) de forma determinista.

    Sigue usando la confianza como prioridad de revision (no de calidad):
    es el orden sugerido para que una persona revise, no un juicio de valor.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (-c.proposal.confidence, c.proposal.start_seconds, c.clip_id),
    )
    for position, candidate in enumerate(ordered, start=1):
        candidate.rank = position
    return ordered


def apply_total_limit(
    candidates: Sequence[EditorialCandidate], *, max_total: int
) -> Tuple[List[EditorialCandidate], List[Dict[str, object]]]:
    """Recorta al maximo total. Devuelve (conservados, descartes)."""
    if max_total < 0:
        raise ValueError("max_total no puede ser negativo")
    kept = list(candidates[:max_total])
    removed = [
        {
            "reason": "total_limit",
            "detail": f"excede max_candidates_total={max_total}",
            "clip_id": c.clip_id,
        }
        for c in candidates[max_total:]
    ]
    return kept, removed
