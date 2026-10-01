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

SCHEMA_VERSION = 2

# Migraciones incrementales, indexadas por la version a la que llevan el
# esquema. Sprint 8 (v1 -> v2) es la primera.
#
# Hace falta porque `SCHEMA_SQL` usa CREATE TABLE IF NOT EXISTS: añadir una
# columna a la definición NO cambia una tabla que ya existe. Sin esto, la DB de
# quien ya tivesse Sprint 7 se quedaria sin las columnas nuevas y el primer
# INSERT de un snapshot fallaria con "no such column" sin más pista.
#
# Cada sentencia se aplica una sola vez y es idempotente por construccion: el
# runner comprueba `PRAGMA table_info` antes de alterar, asi que reejecutar
# `initialize()` no rompe nada aunque el INSERT de la version se hubiera
# perdido a mitad.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        # `performance_snapshots` existia desde el Sprint 2 con un puñado de
        # columnas de ejemplo, inventadas antes de leer las secciones 38-40, y
        # con la identidad equivocada. Se rehace la tabla entera en vez de
        # parchearla, porque SQLite no sabe quitar una UNIQUE de una tabla que
        # ya existe.
        #
        # Dos cosas cambian a proposito:
        #
        # 1. La identidad pasa de (clip_record_id, platform, age_hours) a
        #    (publication_id, age_hours). La seccion 70 encadena
        #    clip_record -> publication -> performance_snapshot: lo que se mide
        #    es la EDAD DE UNA PUBLICACION, no la edad de un clip. Con la
        #    identidad anterior, medir la publicacion de hoy de un clip se
        #    comia la medicion de la de anteayer y, peor, `sync` se la saltaba
        #    en silencio sin dejar ni un error. Solo se nota al publicar el
        #    mismo clip dos veces en la misma plataforma, que es exactamente el
        #    caso para el que existe la tabla de publicaciones.
        #
        # 2. Se completa con TODA la metadata de la seccion 39 y con
        #    `raw_metrics_json`, que es la parte importante: la seccion 40 dice
        #    "nunca eliminar los valores originales" porque las formulas pueden
        #    cambiar, y la 42 dice que hay que guardar el nombre exacto de la
        #    metrica y la fecha de obtencion porque YouTube cambio como cuenta
        #    las vistas de Shorts. Eso solo se puede cumplir guardando el
        #    payload tal cual lo devolvio la plataforma.
        #
        # `publication_id` queda NULLABLE a proposito: los snapshots que ya
        # existian en v1 no tienen publicacion asociada porque la tabla ni
        # siquiera existia cuando se escribieron, y rellenarlos con una
        # publicacion inventada seria fabricar evidencia.
        "CREATE TABLE performance_snapshots__v2 (\n"
        "    id                           TEXT PRIMARY KEY,\n"
        "    clip_record_id               TEXT NOT NULL REFERENCES clip_records(id),\n"
        "    publication_id               TEXT REFERENCES publications(id),\n"
        "    platform                     TEXT NOT NULL,\n"
        "    platform_clip_id             TEXT,\n"
        "    age_hours                    INTEGER NOT NULL,\n"
        "    views                        INTEGER,\n"
        "    shown_in_feed                INTEGER,\n"
        "    unique_viewers               INTEGER,\n"
        "    average_view_duration_seconds REAL,\n"
        "    average_percentage_viewed    REAL,\n"
        "    completion_rate              REAL,\n"
        "    likes                        INTEGER,\n"
        "    comments                     INTEGER,\n"
        "    shares                       INTEGER,\n"
        "    saves                        INTEGER,\n"
        "    subscribers_gained           INTEGER,\n"
        "    followers_gained             INTEGER,\n"
        "    profile_visits               INTEGER,\n"
        "    traffic_source               TEXT,\n"
        "    audience_source              TEXT,\n"
        "    like_rate                    REAL,\n"
        "    comment_rate                 REAL,\n"
        "    share_rate                   REAL,\n"
        "    subscriber_conversion_rate   REAL,\n"
        "    captured_at                  TEXT NOT NULL,\n"
        "    metric_source                TEXT,\n"
        "    derived_metrics_version      TEXT,\n"
        "    raw_metrics_json             TEXT,\n"
        "    derived_metrics_json         TEXT,\n"
        "    UNIQUE (publication_id, age_hours)\n"
        ")",
        # Se enumeran las columnas de origen una a una en vez de `SELECT *`:
        # el orden de `SELECT *` es el de la tabla de origen, que en una v1
        # tiene 16 columnas y en la nueva 30. Un `SELECT *` aqui guardaria
        # "views" (posicion 5) en "views" por casualidad y "captured_at" (posicion
        # 14) en "average_view_duration_seconds" sin decir nada.
        # Antes de copiar, se apunta cuantas filas no se pueden conservar.
        #
        # La tabla v1 NO tenia FK hacia clip_records, asi que puede contener
        # snapshots de clips que ya no existen: filas huerfanas que la v2, si
        # las copiase, harian fallar la migracion entera. Como la migracion va
        # en transaccion, el fallo no corrompe nada, pero deja al usuario
        # atascado: cada `open_database` reintenta y vuelve a fallar, y el
        # pipeline entero deja de arrancar por datos que nadie mira.
        #
        # se dejan fuera y se cuenta el numero en `schema_meta`. No se borran en
        # silencio: queda registrado, y quien los necesite puede sacarlos del
        # fichero .db antes de migrar.
        "INSERT OR REPLACE INTO schema_meta (key, value) SELECT 'orphaned_performance_snapshots', CAST(COUNT(*) AS TEXT) FROM performance_snapshots s WHERE NOT EXISTS (SELECT 1 FROM clip_records c WHERE c.id = s.clip_record_id)",
        "INSERT INTO performance_snapshots__v2 (\n"
        "    id, clip_record_id, publication_id, platform, platform_clip_id, age_hours,\n"
        "    views, shown_in_feed, unique_viewers, average_view_duration_seconds,\n"
        "    average_percentage_viewed, completion_rate, likes, comments, shares, saves,\n"
        "    subscribers_gained, followers_gained, profile_visits, traffic_source,\n"
        "    audience_source, like_rate, comment_rate, share_rate,\n"
        "    subscriber_conversion_rate, captured_at, metric_source,\n"
        "    derived_metrics_version, raw_metrics_json, derived_metrics_json\n"
        ")\n"
        "SELECT s.id, s.clip_record_id, NULL, s.platform, s.platform_clip_id, s.age_hours,\n"
        "    s.views, s.shown_in_feed, s.unique_viewers, NULL,\n"
        "    s.average_percentage_viewed, NULL, s.likes, s.comments, s.shares, NULL,\n"
        "    NULL, NULL, NULL, NULL, NULL,\n"
        "    s.like_rate, s.comment_rate, NULL,\n"
        "    NULL, s.captured_at, NULL,\n"
        "    s.derived_metrics_version, NULL, NULL\n"
        "FROM performance_snapshots s\n"
        "WHERE EXISTS (SELECT 1 FROM clip_records c WHERE c.id = s.clip_record_id)",
        "DROP TABLE performance_snapshots",
        "ALTER TABLE performance_snapshots__v2 RENAME TO performance_snapshots",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_clip ON performance_snapshots(clip_record_id, platform)",
        "CREATE INDEX IF NOT EXISTS idx_snapshots_publication ON performance_snapshots(publication_id, age_hours)",
    ),
}

# Columnas que tienen que existir cuando la DB dice tener esa version. Se
# comprueban despues de migrar, y tambien en una DB que no ha necesitado
# migracion.
#
# Motivo: `_migrate` se fia de `schema_meta`. Si ese numero no corresponde al
# esquema real, se salta los pasos pendientes y el fallo aparece mas tarde y
# lejos de su causa: un "no such column" al escribir el primer snapshot, o un
# "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"
# porque la UNIQUE antigua sigue ahí. Durante el Sprint 8 paso justo eso con una
# base local, y el sintoma (cuatro lineas de traceback dentro de `sync`) no
# senalaba que el problema era el esquema. Aqui se falla al abrir, diciendo que
# pasa y como se arregla.
SCHEMA_SENTINELS: dict[int, tuple[str, ...]] = {
    2: ("publication_id", "raw_metrics_json", "derived_metrics_json", "metric_source"),
}


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

CREATE TABLE IF NOT EXISTS publications (
    id                TEXT PRIMARY KEY,
    clip_record_id    TEXT NOT NULL REFERENCES clip_records(id),
    platform          TEXT NOT NULL,
    platform_clip_id  TEXT NOT NULL,
    url               TEXT,
    title             TEXT,
    published_at      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'published',
    notes             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (platform, platform_clip_id)
);

CREATE INDEX IF NOT EXISTS idx_publications_clip ON publications(clip_record_id);
CREATE INDEX IF NOT EXISTS idx_publications_platform ON publications(platform, status);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id                           TEXT PRIMARY KEY,
    clip_record_id               TEXT NOT NULL REFERENCES clip_records(id),
    publication_id               TEXT REFERENCES publications(id),
    platform                     TEXT NOT NULL,
    platform_clip_id             TEXT,
    age_hours                    INTEGER NOT NULL,
    -- Exposure (seccion 39).
    views                        INTEGER,
    shown_in_feed                INTEGER,
    unique_viewers               INTEGER,
    -- Retention (seccion 39).
    average_view_duration_seconds REAL,
    average_percentage_viewed    REAL,
    completion_rate              REAL,
    -- Engagement (seccion 39).
    likes                        INTEGER,
    comments                     INTEGER,
    shares                       INTEGER,
    saves                        INTEGER,
    -- Conversion (seccion 39).
    subscribers_gained           INTEGER,
    followers_gained             INTEGER,
    profile_visits               INTEGER,
    -- Distribution (seccion 39). Texto y no numero: son categorias.
    traffic_source               TEXT,
    audience_source              TEXT,
    -- Derivadas (seccion 40). Guardan las CRUDAS tambien: una formula puede
    -- cambiar y el valor original no se puede recuperar si no se guardo.
    like_rate                    REAL,
    comment_rate                 REAL,
    share_rate                   REAL,
    subscriber_conversion_rate   REAL,
    -- Procedencia (seccion 42): que adapter devolvio la fila y cuando. Sin
    -- esto no se puede distinguir "0 porque no hubo vistas" de "0 porque la
    -- plataforma no mide eso".
    captured_at                  TEXT NOT NULL,
    metric_source                TEXT,
    derived_metrics_version      TEXT,
    raw_metrics_json             TEXT,
    derived_metrics_json         TEXT,
    UNIQUE (publication_id, age_hours)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_clip
    ON performance_snapshots(clip_record_id, platform);
"""


def _column_already_exists(conn: sqlite3.Connection, alter_statement: str) -> bool:
    """¿La columna que esta sentencia ALTER pretende añadir ya existe?

    Parsea `ALTER TABLE <tabla> ADD COLUMN <columna>` del propio enunciado en
    vez de mantener una lista de nombres aparte: la lista se desincroniza en
    cuanto alguien anade una columna a la migracion y se le olvida la otra, y
    el fallo aparece como "duplicate column name" en produccion.

    El parser distingue las dos mitades del enunciado, que es donde esta la
    trampa: despues de `ADD COLUMN` va el NOMBRE de la columna, no el de la
    tabla. Partir el resto por el primer espacio y usar el primer token como
    tabla hace que `publication_id TEXT` se lea como tabla `publication_id` y
    columna `TEXT`, la comprobacion nunca encuentra nada, y la migracion
    reintenta anadir columnas que la DB nueva ya tiene porque las creo
    `SCHEMA_SQL` entero.

    Una sentencia que no sea un ALTER de ese tipo (un `CREATE INDEX`, por
    ejemplo) devuelve False y se ejecuta siempre: las de indice son
    idempotentes por `IF NOT EXISTS`.
    """
    _, _, after_table = alter_statement.partition("ALTER TABLE")
    if not after_table:
        return False
    table, _, after_column = after_table.partition("ADD COLUMN")
    if not after_column:
        return False
    table = table.strip()
    column = after_column.strip().split()[0] if after_column.strip() else ""
    if not table or not column:
        return False
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


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
            self._migrate(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        finally:
            conn.close()
        return self

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Aplica las migraciones pendientes desde la version guardada.

        El bucle por pasos (y no un "aplica todo lo que falte") es
        deliberado: si alguien salta de v1 a v3 dentro de este mismo fichero,
        los pasos intermedios se ejecutan en orden y cada uno deja el esquema en
        el estado que espera el siguiente. Con un `if version < 2` la v3
        intentaria alterar columnas que todavia no existen.

        Cada version se aplica dentro de una transaccion propia. La v2
        reconstruye `performance_snapshots` (DROP + RENAME) y a medio camino, sin
        transaccion, deja la base sin tabla y sin copia: el pipeline entero se
        quedaria sin sitio donde escribir. Con transaccion, un fallo deja la
        tabla vieja intacta.
        """
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current = int(row["value"]) if row else 0
        for target in sorted(MIGRATIONS):
            if target <= current:
                continue
            conn.execute("BEGIN")
            try:
                for statement in MIGRATIONS[target]:
                    # Las sentencias que anaden columnas no son idempotentes en
                    # SQLite ("duplicate column name"), asi que se comprueban
                    # antes. Las demas (CREATE TABLE, CREATE INDEX IF NOT
                    # EXISTS) ya lo son.
                    if _column_already_exists(conn, statement):
                        continue
                    conn.execute(statement)
            except Exception:
                conn.execute("ROLLBACK")
                raise
            # La version se escribe DENTRO de la transaccion: si la migracion
            # se deshace, la version tampoco avanza, y reintentar la vuelve a
            # intentar desde el principio.
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(target),),
            )
            conn.execute("COMMIT")
            current = target

        self._check_sentinels(conn, current)

    @staticmethod
    def _check_sentinels(conn: sqlite3.Connection, version: int) -> None:
        """Falla pronto si la version guardada no cuadra con el esquema real."""
        required = SCHEMA_SENTINELS.get(version)
        if not required:
            return
        present = {
            row["name"] for row in conn.execute("PRAGMA table_info(performance_snapshots)")
        }
        missing = [column for column in required if column not in present]
        if not missing:
            return
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
        raise RuntimeError(
            f"La base de datos {path} declara schema v{version}, pero le faltan "
            f"columnas de performance_snapshots: {', '.join(missing)}.\n"
            "El numero de version no corresponde al esquema real, asi que las "
            "migraciones se saltan y el fallo saldria mas tarde y sin causa "
            "aparente.\n"
            "Se arregla restaurando una copia coherente, o borrando la fila de "
            "schema_version de schema_meta para que las migraciones vuelvan a "
            "aplicarse desde cero."
        )

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

    # --- clip_records (seccion 37): la fila que hace CONSULTABLE un clip ----

    def upsert_clip_record(
        self,
        *,
        job_id: str,
        clip_id: str,
        candidate_id: str | None,
        start: float,
        end: float,
        duration: float,
        score_total: float | None = None,
        hook_type: str | None = None,
        content_type: str | None = None,
        topic: str | None = None,
        hook_origin: str | None = None,
        requires_generated_hook: bool | None = None,
        record_path: str | None = None,
        edit_plan_path: str | None = None,
        render_path: str | None = None,
        qc_passed: bool | None = None,
    ) -> str:
        """Guarda la fila de `clip_records` de un clip. Idempotente por
        (job_id, clip_id).

        El `clip_record.json` del workspace es el registro COMPLETO y legible;
        esta tabla es el indice que permite consultar ("dame los clips de este
        job que pasaron el QC", "cual fue el score de este clip") sin tener que
        abrir y parsear un JSON por clip. Sprint 8 cuelga de aqui: la FK de
        `performance_snapshots` apunta a esta tabla.

        El id primary key es `job_id::clip_id` y no el `clip_id` a secas porque
        los ids de clip se derivan del bloque y del indice (`cand_w2_0`) y se
        repiten en cuanto se procesa otro video: como PK a secas, el segundo job
        machacaria el clip del primero.

        Es un UPSERT y no un INSERT porque `report` se puede repetir (es
        idempotente por diseño) y porque un re-QC tiene que poder corregir la
        fila anterior en vez de duplicar el clip o fallar por clave primaria.
        """
        primary = f"{job_id}::{clip_id}"
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO clip_records
                    (id, job_id, candidate_id, start, end, duration, score_total,
                     hook_type, content_type, topic, hook_origin,
                     requires_generated_hook, record_path, edit_plan_path, render_path,
                     qc_passed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    candidate_id          = excluded.candidate_id,
                    start                 = excluded.start,
                    end                   = excluded.end,
                    duration              = excluded.duration,
                    score_total           = excluded.score_total,
                    hook_type             = excluded.hook_type,
                    content_type          = excluded.content_type,
                    topic                 = excluded.topic,
                    hook_origin           = excluded.hook_origin,
                    requires_generated_hook = excluded.requires_generated_hook,
                    record_path           = excluded.record_path,
                    edit_plan_path        = excluded.edit_plan_path,
                    render_path           = excluded.render_path,
                    qc_passed             = excluded.qc_passed
                """,
                (
                    primary,
                    job_id,
                    candidate_id,
                    float(start),
                    float(end),
                    float(duration),
                    float(score_total) if score_total is not None else None,
                    hook_type,
                    content_type,
                    topic,
                    hook_origin,
                    None if requires_generated_hook is None else int(requires_generated_hook),
                    record_path,
                    edit_plan_path,
                    render_path,
                    None if qc_passed is None else int(qc_passed),
                    utc_now(),
                ),
            )
        return primary

    def list_clip_records(
        self, job_id: str | None = None, *, only_published: bool = False
    ) -> list[dict[str, Any]]:
        """Los clip_records, del mas reciente al mas antiguo.

        `only_published` filtra por los que pasaron el QC, que es lo que se
        puede publicar: un clip con el QC fallido se puede revisar, pero no
        subir.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if job_id is not None:
            clauses.append("job_id = ?")
            params.append(job_id)
        if only_published:
            clauses.append("qc_passed = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM clip_records{where} ORDER BY created_at DESC, id",
            params,
        )
        return [dict(row) for row in rows]

    def get_clip_record(self, job_id: str, clip_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM clip_records WHERE id = ?", (f"{job_id}::{clip_id}",)
        )
        return dict(row) if row is not None else None

    # --- Sprint 8: publications y performance_snapshots ---

    def upsert_publication(
        self,
        *,
        clip_record_id: str,
        platform: str,
        platform_clip_id: str,
        published_at: str,
        url: str | None = None,
        title: str | None = None,
        status: str = "published",
        notes: str | None = None,
        publication_id: str | None = None,
    ) -> str:
        """Registra que un clip se publico en una plataforma. Idempotente por
        (platform, platform_clip_id).

        El identificador externo es la clave, no el `clip_record_id`: es lo
        unico con lo que se piden metricas despues, y es lo que impide que un
        mismo video subido dos veces cuente como dos publicaciones distintas.
        Por eso no se puede dejar vacio. Una publicacion sin id externo no
        tiene metricas que pedir, asi que no es una publicacion todavia: si se
        quiere dejar constancia de un clip subido a mano, se usa un
        identificador local del tipo `manual:<clip_id>` y el adaptador manual
        lo trata como tal.

        `published_at` no se deduce del `created_at` de la fila: la fecha en la
        que se subio es la que define las ventanas de 1h/6h/24h... de la
        seccion 41, y se mira hacia atras en el tiempo, no hacia el momento en
        que se decidio registrarlo.
        """
        existing = self.find_publication(platform, platform_clip_id)
        primary = publication_id or (existing["id"] if existing else new_id("pub"))
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO publications
                    (id, clip_record_id, platform, platform_clip_id, url, title,
                     published_at, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, platform_clip_id) DO UPDATE SET
                    clip_record_id = excluded.clip_record_id,
                    url            = excluded.url,
                    title          = COALESCE(excluded.title, publications.title),
                    published_at   = excluded.published_at,
                    status         = excluded.status,
                    notes          = COALESCE(excluded.notes, publications.notes),
                    updated_at     = excluded.updated_at
                """,
                (
                    primary,
                    clip_record_id,
                    platform,
                    platform_clip_id,
                    url,
                    title,
                    published_at,
                    status,
                    notes,
                    now,
                    now,
                ),
            )
        row = self.db.query_one(
            "SELECT id FROM publications WHERE platform = ? AND platform_clip_id = ?",
            (platform, platform_clip_id),
        )
        return str(row["id"]) if row is not None else primary

    def find_publication(self, platform: str, platform_clip_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM publications WHERE platform = ? AND platform_clip_id = ?",
            (platform, platform_clip_id),
        )
        return dict(row) if row is not None else None

    def get_publication(self, publication_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM publications WHERE id = ?", (publication_id,))
        return dict(row) if row is not None else None

    def list_publications(
        self,
        *,
        clip_record_id: str | None = None,
        job_id: str | None = None,
        platform: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Publicaciones, de la mas reciente a la mas antigua.

        Acepta `job_id` como atajo porque en la CLI se piensa en clips de un
        job, no en ids compuestos; se resuelve a traves de `clip_records`.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if clip_record_id is not None:
            clauses.append("p.clip_record_id = ?")
            params.append(clip_record_id)
        if job_id is not None:
            clauses.append("c.job_id = ?")
            params.append(job_id)
        if platform is not None:
            clauses.append("p.platform = ?")
            params.append(platform)
        if status is not None:
            clauses.append("p.status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            "SELECT p.*, c.candidate_id, c.score_total, c.qc_passed "
            "FROM publications p "
            "JOIN clip_records c ON c.id = p.clip_record_id"
            f"{where} ORDER BY p.published_at DESC, p.id",
            params,
        )
        return [dict(row) for row in rows]

    def upsert_performance_snapshot(
        self,
        *,
        clip_record_id: str,
        platform: str,
        age_hours: int,
        captured_at: str,
        publication_id: str | None = None,
        platform_clip_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        derived: dict[str, Any] | None = None,
        metric_source: str | None = None,
        derived_metrics_version: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> str:
        """Guarda una foto de las metricas de UNA PUBLICACION. Idempotente por
        (publication_id, age_hours).

        No por (clip_record_id, platform, age_hours): un clip republicado en la
        misma plataforma tiene su propia escalera, y con la identidad anterior
        la segunda publicacion se comia la medicion de la primera.

        Las metricas de la seccion 39 van en columnas para poder consultarlas,
        pero el payload tal cual lo devolvio la plataforma se guarda entero en
        `raw_metrics_json`. No es/redundancia: la seccion 40 prohibe borrar los
        valores originales porque las formulas pueden cambiar, y la 42 exige
        guardar el nombre exacto de la metrica y cuando se obtuvo, porque
        YouTube cambio la forma de contar las vistas de Shorts. Unicamente con
        columnas tipadas, esa evidencia no se podria recuperar.

        Una resincronizacion NO borra lo que ya se sabia: las columnas usan
        COALESCE contra el valor anterior, asi que que la plataforma deje de
        devolver una metrica la deja como estaba en vez de ponerla a NULL.
        distinguish "no lo mido" de "antes lo medias y ahora no lo se" importa
        mas que la fila quede exacta para hoy.
        Los snapshots que ya venian de la v1 no tienen publicacion, y por eso
        `publication_id` y `platform_clip_id` son opcionales aqui. Para los
        nuevos no lo son en la practica: si viene `publication_id`, el
        `platform_clip_id` se toma de la publicacion si el llamador no lo pasa,
        porque la publicacion es la autoridad y dejar el campo a mano era una
        forma de guardar un snapshot que dice medir un video que no es el
        suyo.
        """
        values = metrics or {}
        calculated = derived or {}
        primary = new_id("snap")
        if platform_clip_id is None and publication_id is not None:
            publication = self.get_publication(publication_id)
            if publication is None:
                raise ValueError(
                    f"no existe la publicacion '{publication_id}'. El snapshot "
                    "no se guarda: sin ella no se sabe que video se midio."
                )
            platform_clip_id = str(publication["platform_clip_id"])
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO performance_snapshots
                    (id, clip_record_id, publication_id, platform, platform_clip_id,
                     age_hours, views, shown_in_feed, unique_viewers,
                     average_view_duration_seconds, average_percentage_viewed,
                     completion_rate, likes, comments, shares, saves,
                     subscribers_gained, followers_gained, profile_visits,
                     traffic_source, audience_source,
                     like_rate, comment_rate, share_rate, subscriber_conversion_rate,
                     captured_at, metric_source, derived_metrics_version,
                     raw_metrics_json, derived_metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publication_id, age_hours) DO UPDATE SET
                    publication_id    = COALESCE(excluded.publication_id, performance_snapshots.publication_id),
                    platform_clip_id  = COALESCE(excluded.platform_clip_id, performance_snapshots.platform_clip_id),
                    views             = COALESCE(excluded.views, performance_snapshots.views),
                    shown_in_feed     = COALESCE(excluded.shown_in_feed, performance_snapshots.shown_in_feed),
                    unique_viewers    = COALESCE(excluded.unique_viewers, performance_snapshots.unique_viewers),
                    average_view_duration_seconds = COALESCE(excluded.average_view_duration_seconds, performance_snapshots.average_view_duration_seconds),
                    average_percentage_viewed    = COALESCE(excluded.average_percentage_viewed, performance_snapshots.average_percentage_viewed),
                    completion_rate   = COALESCE(excluded.completion_rate, performance_snapshots.completion_rate),
                    likes             = COALESCE(excluded.likes, performance_snapshots.likes),
                    comments          = COALESCE(excluded.comments, performance_snapshots.comments),
                    shares            = COALESCE(excluded.shares, performance_snapshots.shares),
                    saves             = COALESCE(excluded.saves, performance_snapshots.saves),
                    subscribers_gained = COALESCE(excluded.subscribers_gained, performance_snapshots.subscribers_gained),
                    followers_gained  = COALESCE(excluded.followers_gained, performance_snapshots.followers_gained),
                    profile_visits    = COALESCE(excluded.profile_visits, performance_snapshots.profile_visits),
                    traffic_source    = COALESCE(excluded.traffic_source, performance_snapshots.traffic_source),
                    audience_source   = COALESCE(excluded.audience_source, performance_snapshots.audience_source),
                    like_rate         = COALESCE(excluded.like_rate, performance_snapshots.like_rate),
                    comment_rate      = COALESCE(excluded.comment_rate, performance_snapshots.comment_rate),
                    share_rate        = COALESCE(excluded.share_rate, performance_snapshots.share_rate),
                    subscriber_conversion_rate = COALESCE(excluded.subscriber_conversion_rate, performance_snapshots.subscriber_conversion_rate),
                    captured_at       = excluded.captured_at,
                    metric_source     = COALESCE(excluded.metric_source, performance_snapshots.metric_source),
                    derived_metrics_version = COALESCE(excluded.derived_metrics_version, performance_snapshots.derived_metrics_version),
                    raw_metrics_json  = COALESCE(excluded.raw_metrics_json, performance_snapshots.raw_metrics_json),
                    derived_metrics_json = COALESCE(excluded.derived_metrics_json, performance_snapshots.derived_metrics_json)
                """,
                (
                    primary,
                    clip_record_id,
                    publication_id,
                    platform,
                    platform_clip_id,
                    int(age_hours),
                    _as_int(values.get("views")),
                    _as_int(values.get("shown_in_feed")),
                    _as_int(values.get("unique_viewers")),
                    _as_float(values.get("average_view_duration_seconds")),
                    _as_float(values.get("average_percentage_viewed")),
                    _as_float(values.get("completion_rate")),
                    _as_int(values.get("likes")),
                    _as_int(values.get("comments")),
                    _as_int(values.get("shares")),
                    _as_int(values.get("saves")),
                    _as_int(values.get("subscribers_gained")),
                    _as_int(values.get("followers_gained")),
                    _as_int(values.get("profile_visits")),
                    values.get("traffic_source"),
                    values.get("audience_source"),
                    _as_float(calculated.get("like_rate")),
                    _as_float(calculated.get("comment_rate")),
                    _as_float(calculated.get("share_rate")),
                    _as_float(calculated.get("subscriber_conversion_rate")),
                    captured_at,
                    metric_source,
                    derived_metrics_version,
                    dumps(raw) if raw is not None else None,
                    dumps(calculated) if calculated else None,
                ),
            )
        row = self.db.query_one(
            "SELECT id FROM performance_snapshots "
            "WHERE publication_id = ? AND age_hours = ?",
            (publication_id, int(age_hours)),
        )
        return str(row["id"]) if row is not None else primary

    def get_performance_snapshot(self, publication_id: str, age_hours: int) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM performance_snapshots WHERE publication_id = ? AND age_hours = ?",
            (publication_id, int(age_hours)),
        )
        return dict(row) if row is not None else None

    def list_performance_snapshots(
        self,
        *,
        clip_record_id: str | None = None,
        publication_id: str | None = None,
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Snapshots de un clip (o de una publicacion), de mas viejo a mas
        nuevo: el orden cronologico es el que hace legible la evolucion."""
        clauses: list[str] = []
        params: list[Any] = []
        if clip_record_id is not None:
            clauses.append("s.clip_record_id = ?")
            params.append(clip_record_id)
        if publication_id is not None:
            clauses.append("s.publication_id = ?")
            params.append(publication_id)
        if platform is not None:
            # Con el JOIN hay que prefijar: `publications` tambien tiene
            # `platform` y SQLite daria "ambiguous column name".
            clauses.append("s.platform = ?")
            params.append(platform)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # Se ordena por publicacion y despues por edad. Ordenar solo por edad
        # intercalaria las escaleras de dos publicaciones del mismo clip en la
        # misma plataforma, y la tabla resultante repetiria 24h, 48h, 24h, 48h
        # pareciendo que las metricas de una sola publicacion dan saltos.
        #
        # La publicacion se ordena por `published_at` y no por su id: los ids
        # llevan un hash y su orden no significa nada, asi que dos ladder
        # aparecerian en orden arbitrario de una ejecucion a otra.
        rows = self.db.query(
            "SELECT s.* FROM performance_snapshots s"
            " LEFT JOIN publications p ON p.id = s.publication_id"
            f"{where}"
            " ORDER BY COALESCE(p.published_at, '') ASC, s.publication_id ASC,"
            " s.age_hours ASC, s.captured_at ASC",
            params,
        )
        return [dict(row) for row in rows]

    def recorded_ages(self, publication_id: str) -> set[int]:
        """Edades (en horas) ya registradas para UNA PUBLICACION.

        Se pregunta por publicacion y no por (clip, platform) a proposito. Con
        la identidad antigua, medir la publicacion de hoy de un clip se
        comia la de anteayer y `sync` se la saltaba en silencio, sin dejar
        ni un error: dos publicaciones del mismo clip en la misma plataforma
        solo existen si el clip se reposta, y es justo el caso para el que
        existe la tabla de publicaciones.

        Los snapshots heredados de la v1 no tienen `publication_id` y por eso
        no aparecen en ninguna escalera: son datos sin publicacion asociada y
        no hay forma honesta de atribuirlos a una.
        """
        rows = self.db.query(
            "SELECT age_hours FROM performance_snapshots WHERE publication_id = ?",
            (publication_id,),
        )
        return {int(row["age_hours"]) for row in rows}


def _as_int(value: Any) -> int | None:
    """Convierte a int conservando el null de "no lo sé" (seccion 38).

    `int(...)` a pelo convertiria None en un fallo y "0" en cero; lo que hace
    falta es que un cero real siga siendo cero y que una metrica ausente siga
    ausente. Se distinguen porque la plataforma devuelve la cadena "0" para
    un cero y no devuelve nada para lo que no mide.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if stripped in ("", "--", "null", "None", "N/A"):
            return None
        try:
            return int(round(float(stripped)))
        except ValueError:
            return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    """Igual que `_as_int`, para métricas fraccionarias (porcentajes, tasas)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip().replace(",", "").rstrip("%")
        if stripped in ("", "--", "null", "None", "N/A"):
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
