"""Interpretacion y validacion de los metadatos entregados por FFprobe.

Respeta la salvaguarda 5: los metadatos opcionales ausentes se guardan
como `None` o coleccion vacia; un archivo no se rechaza solo porque no
tenga audio o no declare FPS.

Respeta la salvaguarda 6: se rechazan archivos sin pista de video, sin
duracion utilizable, con duracion no finita o con duracion <= 0.

`duration_seconds` no se copia dentro de `Project.metadata` (BLUEPRINT
7.1 lo define como campo superior del proyecto); el artefacto
`source_metadata.json` si lo incluye porque ahi es donde se registran los
datos crudos derivados de FFprobe (BLUEPRINT 8).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import InvalidSourceError, MetadataInvalidError

SCHEMA_VERSION = 1

# Campos opcionales de Project.metadata (BLUEPRINT 7.1 + objetivos de la
# Fase 1: tamano, pistas y extension). Ausente -> None o recuento 0.
METADATA_KEYS = (
    "width",
    "height",
    "fps",
    "video_codec",
    "audio_codec",
    "size_bytes",
    "video_track_count",
    "audio_track_count",
    "has_audio",
    "extension",
)


@dataclass(frozen=True)
class SourceMetadata:
    """Metadatos normalizados de un archivo fuente."""

    duration_seconds: float
    metadata: Dict[str, Any]
    streams: List[Dict[str, Any]]
    container: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    def to_artifact_dict(
        self, *, source_asset_id: str, source_path: str, tool_versions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Representacion persistida como `source_metadata.json`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "source_asset_id": source_asset_id,
            "source_path": source_path,
            "duration_seconds": self.duration_seconds,
            "metadata": dict(self.metadata),
            "container": dict(self.container),
            "streams": [dict(stream) for stream in self.streams],
            "warnings": list(self.warnings),
            "tool_versions": dict(tool_versions),
        }


# ---------------------------------------------------------------------------
# Pequeñas conversiones tolerantes
# ---------------------------------------------------------------------------

def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> Optional[int]:
    number = _as_float(value)
    if number is None or not math.isfinite(number):
        return None
    if number != int(number):
        return None
    return int(number)


def _positive_int(value: Any) -> Optional[int]:
    number = _as_int(value)
    if number is None or number <= 0:
        return None
    return number


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _parse_rate(value: Any) -> Optional[float]:
    """Convierte '30000/1001' en 29.97. Devuelve None si no es util."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and "/" in value:
        left, _, right = value.partition("/")
        try:
            numerator, denominator = float(left), float(right)
        except ValueError:
            return None
        if not math.isfinite(numerator) or not math.isfinite(denominator):
            return None
        if denominator <= 0 or numerator <= 0:
            return None
        rate = numerator / denominator
    else:
        rate = _as_float(value)
        if rate is None or rate <= 0:
            return None
    if not math.isfinite(rate):
        return None
    return round(rate, 3)


def _is_attached_picture(stream: Dict[str, Any]) -> bool:
    disposition = stream.get("disposition")
    if isinstance(disposition, dict):
        return bool(disposition.get("attached_pic", 0))
    return False


def _codec_type(stream: Dict[str, Any]) -> Optional[str]:
    return _text(stream.get("codec_type"))


# ---------------------------------------------------------------------------
# Duracion
# ---------------------------------------------------------------------------

def _resolve_duration(
    streams: List[Dict[str, Any]], container: Dict[str, Any], source_path: Any
) -> float:
    """Devuelve una duracion utilizable o lanza `InvalidSourceError`.

    Candidatos en orden de prioridad: la duracion declarada por el
    contenedor y, despues, la mayor duracion declarada por las pistas.
    Solo se aceptan valores finitos mayores que cero (salvaguarda 6); si
    ninguno es util, el archivo se considera invalido.
    """
    candidates: List[Any] = [container.get("duration")]
    candidates.extend(stream.get("duration") for stream in streams)

    for value in candidates:
        duration = _usable_duration(value)
        if duration is not None:
            return duration

    raise InvalidSourceError(
        "El archivo no tiene una duracion utilizable (ausente, no finita o menor o igual a cero)",
        path=source_path,
        details={
            "candidatos": [repr(value) for value in candidates],
        },
    )


def _usable_duration(value: Any) -> Optional[float]:
    duration = _as_float(value)
    if duration is None or not math.isfinite(duration) or duration <= 0:
        return None
    return duration


# ---------------------------------------------------------------------------
# Parseo principal
# ---------------------------------------------------------------------------

def parse_probe(data: Any, *, source_path: Any) -> SourceMetadata:
    """Valida el JSON de FFprobe y extrae los metadatos normalizados."""
    if not isinstance(data, dict):
        raise MetadataInvalidError(
            f"Se esperaba un objeto JSON de FFprobe y se recibio {type(data).__name__}",
            path=source_path,
        )

    streams = data.get("streams")
    if not isinstance(streams, list):
        raise MetadataInvalidError(
            "FFprobe no devolvio la lista de pistas 'streams'", path=source_path,
            details={"claves": sorted(data.keys())},
        )
    if not streams:
        raise InvalidSourceError("El archivo no contiene ninguna pista", path=source_path)

    container_raw = data.get("format", {})
    if container_raw is None:
        container_raw = {}
    if not isinstance(container_raw, dict):
        raise MetadataInvalidError(
            f"FFprobe devolvio 'format' como {type(container_raw).__name__}",
            path=source_path,
        )

    duration = _resolve_duration(streams, container_raw, source_path)

    video_streams = [
        s for s in streams if _codec_type(s) == "video" and not _is_attached_picture(s)
    ]
    if not video_streams:
        tipos = sorted({_codec_type(s) or "desconocido" for s in streams})
        raise InvalidSourceError(
            "El archivo no tiene una pista de video utilizable",
            path=source_path,
            details={"tipos_de_pista": tipos},
        )
    audio_streams = [s for s in streams if _codec_type(s) == "audio"]

    primary_video = video_streams[0]
    size_bytes = _positive_int(container_raw.get("size"))
    if size_bytes is None:
        try:
            size_bytes = os.path.getsize(str(source_path))
        except OSError as exc:
            raise InvalidSourceError(
                f"No se pudo determinar el tamano del archivo: {exc}", path=source_path
            ) from exc

    extension = Path(str(source_path)).suffix.lower().lstrip(".") or None

    metadata: Dict[str, Any] = {
        "width": _positive_int(primary_video.get("width")),
        "height": _positive_int(primary_video.get("height")),
        "fps": _parse_rate(primary_video.get("avg_frame_rate"))
        or _parse_rate(primary_video.get("r_frame_rate")),
        "video_codec": _text(primary_video.get("codec_name")),
        "audio_codec": _text(audio_streams[0].get("codec_name")) if audio_streams else None,
        "size_bytes": size_bytes,
        "video_track_count": len(video_streams),
        "audio_track_count": len(audio_streams),
        "has_audio": bool(audio_streams),
        "extension": extension,
    }

    warnings: List[str] = []
    if not audio_streams:
        warnings.append("sin_pista_de_audio")

    container = {
        "format_name": _text(container_raw.get("format_name")),
        "format_long_name": _text(container_raw.get("format_long_name")),
        "bit_rate": _positive_int(container_raw.get("bit_rate")),
        "size_bytes": size_bytes,
    }

    normalized_streams = [
        {
            "index": _as_int(stream.get("index")),
            "codec_type": _codec_type(stream),
            "codec_name": _text(stream.get("codec_name")),
            "width": _positive_int(stream.get("width")),
            "height": _positive_int(stream.get("height")),
            "avg_frame_rate": _text(stream.get("avg_frame_rate")),
            "sample_rate": _text(stream.get("sample_rate")),
            "channels": _as_int(stream.get("channels")),
            "attached_pic": _is_attached_picture(stream),
        }
        for stream in streams
    ]

    return SourceMetadata(
        duration_seconds=duration,
        metadata=metadata,
        streams=normalized_streams,
        container=container,
        warnings=warnings,
    )

def load_source_metadata(project_dir):
    from pathlib import Path
    import json
    p = Path(project_dir) / 'source_metadata.json'
    if not p.is_file():
        from .errors import InvalidSourceError
        raise InvalidSourceError('No se encuentra source_metadata.json', path=str(p))
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        from .errors import MetadataInvalidError
        raise MetadataInvalidError('No se pudo leer source_metadata.json', path=str(p)) from exc
