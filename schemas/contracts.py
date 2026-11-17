"""Contratos de datos iniciales (Fase 0) para Local Video Studio.

Este modulo define unicamente los contratos minimos documentados en
BLUEPRINT.md (seccion 7) y AGENTS.md:

- Project
- TranscriptSegment (con Word opcional)
- ClipProposal
- Task

Reglas respetadas:
- Tiempos en segundos como numeros decimales.
- La duracion del archivo fuente, los limites del clip y los timestamps
  de transcripcion son conceptos distintos y se validan por separado.
- Los estados de tarea son los documentados:
  pending, running, succeeded, failed, cancelled.
- No se prohibe el solapamiento entre clips distintos.
- Los originales no se modifican: estos contratos solo describen datos.

No se agregan campos ni estados que no esten respaldados por los
documentos. Las decisiones que los documentos no resuelven se anotan
como PENDIENTE en los comentarios y en el README.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class ValidationError(ValueError):
    """Error de validacion de un contrato.

    Hereda de ValueError para que sea capturable como error explicito y
    visible (AGENTS.md, principio 10).
    """


# ---------------------------------------------------------------------------
# Estados y etapas documentados
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """Estados validos de una tarea (BLUEPRINT.md 7.4)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(str, Enum):
    """Estado de revision de una propuesta de clip.

    BLUEPRINT.md 7.3 usa "pending" como ejemplo y la Etapa E pide
    "aprobar, descartar o dejar pendiente". Los tres valores derivan de
    esa descripcion.
    """

    PENDING = "pending"
    APPROVED = "approved"
    DISCARDED = "discarded"


class Stage(str, Enum):
    """Etapas del pipeline.

    Los nombres corresponden a los servicios documentados en
    AGENTS.md 5 y BLUEPRINT.md 8 (ingestion, transcription,
    scene_analysis, editorial, clip_planning, rendering,
    quality_control, cleanup).
    """

    INGESTION = "ingestion"
    TRANSCRIPTION = "transcription"
    SCENE_ANALYSIS = "scene_analysis"
    EDITORIAL = "editorial"
    CLIP_PLANNING = "clip_planning"
    RENDERING = "rendering"
    QUALITY_CONTROL = "quality_control"
    CLEANUP = "cleanup"


# ---------------------------------------------------------------------------
# Utilidades de validacion
# ---------------------------------------------------------------------------

def _require_non_empty_str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} debe ser texto, se recibio {type(value).__name__}")
    if value.strip() == "":
        raise ValidationError(f"{name} no puede estar vacio")
    return value


def _require_number(value: Any, name: str) -> float:
    # bool es subclase de int: se rechaza explicitamente.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} debe ser un numero, se recibio {type(value).__name__}")
    number = float(value)
    # NaN e infinito se aceptarian como "numerico": todas las comparaciones
    # con nan dan False y saltarian los guards de validacion.
    if not math.isfinite(number):
        raise ValidationError(f"{name} debe ser finito, se recibio {number!r}")
    return number


def validate_time_range(
    start_seconds: Any,
    end_seconds: Any,
    *,
    label: str = "rango",
    upper_bound_seconds: Optional[Any] = None,
) -> tuple:
    """Valida 0 <= start < end y, si se da, end <= upper_bound.

    upper_bound_seconds representa la duracion del archivo fuente cuando
    se conoce. No es un limite propio del clip ni de la transcripcion.
    """
    start = _require_number(start_seconds, f"{label}.start_seconds")
    end = _require_number(end_seconds, f"{label}.end_seconds")

    if start < 0:
        raise ValidationError(f"{label}.start_seconds no puede ser negativo: {start}")
    if end <= start:
        raise ValidationError(
            f"{label} invalido: end_seconds ({end}) debe ser mayor que start_seconds ({start})"
        )
    if upper_bound_seconds is not None:
        upper = _require_number(upper_bound_seconds, "duration_seconds")
        if end > upper:
            raise ValidationError(
                f"{label}.end_seconds ({end}) excede la duracion del archivo fuente ({upper})"
            )
    return start, end


def _validate_iso8601(value: Any, name: str, *, required: bool = True) -> Optional[str]:
    """Valida una fecha ISO-8601.

    required=True (por defecto) rechaza None, cadenas vacias e ISO
    invalidas. required=False se usa para campos opcionales
    (Task.started_at / Task.finished_at), donde None es valido y se
    conserva.
    """
    if value is None:
        if required:
            raise ValidationError(f"{name} es obligatorio")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} debe ser una cadena ISO-8601")
    if value.strip() == "":
        raise ValidationError(f"{name} no puede estar vacio")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{name} no es una fecha ISO-8601 valida: {value!r}") from exc
    return value


def _require_str_list(value: Any, name: str) -> List[str]:
    """Valida explicitamente una coleccion de textos.

    Rechaza None, cadenas (evita el split silencioso en caracteres) y
    cualquier tipo no iterable acotado, siempre con ValidationError.
    """
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValidationError(f"{name} debe ser una lista, se recibio {type(value).__name__}")
    result: List[str] = []
    for index, item in enumerate(value):
        result.append(_require_non_empty_str(item, f"{name}[{index}]"))
    return result


def _require_mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{name} debe ser un diccionario")
    return dict(value)


# ---------------------------------------------------------------------------
# Word y TranscriptSegment
# ---------------------------------------------------------------------------

@dataclass
class Word:
    """Palabra con timestamps opcionales (BLUEPRINT.md 7.2).

    Los timestamps por palabra solo existen si la transcripcion los
    entrega; nunca deben simularse.
    """

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        validate_time_range(self.start_seconds, self.end_seconds, label="word")
        self.start_seconds = float(self.start_seconds)
        self.end_seconds = float(self.end_seconds)
        self.text = _require_non_empty_str(self.text, "word.text")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Word":
        return cls(
            start_seconds=data["start_seconds"],
            end_seconds=data["end_seconds"],
            text=data["text"],
        )


@dataclass
class TranscriptSegment:
    """Segmento de transcripcion (BLUEPRINT.md 7.2).

    `words` puede estar vacio. Cuando hay palabras, sus timestamps deben
    caer dentro de los limites del propio segmento (validacion automatica).

    La comprobacion `end_seconds <= duracion del archivo fuente` es
    explicita y depende del llamador: `validate_segment_against_source_duration`.
    """

    segment_id: str
    start_seconds: float
    end_seconds: float
    text: str
    words: List[Word] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.segment_id = _require_non_empty_str(self.segment_id, "segment_id")
        validate_time_range(self.start_seconds, self.end_seconds, label="segment")
        self.start_seconds = float(self.start_seconds)
        self.end_seconds = float(self.end_seconds)
        self.text = _require_non_empty_str(self.text, "segment.text")
        if self.words is None or isinstance(self.words, str) or not isinstance(
            self.words, (list, tuple)
        ):
            raise ValidationError(
                f"words debe ser una lista, se recibio {type(self.words).__name__}"
            )
        normalized_words: List[Word] = []
        for index, item in enumerate(self.words):
            if isinstance(item, Word):
                normalized_words.append(item)
            elif isinstance(item, dict):
                normalized_words.append(Word.from_dict(item))
            else:
                raise ValidationError(
                    f"words[{index}] debe ser un Word o un diccionario, "
                    f"se recibio {type(item).__name__}"
                )
        self.words = normalized_words
        for word in self.words:
            if word.start_seconds < self.start_seconds or word.end_seconds > self.end_seconds:
                raise ValidationError(
                    f"word ({word.start_seconds}, {word.end_seconds}) fuera de los limites "
                    f"del segmento {self.segment_id} ({self.start_seconds}, {self.end_seconds})"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptSegment":
        return cls(
            segment_id=data["segment_id"],
            start_seconds=data["start_seconds"],
            end_seconds=data["end_seconds"],
            text=data["text"],
            words=data.get("words", []),
        )


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class Project:
    """Proyecto asociado a un archivo fuente (BLUEPRINT.md 7.1).

    `duration_seconds` es la duracion del archivo fuente, no un limite de
    clip. PENDIENTE: los documentos muestran ejemplos ("ingested",
    "transcribing") pero no enumeran el conjunto completo de estados de
    proyecto; por eso `status` se valida solo como texto no vacio.
    """

    project_id: str
    source_asset_id: str
    status: str
    created_at: str
    source_path: str
    duration_seconds: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    tool_versions: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_id = _require_non_empty_str(self.project_id, "project_id")
        self.source_asset_id = _require_non_empty_str(self.source_asset_id, "source_asset_id")
        self.status = _require_non_empty_str(self.status, "status")
        self.created_at = _validate_iso8601(self.created_at, "created_at")
        self.source_path = _require_non_empty_str(self.source_path, "source_path")
        duration = _require_number(self.duration_seconds, "duration_seconds")
        if duration <= 0:
            raise ValidationError(
                f"duration_seconds debe ser mayor que cero, se recibio {duration}"
            )
        self.duration_seconds = duration
        self.metadata = _require_mapping(self.metadata, "metadata")
        self.tool_versions = _require_mapping(self.tool_versions, "tool_versions")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_asset_id": self.source_asset_id,
            "status": self.status,
            "created_at": self.created_at,
            "source_path": self.source_path,
            "duration_seconds": self.duration_seconds,
            "metadata": dict(self.metadata),
            "tool_versions": dict(self.tool_versions),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        return cls(
            project_id=data["project_id"],
            source_asset_id=data["source_asset_id"],
            status=data["status"],
            created_at=data["created_at"],
            source_path=data["source_path"],
            duration_seconds=data["duration_seconds"],
            metadata=data.get("metadata", {}),
            tool_versions=data.get("tool_versions", {}),
        )


# ---------------------------------------------------------------------------
# ClipProposal
# ---------------------------------------------------------------------------

@dataclass
class ClipProposal:
    """Propuesta editorial de clip (BLUEPRINT.md 7.3).

    Los limites del clip se validan automaticamente como 0 <= start < end.
    La comprobacion `end_seconds <= duracion del archivo fuente` NO se
    ejecuta sola: este contrato no duplica `duration_seconds` (BLUEPRINT
    7.3 no lo define) y por eso depende de que el llamador invoque
    `validate_clip_against_source_duration` cuando conozca la duracion
    real. Ver README, seccion sobre validacion explicita de la duracion.

    No se prohibe que dos clip distintos se solapen.
    """

    clip_id: str
    source_asset_id: str
    start_seconds: float
    end_seconds: float
    title: str
    hook: str = ""
    main_idea: str = ""
    ending: str = ""
    editorial_reason: str = ""
    transcript_refs: List[str] = field(default_factory=list)
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    review_status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        self.clip_id = _require_non_empty_str(self.clip_id, "clip_id")
        self.source_asset_id = _require_non_empty_str(self.source_asset_id, "source_asset_id")
        validate_time_range(self.start_seconds, self.end_seconds, label="clip")
        self.start_seconds = float(self.start_seconds)
        self.end_seconds = float(self.end_seconds)
        self.title = _require_non_empty_str(self.title, "title")
        for attr in ("hook", "main_idea", "ending", "editorial_reason"):
            value = getattr(self, attr)
            if not isinstance(value, str):
                raise ValidationError(f"{attr} debe ser texto")
        confidence = _require_number(self.confidence, "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValidationError(f"confidence debe estar entre 0 y 1, se recibio {confidence}")
        self.confidence = confidence
        self.transcript_refs = _require_str_list(self.transcript_refs, "transcript_refs")
        self.warnings = _require_str_list(self.warnings, "warnings")
        self.review_status = _coerce_review_status(self.review_status)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "source_asset_id": self.source_asset_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "title": self.title,
            "hook": self.hook,
            "main_idea": self.main_idea,
            "ending": self.ending,
            "editorial_reason": self.editorial_reason,
            "transcript_refs": list(self.transcript_refs),
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "review_status": self.review_status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClipProposal":
        return cls(
            clip_id=data["clip_id"],
            source_asset_id=data["source_asset_id"],
            start_seconds=data["start_seconds"],
            end_seconds=data["end_seconds"],
            title=data["title"],
            hook=data.get("hook", ""),
            main_idea=data.get("main_idea", ""),
            ending=data.get("ending", ""),
            editorial_reason=data.get("editorial_reason", ""),
            transcript_refs=data.get("transcript_refs", []),
            confidence=data.get("confidence", 0.0),
            warnings=data.get("warnings", []),
            review_status=data.get("review_status", ReviewStatus.PENDING),
        )


def _coerce_review_status(value: Any) -> ReviewStatus:
    if isinstance(value, ReviewStatus):
        return value
    try:
        return ReviewStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in ReviewStatus)
        raise ValidationError(
            f"review_status invalido: {value!r}. Valores permitidos: {valid}"
        ) from exc


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """Tarea de una etapa del pipeline (BLUEPRINT.md 7.4).

    Estados permitidos: pending, running, succeeded, failed, cancelled.
    """

    task_id: str
    project_id: str
    stage: Stage
    status: TaskStatus = TaskStatus.PENDING
    attempt_count: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    artifact_paths: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.task_id = _require_non_empty_str(self.task_id, "task_id")
        self.project_id = _require_non_empty_str(self.project_id, "project_id")
        self.stage = _coerce_stage(self.stage)
        self.status = _coerce_task_status(self.status)
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int):
            raise ValidationError("attempt_count debe ser un entero")
        if self.attempt_count < 0:
            raise ValidationError(
                f"attempt_count no puede ser negativo: {self.attempt_count}"
            )
        self.started_at = _validate_iso8601(self.started_at, "started_at", required=False)
        self.finished_at = _validate_iso8601(self.finished_at, "finished_at", required=False)
        if self.error_code is not None:
            self.error_code = _require_non_empty_str(self.error_code, "error_code")
        if self.error_message is not None:
            self.error_message = _require_non_empty_str(self.error_message, "error_message")
        self.artifact_paths = _require_str_list(self.artifact_paths, "artifact_paths")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "stage": self.stage.value,
            "status": self.status.value,
            "attempt_count": self.attempt_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "artifact_paths": list(self.artifact_paths),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(
            task_id=data["task_id"],
            project_id=data["project_id"],
            stage=data["stage"],
            status=data.get("status", TaskStatus.PENDING),
            attempt_count=data.get("attempt_count", 0),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            artifact_paths=data.get("artifact_paths", []),
        )


def _coerce_task_status(value: Any) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in TaskStatus)
        raise ValidationError(
            f"status invalido: {value!r}. Estados permitidos: {valid}"
        ) from exc


def _coerce_stage(value: Any) -> Stage:
    if isinstance(value, Stage):
        return value
    try:
        return Stage(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Stage)
        raise ValidationError(
            f"stage invalido: {value!r}. Etapas permitidas: {valid}"
        ) from exc


# ---------------------------------------------------------------------------
# Validaciones cruzadas
# ---------------------------------------------------------------------------

def validate_clip_against_source_duration(clip: ClipProposal, duration_seconds: Any) -> None:
    """Comprueba que el clip quede dentro de la duracion del archivo fuente."""
    validate_time_range(
        clip.start_seconds,
        clip.end_seconds,
        label=f"clip {clip.clip_id}",
        upper_bound_seconds=duration_seconds,
    )


def validate_segment_against_source_duration(
    segment: TranscriptSegment, duration_seconds: Any
) -> None:
    """Comprueba que el segmento quede dentro de la duracion del archivo fuente."""
    validate_time_range(
        segment.start_seconds,
        segment.end_seconds,
        label=f"segment {segment.segment_id}",
        upper_bound_seconds=duration_seconds,
    )


def validate_clip_transcript_refs(
    clip: ClipProposal, segments: Iterable[TranscriptSegment]
) -> None:
    """Comprueba que cada transcript_ref apunte a un segmento existente."""
    known = {seg.segment_id for seg in segments}
    missing = [ref for ref in clip.transcript_refs if ref not in known]
    if missing:
        raise ValidationError(
            f"clip {clip.clip_id} referencia segmentos inexistentes: {missing}"
        )


def validate_clip_source_asset(clip: ClipProposal, project: Project) -> None:
    """Comprueba que el clip pertenezca al activo fuente del proyecto."""
    if clip.source_asset_id != project.source_asset_id:
        raise ValidationError(
            f"clip {clip.clip_id} pertenece al activo {clip.source_asset_id}, "
            f"pero el proyecto usa {project.source_asset_id}"
        )
