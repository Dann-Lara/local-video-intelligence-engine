"""Parseo y validacion de la respuesta del modelo.

Cadena de confianza: el modelo propone, el codigo valida. Aqui se rechaza
todo lo que no este respaldado por la transcripcion real:

- el JSON debe ser valido y tener la forma esperada;
- cada propuesta referencia solo `segment_id` del CUERPO de la ventana;
- los limites temporales los calcula el codigo desde los segmentos referidos
  (nunca se aceptan timestamps del modelo);
- `evidence_text` debe ser subcadena literal del texto referido;
- la duracion debe caer en [min, max] (salvo excepcion explicita);
- el numero de propuestas por ventana se acota; el resto se registra como
  descarte auditable.

No se lanza excepcion por una propuesta invalida: se rechaza y se registra.
La falla de una ventana completa (JSON irrecuperable) si es un error.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from schemas.contracts import (
    ClipProposal,
    ReviewStatus,
    TranscriptSegment,
    validate_time_range,
)
from schemas.editorial import (
    AdClassification,
    EditorialCandidate,
    EditorialDecision,
    EditorialSignal,
    SignalStatus,
)

from .config import EditorialConfig
from .errors import EditorialInvalidResponseError

# Tolerancia para comparar el fin del clip con la duracion de la fuente.
_TIME_TOLERANCE = 0.01

_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normaliza texto para comparar evidencia de forma robusta y determinista."""
    if not isinstance(text, str):
        return ""
    return _WHITESPACE.sub(" ", text).strip().lower()


def extract_json_object(raw: str) -> Dict[str, Any]:
    """Extrae el objeto JSON de una respuesta, tolerando cercas de codigo."""
    if not isinstance(raw, str) or raw.strip() == "":
        raise EditorialInvalidResponseError("Respuesta del modelo vacia")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise EditorialInvalidResponseError(
            "La respuesta del modelo no contiene un objeto JSON"
        )
    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except Exception as exc:
        raise EditorialInvalidResponseError(
            f"El JSON del modelo no es valido: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise EditorialInvalidResponseError("El JSON del modelo no es un objeto")
    return parsed


def _build_signals(
    raw_signals: Any, referenced_text_norm: str
) -> Tuple[List[EditorialSignal], List[str]]:
    signals: List[EditorialSignal] = []
    limitations: List[str] = []
    if raw_signals is None:
        return signals, limitations
    if not isinstance(raw_signals, list):
        limitations.append("El modelo envio 'signals' con formato inesperado.")
        return signals, limitations
    for item in raw_signals:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name.strip() == "":
            continue
        status = item.get("status", "unknown")
        try:
            coerced = SignalStatus(str(status).strip().lower())
        except ValueError:
            coerced = SignalStatus.UNKNOWN
        evidence = item.get("evidence", "")
        note = item.get("note", "")
        if not isinstance(evidence, str):
            evidence = ""
        if not isinstance(note, str):
            note = ""
        # Una senal "observada" sin evidencia verificable se degrada: no se
        # afirma como observacion lo que no se puede comprobar en el texto.
        if coerced == SignalStatus.OBSERVED:
            if not evidence or normalize_text(evidence) not in referenced_text_norm:
                coerced = SignalStatus.UNKNOWN
                evidence = ""
                note = (note + " Evidencia no verificable; degradada a unknown.").strip()
        signals.append(
            EditorialSignal(name=name.strip(), status=coerced, evidence=evidence, note=note)
        )
    return signals, limitations


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


# Patrones de promocion EXPLICITA. Se usan como red de seguridad local: si el
# modelo no detecta el anuncio, el codigo no lo deja pasar como contenido
# organico. No son una lista de marcas ni sustituyen la clasificacion del
# modelo; solo reconocen llamadas a la accion literales.
_PROMO_PATTERNS = (
    re.compile(r"\bsponsor(?:ed)? (?:of )?this video\b", re.IGNORECASE),
    re.compile(r"\bdownload [^.!?]{1,80}? today\b", re.IGNORECASE),
    re.compile(r"\bbuy the book\b", re.IGNORECASE),
    re.compile(r"\bscan (?:this|the) qr code\b", re.IGNORECASE),
    re.compile(r"\b(?:click|use) the link in the description\b", re.IGNORECASE),
    re.compile(r"\blink in the description\b", re.IGNORECASE),
    re.compile(r"\b(?:promo|discount) code\b", re.IGNORECASE),
    re.compile(r"\buse code [a-z0-9]+\b", re.IGNORECASE),
    re.compile(r"\bpatrocinado por\b", re.IGNORECASE),
    re.compile(r"\benlace en la descripcion\b", re.IGNORECASE),
    re.compile(r"\bcodigo de descuento\b", re.IGNORECASE),
    re.compile(r"\bcompra (?:el|la) (?:libro|curso|producto)\b", re.IGNORECASE),
)

_AD_WITH_EVIDENCE = (
    AdClassification.PROMOTIONAL.value,
    AdClassification.SPONSORED_OR_INTEGRATED.value,
)


def detect_promotional_evidence(text: str) -> List[str]:
    """Devuelve las frases de promocion explicita halladas en `text`.

    Las coincidencias son subcadenas literales del texto de entrada; no se
    normalizan ni se reformulan. Un resultado vacio no demuestra que el
    fragmento no sea publicitario: solo que no contiene una CTA reconocible.
    """
    found: List[str] = []
    if not isinstance(text, str):
        return found
    for pattern in _PROMO_PATTERNS:
        for match in pattern.finditer(text):
            literal = match.group(0)
            if literal not in found:
                found.append(literal)
    return found


def _coerce_ad_classification(value: Any) -> str:
    if isinstance(value, AdClassification):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in tuple(item.value for item in AdClassification):
            return normalized
    return AdClassification.UNCERTAIN.value


def _coerce_editorial_decision(value: Any) -> str:
    if isinstance(value, EditorialDecision):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in tuple(item.value for item in EditorialDecision):
            return normalized
    return EditorialDecision.NEEDS_REVIEW.value


def _evaluate_boundary(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Coacciona `boundary_evaluation` y registra lo que falte.

    No se inventan descripciones ni se marcan limites como validos: solo se
    conservan los valores booleanos realmente enviados por el modelo.
    """
    limitations: List[str] = []
    if not isinstance(value, dict):
        limitations.append(
            "Sin `boundary_evaluation`: no se puede justificar el inicio/final."
        )
        return {}, limitations
    evaluation: Dict[str, Any] = {}
    for key in ("start", "end", "notes"):
        item = value.get(key)
        if isinstance(item, str):
            evaluation[key] = item
    for key in ("start_ok", "end_ok"):
        item = value.get(key)
        if isinstance(item, bool):
            evaluation[key] = item
    if "start_ok" not in evaluation or "end_ok" not in evaluation:
        limitations.append(
            "`boundary_evaluation` incompleto: falta start_ok o end_ok."
        )
    elif evaluation["start_ok"] is not True or evaluation["end_ok"] is not True:
        limitations.append(
            "La evaluacion de limites no valida el inicio y/o el final."
        )
    return evaluation, limitations


def _apply_ad_policy(
    *,
    classification: Any,
    ad_evidence: Any,
    decision: Any,
    decision_reason: Any,
    referenced_text: str,
    referenced_text_norm: str,
    boundary_evaluation: Dict[str, Any],
    boundary_limitations: List[str],
) -> Tuple[str, str, str, str, List[str]]:
    """Aplica la politica anti-anuncios y devuelve campos normalizados.

    Reglas:
    - `promotional` y `sponsored_or_integrated` nunca quedan en `accept`.
    - `uncertain` pasa a `needs_review`.
    - Una CTA literal detectada por el codigo prevalece sobre `none`/
      `incidental_mention`.
    - `accept` exige limites validados y evidencia publicitaria verificable.
    """
    limitations: List[str] = list(boundary_limitations)
    ad_classification = _coerce_ad_classification(classification)
    ad_evidence_text = ad_evidence if isinstance(ad_evidence, str) else ""
    reason = decision_reason if isinstance(decision_reason, str) else ""
    editorial_decision = _coerce_editorial_decision(decision)

    if ad_evidence_text.strip():
        if normalize_text(ad_evidence_text) not in referenced_text_norm:
            limitations.append(
                "`ad_evidence` no es subcadena literal de los segmentos "
                "referidos; se descarta."
            )
            ad_evidence_text = ""
        elif ad_classification in (
            AdClassification.NONE.value,
            AdClassification.INCIDENTAL_MENTION.value,
        ):
            limitations.append(
                "Hay `ad_evidence` verificable pero la clasificacion era "
                "organica; se degrada a uncertain."
            )
            ad_classification = AdClassification.UNCERTAIN.value

    detected = detect_promotional_evidence(referenced_text)
    if detected:
        if ad_classification not in _AD_WITH_EVIDENCE:
            # Solo se promueve a `promotional` si el modelo no habia
            # reconocido ya un tramo publicitario. Si ya reporto
            # `sponsored_or_integrated`, esa clasificacion es mas especifica
            # y se conserva.
            limitations.append(
                "El codigo detecto una llamada a la accion literal "
                f"({detected[0]!r}); se clasifica como promotional."
            )
            ad_classification = AdClassification.PROMOTIONAL.value
        if not ad_evidence_text:
            ad_evidence_text = detected[0]

    if ad_classification in _AD_WITH_EVIDENCE:
        if not ad_evidence_text:
            limitations.append(
                "Clasificacion publicitaria sin evidencia literal verificable; "
                "se degrada a uncertain."
            )
            ad_classification = AdClassification.UNCERTAIN.value
        else:
            editorial_decision = EditorialDecision.REJECT.value
            reason = (
                reason
                + " Fragmento publicitario: no elegible para render."
            ).strip()

    if ad_classification == AdClassification.UNCERTAIN.value:
        if editorial_decision == EditorialDecision.ACCEPT.value:
            limitations.append(
                "Clasificacion publicitaria incierta: se degrada a needs_review."
            )
        if editorial_decision != EditorialDecision.REJECT.value:
            editorial_decision = EditorialDecision.NEEDS_REVIEW.value

    if editorial_decision == EditorialDecision.ACCEPT.value:
        if boundary_evaluation.get("start_ok") is not True or boundary_evaluation.get(
            "end_ok"
        ) is not True:
            limitations.append(
                "No se acepta sin inicio y final validados: se degrada a "
                "needs_review."
            )
            editorial_decision = EditorialDecision.NEEDS_REVIEW.value

    if not reason:
        if editorial_decision == EditorialDecision.ACCEPT.value:
            reason = "Idea completa, limites validos y sin promocion."
        elif editorial_decision == EditorialDecision.REJECT.value:
            reason = "Fragmento no elegible (publicidad o falta de unidad)."
        else:
            reason = "Requiere revision humana antes de renderizar."

    return (
        ad_classification,
        ad_evidence_text,
        editorial_decision,
        reason,
        limitations,
    )


DIAGNOSTIC_SCHEMA_VERSION = "1.0"

# Campos basicos que se conservan en todo registro de rechazo.
_BASIC_REJECT_FIELDS = ("segment_ids", "evidence_text", "title")


def _reject(
    rejected: List[Dict[str, Any]],
    reason: str,
    detail: str,
    *,
    raw_candidate: Any = None,
    proposed_index: Optional[int] = None,
    computed: Optional[Dict[str, Any]] = None,
    body_bounds: Optional[Tuple[str, str]] = None,
) -> None:
    """Registra un rechazo auditable, con diagnostico aditivo y opcional.

    Los campos `reason` y `detail` se conservan exactamente como antes para
    no romper consumidores ni artefactos existentes. Los campos
    diagnosticos solo se anaden cuando hay un valor real disponible: nunca se
    inventan datos que el parser no haya calculado. Este registro NO altera
    los criterios de aceptacion; es puramente observacional.
    """
    entry: Dict[str, Any] = {"reason": reason, "detail": detail}
    if proposed_index is not None:
        entry["diagnostic_schema_version"] = DIAGNOSTIC_SCHEMA_VERSION
        entry["proposed_index"] = proposed_index
    if isinstance(raw_candidate, dict):
        seg_ids = raw_candidate.get("segment_ids")
        if isinstance(seg_ids, list):
            entry["segment_ids"] = seg_ids
        evidence = raw_candidate.get("evidence_text")
        if isinstance(evidence, str):
            entry["evidence_text"] = evidence
        title = raw_candidate.get("title")
        if isinstance(title, str):
            entry["title"] = title
    if computed:
        entry["computed"] = computed
    if body_bounds:
        entry["body_min"], entry["body_max"] = body_bounds
    rejected.append(entry)


def _computed_values(
    *,
    start: float,
    end: float,
    config: EditorialConfig,
    referenced_segment_count: int,
) -> Dict[str, Any]:
    """Valores calculados por el sistema, utiles para diagnosticar un rechazo."""
    return {
        "start_seconds": float(start),
        "end_seconds": float(end),
        "duration_seconds": float(end) - float(start),
        "clip_min": float(config.clip_min_seconds),
        "clip_target": float(config.clip_target_seconds),
        "clip_max": float(config.clip_max_seconds),
        "referenced_segment_count": int(referenced_segment_count),
    }


def _parse_one(
    raw: Dict[str, Any],
    *,
    source_asset_id: str,
    window_id: str,
    body_set: set,
    segments_by_id: Dict[str, TranscriptSegment],
    config: EditorialConfig,
    duration_seconds: float,
    clip_id_factory: Callable[[], str],
    rejected: List[Dict[str, Any]],
    proposed_index: Optional[int] = None,
    body_bounds: Optional[Tuple[str, str]] = None,
) -> EditorialCandidate | None:
    def _diag(reason: str, detail: str, computed: Optional[Dict[str, Any]] = None) -> None:
        _reject(
            rejected,
            reason,
            detail,
            raw_candidate=raw,
            proposed_index=proposed_index,
            computed=computed,
            body_bounds=body_bounds,
        )

    seg_ids_raw = raw.get("segment_ids")
    if not isinstance(seg_ids_raw, list) or not seg_ids_raw:
        _diag("missing_segment_ids", "propuesta sin segment_ids")
        return None
    refs: List[str] = []
    for ref in seg_ids_raw:
        if not isinstance(ref, str) or ref.strip() == "":
            _diag("invalid_segment_id", f"segment_id invalido: {ref!r}")
            return None
        if ref not in body_set:
            _diag(
                "segment_out_of_body",
                f"el segment_id {ref!r} no pertenece al cuerpo de la ventana",
            )
            return None
        if ref not in refs:
            refs.append(ref)

    referenced: List[TranscriptSegment] = [segments_by_id[r] for r in refs]
    ordered = sorted(referenced, key=lambda s: (s.start_seconds, s.segment_id))
    start = min(s.start_seconds for s in ordered)
    end = max(s.end_seconds for s in ordered)
    computed = _computed_values(
        start=start, end=end, config=config, referenced_segment_count=len(refs)
    )

    try:
        validate_time_range(
            start, end, label="clip propuesto", upper_bound_seconds=duration_seconds
        )
    except Exception as exc:
        _diag("invalid_time_range", str(exc), computed)
        return None

    title = raw.get("title")
    if not isinstance(title, str) or title.strip() == "":
        _diag("missing_title", "propuesta sin titulo", computed)
        return None

    evidence = raw.get("evidence_text", "")
    if not isinstance(evidence, str) or evidence.strip() == "":
        _diag("missing_evidence", "propuesta sin evidence_text", computed)
        return None
    referenced_text_norm = normalize_text(" ".join(s.text for s in ordered))
    if normalize_text(evidence) not in referenced_text_norm:
        _diag(
            "evidence_not_found",
            "evidence_text no es subcadena del texto de los segmentos referidos",
            computed,
        )
        return None

    duration = end - start
    warnings: List[str] = []
    limitations: List[str] = []
    if duration < config.clip_min_seconds or duration > config.clip_max_seconds:
        if not config.allow_duration_exception:
            _diag(
                "duration_out_of_range",
                f"duracion {duration:.3f}s fuera de "
                f"[{config.clip_min_seconds}, {config.clip_max_seconds}]",
                computed,
            )
            return None
        warnings.append(
            f"Duracion {duration:.3f}s fuera de "
            f"[{config.clip_min_seconds}, {config.clip_max_seconds}] (excepcion permitida)."
        )
        limitations.append("Duracion fuera del rango objetivo; requiere revision humana.")

    signals, signal_limits = _build_signals(raw.get("signals"), referenced_text_norm)
    limitations.extend(signal_limits)

    boundary_evaluation, boundary_limits = _evaluate_boundary(
        raw.get("boundary_evaluation")
    )

    (
        ad_classification,
        ad_evidence,
        editorial_decision,
        decision_reason,
        ad_limits,
    ) = _apply_ad_policy(
        classification=raw.get("ad_classification"),
        ad_evidence=raw.get("ad_evidence"),
        decision=raw.get("editorial_decision"),
        decision_reason=raw.get("editorial_decision_reason"),
        referenced_text=" ".join(s.text for s in ordered),
        referenced_text_norm=referenced_text_norm,
        boundary_evaluation=boundary_evaluation,
        boundary_limitations=boundary_limits,
    )
    limitations.extend(ad_limits)

    context_dependency = raw.get("context_dependency", "unknown")

    def _str_field(name: str) -> str:
        value = raw.get(name, "")
        return value if isinstance(value, str) else ""

    proposal = ClipProposal(
        clip_id=clip_id_factory(),
        source_asset_id=source_asset_id,
        start_seconds=start,
        end_seconds=end,
        title=title.strip(),
        hook=_str_field("hook"),
        main_idea=_str_field("main_idea"),
        ending=_str_field("ending"),
        editorial_reason=_str_field("editorial_reason"),
        transcript_refs=refs,
        confidence=_coerce_confidence(raw.get("confidence")),
        warnings=warnings,
        review_status=ReviewStatus.PENDING,
    )
    try:
        return EditorialCandidate(
            proposal=proposal,
            duration_seconds=duration,
            evidence_text=evidence,
            signals=signals,
            context_dependency=context_dependency,
            boundary_quality={},
            limitations=limitations,
            originated_from_window=window_id,
            superseded_candidate_ids=[],
            rank=0,
            ad_classification=ad_classification,
            ad_evidence=ad_evidence,
            editorial_decision=editorial_decision,
            editorial_decision_reason=decision_reason,
            boundary_evaluation=boundary_evaluation,
        )
    except Exception as exc:
        _diag("candidate_contract_error", str(exc), computed)
        return None


def parse_model_response(
    raw: str,
    *,
    source_asset_id: str,
    window_id: str,
    body_ids: Sequence[str],
    segments_by_id: Dict[str, TranscriptSegment],
    config: EditorialConfig,
    duration_seconds: float,
    clip_id_factory: Callable[[], str],
) -> Tuple[List[EditorialCandidate], List[Dict[str, Any]]]:
    """Convierte la respuesta del modelo en candidatos validados + descartes."""
    parsed = extract_json_object(raw)
    candidates_raw = parsed.get("candidates")
    rejected: List[Dict[str, Any]] = []
    if candidates_raw is None:
        _reject(rejected, "missing_candidates", "respuesta sin clave 'candidates'")
        return [], rejected
    if not isinstance(candidates_raw, list):
        raise EditorialInvalidResponseError("'candidates' no es una lista")

    body_set = set(body_ids)
    body_bounds = (
        (body_ids[0], body_ids[-1]) if len(body_ids) >= 1 else None
    )
    accepted: List[EditorialCandidate] = []
    for index, raw_candidate in enumerate(candidates_raw):
        if len(accepted) >= config.max_candidates_per_window:
            _reject(
                rejected,
                "window_limit",
                f"se supero max_candidates_per_window="
                f"{config.max_candidates_per_window}",
                raw_candidate=raw_candidate,
                proposed_index=index,
                body_bounds=body_bounds,
            )
            break
        if not isinstance(raw_candidate, dict):
            _reject(
                rejected,
                "invalid_candidate",
                "candidato no es un objeto",
                proposed_index=index,
                body_bounds=body_bounds,
            )
            continue
        candidate = _parse_one(
            raw_candidate,
            source_asset_id=source_asset_id,
            window_id=window_id,
            body_set=body_set,
            segments_by_id=segments_by_id,
            config=config,
            duration_seconds=duration_seconds,
            clip_id_factory=clip_id_factory,
            rejected=rejected,
            proposed_index=index,
            body_bounds=body_bounds,
        )
        if candidate is not None:
            accepted.append(candidate)
    return accepted, rejected
