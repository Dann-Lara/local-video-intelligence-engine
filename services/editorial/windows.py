"""Construccion determinista de ventanas de analisis.

Una ventana es un rango CONTIGUO de segmentos (`body_ids`) mas, opcionalmente,
segmentos de contexto antes y despues. El contexto sirve para que el modelo
entienda la conversacion, pero NO es elegible: sus `segment_id` no pueden
formar parte del clip (lo impone el parser).

Reglas:

- Se acumula texto hasta `window_target_chars`; una ventana puede superarlo
  para no partir un segmento por la mitad. Un segmento individual mas grande
  que `window_hard_max_chars` no se trunca jamas: forma su propia ventana.
- Las ventanas consecutivas se solapan `overlap_segments` segmentos para no
  perder cortes que cruzan el limite. Si el solapamiento impidiera avanzar
  (ventana de un solo segmento), se desactiva para garantizar progreso.
- El resultado es determinista: mismos segmentos y config producen siempre
  las mismas ventanas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from schemas.contracts import TranscriptSegment


@dataclass(frozen=True)
class Window:
    """Ventana de analisis con cuerpo elegible y contexto no elegible."""

    window_id: str
    body_ids: List[str]
    context_before_ids: List[str] = field(default_factory=list)
    context_after_ids: List[str] = field(default_factory=list)

    @property
    def segment_ids(self) -> List[str]:
        return list(self.body_ids)

    @property
    def first_segment_id(self) -> str:
        return self.body_ids[0]

    @property
    def last_segment_id(self) -> str:
        return self.body_ids[-1]


def _text_len(segment: TranscriptSegment) -> int:
    return len(segment.text or "")


def build_windows(
    segments: Sequence[TranscriptSegment],
    *,
    target_chars: int,
    hard_max_chars: int,
    overlap_segments: int,
    context_segments: int,
) -> List[Window]:
    """Divide una lista ordenada de segmentos en ventanas contiguas."""
    if target_chars <= 0:
        raise ValueError("target_chars debe ser > 0")
    if hard_max_chars < target_chars:
        raise ValueError("hard_max_chars debe ser >= target_chars")
    if overlap_segments < 0:
        raise ValueError("overlap_segments no puede ser negativo")
    if context_segments < 0:
        raise ValueError("context_segments no puede ser negativo")

    n = len(segments)
    windows: List[Window] = []
    if n == 0:
        return windows

    i = 0
    index = 0
    while i < n:
        j = i
        chars = 0
        while j < n:
            seg_chars = _text_len(segments[j])
            if j > i and chars + seg_chars > target_chars:
                break
            chars += seg_chars
            j += 1
            if chars >= target_chars:
                break
            if chars >= hard_max_chars:
                break

        body = list(range(i, j))
        if j < n:
            next_i = j - overlap_segments
            if next_i <= i:
                next_i = j
        else:
            next_i = j

        before = list(range(max(0, i - context_segments), i))
        after = list(range(j, min(n, j + context_segments)))
        index += 1
        windows.append(
            Window(
                window_id=f"win_{index:04d}",
                body_ids=[segments[k].segment_id for k in body],
                context_before_ids=[segments[k].segment_id for k in before],
                context_after_ids=[segments[k].segment_id for k in after],
            )
        )
        i = next_i
    return windows


def index_segments(segments: Sequence[TranscriptSegment]) -> Dict[str, TranscriptSegment]:
    return {seg.segment_id: seg for seg in segments}
