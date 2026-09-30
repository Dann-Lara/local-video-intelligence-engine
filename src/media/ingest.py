"""Motor de ingesta (blueprint seccion 12, pasos 1 a 7).

    1. Detectar archivo.
    2. Calcular hash.
    3. Obtener metadata.
    4. Crear Job ID.
    5. Crear workspace.
    6. Validar formato.
    7. Extraer audio.

El paso 8 ("mandar a transcripcion") es de la seccion 14 y no se ejecuta aqui.

Sobre el orden: el blueprint enumera validar formato en el paso 6, DESPUES de
crear el workspace. Se mantiene ese orden a proposito, y merece una
explicacion, porque parece al reves. Si el fichero no tiene audio, conviene que
exista ya el workspace con su `ingest.json` explicando por que se paro. Un
fallo limpio y localizado vale mas que un error sin contexto. Lo que si se
hace ANTES de crear el workspace es lo barato y no destructivo: existencia del
fichero, hash y metadata. Si algo falla ahi, no queda ningun rastro en disco
que limpiar.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.media import extract, ffprobe, workspace as workspace_module

# Formatos que ffprobe puede leer y que el pipeline puede recortar.
SUPPORTED_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv"}
)

HASH_CHUNK_BYTES = 1024 * 1024


class IngestError(RuntimeError):
    """La ingesta no puede continuar. Es un fallo FATAL (seccion 58)."""

    def __init__(self, message: str, *, code: str = "ingest_failed") -> None:
        self.code = code
        super().__init__(message)


def find_candidates(directory: Path) -> list[Path]:
    """Paso 1: que ficheros hay. Orden estable por nombre, no por fecha.

    Ordenar por nombre y no por mtime es a proposito: procesar la misma carpeta
    dos veces tiene que visitar los ficheros en el mismo orden, y mtime cambia
    al copiar o al sincronizar desde el movil.
    """
    if not directory.is_dir():
        raise IngestError(
            f"no existe el directorio de entrada: {directory}", code="missing_input_dir"
        )

    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def sha256_of(path: Path, *, chunk_bytes: int = HASH_CHUNK_BYTES) -> str:
    """Paso 2. Por bloques: un video de 2 h no cabe en memoria de una vez."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class IngestResult:
    job_id: str
    source_path: str
    source_name: str
    sha256: str
    created_at: str
    workspace_root: str
    media: dict[str, Any]
    audio_path: str | None
    audio: dict[str, Any] | None
    manifest_path: str | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"job_id      {self.job_id}",
            f"origen      {self.source_name}",
            f"sha256      {self.sha256[:16]}...",
            f"workspace   {self.workspace_root}",
        ]
        if self.media.get("duration"):
            lines.append(f"duracion    {self.media['duration']:.1f}s")
        if self.media.get("video"):
            video = self.media["video"]
            lines.append(f"video       {video.get('width')}x{video.get('height')} @ {video.get('fps')}")
        lines.append(f"audio       {self.audio_path or '(no extraido)'}")
        for warning in self.warnings:
            lines.append(f"AVISO       {warning}")
        return "\n".join(lines)


def _validate(source: Path, info: ffprobe.MediaInfo) -> list[str]:
    """Paso 6. Devuelve advertencias; los errores graves lanzan."""
    warnings: list[str] = []

    if info.video is not None and info.video.rotation % 360:
        warnings.append(
            f"el contenedor declara una rotacion de {info.video.rotation} grados; "
            "se usara la orientacion real para el recorte 9:16"
        )

    if not info.has_audio:
        raise IngestError(
            f"{source.name} no tiene audio. El pipeline transcribe la voz, asi que un "
            "video mudo no se puede procesar.",
            code="no_audio",
        )

    if not info.has_video:
        raise IngestError(f"{source.name} no tiene stream de video", code="no_video")

    if info.duration is not None and info.duration < 5:
        warnings.append(
            f"el video dura {info.duration:.1f}s. Es muy corto para extraer un clip "
            "vertical de 30-60s (seccion 23)."
        )

    if info.duration is not None and info.duration > 4 * 3600:
        warnings.append(
            f"el video dura {info.duration / 3600:.1f} h. La transcripcion y el analisis "
            "van a tardar; conviene el modo 'analyze' y no 'process'."
        )

    if info.video and info.video.fps and info.video.fps < 20:
        warnings.append(
            f"fps bajo ({info.video.fps:.2f}). El recorte vertical se vera menos nitido."
        )

    if info.video and info.video.codec == "vp8" and info.format_name and "matroska" in info.format_name:
        warnings.append("WebM/VP8: comprobar que el render vertical conserve la nitidez.")

    return warnings


def ingest(
    source: Path | str,
    config: dict[str, Any],
    *,
    job_id: str | None = None,
    overwrite_audio: bool = False,
) -> IngestResult:
    """Ejecuta los pasos 1 a 7 de la seccion 12.

    Idempotente en el audio: si el WAV ya existe y es valido, no se vuelve a
    extraer (seccion 51).
    """
    path = Path(source)
    if not path.is_file():
        raise IngestError(f"no existe el fichero: {path}", code="missing_file")
    if not path.is_absolute():
        path = (Path(config["_project_root"]) / path).resolve()

    # 2. hash
    sha256 = sha256_of(path)

    # 3. metadata
    try:
        info = ffprobe.probe(path)
    except ffprobe.ProbeError as exc:
        raise IngestError(
            f"ffprobe no pudo leer {path.name}: {exc}. ¿Es un fichero de video valido?",
            code="unreadable",
        ) from exc

    # 4. job id
    resolved_job_id = job_id or workspace_module.build_job_id(path, sha256)

    # 5. workspace
    work = workspace_module.create(path, sha256, config, job_id=resolved_job_id)

    # 6. validacion de formato
    warnings = _validate(path, info)

    # 7. audio
    audio_path: Path | None = None
    audio_payload: dict[str, Any] | None = None
    try:
        audio = extract.extract_audio(path, work.audio, config, overwrite=overwrite_audio)
        audio_path = audio.path
        audio_payload = {
            "path": str(audio.path),
            "sample_rate": audio.sample_rate,
            "channels": audio.channels,
            "codec": audio.codec,
            "duration": audio.duration,
            "size_bytes": audio.size_bytes,
            "whisper_ready": audio.is_whisper_ready,
        }
    except extract.ExtractionError as exc:
        raise IngestError(
            f"se pudo leer el video pero no extraer su audio: {exc}",
            code="audio_extraction_failed",
        ) from exc

    if not audio_payload or not audio_payload["whisper_ready"]:
        warnings.append(
            "el audio extraido no coincide con lo que espera Whisper "
            f"({extract.WHISPER_SAMPLE_RATE} Hz mono). Revisa whisper.audio_sample_rate."
        )

    created_at = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = IngestResult(
        job_id=resolved_job_id,
        source_path=str(path),
        source_name=path.name,
        sha256=sha256,
        created_at=created_at,
        workspace_root=str(work.root),
        media=info.to_dict(),
        audio_path=str(audio_path) if audio_path else None,
        audio=audio_payload,
        manifest_path=None,
        warnings=warnings,
    )

    # El manifiesto va al workspace: si la ingesta falla, el motivo queda escrito.
    manifest = work.ingest_manifest
    manifest.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result.manifest_path = str(manifest)

    return result
