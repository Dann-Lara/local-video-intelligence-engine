"""Transcript engine (blueprint secciones 14 y 15).

`whisper.py`    -> `transcript.json`, salida cruda e inmutable.
`normalize.py`  -> `normalized_transcript.json`, segunda representacion derivada.

El original no se modifica nunca. Todo lo demas se deriva de el y se puede
regenerar sin perdida.
"""

from __future__ import annotations

__all__ = ["normalize", "whisper"]
