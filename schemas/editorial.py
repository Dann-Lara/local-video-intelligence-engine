"""Contratos editoriales de la Fase 3, construidos sobre los existentes.

Decision de diseno (documentada tambien en README): NO se crea un contrato
de clip paralelo. `EditorialCandidate` EMBEBE un `ClipProposal` (Fase 0) y
anade unicamente los campos de analisis que la Fase 3 necesita:

- duracion calculada por el sistema,
- evidencia literal verificable,
- senales editoriales (observed/inferred/unknown),
- dependencia del contexto anterior,
- calidad de los limites temporales,
- limitaciones conocidas,
- trazabilidad a la ventana de origen y a los candidatos reemplazados.

Al reutilizar `ClipProposal` se heredan `review_status` (human-in-the-loop),
`transcript_refs` y la validacion temporal de `ProjectContext.validate_clip`.

`EditorialRun` es el sobre de ejecucion persistido en `candidates.json`
(version de esquema, huella de identidad, motor, configuracion efectiva,
ventanas procesadas, seleccion y candidatos).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .contracts import (
    ClipProposal,
    ValidationError,
    _require_mapping,
    _require_non_empty_str,
    _require_number,
    _require_str_list,
)


class SignalStatus(str, Enum):
    """Origen de una senal editorial.

    - OBSERVED: evidencia directamente observable en el texto de la
      transcripcion.
    - INFERRED: interpretacion editorial del modelo.
    - UNKNOWN: no puede evaluarse con la informacion disponible.
    """

    OBSERVED = "observed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class AdClassification(str, Enum):
    """Clasificacion publicitaria de un fragmento.

    - NONE: no menciona productos, marcas ni servicios.
    - INCIDENTAL_MENTION: menciona una marca o producto como parte de la
      historia, sin promocion, llamada a la accion ni oferta.
    - PROMOTIONAL: promociona explicitamente un producto, servicio o marca
      (descarga, compra, codigo, enlace, oferta).
    - SPONSORED_OR_INTEGRATED: tramo patrocinado o anuncio integrado.
    - UNCERTAIN: la evidencia no permite decidir.
    """

    NONE = "none"
    INCIDENTAL_MENTION = "incidental_mention"
    PROMOTIONAL = "promotional"
    SPONSORED_OR_INTEGRATED = "sponsored_or_integrated"
    UNCERTAIN = "uncertain"


class EditorialDecision(str, Enum):
    """Decision editorial propuesta por el modelo, validada por el codigo."""

    ACCEPT = "accept"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"


_AD_CLASSIFICATIONS = tuple(item.value for item in AdClassification)
_EDITORIAL_DECISIONS = tuple(item.value for item in EditorialDecision)
_CONTEXT_DEPENDENCY_LEVELS = ("low", "medium", "high", "unknown")


def _coerce_ad_classification(value: Any) -> str:
    """Coacciona a una clasificacion valida; lo desconocido es `uncertain`."""
    if isinstance(value, AdClassification):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _AD_CLASSIFICATIONS:
            return normalized
    return AdClassification.UNCERTAIN.value


def _coerce_editorial_decision(value: Any) -> str:
    """Coacciona a una decision valida; lo desconocido es `needs_review`."""
    if isinstance(value, EditorialDecision):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _EDITORIAL_DECISIONS:
            return normalized
    return EditorialDecision.NEEDS_REVIEW.value


def _coerce_signal_status(value: Any) -> SignalStatus:
    if isinstance(value, SignalStatus):
        return value
    if isinstance(value, str):
        try:
            return SignalStatus(value.strip().lower())
        except ValueError:
            return SignalStatus.UNKNOWN
    return SignalStatus.UNKNOWN


def _coerce_context_dependency(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in _CONTEXT_DEPENDENCY_LEVELS:
        return value.strip().lower()
    return "unknown"


# ---------------------------------------------------------------------------
# Senal editorial
# ---------------------------------------------------------------------------

@dataclass
class EditorialSignal:
    """Senal editorial evaluada, con su evidencia textual.

    `evidence` puede ser vacia para senales `unknown`; para `observed` se
    exige evidencia (lo comprueba el constructor).
    """

    name: str
    status: SignalStatus = SignalStatus.UNKNOWN
    evidence: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        self.name = _require_non_empty_str(self.name, "signal.name")
        self.status = _coerce_signal_status(self.status)
        if not isinstance(self.evidence, str):
            raise ValidationError("signal.evidence debe ser texto")
        if not isinstance(self.note, str):
            raise ValidationError("signal.note debe ser texto")
        if self.status == SignalStatus.OBSERVED and self.evidence.strip() == "":
            raise ValidationError(
                f"la senal observada {self.name!r} requiere evidencia textual"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence": self.evidence,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditorialSignal":
        return cls(
            name=data["name"],
            status=data.get("status", SignalStatus.UNKNOWN),
            evidence=data.get("evidence", ""),
            note=data.get("note", ""),
        )


# ---------------------------------------------------------------------------
# Candidato editorial
# ---------------------------------------------------------------------------

@dataclass
class EditorialCandidate:
    """Propuesta editorial: un `ClipProposal` mas su analisis.

    `duration_seconds` la calcula SIEMPRE el sistema a partir de
    `proposal.end_seconds - proposal.start_seconds`; el constructor verifica
    que el valor recibido coincida con ese calculo (tolerancia 1e-3).
    """

    proposal: ClipProposal
    duration_seconds: float
    evidence_text: str
    signals: List[EditorialSignal] = field(default_factory=list)
    context_dependency: str = "unknown"
    boundary_quality: Dict[str, Any] = field(default_factory=dict)
    limitations: List[str] = field(default_factory=list)
    originated_from_window: str = ""
    superseded_candidate_ids: List[str] = field(default_factory=list)
    rank: int = 0
    # Filtro anti-anuncios y decision editorial (v3). Los valores por defecto
    # son deliberadamente conservadores para que los artefactos antiguos
    # (sin estos campos) NO sean elegibles para render sin revision.
    ad_classification: str = AdClassification.UNCERTAIN.value
    ad_evidence: str = ""
    editorial_decision: str = EditorialDecision.NEEDS_REVIEW.value
    editorial_decision_reason: str = ""
    boundary_evaluation: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.proposal, dict):
            self.proposal = ClipProposal.from_dict(self.proposal)
        if not isinstance(self.proposal, ClipProposal):
            raise ValidationError("proposal debe ser un ClipProposal o un diccionario")

        computed = float(self.proposal.end_seconds) - float(self.proposal.start_seconds)
        provided = _require_number(self.duration_seconds, "duration_seconds")
        if abs(provided - computed) > 1e-3:
            raise ValidationError(
                "duration_seconds no coincide con end_seconds - start_seconds "
                f"({provided} != {computed})"
            )
        self.duration_seconds = computed

        self.evidence_text = _require_non_empty_str(self.evidence_text, "evidence_text")

        normalized: List[EditorialSignal] = []
        for index, item in enumerate(self.signals):
            if isinstance(item, EditorialSignal):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(EditorialSignal.from_dict(item))
            else:
                raise ValidationError(
                    f"signals[{index}] debe ser EditorialSignal o diccionario"
                )
        self.signals = normalized

        self.context_dependency = _coerce_context_dependency(self.context_dependency)
        self.boundary_quality = _require_mapping(self.boundary_quality, "boundary_quality")
        self.limitations = _require_str_list(self.limitations, "limitations")
        self.originated_from_window = _require_non_empty_str(
            self.originated_from_window, "originated_from_window"
        )
        self.superseded_candidate_ids = _require_str_list(
            self.superseded_candidate_ids, "superseded_candidate_ids"
        )
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise ValidationError("rank debe ser un entero")
        if self.rank < 0:
            raise ValidationError(f"rank no puede ser negativo: {self.rank}")

        self.ad_classification = _coerce_ad_classification(self.ad_classification)
        self.editorial_decision = _coerce_editorial_decision(self.editorial_decision)
        if not isinstance(self.ad_evidence, str):
            raise ValidationError("ad_evidence debe ser texto")
        if not isinstance(self.editorial_decision_reason, str):
            raise ValidationError("editorial_decision_reason debe ser texto")
        self.boundary_evaluation = _require_mapping(
            self.boundary_evaluation, "boundary_evaluation"
        )
        self._apply_ad_gate()

    def _apply_ad_gate(self) -> None:
        """Impide que una propuesta publicitaria quede como `accept`.

        El modelo propone; el codigo impone la politica. La degradacion queda
        registrada en `limitations` para que sea visible en el artefacto.
        """
        if self.editorial_decision == EditorialDecision.ACCEPT.value:
            reason = ""
            if self.ad_classification in (
                AdClassification.PROMOTIONAL.value,
                AdClassification.SPONSORED_OR_INTEGRATED.value,
            ):
                self.editorial_decision = EditorialDecision.REJECT.value
                reason = (
                    "Clasificacion publicitaria "
                    f"{self.ad_classification!r}: no se acepta para render."
                )
            elif self.ad_classification == AdClassification.UNCERTAIN.value:
                self.editorial_decision = EditorialDecision.NEEDS_REVIEW.value
                reason = (
                    "Clasificacion publicitaria incierta: requiere revision humana "
                    "antes de renderizar."
                )
            else:
                start_ok = self.boundary_evaluation.get("start_ok")
                end_ok = self.boundary_evaluation.get("end_ok")
                if start_ok is False or end_ok is False:
                    self.editorial_decision = EditorialDecision.NEEDS_REVIEW.value
                    reason = (
                        "La evaluacion de limites marca un inicio o un final no "
                        "valido: requiere revision humana."
                    )
            if reason:
                self.editorial_decision_reason = (
                    self.editorial_decision_reason + " " + reason
                ).strip()
                if reason not in self.limitations:
                    self.limitations.append(reason)

    @property
    def clip_id(self) -> str:
        return self.proposal.clip_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "duration_seconds": self.duration_seconds,
            "evidence_text": self.evidence_text,
            "signals": [s.to_dict() for s in self.signals],
            "context_dependency": self.context_dependency,
            "boundary_quality": dict(self.boundary_quality),
            "limitations": list(self.limitations),
            "originated_from_window": self.originated_from_window,
            "superseded_candidate_ids": list(self.superseded_candidate_ids),
            "rank": self.rank,
            "ad_classification": self.ad_classification,
            "ad_evidence": self.ad_evidence,
            "editorial_decision": self.editorial_decision,
            "editorial_decision_reason": self.editorial_decision_reason,
            "boundary_evaluation": dict(self.boundary_evaluation),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditorialCandidate":
        return cls(
            proposal=data["proposal"],
            duration_seconds=data["duration_seconds"],
            evidence_text=data["evidence_text"],
            signals=data.get("signals", []),
            context_dependency=data.get("context_dependency", "unknown"),
            boundary_quality=data.get("boundary_quality", {}),
            limitations=data.get("limitations", []),
            originated_from_window=data.get("originated_from_window", ""),
            superseded_candidate_ids=data.get("superseded_candidate_ids", []),
            rank=data.get("rank", 0),
            ad_classification=data.get("ad_classification", AdClassification.UNCERTAIN.value),
            ad_evidence=data.get("ad_evidence", ""),
            editorial_decision=data.get(
                "editorial_decision", EditorialDecision.NEEDS_REVIEW.value
            ),
            editorial_decision_reason=data.get("editorial_decision_reason", ""),
            boundary_evaluation=data.get("boundary_evaluation", {}),
        )


# ---------------------------------------------------------------------------
# Registro de ventana
# ---------------------------------------------------------------------------

@dataclass
class WindowRecord:
    """Resultado del procesamiento de una ventana de analisis."""

    window_id: str
    first_segment_id: str
    last_segment_id: str
    segment_ids: List[str] = field(default_factory=list)
    status: str = "succeeded"
    attempts: int = 0
    candidate_count: int = 0
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        self.window_id = _require_non_empty_str(self.window_id, "window_id")
        self.first_segment_id = _require_non_empty_str(
            self.first_segment_id, "first_segment_id"
        )
        self.last_segment_id = _require_non_empty_str(
            self.last_segment_id, "last_segment_id"
        )
        self.segment_ids = _require_str_list(self.segment_ids, "segment_ids")
        if self.status not in ("succeeded", "failed"):
            raise ValidationError(
                f"status de ventana invalido: {self.status!r} "
                "(permitido: succeeded, failed)"
            )
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int):
            raise ValidationError("attempts debe ser un entero")
        if self.attempts < 0:
            raise ValidationError("attempts no puede ser negativo")
        if isinstance(self.candidate_count, bool) or not isinstance(self.candidate_count, int):
            raise ValidationError("candidate_count debe ser un entero")
        if self.candidate_count < 0:
            raise ValidationError("candidate_count no puede ser negativo")
        cleaned: List[Dict[str, Any]] = []
        for index, item in enumerate(self.rejected):
            mapping = _require_mapping(item, f"rejected[{index}]")
            _require_non_empty_str(mapping.get("reason"), f"rejected[{index}].reason")
            mapping.setdefault("detail", "")
            cleaned.append(mapping)
        self.rejected = cleaned
        if self.error_code is not None:
            self.error_code = _require_non_empty_str(self.error_code, "error_code")
        if self.error_message is not None:
            self.error_message = _require_non_empty_str(self.error_message, "error_message")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "first_segment_id": self.first_segment_id,
            "last_segment_id": self.last_segment_id,
            "segment_ids": list(self.segment_ids),
            "status": self.status,
            "attempts": self.attempts,
            "candidate_count": self.candidate_count,
            "rejected": [dict(item) for item in self.rejected],
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowRecord":
        return cls(
            window_id=data["window_id"],
            first_segment_id=data["first_segment_id"],
            last_segment_id=data["last_segment_id"],
            segment_ids=data.get("segment_ids", []),
            status=data.get("status", "succeeded"),
            attempts=data.get("attempts", 0),
            candidate_count=data.get("candidate_count", 0),
            rejected=data.get("rejected", []),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )


# ---------------------------------------------------------------------------
# Sobre de ejecucion editorial
# ---------------------------------------------------------------------------

@dataclass
class EditorialRun:
    """Resultado completo de una ejecucion de analisis editorial."""

    project_id: str
    source_asset_id: str
    duration_seconds: float
    identity: Dict[str, Any]
    engine: Dict[str, Any]
    effective_config: Dict[str, Any]
    windows: List[WindowRecord] = field(default_factory=list)
    candidates: List[EditorialCandidate] = field(default_factory=list)
    selection: Dict[str, Any] = field(default_factory=dict)
    editorial_schema_version: str = "1.0"
    status: str = "succeeded"
    created_at: str = ""

    def __post_init__(self) -> None:
        self.project_id = _require_non_empty_str(self.project_id, "project_id")
        self.source_asset_id = _require_non_empty_str(self.source_asset_id, "source_asset_id")
        duration = _require_number(self.duration_seconds, "duration_seconds")
        if duration <= 0:
            raise ValidationError(
                f"duration_seconds debe ser > 0, se recibio {duration}"
            )
        self.duration_seconds = duration
        self.editorial_schema_version = _require_non_empty_str(
            self.editorial_schema_version, "editorial_schema_version"
        )
        self.identity = _require_mapping(self.identity, "identity")
        self.engine = _require_mapping(self.engine, "engine")
        self.effective_config = _require_mapping(self.effective_config, "effective_config")
        self.selection = _require_mapping(self.selection, "selection")
        self.status = _require_non_empty_str(self.status, "status")
        self.created_at = _require_non_empty_str(self.created_at, "created_at")

        normalized_windows: List[WindowRecord] = []
        for index, item in enumerate(self.windows):
            if isinstance(item, WindowRecord):
                normalized_windows.append(item)
            elif isinstance(item, dict):
                normalized_windows.append(WindowRecord.from_dict(item))
            else:
                raise ValidationError(f"windows[{index}] debe ser WindowRecord o diccionario")
        self.windows = normalized_windows

        normalized_candidates: List[EditorialCandidate] = []
        for index, item in enumerate(self.candidates):
            if isinstance(item, EditorialCandidate):
                normalized_candidates.append(item)
            elif isinstance(item, dict):
                normalized_candidates.append(EditorialCandidate.from_dict(item))
            else:
                raise ValidationError(
                    f"candidates[{index}] debe ser EditorialCandidate o diccionario"
                )
        self.candidates = normalized_candidates

    def candidate_by_id(self, clip_id: str) -> Optional[EditorialCandidate]:
        for candidate in self.candidates:
            if candidate.clip_id == clip_id:
                return candidate
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "editorial_schema_version": self.editorial_schema_version,
            "project_id": self.project_id,
            "source_asset_id": self.source_asset_id,
            "duration_seconds": self.duration_seconds,
            "identity": dict(self.identity),
            "engine": dict(self.engine),
            "effective_config": dict(self.effective_config),
            "windows": [w.to_dict() for w in self.windows],
            "candidates": [c.to_dict() for c in self.candidates],
            "selection": dict(self.selection),
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditorialRun":
        return cls(
            project_id=data["project_id"],
            source_asset_id=data["source_asset_id"],
            duration_seconds=data["duration_seconds"],
            identity=data.get("identity", {}),
            engine=data.get("engine", {}),
            effective_config=data.get("effective_config", {}),
            windows=data.get("windows", []),
            candidates=data.get("candidates", []),
            selection=data.get("selection", {}),
            editorial_schema_version=data.get("editorial_schema_version", "1.0"),
            status=data.get("status", "succeeded"),
            created_at=data.get("created_at", ""),
        )


__all__ = [
    "AdClassification",
    "EditorialCandidate",
    "EditorialDecision",
    "EditorialRun",
    "EditorialSignal",
    "SignalStatus",
    "WindowRecord",
]
