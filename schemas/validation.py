"""Validacion contextual centralizada (Fase 1, objetivo 5).

Agrupa en un unico punto de entrada las comprobaciones que dependen del
proyecto concreto:

- limites temporales finitos y ordenados (`0 <= start < end`);
- limites dentro de la **duracion del archivo fuente**;
- referencias a segmentos de transcripcion existentes;
- consistencia del activo fuente (`source_asset_id`).

`ProjectContext` es un objeto **efimero**: se construye a partir de
`Project` y de los segmentos disponibles, se usa para validar y se
descarta. **No se persiste, no es un contrato y no añade campos a los
contratos existentes.** Tampoco duplica `duration_seconds`: la unica
fuente sigue siendo `Project.duration_seconds`, obtenida de FFprobe en la
Etapa A (plan de la Fase 1, decision D5/D6).

Ningun servicio puede olvidarse ya de validar contra la duracion real:
si necesita comprobar un clip o un segmento, pasa por aqui.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from .contracts import (
    ClipProposal,
    Project,
    TranscriptSegment,
    ValidationError,
    validate_time_range,
)


@dataclass(frozen=True)
class ProjectContext:
    """Contexto de validacion derivado de un proyecto."""

    source_asset_id: str
    duration_seconds: float
    segment_ids: frozenset = field(default_factory=frozenset)

    @classmethod
    def from_project(
        cls,
        project: Project,
        segments: Iterable[TranscriptSegment] = (),
    ) -> "ProjectContext":
        return cls(
            source_asset_id=project.source_asset_id,
            duration_seconds=float(project.duration_seconds),
            segment_ids=frozenset(segment.segment_id for segment in segments),
        )

    # ------------------------------------------------------------------
    # Segmentos
    # ------------------------------------------------------------------
    def segment_errors(self, segment: TranscriptSegment) -> List[str]:
        errors: List[str] = []
        try:
            validate_time_range(
                segment.start_seconds,
                segment.end_seconds,
                label=f"segment {segment.segment_id}",
                upper_bound_seconds=self.duration_seconds,
            )
        except ValidationError as exc:
            errors.append(str(exc))
        return errors

    def validate_segment(self, segment: TranscriptSegment) -> None:
        errors = self.segment_errors(segment)
        if errors:
            raise ValidationError("; ".join(errors))

    # ------------------------------------------------------------------
    # Clips
    # ------------------------------------------------------------------
    def clip_errors(self, clip: ClipProposal) -> List[str]:
        errors: List[str] = []
        try:
            validate_time_range(
                clip.start_seconds,
                clip.end_seconds,
                label=f"clip {clip.clip_id}",
                upper_bound_seconds=self.duration_seconds,
            )
        except ValidationError as exc:
            errors.append(str(exc))
        if clip.source_asset_id != self.source_asset_id:
            errors.append(
                f"clip {clip.clip_id} pertenece al activo {clip.source_asset_id!r}, "
                f"pero el proyecto usa {self.source_asset_id!r}"
            )
        missing = [ref for ref in clip.transcript_refs if ref not in self.segment_ids]
        if missing:
            errors.append(
                f"clip {clip.clip_id} referencia segmentos inexistentes: {missing}"
            )
        return errors

    def validate_clip(self, clip: ClipProposal) -> None:
        """Lanza `ValidationError` con **todas** las incidencias del clip."""
        errors = self.clip_errors(clip)
        if errors:
            raise ValidationError("; ".join(errors))


def validate_clip_for_project(
    clip: ClipProposal, project: Project, segments: Iterable[TranscriptSegment] = ()
) -> None:
    """Conveniencia: valida un clip contra un proyecto y sus segmentos."""
    ProjectContext.from_project(project, segments).validate_clip(clip)


def validate_segment_for_project(segment: TranscriptSegment, project: Project) -> None:
    """Conveniencia: valida un segmento contra la duracion del proyecto."""
    ProjectContext.from_project(project).validate_segment(segment)
