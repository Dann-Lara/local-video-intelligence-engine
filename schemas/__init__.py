"""Contratos de datos iniciales (Fase 0) de Local Video Studio."""

from .contracts import (
    ClipProposal,
    Project,
    ReviewStatus,
    Stage,
    Task,
    TaskStatus,
    TranscriptSegment,
    ValidationError,
    Word,
    validate_clip_against_source_duration,
    validate_clip_source_asset,
    validate_clip_transcript_refs,
    validate_segment_against_source_duration,
    validate_time_range,
)

from .validation import (
    ProjectContext,
    validate_clip_for_project,
    validate_segment_for_project,
)

from .editorial import (
    EditorialCandidate,
    EditorialRun,
    EditorialSignal,
    SignalStatus,
    WindowRecord,
)

__all__ = [
    "ClipProposal",
    "EditorialCandidate",
    "EditorialRun",
    "EditorialSignal",
    "SignalStatus",
    "WindowRecord",
    "Project",
    "ProjectContext",
    "ReviewStatus",
    "Stage",
    "Task",
    "TaskStatus",
    "TranscriptSegment",
    "ValidationError",
    "Word",
    "validate_clip_against_source_duration",
    "validate_clip_for_project",
    "validate_clip_source_asset",
    "validate_clip_transcript_refs",
    "validate_segment_against_source_duration",
    "validate_segment_for_project",
    "validate_time_range",
]
