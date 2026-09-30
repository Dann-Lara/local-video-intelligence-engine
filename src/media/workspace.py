"""Workspace por job (blueprint secciones 11 y 12, paso 5).

    work/
    └── video_001/
        ├── audio.wav
        ├── transcript.json
        ├── scenes.json
        ├── content_map.json
        ├── clip_candidates.json
        ├── edit_plans/
        ├── frames/
        └── renders/

Se crean los directorios de una vez, aunque algunos no se usen hasta etapas
posteriores. Motivo concreto: si un stage falla a mitad y hay que depurar
(seccion 66, modo `qc`), todos los artefactos esperados existen ya y un
`FileNotFoundError` nunca confunde el diagnostico.

Ojo con un detalle: la raiz de trabajo es configurable
(`paths.work_root`), y el nombre del workspace es el del fichero de entrada.
Dos ficheros con el mismo nombre pero distinto contenido comparten workspace.
No es un problema en el MVP porque el `job_id` lleva el sha256 y es lo que
identifica el trabajo, pero queda anotado por si el workspace pasa a ser clave.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Subdirectorios que ffmpeg / remotion necesitan poder escribir.
SUBDIRECTORIES = ("edit_plans", "frames", "renders")

# Ficheros que cada etapa produce dentro del workspace. Las claves tienen que
# coincidir con los nombres de las propiedades de `Workspace`: `as_dict()` las
# recorre con `getattr` y un desajuste aqui solo aparece en tiempo de ejecucion.
ARTIFACT_FILES = {
    "audio": "audio.wav",
    "transcript": "transcript.json",
    "normalized_transcript": "normalized_transcript.json",
    "scenes": "scenes.json",
    "content_map": "content_map.json",
    "clip_candidates": "clip_candidates.json",
    "edit_plan": "edit_plan.json",
    "ingest_manifest": "ingest.json",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(name: str) -> str:
    """Convierte un nombre de fichero en algo seguro para un path de Windows.

    Se eliminan los caracteres no ASCII en lugar de sustituirlos: un nombre con
    acentos crearia un directorio con acento, y la ruta del proyecto YA lleva
    un acento, asi que duplicar ese problema en cada workspace no ayuda a nadie.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _UNSAFE.sub("_", ascii_only).strip("._")
    return cleaned or "video"


@dataclass
class Workspace:
    """Rutas de un job. Solo contiene paths, no ejecuta nada."""

    root: Path

    @property
    def audio(self) -> Path:
        return self.root / ARTIFACT_FILES["audio"]

    @property
    def transcript(self) -> Path:
        return self.root / ARTIFACT_FILES["transcript"]

    @property
    def normalized_transcript(self) -> Path:
        return self.root / ARTIFACT_FILES["normalized_transcript"]

    @property
    def scenes(self) -> Path:
        return self.root / ARTIFACT_FILES["scenes"]

    @property
    def content_map(self) -> Path:
        return self.root / ARTIFACT_FILES["content_map"]

    @property
    def clip_candidates(self) -> Path:
        return self.root / ARTIFACT_FILES["clip_candidates"]

    @property
    def edit_plans(self) -> Path:
        """Directorio de planes de edicion (seccion 11)."""
        return self.root / "edit_plans"

    @property
    def edit_plan(self) -> Path:
        """Plan de edicion del clip que se esta renderizando."""
        return self.edit_plans / ARTIFACT_FILES["edit_plan"]

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def renders(self) -> Path:
        return self.root / "renders"

    @property
    def ingest_manifest(self) -> Path:
        return self.root / ARTIFACT_FILES["ingest_manifest"]

    def create(self) -> "Workspace":
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRECTORIES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        return self

    def exists(self) -> bool:
        return self.root.is_dir()

    def as_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in ARTIFACT_FILES}


def build_job_id(source_path: Path, sha256: str, *, date: str | None = None) -> str:
    """Job ID con el formato de la seccion 12: `2026-09-28_video001_a83f91`.

    El sufijo son los primeros 8 caracteres del sha256, no un aleatorio: dos
    ingresos del mismo fichero producen el mismo Job ID, que es justo lo que
    necesita la idempotencia de la seccion 51.
    """
    from datetime import date as date_type

    day = date or date_type.today().isoformat()
    stem = safe_stem(source_path.stem)
    return f"{day}_{stem}_{sha256[:8]}"


def create(
    source_path: Path,
    sha256: str,
    config: dict[str, Any],
    *,
    job_id: str | None = None,
) -> Workspace:
    """Crea el workspace del job y devuelve sus rutas.

    `sha256` y `job_id` se aceptan para dejar la firma lista, pero el workspace
    se nombra por `source_path.stem` tal y como pide la seccion 11. El `job_id`
    no se usa para el path porque incluirlo haria que reprocesar el mismo
    video creara un directorio nuevo en cada intento, y los checkpoints de la
    seccion 50 dejarian de encontrar los artefactos anteriores.
    """
    del sha256, job_id
    from src.core import config as config_module

    work_root = config_module.resolve_path(config, "work_root")
    return Workspace(root=work_root / safe_stem(source_path.stem)).create()
