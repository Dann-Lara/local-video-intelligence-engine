"""Modelo para representar resultados de transcripción."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from schemas.contracts import TranscriptSegment, Word, validate_time_range
from schemas.validation import ProjectContext, validate_segment_for_project


@dataclass
class TranscriptionResult:
    """Resultado completo de una transcripción para un proyecto."""

    project_id: str
    source_asset_id: str
    model_size: str
    device: str
    compute_type: str
    language: Optional[str]
    duration_seconds: float
    segments: List[TranscriptSegment] = field(default_factory=list)
    words: List[Word] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    # Huella de identidad (audio + config + motor). None solo en artefactos
    # legacy; la política de reutilización nunca acepta huella ausente.
    fingerprint: Optional[Dict[str, Any]] = None
    # True solo cuando la inferencia se consumió por completo. Un resultado
    # sin este marcador se considera incompleto y no se reutiliza. Default
    # False es la política segura ante artefactos parciales o legacy.
    inference_completed: bool = False

    def __post_init__(self) -> None:
        # Validaciones básicas
        if not self.project_id:
            raise ValueError("project_id requerido")
        if not self.source_asset_id:
            raise ValueError("source_asset_id requerido")
        duration = float(self.duration_seconds)
        if duration <= 0:
            raise ValueError(f"duration_seconds debe ser > 0, recibido {self.duration_seconds}")
        self.duration_seconds = duration

        if self.fingerprint is not None and not isinstance(self.fingerprint, dict):
            raise TypeError("fingerprint debe ser dict o None")
        if isinstance(self.fingerprint, dict) and not self.fingerprint:
            self.fingerprint = None
        if not isinstance(self.inference_completed, bool):
            raise TypeError("inference_completed debe ser bool")

        # Normalizar segmentos
        normalized: List[TranscriptSegment] = []
        for i, s in enumerate(self.segments):
            if isinstance(s, dict):
                s = TranscriptSegment.from_dict(s)
            if not isinstance(s, TranscriptSegment):
                raise TypeError(f"segment[{i}] debe ser TranscriptSegment o dict")
            normalized.append(s)
        self.segments = normalized

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_asset_id": self.source_asset_id,
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "segments": [s.to_dict() for s in self.segments],
            "words": [w.to_dict() for w in self.words] if hasattr(self, "words") else [],
            "created_at": self.created_at,
            "fingerprint": dict(self.fingerprint) if isinstance(self.fingerprint, dict) else None,
            "inference_completed": bool(self.inference_completed),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptionResult":
        return cls(
            project_id=data["project_id"],
            source_asset_id=data["source_asset_id"],
            model_size=data.get("model_size", ""),
            device=data.get("device", ""),
            compute_type=data.get("compute_type", ""),
            language=data.get("language"),
            duration_seconds=data["duration_seconds"],
            segments=data.get("segments", []),
            words=data.get("words", []),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            fingerprint=data.get("fingerprint"),
            inference_completed=data.get("inference_completed", False),
        )
