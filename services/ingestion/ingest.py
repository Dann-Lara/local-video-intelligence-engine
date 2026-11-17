"""Etapa A: ingesta de archivos locales y extraccion de metadatos.

Flujo (`ingest_file`):

1. Validar que la fuente existe, es un archivo regular, no esta vacio y
   es legible.
2. Comprobar espacio libre en el volumen de destino.
3. Ejecutar FFprobe y normalizar los metadatos.
4. Calcular el identificador estable del activo (SHA-256 en bloques).
5. Buscar si ya existe un proyecto con ese activo (idempotencia).
6. Crear el proyecto con estado `ingested` y publicar los artefactos.

Garantias:

- El original **nunca** se abre en modo escritura, se mueve ni se borra.
- Los artefactos se escriben en un directorio temporal `projects/.tmp-<uuid>`
  y solo se publican con un `os.rename` final, de modo que un fallo
  intermedio jamas deja un `projects/<uuid>/` incompleto (salvaguarda 3).
- Un directorio existente cuya procedencia no se pueda demostrar hace
  que la ingesta se detenga conservando sus archivos (salvaguarda 4).
- Reintentar sobre el mismo contenido devuelve el proyecto existente en
  lugar de duplicarlo.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from schemas import Project

from .errors import (
    FFprobeNotFoundError,
    IngestionError,
    InsufficientSpaceError,
    InvalidSourceError,
    ProjectDirectoryConflictError,
    ProjectWriteError,
    SourceNotFoundError,
)
from .ffprobe import FFprobeRunner, ffprobe_version, probe_media
from .metadata import SourceMetadata, parse_probe

PROJECTS_DIRNAME = "projects"
LOGS_DIRNAME = "logs"
INPUT_DIRNAME = "input"
PROJECT_JSON = "project.json"
SOURCE_METADATA_JSON = "source_metadata.json"
TEMP_PREFIX = ".tmp-"
MIN_FREE_BYTES = 10 * 1024 * 1024
HASH_BLOCK_SIZE = 1024 * 1024

# Raiz del repositorio derivada de la ubicacion del codigo (no se
# codifican rutas absolutas de una maquina concreta, BLUEPRINT 10).
REPO_ROOT = Path(__file__).resolve().parents[2]


def default_project_root() -> Path:
    return REPO_ROOT / PROJECTS_DIRNAME


def default_input_dir() -> Path:
    return REPO_ROOT / INPUT_DIRNAME


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestionResult:
    """Resultado de una ingesta, exitosa o no duplicada."""

    project: Project
    project_json_path: Path
    source_metadata_path: Path
    created: bool


@dataclass(frozen=True)
class DirectoryIngestionResult:
    """Resultado de ingerir todo un directorio."""

    results: List[IngestionResult]
    failures: List[Tuple[Path, IngestionError]]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_source_asset_id(source_path: Any, block_size: int = HASH_BLOCK_SIZE) -> str:
    """SHA-256 del contenido, calculado en bloques (salvaguarda 8)."""
    digest = hashlib.sha256()
    try:
        with open(str(source_path), "rb") as handle:
            while True:
                chunk = handle.read(block_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise InvalidSourceError(
            f"No se pudo leer el archivo para calcular el hash: {exc}", path=source_path
        ) from exc
    return "sha256-" + digest.hexdigest()


def list_source_files(input_dir: Any) -> List[Path]:
    """Archivos candidatos de un directorio (sin subcarpetas ni ocultos)."""
    directory = Path(input_dir)
    if not directory.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in directory.iterdir()
            if entry.is_file() and not entry.name.startswith(".")
        ),
        key=lambda p: p.name,
    )


# ---------------------------------------------------------------------------
# Validaciones previas
# ---------------------------------------------------------------------------

def _ensure_source_file(source_path: Path) -> None:
    if not source_path.exists():
        raise SourceNotFoundError("El archivo fuente no existe", path=source_path)
    if not source_path.is_file():
        raise InvalidSourceError(
            "La ruta no corresponde a un archivo regular", path=source_path
        )
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise InvalidSourceError(
            f"No se pudo leer la informacion del archivo: {exc}", path=source_path
        ) from exc
    if stat.st_size <= 0:
        raise InvalidSourceError("El archivo esta vacio (0 bytes)", path=source_path)
    try:
        with open(str(source_path), "rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise InvalidSourceError(
            f"No se pudo abrir el archivo para lectura: {exc}", path=source_path
        ) from exc


def _ensure_free_space(project_root: Path) -> None:
    try:
        project_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProjectWriteError(
            f"No se pudo preparar el directorio de proyectos: {exc}", path=project_root
        ) from exc
    try:
        free = shutil.disk_usage(str(project_root)).free
    except OSError as exc:
        raise InsufficientSpaceError(
            f"No se pudo verificar el espacio libre del disco: {exc}", path=project_root
        ) from exc
    if free < MIN_FREE_BYTES:
        raise InsufficientSpaceError(
            f"Espacio libre insuficiente: {free} bytes disponibles y se requieren "
            f"al menos {MIN_FREE_BYTES} bytes",
            path=project_root,
            details={"free_bytes": free, "required_bytes": MIN_FREE_BYTES},
        )


# ---------------------------------------------------------------------------
# Idempotencia y procedencia
# ---------------------------------------------------------------------------

def _looks_like_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


def _read_project_entry(entry: Path) -> Project:
    project_json = entry / PROJECT_JSON
    metadata_json = entry / SOURCE_METADATA_JSON
    if not project_json.is_file() or not metadata_json.is_file():
        raise ProjectDirectoryConflictError(
            "Directorio de proyecto incompleto (falta project.json o "
            f"{SOURCE_METADATA_JSON}); se conserva sin modificar",
            path=entry,
            details={"contenido": sorted(p.name for p in entry.iterdir())},
        )
    try:
        project_data = json.loads(project_json.read_text(encoding="utf-8"))
        metadata_data = json.loads(metadata_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectDirectoryConflictError(
            f"project.json / {SOURCE_METADATA_JSON} ilegibles; se conserva sin modificar",
            path=entry,
            details={"error": str(exc)},
        ) from exc
    try:
        project = Project.from_dict(project_data)
    except (ValueError, KeyError, TypeError) as exc:
        raise ProjectDirectoryConflictError(
            "project.json no contiene un proyecto valido; se conserva sin modificar",
            path=entry,
            details={"error": str(exc)},
        ) from exc
    if not isinstance(metadata_data, dict):
        raise ProjectDirectoryConflictError(
            f"{SOURCE_METADATA_JSON} no es un objeto JSON valido; se conserva sin modificar",
            path=entry,
        )
    if metadata_data.get("duration_seconds") != project.duration_seconds:
        raise ProjectDirectoryConflictError(
            "Los artefactos del proyecto no coinciden en duration_seconds; "
            "procedencia no demostrada, se conserva sin modificar",
            path=entry,
            details={
                "project.json": project.duration_seconds,
                SOURCE_METADATA_JSON: metadata_data.get("duration_seconds"),
            },
        )
    return project


def _find_existing_project(project_root: Path, source_asset_id: str) -> Optional[Project]:
    if not project_root.is_dir():
        return None
    matches: List[Project] = []
    for entry in sorted(project_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith(TEMP_PREFIX):
            # Procedencia demostrable: nuestro patron temporal de escritura.
            # No se elimina ni se interpreta como un proyecto valido.
            continue
        if not _looks_like_uuid(entry.name):
            raise ProjectDirectoryConflictError(
                "Se encontro un directorio en projects/ que no sigue el formato de "
                "proyecto; revision manual requerida. No se eliminara ni se sobrescribira",
                path=entry,
                details={"contenido": sorted(p.name for p in entry.iterdir())},
            )
        project = _read_project_entry(entry)
        if project.source_asset_id == source_asset_id:
            matches.append(project)
    if len(matches) > 1:
        raise ProjectDirectoryConflictError(
            "Existen varios proyectos con el mismo source_asset_id",
            path=project_root,
            details={"project_ids": [p.project_id for p in matches]},
        )
    return matches[0] if matches else None


def _verify_existing(project: Project, source_meta: SourceMetadata, source_path: Path) -> None:
    if abs(project.duration_seconds - source_meta.duration_seconds) > 1e-6:
        raise ProjectDirectoryConflictError(
            "El proyecto existente no coincide con la duracion actual del archivo; "
            "procedencia no demostrada, se conserva sin modificar",
            path=source_path,
            details={
                "project_id": project.project_id,
                "duration_project": project.duration_seconds,
                "duration_probe": source_meta.duration_seconds,
            },
        )


# ---------------------------------------------------------------------------
# Escritura atomica y publicacion
# ---------------------------------------------------------------------------

def _write_json_atomic(target: Path, data: Dict[str, Any]) -> None:
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(str(tmp), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(target))
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectWriteError(
            f"No se pudo escribir {target.name}: {exc}", path=target
        ) from exc


def _verify_written_pair(directory: Path, project: Project) -> None:
    """Comprueba que ambos artefactos existen, son validos y coinciden."""
    try:
        project_data = json.loads((directory / PROJECT_JSON).read_text(encoding="utf-8"))
        metadata_data = json.loads(
            (directory / SOURCE_METADATA_JSON).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectWriteError(
            f"Los artefactos escritos no se pudieron releer: {exc}", path=directory
        ) from exc
    try:
        restored = Project.from_dict(project_data)
    except (ValueError, KeyError, TypeError) as exc:
        raise ProjectWriteError(
            f"project.json escrito no representa un proyecto valido: {exc}", path=directory
        ) from exc
    if restored.status != project.status:
        raise ProjectWriteError(
            f"project.json perdio el estado {project.status!r}", path=directory
        )
    if restored.duration_seconds != project.duration_seconds:
        raise ProjectWriteError(
            "project.json no conserva duration_seconds", path=directory
        )
    if metadata_data.get("duration_seconds") != project.duration_seconds:
        raise ProjectWriteError(
            "project.json y source_metadata.json no coinciden en duration_seconds",
            path=directory,
            details={
                "project.json": project.duration_seconds,
                SOURCE_METADATA_JSON: metadata_data.get("duration_seconds"),
            },
        )
    if metadata_data.get("metadata") != project.metadata:
        raise ProjectWriteError(
            "project.json y source_metadata.json no coinciden en metadata",
            path=directory,
        )


def _publish_project(
    project: Project, source_meta: SourceMetadata, project_root: Path
) -> Path:
    final_dir = project_root / project.project_id
    tmp_dir = project_root / f"{TEMP_PREFIX}{project.project_id}"

    if final_dir.exists():
        raise ProjectDirectoryConflictError(
            "El directorio del proyecto ya existe; no se sobrescribira",
            path=final_dir,
        )
    if tmp_dir.exists():
        raise ProjectDirectoryConflictError(
            "Ya existe un directorio temporal con este identificador; "
            "revision manual requerida. No se eliminara ni se sobrescribira",
            path=tmp_dir,
        )

    artifact = source_meta.to_artifact_dict(
        source_asset_id=project.source_asset_id,
        source_path=project.source_path,
        tool_versions=project.tool_versions,
    )

    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
        # source_metadata.json primero y project.json al final: si algo
        # falla, nunca aparece un project.json suelto en un directorio
        # publicado (salvaguarda 3; el directorio final solo aparece con
        # el rename de la ultima linea).
        _write_json_atomic(tmp_dir / SOURCE_METADATA_JSON, artifact)
        _write_json_atomic(tmp_dir / PROJECT_JSON, project.to_dict())
        _verify_written_pair(tmp_dir, project)
        if final_dir.exists():
            raise ProjectDirectoryConflictError(
                "El directorio del proyecto aparecio durante la escritura; "
                "no se sobrescribira",
                path=final_dir,
            )
        os.rename(str(tmp_dir), str(final_dir))
    except IngestionError:
        raise
    except OSError as exc:
        raise ProjectWriteError(
            f"No se pudo publicar el proyecto: {exc}", path=final_dir,
            details={"temporal": str(tmp_dir)},
        ) from exc
    return final_dir


def _record_failure(project_root: Path, error: IngestionError, source_path: Path) -> None:
    """Registro de errores en logs/ingestion.log (BLUEPRINT 9).

    Nunca enmascara el error original: cualquier fallo al escribir el log
    se ignora porque el error ya se propaga al llamador.
    """
    log_path = project_root.parent / LOGS_DIRNAME / "ingestion.log"
    line = (
        f"{utc_now_iso()}\tERROR\t{error.__class__.__name__}\t"
        f"{error}\t{source_path}\n"
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(log_path), "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Construccion del proyecto
# ---------------------------------------------------------------------------

def _build_project(
    *, source_path: Path, source_asset_id: str, source_meta: SourceMetadata, ffprobe: str
) -> Project:
    return Project(
        project_id=str(uuid.uuid4()),
        source_asset_id=source_asset_id,
        status="ingested",
        created_at=utc_now_iso(),
        source_path=str(source_path.resolve()),
        duration_seconds=source_meta.duration_seconds,
        metadata=dict(source_meta.metadata),
        tool_versions={
            "ffprobe": ffprobe,
            "python": "{}.{}.{}".format(*sys.version_info[:3]),
        },
    )


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def ingest_file(
    source_path: Any,
    *,
    project_root: Any = None,
    runner: Any = None,
    ffprobe_executable: Any = None,
    timeout: Any = None,
) -> IngestionResult:
    """Ingiere un archivo local y crea su proyecto (o devuelve el existente)."""
    root = Path(project_root) if project_root is not None else default_project_root()
    source = Path(source_path)
    try:
        return _ingest_file(source, root, runner, ffprobe_executable, timeout)
    except IngestionError as exc:
        _record_failure(root, exc, source)
        raise


def _ingest_file(
    source: Path,
    root: Path,
    runner: Any,
    ffprobe_executable: Any,
    timeout: Any,
) -> IngestionResult:
    _ensure_source_file(source)
    _ensure_free_space(root)

    active_runner = runner if runner is not None else FFprobeRunner(ffprobe_executable, timeout)

    probe = probe_media(active_runner, source)
    version = ffprobe_version(active_runner)
    source_meta = parse_probe(probe, source_path=source)
    source_asset_id = compute_source_asset_id(source)

    existing = _find_existing_project(root, source_asset_id)
    if existing is not None:
        _verify_existing(existing, source_meta, source)
        return IngestionResult(
            project=existing,
            project_json_path=root / existing.project_id / PROJECT_JSON,
            source_metadata_path=root / existing.project_id / SOURCE_METADATA_JSON,
            created=False,
        )

    project = _build_project(
        source_path=source,
        source_asset_id=source_asset_id,
        source_meta=source_meta,
        ffprobe=version,
    )
    final_dir = _publish_project(project, source_meta, root)
    return IngestionResult(
        project=project,
        project_json_path=final_dir / PROJECT_JSON,
        source_metadata_path=final_dir / SOURCE_METADATA_JSON,
        created=True,
    )


def ingest_directory(
    input_dir: Any, *, project_root: Any = None, **kwargs: Any
) -> DirectoryIngestionResult:
    """Ingiere todos los archivos candidatos de un directorio.

    Los errores de cada archivo se acumulan en `failures` sin detener el
    lote, salvo cuando el fallo es de entorno (FFprobe ausente), en cuyo
    caso se propaga el primero: repetirlo por cada archivo no aporta nada.
    """
    directory = Path(input_dir)
    if not directory.exists():
        raise SourceNotFoundError("El directorio de entrada no existe", path=directory)
    if not directory.is_dir():
        raise InvalidSourceError("La ruta de entrada no es un directorio", path=directory)

    results: List[IngestionResult] = []
    failures: List[Tuple[Path, IngestionError]] = []
    for candidate in list_source_files(directory):
        try:
            results.append(ingest_file(candidate, project_root=project_root, **kwargs))
        except IngestionError as exc:
            failures.append((candidate, exc))

    if failures and all(isinstance(err, FFprobeNotFoundError) for _, err in failures):
        raise failures[0][1]
    return DirectoryIngestionResult(results=results, failures=failures)
