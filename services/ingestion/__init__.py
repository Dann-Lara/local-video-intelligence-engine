"""Etapa A --- Ingesta de archivos locales y metadatos con FFprobe.

API publica de la Fase 1:

    from services.ingestion import ingest_file, ingest_directory

Los errores derivan siempre de `IngestionError`.
"""

from .errors import (
    FFprobeFailedError,
    FFprobeNotFoundError,
    FFprobeTimeoutError,
    IngestionError,
    InsufficientSpaceError,
    InvalidSourceError,
    MetadataInvalidError,
    ProjectDirectoryConflictError,
    ProjectWriteError,
    SourceNotFoundError,
)
from .ffprobe import (
    DEFAULT_FFPROBE_EXECUTABLE,
    DEFAULT_TIMEOUT_SECONDS,
    FFprobeRunner,
    ffprobe_version,
    probe_media,
    resolve_ffprobe_executable,
    resolve_timeout,
)
from .ingest import (
    DirectoryIngestionResult,
    IngestionResult,
    compute_source_asset_id,
    default_input_dir,
    default_project_root,
    ingest_directory,
    ingest_file,
    list_source_files,
)
from .metadata import METADATA_KEYS, SCHEMA_VERSION, SourceMetadata, parse_probe

__all__ = [
    "DEFAULT_FFPROBE_EXECUTABLE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DirectoryIngestionResult",
    "FFprobeFailedError",
    "FFprobeNotFoundError",
    "FFprobeRunner",
    "FFprobeTimeoutError",
    "METADATA_KEYS",
    "IngestionError",
    "IngestionResult",
    "InsufficientSpaceError",
    "InvalidSourceError",
    "MetadataInvalidError",
    "ProjectDirectoryConflictError",
    "ProjectWriteError",
    "SCHEMA_VERSION",
    "SourceMetadata",
    "SourceNotFoundError",
    "compute_source_asset_id",
    "default_input_dir",
    "default_project_root",
    "ffprobe_version",
    "ingest_directory",
    "ingest_file",
    "list_source_files",
    "parse_probe",
    "probe_media",
    "resolve_ffprobe_executable",
    "resolve_timeout",
]
