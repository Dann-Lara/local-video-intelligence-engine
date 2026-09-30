"""Persistencia en SQLite (blueprint secciones 44, 49, 50, 51 y 74).

Restricciones que fijan el diseno:

- Seccion 74: SQLite basta para el MVP, pero debe poder migrar a PostgreSQL
  sin tocar el resto del sistema. Por eso no se usa ninguna funcion propietaria
  de SQLite, no hay AUTOINCREMENT, y los tipos son del ANSI portable.
- Seccion 49: el estado de un job recorre una maquina de estados explícita, no
  un string libre. Una transicion invalida se rechaza en Python, no con un
  CHECK de SQL, porque el mensaje de error tiene que ser util.
- Seccion 50: los checkpoints se guardan por etapa, para poder reanudar desde
  RENDERING sin repetir Whisper ni el analisis.
- Seccion 51: la idempotencia se apoya en una clave de contenido (sha256 del
  video de origen), no en el nombre del fichero.

Decision deliberada: los documentos completos (transcript, content_map, clip)
NO se guardan en columnas. Se guardan como JSON en disco y en la base va el
ruta, el sha256 y el id. La base es para consultar y aprender, no para ser el
almacen de trabajo.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_VERSION = 1


class JobState(StrEnum):
    """Seccion 49."""

    QUEUED = "QUEUED"
    INGESTING = "INGESTING"
    ANALYZING_MEDIA = "ANALYZING_MEDIA"
    TRANSCRIBING = "TRANSCRIBING"
    BUILDING_CONTENT_MAP = "BUILDING_CONTENT_MAP"
    GENERATING_CANDIDATES = "GENERATING_CANDIDATES"
    RANKING = "RANKING"
    PLANNING_EDITS = "PLANNING_EDITS"
    RENDERING = "RENDERING"
    QUALITY_CONTROL = "QUALITY_CONTROL"
    GENERATING_SEO = "GENERATING_SEO"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


# Flujo feliz de la seccion 49, en orden. Cualquier salto fuera de esta
# secuencia es un bug de orquestacion, no un estado valido.
PIPELINE_ORDER: tuple[JobState, ...] = (
    JobState.QUEUED,
    JobState.INGESTING,
    JobState.ANALYZING_MEDIA,
    JobState.TRANSCRIBING,
    JobState.BUILDING_CONTENT_MAP,
    JobState.GENERATING_CANDIDATES,
    JobState.RANKING,
    JobState.PLANNING_EDITS,
    JobState.RENDERING,
    JobState.QUALITY_CONTROL,
    JobState.GENERATING_SEO,
    JobState.COMPLETED,
)

# Estados desde los que tiene sentido reintentar (seccion 58: recoverable).
RETRYABLE: frozenset[JobState] = frozenset(PIPELINE_ORDER) - {
    JobState.COMPLETED,
    JobState.FAILED,
}

# Etapas con trabajo real: ni QUEUED (estado de pre-parada) ni COMPLETED
# (estado terminal) son etapas que se puedan "completar" una por una. Sin este
# recorte, `resume_stage` de un job recien creado devolveria QUEUED siempre.
WORK_STAGES: tuple[JobState, ...] = PIPELINE_ORDER[1:-1]

# Solo COMPLETED es terminal. FAILED se puede abandonar: la seccion 50 manda
# reanudar un job fallido desde el ultimo checkpoint, y el unico camino para
# volver a el es FAILED -> RETRYING -> <etapa>. Marcarlo terminal (como estaba)
# hacia imposible cualquier reintento y contradecía a `resume_stage`, que si
# sabe indicar la etapa desde la que seguir.
TERMINAL: frozenset[JobState] = frozenset({JobState.COMPLETED})


class InvalidTransitionError(RuntimeError):
    """Seccion 49: transicion de estado no permitida."""


def can_transition(current: JobState, target: JobState) -> bool:
    if target in (JobState.FAILED, JobState.RETRYING):
        # Fallar o reintentar es legitimo desde cualquier estado que no haya
        # terminado con exito. COMPLETED es el unico final real.
        return current != JobState.COMPLETED
    if current == JobState.RETRYING:
        # Al reintentar se vuelve al inicio del pipeline; el orquestador decide
        # en realidad a que etapa volver (seccion 50).
        return target in PIPELINE_ORDER
    if current not in PIPELINE_ORDER or target not in PIPELINE_ORDER:
        return False
    return (
        PIPELINE_ORDER.index(target) == PIPELINE_ORDER.index(current) + 1
    )


def next_stage(current: JobState) -> JobState | None:
    """Siguiente etapa del flujo feliz, o None si ya se llego a COMPLETED."""
    if current not in PIPELINE_ORDER:
        return None
    index = PIPELINE_ORDER.index(current)
    if index + 1 >= len(PIPELINE_ORDER):
        return None
    return PIPELINE_ORDER[index + 1]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id            TEXT PRIMARY KEY,
    path          TEXT NOT NULL,
    sha256        TEXT NOT NULL UNIQUE,
    duration      REAL,
    width         INTEGER,
    height        INTEGER,
    fps           REAL,
    audio_codec   TEXT,
    video_codec   TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    video_id        TEXT NOT NULL REFERENCES videos(id),
    state           TEXT NOT NULL,
    current_stage   TEXT,
    error_kind      TEXT,
    error_message   TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    config_hash     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    stage       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (job_id, stage, kind)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);

CREATE TABLE IF NOT EXISTS stage_runs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(id),
    stage        TEXT NOT NULL,
    attempt      INTEGER NOT NULL DEFAULT 1,
    state        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_ms  INTEGER,
    error_kind   TEXT,
    error_detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_stage_runs_job ON stage_runs(job_id, stage);

CREATE TABLE IF NOT EXISTS model_runs (
    id            TEXT PRIMARY KEY,
    job_id        TEXT REFERENCES jobs(id),
    stage         TEXT NOT NULL,
    model         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    prompt_version TEXT,
    schema_name   TEXT,
    attempts      INTEGER NOT NULL DEFAULT 1,
    succeeded     INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    error_detail  TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_runs_job ON model_runs(job_id);

CREATE TABLE IF NOT EXISTS clip_records (
    id                TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL REFERENCES jobs(id),
    candidate_id      TEXT NOT NULL,
    start             REAL NOT NULL,
    end               REAL NOT NULL,
    duration          REAL NOT NULL,
    score_total       REAL,
    hook_type         TEXT,
    content_type      TEXT,
    topic             TEXT,
    hook_origin       TEXT,
    requires_generated_hook INTEGER,
    record_path       TEXT,
    edit_plan_path    TEXT,
    render_path       TEXT,
    qc_passed         INTEGER,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clip_records_job ON clip_records(job_id);
CREATE INDEX IF NOT EXISTS idx_clip_records_score ON clip_records(score_total DESC);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id                TEXT PRIMARY KEY,
    clip_record_id    TEXT NOT NULL REFERENCES clip_records(id),
    platform          TEXT NOT NULL,
    platform_clip_id  TEXT,
    age_hours         INTEGER NOT NULL,
    views             INTEGER,
    shown_in_feed     INTEGER,
    unique_viewers    INTEGER,
    likes             INTEGER,
    comments          INTEGER,
    shares            INTEGER,
    average_percentage_viewed REAL,
    like_rate         REAL,
    comment_rate      REAL,
    captured_at       TEXT NOT NULL,
    derived_metrics_version TEXT,
    UNIQUE (clip_record_id, platform, age_hours)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_clip
    ON performance_snapshots(clip_record_id, platform);
"""


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_database_url(url: str) -> Path:
    """Extrae la ruta del fichero de un `sqlite:///...`.

    Se acepta el prefijo que documenta .env.example. Cualquier otro esquema es
    un error explicito: la seccion 74 pide que la migracion a PostgreSQL sea
    posible despues, no que se suporte a medias ahora.
    """
    if not url.startswith("sqlite:///"):
        raise ValueError(
            f"solo se soporta sqlite:/// en el MVP (seccion 74). Recibido: {url!r}"
        )
    raw = url[len("sqlite:///") :]
    if not raw:
        raise ValueError(f"URL de base de datos sin ruta: {url!r}")
    return Path(raw)


@dataclass
class Database:
    """Envoltura fina sobre la conexion SQLite.

    No implementa un ORM: la seccion 74 lo declara opcional, y anadirlo ahora
    solo complicaria la migracion a PostgreSQL en lugar de facilitarla.
    """

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        # detect_types y row_factory solo usan la API estandar.
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaccion con rollback explicito ante cualquier excepcion."""
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self.connect()
        try:
            yield conn.cursor()
        finally:
            conn.close()

    def initialize(self) -> "Database":
        conn = self.connect()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        finally:
            conn.close()
        return self

    def schema_version(self) -> int:
        with self.cursor() as cur:
            cur.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
            row = cur.fetchone()
        return int(row["value"]) if row else 0

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchone()


def open_database(config: dict[str, Any] | None = None, *, initialize: bool = True) -> Database:
    """Abre (y por defecto crea) la base del proyecto.

    `sqlite:///database/x.db` se resuelve contra la raiz del proyecto, igual que
    cualquier otra ruta relativa del config.
    """
    from src.core import config as config_module

    config = config or config_module.load()
    url = config_module.get(
        config, "orchestration.database_url", "sqlite:///database/local_video_intelligence.db"
    )
    raw = parse_database_url(str(url))
    path = raw if raw.is_absolute() else (config_module.project_root(config) / raw)
    database = Database(path=path)
    return database.initialize() if initialize else database


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def dumps(value: Any) -> str:
    """Serializacion estable para columnas JSON, si hiciera falta."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def new_id(prefix: str) -> str:
    """Id legible y ordenable en el tiempo: `clip_20260929_015310_a1b2c3`.

    El sufijo aleatorio evita colisiones cuando dos jobs nacen en el mismo
    segundo, que es justo lo que pasa al procesar varios ficheros seguidos.
    """
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}"


class JobRepository:
    """Operaciones sobre la maquina de estados de jobs (secciones 49, 50, 51).

    Vive aqui, y no en `src/orchestration/`, porque en Sprint 1 todavia no hay
    etapas que orquestar: solo hace falta poder registrar y consultar el estado
    de forma fiable.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    # --- videos (seccion 45) ---

    def register_video(
        self,
        *,
        path: str,
        sha256: str,
        video_id: str | None = None,
        duration: float | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        video_codec: str | None = None,
        audio_codec: str | None = None,
    ) -> str:
        """Registra un video de forma idempotente por sha256 (seccion 51).

        Volver a procesar el mismo fichero no crea un duplicado: se devuelve el
        id existente. La identidad es el contenido, no el nombre ni la ruta.

        Si el video ya existia con la metadata a null, se rellena. Sin esto, un
        job creado por un intento fallido deja la fila con `duration`/`width` a
        null para siempre, porque la segunda llamada solo devolvia el id.
        """
        existing = self.db.query_one("SELECT id FROM videos WHERE sha256 = ?", (sha256,))
        if existing is not None:
            vid = str(existing["id"])
            # COALESCE: no se pisa un valor ya conocido con uno desconocido.
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE videos
                       SET duration  = COALESCE(duration, ?),
                           width     = COALESCE(width, ?),
                           height    = COALESCE(height, ?),
                           fps       = COALESCE(fps, ?),
                           video_codec = COALESCE(video_codec, ?),
                           audio_codec = COALESCE(audio_codec, ?)
                     WHERE id = ?
                    """,
                    (duration, width, height, fps, video_codec, audio_codec, vid),
                )
            return vid

        vid = video_id or new_id("video")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO videos
                    (id, path, sha256, duration, width, height, fps,
                     audio_codec, video_codec, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (vid, path, sha256, duration, width, height, fps,
                 audio_codec, video_codec, now),
            )
        return vid

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.query_one("SELECT * FROM videos WHERE id = ?", (video_id,)))

    def find_video_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.query_one("SELECT * FROM videos WHERE sha256 = ?", (sha256,)))

    # --- jobs ---

    def create_job(
        self, video_id: str, *, job_id: str | None = None, config_hash: str | None = None
    ) -> dict[str, Any]:
        jid = job_id or new_id("job")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO jobs
                    (id, video_id, state, current_stage, attempts, config_hash,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (jid, video_id, JobState.QUEUED, JobState.QUEUED, config_hash, now, now),
            )
        job = self.get_job(jid)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return row_to_dict(self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,)))

    def list_jobs(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        if state:
            return rows_to_dicts(
                self.db.query(
                    "SELECT * FROM jobs WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state, limit),
                )
            )
        return rows_to_dicts(
            self.db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        )

    def transition(
        self,
        job_id: str,
        target: JobState,
        *,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Mueve el job a `target` validando la transicion (seccion 49).

        Lanza InvalidTransitionError en vez de dejar que un estado invalido llegue
        al log, porque un salto de etapa que nadie detecto produce artefactos
        incoherentes que son muy caros de diagnosticar despues.
        """
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"job desconocido: {job_id}")

        current = JobState(job["state"])
        if not can_transition(current, target):
            raise InvalidTransitionError(
                f"{job_id}: transicion invalida {current} -> {target}. "
                f"Flujo permitido: {' -> '.join(s.value for s in PIPELINE_ORDER)}"
            )

        attempts = int(job["attempts"]) + (1 if target == JobState.RETRYING else 0)
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE jobs
                   SET state = ?, current_stage = ?, error_kind = ?, error_message = ?,
                       attempts = ?, updated_at = ?
                 WHERE id = ?
                """,
                (str(target), str(target), error_kind, error_message, attempts, now, job_id),
            )
        updated = self.get_job(job_id)
        assert updated is not None
        return updated

    def enter_stage(self, job_id: str, target: JobState) -> bool:
        """Deja el job listo para ejecutar `target`. Devuelve True si se movio.

        Idempotente, a diferencia de `transition()`. La seccion 51 exige que
        reejecutar un comando sobre el mismo contenido no haga trabajo, y eso
        significa que `python main.py analyze video.mp4` se pueda lanzar dos
        veces seguidas sin reventar.

        Hay tres casos, y los tres son reales:

        * El job ya esta en `target`: la etapa se relanzo (por ejemplo porque la
          transcripcion fallo a mitad y se reintenta). No se transiciona, porque
          `X -> X` no es una transicion valida.
        * El job fallo o esta reintentando: se pasa por RETRYING, que es el
          estado que representa "vuelvo a intentarlo" (seccion 49). Saltar
          directo de FAILED a la etapa dejaria el intento sin contabilizar.
        * El job ya esta MAS ADELANTE que `target`: no se retrocede. Volver
          atras el estado mientras se regenera un artefacto Early produce un log
          que parece que el pipeline avanza y luego retrocede, y rompe la idea
          de `resume_stage`. En este caso la etapa se ejecuta igualmente, pero
          apoyandose en la idempotencia de los artefactos.
        """
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"job desconocido: {job_id}")

        current = JobState(job["state"])
        if current == target:
            return False

        if current in {JobState.FAILED, JobState.RETRYING}:
            self.transition(job_id, JobState.RETRYING)
            current = JobState.RETRYING

        if current in PIPELINE_ORDER and PIPELINE_ORDER.index(current) > PIPELINE_ORDER.index(target):
            return False

        self.transition(job_id, target)
        return True

    # --- checkpoints (seccion 50) ---

    def start_stage(
        self, job_id: str, stage: JobState, *, run_id: str | None = None
    ) -> str:
        """Abre una etapa y la refleja en el estado del job."""
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"job desconocido: {job_id}")

        attempt = int(job["attempts"]) + 1
        rid = run_id or new_id("run")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO stage_runs (id, job_id, stage, attempt, state, started_at)
                VALUES (?, ?, ?, ?, 'RUNNING', ?)
                """,
                (rid, job_id, str(stage), attempt, now),
            )
            conn.execute(
                "UPDATE jobs SET current_stage = ?, updated_at = ? WHERE id = ?",
                (str(stage), now, job_id),
            )
        return rid

    def finish_stage(
        self,
        run_id: str,
        *,
        state: str = "COMPLETED",
        duration_ms: int | None = None,
        error_kind: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE stage_runs
                   SET state = ?, finished_at = ?, duration_ms = ?,
                       error_kind = ?, error_detail = ?
                 WHERE id = ?
                """,
                (state, utc_now(), duration_ms, error_kind, error_detail, run_id),
            )

    def completed_stages(self, job_id: str) -> list[str]:
        """Etapas completadas con exito, en el orden del flujo feliz (seccion 50)."""
        rows = self.db.query(
            """
            SELECT DISTINCT stage FROM stage_runs
             WHERE job_id = ? AND state = 'COMPLETED'
            """,
            (job_id,),
        )
        done = {str(row["stage"]) for row in rows}
        return [str(stage) for stage in PIPELINE_ORDER if str(stage) in done]

    def resume_stage(self, job_id: str) -> JobState:
        """Etapa desde la que hay que reanudar (seccion 50).

        El objetivo es NO repetir Whisper ni el analisis si Remotion fallo. Se
        devuelve la primera etapa CON TRABAJO que no tenga una corrida completada.
        QUEUED se excluye a proposito: es un estado de pre-parada y siempre
        devolveria un pipeline recien creado.
        """
        done = set(self.completed_stages(job_id))
        for stage in WORK_STAGES:
            if str(stage) not in done:
                return stage
        return JobState.COMPLETED

    # --- artifacts ---

    def record_artifact(
        self,
        job_id: str,
        stage: JobState | str,
        kind: str,
        path: str,
        *,
        artifact_id: str | None = None,
        sha256: str | None = None,
    ) -> str:
        aid = artifact_id or new_id("artifact")
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, job_id, stage, kind, path, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, stage, kind)
                DO UPDATE SET path = excluded.path,
                              -- COALESCE en el sha: un hash conocido no se
                              -- degrada a NULL porque un llamador no lo
                              -- calculo. El artefacto en disco no ha cambiado,
                              -- luego su hash tampoco. El path si se actualiza
                              -- siempre, porque ahi si puede haber cambiado.
                              sha256 = COALESCE(excluded.sha256, artifacts.sha256),
                              created_at = excluded.created_at
                """,
                (aid, job_id, str(stage), kind, path, sha256, utc_now()),
            )
        return aid

    def get_artifact(self, job_id: str, stage: JobState | str, kind: str) -> dict[str, Any] | None:
        return row_to_dict(
            self.db.query_one(
                "SELECT * FROM artifacts WHERE job_id = ? AND stage = ? AND kind = ?",
                (job_id, str(stage), kind),
            )
        )

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        return rows_to_dicts(
            self.db.query("SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at", (job_id,))
        )

    # --- LLM telemetry (seccion 44: model_runs) ---

    def record_model_run(
        self,
        *,
        stage: str,
        model: str,
        provider: str,
        job_id: str | None = None,
        prompt_version: str | None = None,
        schema_name: str | None = None,
        attempts: int = 1,
        succeeded: bool = False,
        duration_ms: int | None = None,
        error_detail: str | None = None,
    ) -> str:
        rid = new_id("mrun")
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO model_runs
                    (id, job_id, stage, model, provider, prompt_version, schema_name,
                     attempts, succeeded, duration_ms, error_detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, job_id, stage, model, provider, prompt_version, schema_name,
                 attempts, 1 if succeeded else 0, duration_ms, error_detail, utc_now()),
            )
        return rid

    def model_run_stats(self) -> dict[str, Any]:
        """Metricas de calidad del modelo, que es lo que mide el riesgo C-4."""
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(succeeded) AS succeeded,
                   SUM(attempts) AS attempts,
                   AVG(attempts) AS avg_attempts
              FROM model_runs
            """
        )
        if row is None:
            return {"total": 0, "succeeded": 0, "attempts": 0, "avg_attempts": 0.0}
        return {key: row[key] for key in row.keys()}
