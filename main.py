"""CLI del Local Video Intelligence Engine (blueprint secciones 65 y 66).

    python main.py doctor
    python main.py db init
    python main.py jobs list
    python main.py jobs show <job_id>
    python main.py jobs resume <job_id>
    python main.py process input/video.mp4
    python main.py analyze input/video.mp4
    python main.py candidates input/video.mp4
    python main.py render <job_id>
    python main.py report <job_id>
    python main.py performance sync

Codigos de salida:

    0  correcto
    1  error de ejecucion (entorno, argumentos, base de datos)
    2  el comando existe pero su etapa todavia no esta implementada

Sobre la seccion 78 ("primero pipeline funcional, luego CLI"): este fichero
implementa la superficie de comandos completa desde el Sprint 1, pero las
etapas que dependen de modulos de sprints posteriores responden con codigo 2 y
dicen que sprint las trae. Es preferible a un `NotImplementedError` o a un stub
silencioso, porque alguien que invoque el comando obtiene una respuesta util en
lugar de un traceback.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import typer

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import diagnostics  # noqa: E402
from src.core import artifacts as artifacts_module  # noqa: E402
from src.core import config as config_module  # noqa: E402
from src.core import db as db_module  # noqa: E402
from src.core import logs  # noqa: E402
from src.core.db import JobState  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local AI Video Clipping & Content Intelligence Engine",
)


def _configure_console() -> None:
    """Fuerza UTF-8 en stdout/stderr.

    Sin esto, la consola de Windows usa cp1252 y una transcripcion en japones,
    ruso o con emoji revienta con `UnicodeEncodeError` justo DESPUES de haber
    transcrito 40 minutos. `errors="replace"` convierte el problema en un
    caracter ilegible en lugar de un crash, que es la diferencia entre un
    pipeline que continua y uno que hay que relanzar.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Streams redefinidos por un test o embebidos en otro proceso.
            pass

db_app = typer.Typer(no_args_is_help=True, help="Base de datos (secciones 44 y 74)")
jobs_app = typer.Typer(no_args_is_help=True, help="Estado de los jobs (secciones 49 y 50)")
performance_app = typer.Typer(no_args_is_help=True, help="Metricas de plataforma (seccion 44)")

app.add_typer(db_app, name="db")
app.add_typer(jobs_app, name="jobs")
app.add_typer(performance_app, name="performance")


def _load_config() -> dict[str, Any]:
    try:
        return config_module.load()
    except config_module.ConfigError as exc:
        typer.secho(f"Error de configuracion: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc


def _setup_logging(config: dict[str, Any]) -> Any:
    logger = logs.setup(config)
    return logger


def _open_repo(config: dict[str, Any]) -> db_module.JobRepository:
    try:
        database = db_module.open_database(config)
    except (ValueError, OSError) as exc:
        typer.secho(f"Error abriendo la base de datos: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    return db_module.JobRepository(database)


# --- doctor (seccion 73) ---


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="No contactar a LM Studio"),
) -> None:
    """Comprueba el entorno completo (Python, Node, FFmpeg, CUDA, LM Studio, modelos)."""
    args: list[str] = []
    if as_json:
        args.append("--json")
    if skip_llm:
        args.append("--skip-llm")
    raise typer.Exit(diagnostics.main(args))


# --- base de datos ---


@db_app.command("init")
def db_init() -> None:
    """Crea el esquema si no existe. Es idempotente."""
    config = _load_config()
    try:
        database = db_module.open_database(config)
    except (ValueError, OSError) as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    typer.secho(
        f"Base de datos lista en {database.path} (schema v{database.schema_version()})",
        fg=typer.colors.GREEN,
    )


@db_app.command("path")
def db_path() -> None:
    """Muestra la ruta de la base de datos resuelta."""
    config = _load_config()
    database = db_module.open_database(config, initialize=False)
    typer.echo(str(database.path))


# --- jobs ---


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    state: str = typer.Option(None, "--state", help="Filtra por estado (seccion 49)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Lista los jobs registrados."""
    config = _load_config()
    repo = _open_repo(config)
    rows = repo.list_jobs(limit=limit, state=state)
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("No hay jobs registrados.")
        return
    for row in rows:
        video_id = row["video_id"]
        marker = "  [reanudar: " + str(repo.resume_stage(row["id"])) + "]" if row["state"] in {
            JobState.FAILED, JobState.RETRYING
        } else ""
        typer.echo(
            f"{row['id']}  {row['state']:<20} video={video_id}{marker}"
        )


@jobs_app.command("show")
def jobs_show(job_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Muestra el detalle de un job y por donde habria que reanudar."""
    config = _load_config()
    repo = _open_repo(config)
    job = repo.get_job(job_id)
    if job is None:
        typer.secho(f"Job desconocido: {job_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    resume = repo.resume_stage(job_id)
    detail = {
        **job,
        "resume_stage": str(resume),
        "completed_stages": repo.completed_stages(job_id),
        "artifacts": repo.list_artifacts(job_id),
    }
    if as_json:
        typer.echo(json.dumps(detail, ensure_ascii=False, indent=2, default=str))
        return

    typer.echo(f"job_id      {job['id']}")
    typer.echo(f"video_id    {job['video_id']}")
    typer.echo(f"estado      {job['state']}")
    typer.echo(f"etapa       {job['current_stage']}")
    typer.echo(f"intentos    {job['attempts']}")
    typer.echo(f"reanudar en {resume}")
    if job.get("error_message"):
        typer.echo(f"error       {job['error_kind']}: {job['error_message']}")
    completed = detail["completed_stages"]
    typer.echo(f"completadas {', '.join(completed) if completed else '(ninguna)'}")


@jobs_app.command("resume")
def jobs_resume(job_id: str) -> None:
    """Dice desde que etapa hay que continuar (seccion 50)."""
    config = _load_config()
    repo = _open_repo(config)
    if repo.get_job(job_id) is None:
        typer.secho(f"Job desconocido: {job_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)
    stage = repo.resume_stage(job_id)
    if stage == JobState.COMPLETED:
        typer.secho("El job ya esta COMPLETED. No hay nada que reanudar.", fg=typer.colors.GREEN)
    else:
        typer.echo(f"Reanudar desde: {stage}")


# --- etapas del pipeline (secciones 65 y 66) ---

STAGE_ROADMAP = {
    "performance sync": ("Sprint 8 (adaptadores de plataforma)", "src/performance/sync.py"),
}


def _not_ready(command: str) -> None:
    sprint, module = STAGE_ROADMAP[command]
    typer.secho(
        f"'{command}' todavia no esta implementado ({sprint}; modulo previsto: {module})",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _run_ingest(
    video: str, config: dict[str, Any], *, as_json: bool
) -> tuple[db_module.JobRepository, str, dict[str, Any]]:
    """Ejecuta los pasos 1-7 de la seccion 12 y registra el job en la base.

    Se mantiene separado del comando para que `analyze`, `process` y el futuro
    orquestador compartan exactamente la misma secuencia de estados.

    Devuelve el repositorio, el `job_id` y el payload de la etapa. Quien llama
    no tiene que volver a hashear el fichero: en un video de 2 h son varios
    segundos de disco leidos dos veces, y el hash es lo que determina el Job ID.

    La etapa NO imprime nada. Imprimir aqui y volver a imprimir luego lleva a
    emitir dos documentos JSON seguidos en `--json`, que no es JSON valido ni
    parseable por ninguna herramienta. Quien compone es `analyze`.
    """
    from src.core import gpu
    from src.media import ingest as ingest_module
    from src.media import workspace as workspace_module

    logger = _setup_logging(config)
    repo = _open_repo(config)
    source = Path(video)

    with logs.log_context(stage=JobState.QUEUED.value, source=str(source)):
        try:
            if not source.is_file():
                raise ingest_module.IngestError(
                    f"no existe el fichero: {source}", code="missing_file"
                )
            # El hash se calcula aqui, y no dentro de `ingest()`, para poder
            # construir el Job ID de la seccion 12 y registrar el video antes de
            # tocar el workspace. Asi un fallo de ingesta queda en la base con su
            # sha256 y el siguiente intento no vuelve a hashear el fichero.
            sha256 = ingest_module.sha256_of(source)
        except ingest_module.IngestError as exc:
            logger.error("ingest_rejected", extra={"error_kind": exc.code, "error_detail": str(exc)})
            typer.secho(f"Error de ingesta: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_ERROR) from exc

        # Paso 4. El Job ID es determinista: fecha + nombre + sha8. No es un
        # aleatorio, porque la seccion 51 exige que reprocesar el mismo
        # contenido llegue al mismo sitio.
        job_id = workspace_module.build_job_id(source, sha256)
        video_id = repo.register_video(path=str(source.resolve()), sha256=sha256)

        # Idempotencia (seccion 51): si el job ya existe y la ingesta ya paso,
        # no se vuelve a hacer. Sin esta comprobacion, reejecutar `analyze`
        # sobre el mismo fichero falla con un error de clave duplicada, que es
        # justo el comportamento contrario al que pide el blueprint.
        previous = repo.get_job(job_id)
        if previous is None:
            repo.create_job(video_id, job_id=job_id)
        elif JobState(previous["state"]) not in {
            JobState.QUEUED,
            JobState.INGESTING,
            JobState.FAILED,
            JobState.RETRYING,
        }:
            # Idempotencia (seccion 51): si el job ya existe y la ingesta ya
            # paso, no se vuelve a hacer. Sin esta comprobacion, reejecutar
            # `analyze` sobre el mismo fichero falla con un error de clave
            # duplicada, que es justo lo contrario de lo que pide el blueprint.
            manifest = _read_manifest(workspace_module, source, config)
            state = JobState(previous["state"])
            if not as_json:
                typer.secho(
                    f"Ya ingerido: {job_id} (estado {state.value}). No se repite el trabajo.",
                    fg=typer.colors.YELLOW,
                )
                if manifest:
                    typer.echo(
                        f"workspace  {manifest.get('workspace_root')}\n"
                        f"audio      {manifest.get('audio_path')}"
                    )
                typer.echo(f"\nContinuar en: {state.value}")
            return repo, job_id, {
                **(manifest or {}),
                "job_id": job_id,
                "already_ingested": True,
                "state": state.value,
            }

        with logs.log_context(job_id=job_id, stage=JobState.INGESTING.value):
            # La maquina de estados (seccion 49) prohibe saltar etapas. Desde
            # FAILED no se puede ir directo a INGESTING: hay que pasar por
            # RETRYING, que es el estado que representa "vuelvo a intentarlo".
            # `enter_stage` es idempotente y no retrocede si el job ya esta mas
            # adelante, de modo que reejecutar `analyze` no rompe (seccion 51).
            repo.enter_stage(job_id, JobState.INGESTING)
            run_id = repo.start_stage(job_id, JobState.INGESTING)
            started = time.monotonic()
            try:
                result = ingest_module.ingest(source, config, job_id=job_id)
            except ingest_module.IngestError as exc:
                repo.finish_stage(
                    run_id,
                    state=JobState.FAILED.value,
                    error_kind=exc.code,
                    error_detail=str(exc),
                )
                repo.transition(job_id, JobState.FAILED, error_kind=exc.code, error_message=str(exc))
                logger.error("ingest_failed", extra={"error_kind": exc.code, "error_detail": str(exc)})
                typer.secho(f"Error de ingesta: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(EXIT_ERROR) from exc

            # La metadata ya se conoce, se actualiza el video en vez de dejarla
            # a null: `jobs show` y los informes posteriores la necesitan.
            repo.register_video(
                path=result.source_path,
                sha256=result.sha256,
                video_id=video_id,
                duration=result.media.get("duration"),
                width=(result.media.get("video") or {}).get("width"),
                height=(result.media.get("video") or {}).get("height"),
                fps=(result.media.get("video") or {}).get("fps"),
                video_codec=(result.media.get("video") or {}).get("codec"),
                audio_codec=(result.media.get("audio") or {}).get("codec"),
            )
            repo.record_artifact(
                job_id,
                JobState.ANALYZING_MEDIA,
                "media_metadata",
                str(source.resolve()),
                sha256=result.sha256,
            )
            # ANALYZING_MEDIA se marca como etapa completada aunque su trabajo
            # (ffprobe + validacion) lo haya hecho la propia ingesta. Sin esta
            # corrida, `resume_stage` encuentra ANALYZING_MEDIA sin completar en
            # TODOS los jobs y siempre dice "reanudar en ANALYZING_MEDIA", incluso
            # cuando la transcripcion ya termino. `start_stage` no mueve el estado
            # del job, solo abre la corrida, asi que sigue en INGESTING.
            media_run = repo.start_stage(job_id, JobState.ANALYZING_MEDIA)
            repo.finish_stage(media_run, state=JobState.COMPLETED.value, duration_ms=0)
            if result.manifest_path:
                repo.record_artifact(
                    job_id, JobState.INGESTING, "ingest_manifest", result.manifest_path
                )
            if result.audio_path:
                repo.record_artifact(job_id, JobState.INGESTING, "audio", result.audio_path)

            duration_ms = int((time.monotonic() - started) * 1000)
            repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)
            repo.enter_stage(job_id, JobState.ANALYZING_MEDIA)

            logger.info(
                "ingest_completed",
                extra={
                    "sha256": result.sha256,
                    "workspace": result.workspace_root,
                    "media_duration_seconds": result.media.get("duration"),
                    "warnings": result.warnings,
                },
            )
            # Se avisa de la VRAM ahora, no cuando Whisper la pida: si el
            # navegador esta usando 6 de los 8 GB, interesa saberlo antes de
            # esperar veinte minutos de transcripcion para fallar.
            report = gpu.vram_report()
            if report.free_gb is not None:
                logger.info("vram_status", extra={"vram": report.summary()})

            if not as_json:
                typer.secho(result.summary(), fg=typer.colors.GREEN)

    return repo, job_id, result.to_dict()


def _read_manifest(
    workspace_module: Any, source: Path, config: dict[str, Any]
) -> dict[str, Any] | None:
    """Lee el `ingest.json` de un workspace ya ingerido."""
    from src.core import config as config_module

    work_root = config_module.resolve_path(config, "work_root")
    manifest = work_root / workspace_module.safe_stem(source.stem) / "ingest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_transcription(
    source: Path,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    job_id: str,
    as_json: bool,
) -> dict[str, Any]:
    """TRANSCRIBING: Whisper (seccion 14) y normalizacion (seccion 15).

    Las dos van en la misma etapa a proposito. La seccion 49 no tiene un estado
    propio para normalizar, y la normalizacion es una operacion pura y barata
    sobre el transcript. Meterla aqui evita anadir un estado que el blueprint no
    define y que nadie mas va a consultar.

    Al terminar se sueltan los pesos de Whisper: el siguiente paso es Gemma, y
    en 8 GB los ~1.1 GB de Whisper cargado deciding si el modelo cabe (seccion 67).
    """
    from src.media import workspace as workspace_module
    from src.transcription import normalize as normalize_module
    from src.transcription import whisper as whisper_module

    logger = _setup_logging(config)
    work = workspace_module.Workspace(
        root=config_module.resolve_path(config, "work_root") / workspace_module.safe_stem(source.stem)
    )

    if not work.audio.is_file():
        typer.secho(
            f"El audio no existe en {work.audio}. La ingesta tiene que ejecutarse antes.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)

    with logs.log_context(job_id=job_id, stage=JobState.TRANSCRIBING.value):
        repo.enter_stage(job_id, JobState.TRANSCRIBING)
        run_id = repo.start_stage(job_id, JobState.TRANSCRIBING)
        started = time.monotonic()

        try:
            result = whisper_module.transcribe(work.audio, config, job_id=job_id, transcript_path=work.transcript)
            normalized = normalize_module.normalize(work.transcript, job_id=job_id, output_path=work.normalized_transcript)
        except (whisper_module.TranscriptionError, artifacts_module.ArtifactError) as exc:
            code = getattr(exc, "code", "transcription_failed")
            repo.finish_stage(run_id, state=JobState.FAILED.value, error_kind=code, error_detail=str(exc))
            repo.transition(job_id, JobState.FAILED, error_kind=code, error_message=str(exc))
            logger.error("transcription_failed", extra={"error_kind": code, "error_detail": str(exc)})
            typer.secho(f"Error de transcripcion: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_ERROR) from exc

        # Whisper ya no hace falta y Gemma viene despues: se sueltan los pesos
        # ANTES de tocar la base de datos, que puede tardar unos ms.
        whisper_module.release()

        repo.record_artifact(
            job_id,
            JobState.TRANSCRIBING,
            "transcript",
            str(work.transcript),
            sha256=result.transcript_sha256,
        )
        repo.record_artifact(
            job_id,
            JobState.TRANSCRIBING,
            "normalized_transcript",
            str(work.normalized_transcript),
            sha256=normalized.sha256,
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "transcript": result.to_dict(),
            "normalized": normalized.to_dict(),
            "transcript_path": str(work.transcript),
            "normalized_transcript_path": str(work.normalized_transcript),
            "next_state": JobState.BUILDING_CONTENT_MAP.value,
        }

        logger.info(
            "transcription_completed",
            extra={
                "language": result.language,
                "segments": result.segment_count,
                "words": result.word_count,
                "paragraphs": normalized.paragraph_count,
                "duration_ms": duration_ms,
                "reused": result.reused,
            },
        )

        # El JSON lo emite `analyze`, junto con el de la transcripcion, en un
        # unico documento. Imprimirlo aqui supondria dos objetos JSON pegados,
        # que no se puede parsear.
        if not as_json:
            typer.secho(result.summary(), fg=typer.colors.GREEN)
            typer.echo()
            typer.secho(normalized.summary(), fg=typer.colors.GREEN)
            typer.echo(f"\ntranscript  {work.transcript}")
            typer.echo(f"normalizado {work.normalized_transcript}")
            typer.echo(f"\nSiguiente etapa: {JobState.BUILDING_CONTENT_MAP.value} (Gemma, Sprint 4)")

    return payload


def _run_content_map(
    source: Path,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    job_id: str,
    as_json: bool,
    client: Any | None = None,
) -> dict[str, Any]:
    """BUILDING_CONTENT_MAP: Gemma convierte el transcript normalizado en
    `content_map.json` (seccion 17).

    El lease de GPU se toma con `needed_gb=None`: el modelo vive en el proceso
    de LM Studio, que es quien gestiona su VRAM. Lo que se protege es la
    exclusion mutua con Whisper (seccion 49): mientras Gemma analiza, ningun
    stage en proceso puede reservar la tarjeta.
    """
    from src.core import gpu
    from src.media import workspace as workspace_module
    from src.llm import content_map as content_map_module

    logger = _setup_logging(config)
    work = workspace_module.Workspace(
        root=config_module.resolve_path(config, "work_root") / workspace_module.safe_stem(source.stem)
    )

    normalized = artifacts_module.read_or_none(
        work.normalized_transcript, schema_name="normalized_transcript"
    )
    if normalized is None:
        typer.secho(
            f"El transcript normalizado no existe en {work.normalized_transcript}. "
            "La transcripcion tiene que ejecutarse antes.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)

    with logs.log_context(job_id=job_id, stage=JobState.BUILDING_CONTENT_MAP.value):
        repo.enter_stage(job_id, JobState.BUILDING_CONTENT_MAP)
        run_id = repo.start_stage(job_id, JobState.BUILDING_CONTENT_MAP)
        started = time.monotonic()
        try:
            with gpu.gpu_lease("building_content_map", needed_gb=None):
                result = content_map_module.build_content_map(
                    work.normalized_transcript,
                    config,
                    job_id=job_id,
                    output_path=work.content_map,
                    client=client,
                    repo=repo,
                )
        except (content_map_module.ContentMapError, artifacts_module.ArtifactError) as exc:
            code = getattr(exc, "code", "content_map_failed")
            repo.finish_stage(run_id, state=JobState.FAILED.value, error_kind=code, error_detail=str(exc))
            repo.transition(job_id, JobState.FAILED, error_kind=code, error_message=str(exc))
            logger.error("content_map_failed", extra={"error_kind": code, "error_detail": str(exc)})
            typer.secho(f"Error generando el content map: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_ERROR) from exc

        repo.record_artifact(
            job_id,
            JobState.BUILDING_CONTENT_MAP,
            "content_map",
            str(work.content_map),
            sha256=result.sha256,
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "content_map": result.to_dict(),
            "content_map_path": str(work.content_map),
            "next_state": JobState.GENERATING_CANDIDATES.value,
        }

        logger.info(
            "content_map_completed",
            extra={
                "topics": result.topic_count,
                "moments": result.moment_count,
                "hooks": result.hook_count,
                "quotes": result.quote_count,
                "duration_ms": duration_ms,
                "reused": result.reused,
                "warnings": result.warnings,
            },
        )

        # El JSON lo compone `analyze` en un unico documento, junto con el de la
        # ingesta y la transcripcion.
        if not as_json:
            typer.secho(result.summary(), fg=typer.colors.GREEN)
            typer.echo(f"\ncontent map {work.content_map}")
            typer.echo(f"\nSiguiente etapa: {JobState.GENERATING_CANDIDATES.value} (Gemma + scoring, Sprint 5)")

    return payload


def _workspace_for(source: Path, config: dict[str, Any]) -> Any:
    from src.media import workspace as workspace_module

    return workspace_module.Workspace(
        root=config_module.resolve_path(config, "work_root")
        / workspace_module.safe_stem(source.stem)
    )


def _run_candidates(
    source: Path,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    job_id: str,
    as_json: bool,
    client: Any | None = None,
) -> dict[str, Any]:
    """GENERATING_CANDIDATES: Gemma descubre candidatos bloque a bloque (seccion 18).

    Igual que Gemma en BUILDING_CONTENT_MAP, reside en LM Studio: el lease de
    GPU se toma con `needed_gb=None` (seccuidad 49 vs Whisper).
    """
    from src.analysis import candidates as candidates_module
    from src.core import gpu

    logger = _setup_logging(config)
    work = _workspace_for(source, config)

    if artifacts_module.read_or_none(work.content_map, schema_name="content_map") is None:
        typer.secho(
            f"El content map no existe en {work.content_map}. "
            "El candidato engine necesita la etapa anterior.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)

    with logs.log_context(job_id=job_id, stage=JobState.GENERATING_CANDIDATES.value):
        repo.enter_stage(job_id, JobState.GENERATING_CANDIDATES)
        run_id = repo.start_stage(job_id, JobState.GENERATING_CANDIDATES)
        started = time.monotonic()
        sha256 = artifacts_module.sha256_of_file(source)
        try:
            with gpu.gpu_lease("generating_candidates", needed_gb=None):
                result = candidates_module.generate_candidates(
                    work.normalized_transcript,
                    work.content_map,
                    config,
                    job_id=job_id,
                    output_path=work.clip_candidates,
                    input_video=str(source),
                    input_sha256=sha256,
                    blocks_dir=str(work.root / "candidates"),
                    client=client,
                    repo=repo,
                )
        except (candidates_module.CandidateError, artifacts_module.ArtifactError) as exc:
            code = getattr(exc, "code", "candidate_generation_failed")
            repo.finish_stage(run_id, state=JobState.FAILED.value, error_kind=code, error_detail=str(exc))
            repo.transition(job_id, JobState.FAILED, error_kind=code, error_message=str(exc))
            logger.error("candidate_generation_failed", extra={"error_kind": code, "error_detail": str(exc)})
            typer.secho(f"Error generando candidatos: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_ERROR) from exc

        repo.record_artifact(
            job_id,
            JobState.GENERATING_CANDIDATES,
            "clip_candidates",
            str(work.clip_candidates),
            sha256=result.sha256,
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "candidates": result.to_dict(),
            "clip_candidates_path": str(work.clip_candidates),
            "next_state": JobState.RANKING.value,
        }

        logger.info(
            "candidate_generation_completed",
            extra={
                "blocks": result.block_count,
                "candidates": result.candidate_count,
                "selected": result.selected_count,
                "dropped": result.dropped_count,
                "duration_ms": duration_ms,
            },
        )

        if not as_json:
            typer.secho(result.summary(), fg=typer.colors.GREEN)
            typer.echo(f"\nclip_candidates {work.clip_candidates}")
            # Los descartes de GENERACION, uno a uno. El resumen de arriba solo
            # dice cuantos fueron; sin esto no se puede saber si hay que tocar
            # el prompt o el software, que es la pregunta que aparece siempre.
            for reason in result.dropped_generation:
                typer.secho(f"DESCARTE    {reason}", fg=typer.colors.YELLOW)
            typer.echo(f"\nSiguiente etapa: {JobState.RANKING.value} (seleccion, Sprint 5)")

    return payload


def _run_ranking(
    source: Path,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    job_id: str,
    as_json: bool,
) -> dict[str, Any]:
    """RANKING: la seleccion es software puro (secciones 20-23), no IA.

    Se re-lee el clip_candidates recien validado y se re-escribe con el bloque
    `selection` completado; si ya existia, es idempotente.
    """
    from src.analysis import ranking as ranking_module

    logger = _setup_logging(config)
    work = _workspace_for(source, config)

    with logs.log_context(job_id=job_id, stage=JobState.RANKING.value):
        repo.enter_stage(job_id, JobState.RANKING)
        run_id = repo.start_stage(job_id, JobState.RANKING)
        started = time.monotonic()
        set_doc = artifacts_module.read_json(work.clip_candidates, schema_name="candidate_set")
        selection, warnings = ranking_module.select_candidates(set_doc, config)
        set_doc["selection"] = selection
        if warnings:
            # Se ANADE, no se sustituye: `_run_ranking` relee el set que escribio
            # GENERATING_CANDIDATES y sus avisos ("N candidatos descartados en
            # generacion") siguen siendo ciertos. Con `=` desaparecia el
            # resumen y el artefacto se quedaba con el detalle en `drops` pero
            # sin la cifra, que es justo lo que se lee primero.
            previous = [w for w in set_doc.get("warnings", []) if w not in warnings]
            set_doc["warnings"] = previous + list(warnings)
        info = artifacts_module.write_json(
            work.clip_candidates, set_doc, schema_name="candidate_set", overwrite=True
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "selection": selection,
            "clip_candidates_path": str(work.clip_candidates),
            "next_state": JobState.PLANNING_EDITS.value,
        }

        logger.info(
            "ranking_completed",
            extra={
                "selected": len(selection.get("selected", [])),
                "dropped": len(selection.get("dropped", [])),
                "warnings": warnings,
                "duration_ms": duration_ms,
            },
        )

        if not as_json:
            typer.secho(
                f"seleccion   {len(selection['selected'])} elegidos, {len(selection['dropped'])} descartados",
                fg=typer.colors.GREEN,
            )
            for warning in warnings:
                typer.secho(f"AVISO       {warning}", fg=typer.colors.YELLOW)
            typer.echo(f"\nSiguiente etapa: {JobState.PLANNING_EDITS.value} (edit plans, Sprint 5)")

    return payload


def _run_edit_plans(
    source: Path,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    job_id: str,
    as_json: bool,
) -> dict[str, Any]:
    """PLANNING_EDITS: un edit_plan.json por clip seleccionado (seccion 27)."""
    from src.analysis import edit_plan as edit_plan_module

    logger = _setup_logging(config)
    work = _workspace_for(source, config)

    with logs.log_context(job_id=job_id, stage=JobState.PLANNING_EDITS.value):
        repo.enter_stage(job_id, JobState.PLANNING_EDITS)
        run_id = repo.start_stage(job_id, JobState.PLANNING_EDITS)
        started = time.monotonic()
        result = edit_plan_module.build_edit_plans(
            work.clip_candidates,
            config,
            job_id=job_id,
            output_dir=work.edit_plans,
            input_video=str(source),
        )
        for path in result.paths:
            repo.record_artifact(
                job_id,
                JobState.PLANNING_EDITS,
                f"edit_plan:{Path(path).stem}",
                str(path),
                sha256=artifacts_module.sha256_of_file(path),
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "edit_plans": result.to_dict(),
            "next_state": JobState.RENDERING.value,
        }

        logger.info(
            "edit_plans_completed",
            extra={"clip_count": result.clip_count, "duration_ms": duration_ms},
        )

        if not as_json:
            typer.secho(result.summary(), fg=typer.colors.GREEN)
            typer.echo(f"\nSiguiente etapa: {JobState.RENDERING.value} (python main.py render {job_id})")

    return payload


def _run_render(
    job_id: str,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    as_json: bool,
) -> dict[str, Any]:
    """RENDERING: ejecuta los edit_plan.json del job (secciones 27-30).

    A diferencia de las demas etapas, esta NO recibe el video como argumento: la
    lista de clips sale de los planes, y cada plan lleva dentro su ventana de
    origen. El transcript solo se usa para los subtitulos.

    Los planes que ya tienen su fichero renderizado se saltan (seccion 50): un
    re-render tras un fallo de Remotion no vuelve a codificar lo que ya salio.
    """
    from src.media import render as render_module
    from src.media import workspace as workspace_module

    logger = _setup_logging(config)
    job = repo.get_job(job_id)
    if job is None:
        typer.secho(f"Job desconocido: {job_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    video = repo.get_video(job["video_id"])
    if video is None:
        typer.secho(f"El job {job_id} no tiene video asociado.", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    work = workspace_module.Workspace(
        root=config_module.resolve_path(config, "work_root")
        / workspace_module.safe_stem(Path(video["path"]).stem)
    )
    plan_paths = sorted(work.edit_plans.glob("edit_plan_*.json"))
    if not plan_paths:
        typer.secho(
            f"No hay edit_plan.json en {work.edit_plans}. "
            "Ejecuta antes 'python main.py candidates <video>'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)

    # El transcript alimenta los cues de subtitulo. Si no hay, se renderiza sin
    # texto: es preferible un clip mudo a un pipeline parado (seccion 66).
    transcript = artifacts_module.read_or_none(work.transcript, schema_name="transcript")
    if transcript is None:
        logger.warning("render_without_transcript", extra={"job_id": job_id})

    with logs.log_context(job_id=job_id, stage=JobState.RENDERING.value):
        repo.enter_stage(job_id, JobState.RENDERING)
        run_id = repo.start_stage(job_id, JobState.RENDERING)
        started = time.monotonic()
        try:
            result = render_module.render_plans(
                plan_paths,
                config,
                transcript=transcript,
                output_dir=work.renders,
                job_id=job_id,
                remotion_dir=config_module.resolve_path(config, "remotion_dir"),
            )
        except (render_module.RenderError, artifacts_module.ArtifactError) as exc:
            code = getattr(exc, "code", "render_failed")
            repo.finish_stage(run_id, state=JobState.FAILED.value, error_kind=code, error_detail=str(exc))
            repo.transition(job_id, JobState.FAILED, error_kind=code, error_message=str(exc))
            logger.error("render_failed", extra={"error_kind": code, "error_detail": str(exc)})
            typer.secho(f"Error renderizando: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_ERROR) from exc

        for clip in result.plans:
            repo.record_artifact(
                job_id,
                JobState.RENDERING,
                f"render:{clip.clip_id}",
                clip.output_path,
                sha256=clip.sha256,
            )
            for aux in clip.aux_paths:
                # El kind es unico por (job, stage): sin el sufijo, el master y
                # el props.json se pisan y solo queda registrado el ultimo.
                suffix = Path(aux).suffix.lstrip(".") or "aux"
                repo.record_artifact(
                    job_id, JobState.RENDERING, f"render_aux:{clip.clip_id}:{suffix}", aux
                )

        duration_ms = int((time.monotonic() - started) * 1000)
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=duration_ms)

        payload = {
            "job_id": job_id,
            "render": result.to_dict(),
            "renders_dir": str(work.renders),
            "next_state": JobState.QUALITY_CONTROL.value,
        }

        logger.info(
            "render_completed",
            extra={
                "clips": len(result.plans),
                "reused": sum(1 for clip in result.plans if clip.reused),
                "duration_ms": duration_ms,
                "renderers": sorted({clip.renderer for clip in result.plans}),
            },
        )

        if not as_json:
            typer.secho(result.summary(), fg=typer.colors.GREEN)
            typer.echo(f"\nrenders     {work.renders}")
            typer.echo(f"\nSiguiente etapa: {JobState.QUALITY_CONTROL.value} (Sprint 7)")

    return payload


def _run_report(
    job_id: str,
    config: dict[str, Any],
    repo: db_module.JobRepository,
    *,
    as_json: bool,
    skip_llm: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """QUALITY_CONTROL + GENERATING_SEO + report.json (secciones 33-35, 56).

    Para cada clip renderizado:
    1. QC tecnico (media + subtitulos) con FFprobe, sin IA.
    2. Segundo LLM pass (evaluacion semantica) sobre el transcript del clip.
    3. SEO engine: metadata independiente por plataforma.
    4. clip_record.json del clip (seccion 37).

    Y al final el report.json del job (seccion 56).

    Un fallo de IA en un clip NO tumba el job: ese clip se registra como
    fallido con su motivo y se sigue con los demas. Perder el QC semantico de
    tres clips por un cuarto caido no es un informe, es un pipeline parado.
    `skip_llm` hace las comprobaciones tecnicas sin llamar al modelo.
    """
    from src.quality import qc as qc_module
    from src.quality import report as report_module
    from src.quality import seo as seo_module
    from src.quality import semantic as semantic_module
    from src.media import workspace as workspace_module
    from src.subtitles import cues as cues_module

    logger = _setup_logging(config)
    job = repo.get_job(job_id)
    if job is None:
        typer.secho(f"Job desconocido: {job_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    video = repo.get_video(job["video_id"])
    if video is None:
        typer.secho(f"El job {job_id} no tiene video asociado.", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    work = workspace_module.Workspace(
        root=config_module.resolve_path(config, "work_root")
        / workspace_module.safe_stem(Path(video["path"]).stem)
    )
    plan_paths = sorted(work.edit_plans.glob("edit_plan_*.json"))
    if not plan_paths:
        typer.secho(
            f"No hay edit_plan.json en {work.edit_plans}. Ejecuta antes 'render'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(EXIT_ERROR)

    transcript = artifacts_module.read_or_none(work.transcript, schema_name="transcript")
    normalized = artifacts_module.read_or_none(
        work.normalized_transcript, schema_name="normalized_transcript"
    )
    candidate_set = artifacts_module.read_or_none(
        work.clip_candidates, schema_name="candidate_set"
    )
    paragraphs = list((normalized or {}).get("paragraphs", []) or [])

    candidates_by_id = {
        c.get("candidate_id"): c
        for c in ((candidate_set or {}).get("candidates", []) or [])
        if isinstance(c, dict)
    }

    quality_cfg = dict(config.get("quality", {}) or {})
    do_semantic = bool(quality_cfg.get("semantic_qc", True)) and not skip_llm
    do_seo = not skip_llm

    with logs.log_context(job_id=job_id, stage=JobState.QUALITY_CONTROL.value):
        repo.enter_stage(job_id, JobState.QUALITY_CONTROL)
        run_id = repo.start_stage(job_id, JobState.QUALITY_CONTROL)
        started = time.monotonic()
        errors: list[str] = []
        record_results: list[report_module.ClipRecordResult] = []
        seo_client: semantic_module.LMStudioClient | None = None
        if do_semantic or do_seo:
            seo_client = semantic_module.client_from_config(config)

        for plan_path in plan_paths:
            plan = artifacts_module.read_json(plan_path, schema_name="edit_plan")
            clip_id = str(plan["clip_id"])
            render_path = work.renders / f"{clip_id}.mp4"
            source = plan.get("source", {}) or {}
            start = float(source.get("start", 0.0))
            end = float(source.get("end", 0.0))
            expected_duration = float(source.get("duration") or (end - start))

            # Los cues se recalculan con la MISMA logica que el render, para
            # que el QC valide los subtitulos que realmente se quemaron.
            cues: list[dict[str, Any]] = []
            sub_cfg = dict(plan.get("subtitles") or {})
            if sub_cfg.get("enabled", False) and transcript:
                try:
                    built = cues_module.build_cues(
                        transcript,
                        clip_start=start,
                        clip_end=end,
                        max_words_per_line=int(sub_cfg.get("max_words_per_line", 4)),
                        max_lines=int(sub_cfg.get("max_lines", 2)),
                        max_chars_per_line=int(sub_cfg.get("max_chars_per_line", 22)),
                        min_duration_ms=int(sub_cfg.get("min_duration_ms", 800)),
                        max_duration_ms=int(sub_cfg.get("max_duration_ms", 4000)),
                    )
                    cues = [cue.to_dict() for cue in built]
                except Exception as exc:  # noqa: BLE001 - un fallo aqui no tumba el job
                    errors.append(f"{clip_id}: no se pudieron reconstruir los cues: {exc}")
                    logger.warning(
                        "qc_cues_unavailable",
                        extra={"clip_id": clip_id, "error_detail": str(exc)[:300]},
                    )

            clip_qc = qc_module.run_clip_qc(
                render_path,
                cues,
                clip_id=clip_id,
                expected_duration=expected_duration,
                config=config,
            )

            # Segundo LLM pass: evaluacion semantica (secciones 33-34).
            if do_semantic and paragraphs:
                try:
                    verdict = semantic_module.run_semantic_qc(
                        clip_id=clip_id,
                        paragraphs=paragraphs,
                        start=start,
                        end=end,
                        clip_metadata=_clip_metadata_for_llm(plan, candidates_by_id.get(clip_id)),
                        edit_information={
                            "clip_id": clip_id,
                            "source_start": start,
                            "source_end": end,
                            "duration": expected_duration,
                            "crop_mode": (plan.get("crop", {}) or {}).get("mode"),
                            "subtitles": bool(
                                (plan.get("subtitles", {}) or {}).get("enabled", False)
                            ),
                        },
                        config=config,
                        cache_dir=work.root / "qc",
                        client=seo_client,
                        overwrite=overwrite,
                    )
                    clip_qc.semantic = {
                        "approved": verdict.approved,
                        "score": verdict.score,
                        "problems": verdict.to_dict()["problems"],
                        "recommendations": verdict.to_dict()["recommendations"],
                        "model": verdict.body.get("model"),
                        "prompt_version": verdict.body.get("prompt_version"),
                    }
                except (
                    semantic_module.SemanticError,
                    artifacts_module.ArtifactError,
                ) as exc:
                    errors.append(f"{clip_id}: QC semantico fallo: {exc}")
                    logger.warning(
                        "semantic_qc_failed",
                        extra={"clip_id": clip_id, "error_detail": str(exc)[:300]},
                    )

            # SEO engine: metadata por plataforma (seccion 35).
            metadata: dict[str, Any] | None = None
            if do_seo and paragraphs and clip_qc.passed:
                try:
                    seo_result = seo_module.run_seo(
                        clip_id=clip_id,
                        paragraphs=paragraphs,
                        start=start,
                        end=end,
                        clip_metadata=_clip_metadata_for_llm(plan, candidates_by_id.get(clip_id)),
                        config=config,
                        cache_dir=work.root / "qc",
                        client=seo_client,
                        overwrite=overwrite,
                    )
                    report_module.write_metadata_files(
                        seo_result, work.root / "metadata" / clip_id
                    )
                    metadata = seo_result.files()
                except (seo_module.SEOError, artifacts_module.ArtifactError) as exc:
                    errors.append(f"{clip_id}: SEO fallo: {exc}")
                    logger.warning(
                        "seo_failed", extra={"clip_id": clip_id, "error_detail": str(exc)[:300]}
                    )

            record = report_module.build_clip_record(
                clip_id=clip_id,
                job_id=job_id,
                plan=plan,
                candidate=candidates_by_id.get(clip_id, {}),
                qc=clip_qc.to_record_qc(config),
                metadata=metadata,
                source_sha256=str(video.get("sha256") or ""),
            )
            record_path = work.root / "clip_records" / f"{clip_id}.json"
            result = report_module.ClipRecordResult(clip_id=clip_id, record=record)
            try:
                report_module.write_clip_record(result, record_path)
            except artifacts_module.ArtifactError as exc:
                errors.append(f"{clip_id}: clip_record invalido: {exc}")
            record_results.append(result)

            repo.record_artifact(
                job_id,
                JobState.QUALITY_CONTROL,
                f"clip_record:{clip_id}",
                str(record_path),
            )

            # Indice consultable del clip (seccion 37). El JSON de arriba es el
            # registro completo; esta fila es lo que permite preguntar por SQL
            # ("clips de este job que pasaron el QC") y lo que Sprint 8 usa
            # como FK para colgarle las metricas de cada plataforma. Sin ella la
            # tabla queda vacia y las metricas no tienen a que colgarse.
            repo.upsert_clip_record(
                job_id=job_id,
                clip_id=clip_id,
                candidate_id=record.get("candidate_id"),
                start=float(source.get("start", 0.0)),
                end=float(source.get("end", 0.0)),
                duration=expected_duration,
                score_total=(record.get("selection") or {}).get("score"),
                hook_type=(record.get("content") or {}).get("hook_type"),
                content_type=(record.get("content") or {}).get("content_type"),
                topic=(record.get("content") or {}).get("topic"),
                hook_origin=(record.get("content") or {}).get("hook_origin"),
                requires_generated_hook=(record.get("content") or {}).get(
                    "requires_generated_hook"
                ),
                record_path=str(record_path),
                edit_plan_path=str(plan_path),
                render_path=str(render_path) if render_path.is_file() else None,
                qc_passed=bool(clip_qc.passed),
            )

        processing = time.monotonic() - started

        summary = report_module.build_report(
            job_id=job_id,
            source=str(video.get("path") or "") or None,
            duration=float(video.get("duration")) if video.get("duration") else None,
            candidate_set=candidate_set,
            clip_records=record_results,
            processing_time_seconds=processing,
            errors=errors,
        )
        reports_root = config_module.resolve_path(config, "reports_root")
        report_path = reports_root / f"report_{job_id}.json"
        report_module.write_report(summary, report_path)

    # Un QC fallido en algunos clips no es un fallo del JOB: el job se completo,
    # lo que paso es que algunos clips no se publican. Solo se marca FAILED si
    # no paso NINGUN clip, que si es un fallo real del pipeline.
    if summary.clips_rendered == 0 and record_results:
        repo.finish_stage(
            run_id,
            state=JobState.FAILED.value,
            error_kind="qc_all_clips_failed",
            error_detail="ningun clip paso el QC",
        )
        repo.transition(
            job_id,
            JobState.FAILED,
            error_kind="qc_all_clips_failed",
            error_message="ningun clip paso el QC",
        )
    else:
        repo.finish_stage(run_id, state=JobState.COMPLETED.value, duration_ms=int(processing * 1000))
        # El flujo de la seccion 49 obliga a pasar por GENERATING_SEO antes de
        # COMPLETED, y `enter_stage` ni retrocede ni salta etapas. Este comando
        # es dueno de las dos etapas (QC y SEO), asi que se avanza por GENERATING_SEO
        # siempre, tambien con --skip-llm: ahi la etapa se ejecuto en modo
        # diagnostico, pero el estado del job tiene que reflejar las dos.
        repo.enter_stage(job_id, JobState.GENERATING_SEO)
        repo.enter_stage(job_id, JobState.COMPLETED)

    logger.info(
        "report_completed",
        extra={
            "clips": len(record_results),
            "passed": summary.clips_rendered,
            "rejected": summary.clips_rejected,
            "errors": len(summary.errors),
        },
    )

    payload = {
        "job_id": job_id,
        "report": summary.to_dict(),
        "report_path": str(report_path),
        "next_state": (
            JobState.FAILED.value
            if summary.clips_rendered == 0 and record_results
            else JobState.COMPLETED.value
        ),
    }

    if not as_json:
        typer.secho(summary.summary(), fg=typer.colors.GREEN)
        typer.echo(f"\nreport        {report_path}")

    # Si ningun clip paso el QC el job quedo en FAILED, y el proceso tambien tiene
    # que salir con error: un script que encadena `render` + `report` no puede
    # darse por ENTERADO de un exit 0 cuando no hay nada publicable.
    if summary.clips_rendered == 0 and record_results:
        typer.secho("Ningun clip paso el QC.", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_ERROR)

    return payload


def _clip_metadata_for_llm(
    plan: dict[str, Any], candidate: dict[str, Any] | None
) -> dict[str, Any]:
    """Metadata de un clip que se le pasa al modelo (QC semantico y SEO).

    Solo datos que el modelo puede usar para evaluar o describir el clip. Se
    serializa como JSON en el prompt, asi que solo puede contener tipos simples.
    """
    candidate = candidate or {}
    content = candidate.get("content", {}) or {}
    hook = (plan.get("hook") or candidate.get("hook") or {})
    return {
        "clip_id": plan.get("clip_id"),
        "topic": content.get("topic"),
        "summary": content.get("summary"),
        "content_type": content.get("content_type"),
        "hook_text": hook.get("text"),
        "hook_type": hook.get("type"),
        "hook_origin": hook.get("origin"),
        "value_provided": candidate.get("value_provided"),
    }


@app.command()
def process(
    video: str = typer.Argument(..., help="Ruta del video de entrada"),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
    skip_llm: bool = typer.Option(
        False, "--skip-llm", help="Solo QC tecnico al final: sin QC semantico ni SEO"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Vuelve a pedir el QC semantico y el SEO aunque esten cacheados"
    ),
) -> None:
    """VIDEO -> WHISPER -> GEMMA -> CANDIDATES -> RENDER -> QC -> SEO.

    Es el atajo de las cuatro etapas sueltas (`analyze`, `candidates`, `render`,
    `report`) para cuando se quiere el resultado y no el estado intermedio.
    Cada etapa sigue siendo ejecutable por separado: aqui solo se encadenan.

    Las etapas se llaman SIEMPRE, sin preguntar si ya hay artefacto: las de
    analisis reutilizan su cache y las de render/QC son idempotentes, asi que
    repetir el proceso no repaga al modelo ni regraba un mp4 que ya estaba
    bien. Es el mismo criterio que usa `candidates` (seccion 51), y por el
    mismo motivo: saltarse una etapa rompe la maquina de estados cuando un job
    vuelve atras.

    Con `--json` emite UN solo documento con el resumen de todas las etapas, no
    uno por etapa.
    """
    config = _load_config()
    repo, job_id, ingest_payload = _run_ingest(video, config, as_json=as_json)

    source = Path(video)
    if not source.is_absolute():
        source = (config_module.project_root(config) / source).resolve()

    stages: dict[str, Any] = {"ingest": ingest_payload}

    for label, runner in (
        ("transcription", _run_transcription),
        ("content_map", _run_content_map),
        ("candidates", _run_candidates),
        ("ranking", _run_ranking),
        ("edit_plans", _run_edit_plans),
    ):
        stages[label] = runner(source, config, repo, job_id=job_id, as_json=as_json)

    # RENDERING y QUALITY_CONTROL se guiian por job_id, no por ruta.
    stages["render"] = _run_render(job_id, config, repo, as_json=as_json)
    stages["report"] = _run_report(
        job_id, config, repo, as_json=as_json, skip_llm=skip_llm, overwrite=overwrite
    )

    if as_json:
        typer.echo(
            json.dumps(
                {"job_id": job_id, **stages}, ensure_ascii=False, indent=2, default=str
            )
        )
        return

    report_payload = stages["report"]
    typer.secho(f"\njob         {job_id}", fg=typer.colors.CYAN)
    typer.secho(f"estado      {report_payload.get('state', 'desconocido')}", fg=typer.colors.CYAN)
    for message in report_payload.get("warnings", []) or []:
        typer.secho(f"AVISO       {message}", fg=typer.colors.YELLOW)
    for message in report_payload.get("errors", []) or []:
        typer.secho(f"ERROR       {message}", fg=typer.colors.RED)
    typer.echo("\nClips listos en el workspace. Revisa el MP4 antes de publicar.")


@app.command()
def analyze(
    video: str = typer.Argument(..., help="Ruta del video de entrada"),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
    skip_transcription: bool = typer.Option(
        False, "--skip-transcription", help="Solo ingesta: no transcribe (util para validar el entorno)"
    ),
    skip_content_map: bool = typer.Option(
        False, "--skip-content-map", help="Ingesta y transcripcion, sin content map de Gemma"
    ),
) -> None:
    """Ingesta + transcripcion + content map de Gemma. No renderiza (seccion 66).

    Cubre los pasos 1-9 de la seccion 12 (el mapa de contenido de la 17) y se
    detiene en GENERATING_CANDIDATES, que es donde empieza el Sprint 5.
    """
    config = _load_config()
    repo, job_id, ingest_payload = _run_ingest(video, config, as_json=as_json)

    if skip_transcription:
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        **ingest_payload,
                        "job_id": job_id,
                        "transcription": None,
                        "next_state": JobState.ANALYZING_MEDIA.value,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        typer.echo("--skip-transcription: la ingesta para en ANALYZING_MEDIA.")
        return

    source = Path(video)
    if not source.is_absolute():
        source = (config_module.project_root(config) / source).resolve()
    transcription_payload = _run_transcription(
        source, config, repo, job_id=job_id, as_json=as_json
    )

    if skip_content_map:
        if as_json:
            typer.echo(
                json.dumps(
                    {
                        **ingest_payload,
                        "job_id": job_id,
                        "transcription": transcription_payload,
                        "content_map": None,
                        "next_state": JobState.BUILDING_CONTENT_MAP.value,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        typer.echo("--skip-content-map: el pipeline para en BUILDING_CONTENT_MAP.")
        return

    content_payload = _run_content_map(source, config, repo, job_id=job_id, as_json=as_json)

    if as_json:
        # Un SOLO documento. Las claves de la ingesta se quedan en el primer
        # nivel para no romper a quien ya consumia `analyze --json`, y lo de la
        # transcripcion y el content map van anidados bajo sus claves.
        typer.echo(
            json.dumps(
                {
                    **ingest_payload,
                    "job_id": job_id,
                    "transcription": transcription_payload,
                    "content_map": content_payload,
                    "next_state": content_payload["next_state"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


@app.command()
def candidates(
    video: str = typer.Argument(..., help="Ruta del video de entrada"),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
) -> None:
    """Ingesta + transcripcion + content map + candidatos + seleccion + edit plans.

    Es el pipeline completo hasta justo antes de renderizar (secciones 18-27):
    GENERATING_CANDIDATES, RANKING y PLANNING_EDITS. Las etapas previas se
    reutilizan si sus artefactos ya existen (seccion 51).
    """
    config = _load_config()
    repo, job_id, ingest_payload = _run_ingest(video, config, as_json=as_json)

    source = Path(video)
    if not source.is_absolute():
        source = (config_module.project_root(config) / source).resolve()

    work = _workspace_for(source, config)
    # Sin condicionales: `_run_transcription` y `_run_content_map` reutilizan el
    # artefacto si ya existe (whisper/normalize/content_map tienen ruta `reused`),
    # y al llamarse SIEMPRE hacen avanzar la maquina de estados en orden. Si se
    # saltaran cuando el artefacto ya esta, un job que fallo y volvio atras (por
    # ejemplo en GENERATING_CANDIDATES) no podria reentrar la etapa: la transicion
    # ANALYZING_MEDIA -> GENERATING_CANDIDATES no es valida (solo se avanza de
    # uno en uno, seccion 50).
    transcription_payload = _run_transcription(source, config, repo, job_id=job_id, as_json=as_json)
    content_payload = _run_content_map(source, config, repo, job_id=job_id, as_json=as_json)

    candidates_payload = _run_candidates(source, config, repo, job_id=job_id, as_json=as_json)
    ranking_payload = _run_ranking(source, config, repo, job_id=job_id, as_json=as_json)
    edits_payload = _run_edit_plans(source, config, repo, job_id=job_id, as_json=as_json)

    if as_json:
        typer.echo(
            json.dumps(
                {
                    **ingest_payload,
                    "job_id": job_id,
                    "transcription": transcription_payload,
                    "content_map": content_payload,
                    "candidates": candidates_payload,
                    "ranking": ranking_payload,
                    "edit_plans": edits_payload,
                    "next_state": edits_payload["next_state"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


@app.command()
def render(
    job_id: str = typer.Argument(..., help="Job ya planificado"),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
) -> None:
    """RENDERING: renderiza los clips de un job con FFmpeg y Remotion (secciones 27-30).

    Lee los `edit_plan.json` de PLANNING_EDITS y produce un mp4 9:16 por clip,
    con subtitulos. Es idempotente: los clips ya renderizados no se repiten.
    """
    config = _load_config()
    repo = _open_repo(config)
    payload = _run_render(job_id, config, repo, as_json=as_json)
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def report(
    job_id: str = typer.Argument(..., help="Job a reportar"),
    as_json: bool = typer.Option(False, "--json", help="Salida en JSON"),
    skip_llm: bool = typer.Option(
        False, "--skip-llm", help="Solo QC tecnico, sin llamar a Gemma (QC semantico ni SEO)"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Vuelve a pedir el QC semantico y el SEO aunque esten cacheados"
    ),
) -> None:
    """QUALITY_CONTROL + SEO + report.json (secciones 33-35 y 56).

    Valida cada clip ya renderizado (media y subtitulos con FFprobe, narrativa
    con el segundo LLM pass), escribe la metadata por plataforma y cierra con el
    clip_record de cada clip y el report.json del job.

    Con `--skip-llm` hace solo las comprobaciones tecnicas: util para ver si el
    problema es del render o del modelo.
    """
    config = _load_config()
    repo = _open_repo(config)
    payload = _run_report(
        job_id, config, repo, as_json=as_json, skip_llm=skip_llm, overwrite=overwrite
    )
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@performance_app.command("sync")
def performance_sync() -> None:
    """Sincroniza metricas de las plataformas publicadas."""
    _not_ready("performance sync")


def main() -> None:
    """Punto de entrada. `lvi` y `python main.py` llegan aqui."""
    _configure_console()
    config = config_module.load() if (ROOT / "config" / "default.yaml").is_file() else None
    if config is not None:
        try:
            _setup_logging(config)
        except OSError:
            # Un log que no se puede escribir no debe impedir usar la CLI.
            pass
    app()


if __name__ == "__main__":
    main()
