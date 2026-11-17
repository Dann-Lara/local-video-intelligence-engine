"""Servicio de transcripción con faster-whisper."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from schemas.contracts import TranscriptSegment, Word
from services.ingestion.errors import InvalidSourceError

from .config import (
    TranscriptionConfig,
    get_default_model_dir,
    resolve_model_path,
)
from .errors import (
    TranscriptionError,
    TranscriptionInferenceError,
    TranscriptionInvalidAudioError,
    TranscriptionInvalidSegmentsError,
    TranscriptionModelError,
)
from .models import TranscriptionResult


class Transcriber:
    """Transcriptor basado en faster-whisper con carga perezosa del modelo."""

    def __init__(self, config: Optional[TranscriptionConfig] = None) -> None:
        self.config = config or TranscriptionConfig()
        self._model = None
        self._model_path = resolve_model_path(self.config.model_size_or_path)
        if not os.path.isdir(self._model_path):
            # Usar directorio por defecto si es nombre corto
            default_dir = get_default_model_dir()
            if os.path.isdir(default_dir):
                self._model_path = default_dir

    def _ensure_audio_exists(self, audio_path: Path) -> None:
        if not audio_path.exists() or not audio_path.is_file():
            raise TranscriptionInvalidAudioError(
                f"Audio no válido: {audio_path}", path=str(audio_path)
            )
        try:
            st = audio_path.stat()
            if st.st_size <= 0:
                raise TranscriptionInvalidAudioError(
                    f"Audio vacío: {audio_path}", path=str(audio_path)
                )
        except OSError as exc:
            raise TranscriptionInvalidAudioError(
                f"No se pudo leer audio: {exc}", path=str(audio_path)
            ) from exc

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                model_size_or_path=self._model_path,
                device=self.config.device,
                device_index=self.config.device_index,
                compute_type=self.config.compute_type,
                cpu_threads=self.config.cpu_threads,
                num_workers=self.config.num_workers,
                local_files_only=self.config.local_files_only,
                download_root=self.config.download_root,
            )
            return self._model
        except Exception as exc:
            raise TranscriptionModelError(
                f"No se pudo cargar el modelo faster-whisper: {exc}"
            ) from exc

    def transcribe_audio(
        self,
        audio_path: Path,
        *,
        project_id: str = "unknown",
        source_asset_id: str = "unknown",
        duration_seconds: Optional[float] = None,
    ) -> TranscriptionResult:
        """Transcribe un archivo de audio."""
        audio_path = Path(audio_path)
        self._ensure_audio_exists(audio_path)

        model = self._load_model()
        try:
            segments_iter, info = model.transcribe(
                str(audio_path),
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                language=self.config.language,
            )
            segments: List[TranscriptSegment] = []
            words: List[Word] = []
            seg_id = 0
            for seg in segments_iter:
                seg_id += 1
                start = float(seg.start)
                end = float(seg.end)
                text = (seg.text or "").strip()
                # Extraer palabras si existen
                seg_words: List[Word] = []
                if getattr(seg, "words", None):
                    for w in seg.words:
                        try:
                            wstart = float(getattr(w, "start", start))
                            wend = float(getattr(w, "end", end))
                            wtext = (getattr(w, "word", "") or "").strip()
                            if wtext:
                                seg_words.append(Word(start_seconds=wstart, end_seconds=wend, text=wtext))
                        except Exception:
                            continue
                segments.append(
                    TranscriptSegment(
                        segment_id=f"seg_{seg_id:04d}",
                        start_seconds=start,
                        end_seconds=end,
                        text=text,
                        words=seg_words,
                    )
                )
                words.extend(seg_words)
            dur = float(duration_seconds) if duration_seconds is not None else float(info.duration or 0.0)
            if dur <= 0:
                raise TranscriptionInvalidSegmentsError(
                    f"Duración inválida tras transcripción: {dur}"
                )
            return TranscriptionResult(
                project_id=project_id,
                source_asset_id=source_asset_id,
                model_size=self.config.model_size_or_path,
                device=self.config.device,
                compute_type=self.config.compute_type,
                language=getattr(info, "language", self.config.language),
                duration_seconds=dur,
                segments=segments,
                words=words,
                inference_completed=True,
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionInferenceError(
                f"Falló la inferencia de transcripción: {exc}"
            ) from exc
