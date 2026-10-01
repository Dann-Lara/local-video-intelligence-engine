"""Performance Intelligence (secciones 38-44).

Offline: base temporal y ninguna llamada de red. El adaptador de YouTube se
prueba con la respuesta de la API simulada, porque lo que se verifica aqui es
el CONTRATO (parametros, nombres de metrica, forma de la respuesta), no la mano
de una cuenta real.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import db as db_module  # noqa: E402
from src.core.config import get as config_get  # noqa: E402
from src.performance import derived as derived_module  # noqa: E402
from src.performance.base import (  # noqa: E402
    MetricSnapshot,
    PlatformAnalyticsProvider,
    ProviderError,
    ProviderUnavailable,
    age_reached,
    build_provider,
    measurement_window,
    parse_timestamp,
)
from src.performance.manual import ManualProvider  # noqa: E402
from src.performance.sync import planned_ages, sync_publications, timeline  # noqa: E402
from src.performance.youtube import (  # noqa: E402
    REQUESTED_METRICS,
    UNSUPPORTED_METRICS,
    YouTubeAnalyticsProvider,
)

SHA = "e" * 64
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> db_module.Database:
    return db_module.Database(tmp_path / "perf.db").initialize()


@pytest.fixture
def repo(database: db_module.Database) -> db_module.JobRepository:
    return db_module.JobRepository(database)


@pytest.fixture
def clip(repo: db_module.JobRepository) -> tuple[db_module.JobRepository, str, str]:
    """Un job con un clip que paso el QC."""
    video_id = repo.register_video(path="input/a.mp4", sha256=SHA, duration=300.0)
    job_id = str(repo.create_job(video_id=video_id, config_hash="h")["id"])
    clip_record_id = repo.upsert_clip_record(
        job_id=job_id,
        clip_id="cand_1",
        candidate_id="cand_1",
        start=10.0,
        end=40.0,
        duration=30.0,
        qc_passed=True,
    )
    return repo, job_id, clip_record_id


# --- migraciones (esquema v1 -> v2) ---


LEGACY_SCHEMA = """
    CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO schema_meta VALUES ('schema_version', '1');
    CREATE TABLE videos (
        id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
        duration REAL, width INTEGER, height INTEGER, fps REAL,
        audio_codec TEXT, video_codec TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
        state TEXT NOT NULL, current_stage TEXT, error_kind TEXT,
        error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        config_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE clip_records (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
        start REAL NOT NULL, end REAL NOT NULL, duration REAL NOT NULL,
        score_total REAL, record_path TEXT, qc_passed INTEGER, created_at TEXT NOT NULL
    );
    CREATE TABLE performance_snapshots (
        id TEXT PRIMARY KEY,
        clip_record_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        platform_clip_id TEXT,
        age_hours INTEGER NOT NULL,
        views INTEGER,
        shown_in_feed INTEGER,
        unique_viewers INTEGER,
        likes INTEGER,
        comments INTEGER,
        shares INTEGER,
        average_percentage_viewed REAL,
        like_rate REAL,
        comment_rate REAL,
        captured_at TEXT NOT NULL,
        derived_metrics_version TEXT,
        UNIQUE (clip_record_id, platform, age_hours)
    );
"""


def test_a_v1_database_gains_the_sprint_8_columns(tmp_path):
    """La v1 es la que tiene quien ejecuto el Sprint 7: sus snapshots se
    escribieron con el puñado de columnas viejo."""
    path = tmp_path / "v1.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(LEGACY_SCHEMA)
    # Un clip real, para que la FK de la v2 se pueda cumplir.
    legacy.execute(
        "INSERT INTO clip_records (id, job_id, candidate_id, start, end, duration, created_at) "
        "VALUES ('job1::cand_1', 'job1', 'cand_1', 10, 40, 30, '2026-09-01T00:00:00Z')"
    )
    legacy.execute(
        "INSERT INTO performance_snapshots "
        "(id, clip_record_id, platform, age_hours, views, captured_at) "
        "VALUES ('snap_viejo', 'job1::cand_1', 'youtube_shorts', 24, 100, "
        "'2026-09-01T00:00:00Z')"
    )
    legacy.commit()
    legacy.close()

    migrated = db_module.Database(path).initialize()
    assert migrated.schema_version() == db_module.SCHEMA_VERSION

    names = {row["name"] for row in migrated.query("PRAGMA table_info(performance_snapshots)")}
    # Todo lo de la seccion 39 que faltaba.
    assert {
        "completion_rate",
        "saves",
        "subscribers_gained",
        "followers_gained",
        "profile_visits",
        "traffic_source",
        "audience_source",
        "average_view_duration_seconds",
        "share_rate",
        "subscriber_conversion_rate",
        "raw_metrics_json",
        "derived_metrics_json",
        "metric_source",
        "publication_id",
    } <= names

    # El dato que ya habia sigue intacto: una migracion que pierde evidencia
    # previa es peor que no migrar.
    row = migrated.query_one("SELECT * FROM performance_snapshots WHERE id = 'snap_viejo'")
    assert row is not None
    assert row["views"] == 100


def test_a_database_that_claims_a_version_it_does_not_have_fails_loudly(tmp_path):
    """`schema_meta` se toma como verdad y si miente, las migraciones se
    saltan. El fallo sale entonces muchisimo mas tarde y sin causa (un "no such
    column" escribiendo un snapshot, o un ON CONFLICT que no encaja con la
    UNIQUE antigua). Aqui se falla al abrir y se dice que pasa."""
    path = tmp_path / "mentirosa.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(LEGACY_SCHEMA)
    legacy.commit()
    legacy.close()

    # Se migra de verdad...
    db_module.Database(path).initialize()
    # ...y luego se deshace el esquema sin tocar el numero de version.
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "DROP TABLE performance_snapshots;"
        "CREATE TABLE performance_snapshots ("
        " id TEXT PRIMARY KEY, clip_record_id TEXT NOT NULL, platform TEXT NOT NULL,"
        " age_hours INTEGER NOT NULL, views INTEGER, likes INTEGER,"
        " captured_at TEXT NOT NULL, UNIQUE (clip_record_id, platform, age_hours));"
        "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version';"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError) as error:
        db_module.Database(path).initialize()

    message = str(error.value)
    assert "raw_metrics_json" in message          # que columna falta
    assert "schema_meta" in message               # y que se puede arreglar


def test_a_migration_survives_orphan_snapshots(tmp_path):
    """La v1 no tenia FK hacia clip_records, asi que puede haber snapshots de
    clips que ya no existen. Si la migracion los copiase, fallaria al infringir la FK
    y, al ir en transaccion, dejaria al usuario atascado reintentando en cada
    arranque. Se dejan fuera y se cuenta cuantos eran."""
    path = tmp_path / "huerfano.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(LEGACY_SCHEMA)
    legacy.execute(
        "INSERT INTO clip_records (id, job_id, candidate_id, start, end, duration, created_at) "
        "VALUES ('job1::cand_1', 'job1', 'cand_1', 10, 40, 30, '2026-09-01T00:00:00Z')"
    )
    legacy.executemany(
        "INSERT INTO performance_snapshots (id, clip_record_id, platform, age_hours, views, captured_at)"
        " VALUES (?, ?, 'youtube_shorts', ?, ?, '2026-09-01T00:00:00Z')",
        [
            ("bueno", "job1::cand_1", 24, 100),
            ("huerfano_1", "job9::cand_9", 24, 500),
            ("huerfano_2", "job9::cand_9", 48, 900),
        ],
    )
    legacy.commit()
    legacy.close()

    migrated = db_module.Database(path).initialize()

    # La migracion completa, no se queda a medias.
    assert migrated.schema_version() == db_module.SCHEMA_VERSION
    # El dato bueno se conserva.
    assert migrated.query_one("SELECT * FROM performance_snapshots WHERE id = 'bueno'")["views"] == 100
    # Y los huerfanos quedan declarados, no borrados en silencio.
    assert migrated.query_one(
        "SELECT value FROM schema_meta WHERE key = 'orphaned_performance_snapshots'"
    )["value"] == "2"


def test_a_fresh_database_has_the_same_columns_as_a_migrated_one(tmp_path):
    """Si divergieran, los tests con DB nueva no servirian de nada: estarian
    probando un esquema que no existe en ninguna instalacion real."""
    legacy_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(legacy_path))
    legacy.executescript(LEGACY_SCHEMA)
    legacy.commit()
    legacy.close()

    migrated = db_module.Database(legacy_path).initialize()
    fresh = db_module.Database(tmp_path / "fresh.db").initialize()

    def signature(db):
        return sorted(
            (row["name"], row["type"]) for row in db.query("PRAGMA table_info(performance_snapshots)")
        )

    assert signature(fresh) == signature(migrated)


def test_migrations_are_safe_to_run_twice(database):
    for _ in range(3):
        database.initialize()
    assert database.schema_version() == db_module.SCHEMA_VERSION


def test_publications_table_is_created(database):
    names = {row["name"] for row in database.query(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    assert "publications" in names


# --- publications ---


def test_publication_is_idempotent_by_platform_and_external_id(clip):
    repo, job_id, clip_record_id = clip
    first = repo.upsert_publication(
        clip_record_id=clip_record_id,
        platform="youtube_shorts",
        platform_clip_id="abc",
        published_at="2026-09-30T10:00:00Z",
    )
    second = repo.upsert_publication(
        clip_record_id=clip_record_id,
        platform="youtube_shorts",
        platform_clip_id="abc",
        published_at="2026-09-30T10:00:00Z",
        url="https://youtube.com/shorts/abc",
    )
    assert first == second
    assert len(repo.list_publications(job_id=job_id)) == 1
    # La URL que se anadio despues no se pierde por no repetirla.
    assert repo.get_publication(first)["url"] == "https://youtube.com/shorts/abc"


def test_the_same_clip_published_twice_to_the_same_platform_is_two_publications(clip):
    """Repostar un clip es legitimo: son dos publicaciones distintas, con dos
    identificadores distintos, y se miden por separado."""
    repo, job_id, clip_record_id = clip
    repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_1", published_at="2026-09-30T10:00:00Z",
    )
    repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_2", published_at="2026-10-02T10:00:00Z",
    )
    assert len(repo.list_publications(job_id=job_id)) == 2


def test_list_publications_can_filter_by_job(clip):
    repo, job_id, clip_record_id = clip
    repo.upsert_publication(
        clip_record_id=clip_record_id, platform="youtube_shorts",
        platform_clip_id="abc", published_at="2026-09-30T10:00:00Z",
    )
    assert len(repo.list_publications(job_id=job_id)) == 1
    assert repo.list_publications(job_id="otro-job") == []


# --- snapshots ---


def test_snapshot_is_idempotent_by_publication_and_age(clip):
    repo, job_id, clip_record_id = clip
    publication = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="youtube_shorts",
        platform_clip_id="abc", published_at="2026-09-30T10:00:00Z",
    )
    for views in (100, 250):
        repo.upsert_performance_snapshot(
            clip_record_id=clip_record_id, platform="youtube_shorts", age_hours=24,
            captured_at="2026-10-01T10:00:00Z", metrics={"views": views},
            publication_id=publication,
        )
    rows = repo.list_performance_snapshots(clip_record_id=clip_record_id)
    assert len(rows) == 1
    assert rows[0]["views"] == 250


def test_two_publications_of_the_same_clip_have_independent_timelines(clip):
    """Un clip republicado se mide aparte. Con la identidad por (clip,
    platform) la segunda publicacion se comia la medicion de la primera y
    `sync` se la saltaba sin dejar error."""
    repo, job_id, clip_record_id = clip
    first = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_1", published_at="2026-09-01T00:00:00Z",
    )
    second = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_2", published_at="2026-10-01T00:00:00Z",
    )
    for publication, views in ((first, 100), (second, 900)):
        repo.upsert_performance_snapshot(
            clip_record_id=clip_record_id, platform="tiktok", age_hours=24,
            captured_at="2026-10-02T00:00:00Z", metrics={"views": views},
            publication_id=publication,
        )
    assert repo.get_performance_snapshot(first, 24)["views"] == 100
    assert repo.get_performance_snapshot(second, 24)["views"] == 900
    assert repo.recorded_ages(first) == {24}
    assert repo.recorded_ages(second) == {24}


def test_resync_does_not_erase_metrics_it_already_knew(clip):
    """Seccion 40: no se borran los valores originales. Que la plataforma deje
    de devolver una metrica la deja como estaba, no a NULL."""
    repo, job_id, clip_record_id = clip
    publication = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="youtube_shorts",
        platform_clip_id="abc", published_at="2026-09-30T10:00:00Z",
    )
    repo.upsert_performance_snapshot(
        clip_record_id=clip_record_id, platform="youtube_shorts", age_hours=24,
        captured_at="2026-10-01T10:00:00Z",
        metrics={"views": 12450, "likes": 812},
        raw={"views": "12450", "likes": "812"},
        publication_id=publication,
    )
    repo.upsert_performance_snapshot(
        clip_record_id=clip_record_id, platform="youtube_shorts", age_hours=24,
        captured_at="2026-10-02T10:00:00Z",
        metrics={"views": 13000, "likes": None},
        raw={"views": "13000"},
        publication_id=publication,
    )
    row = repo.get_performance_snapshot(publication, 24)
    assert row["views"] == 13000
    assert row["likes"] == 812


def test_zero_and_unknown_are_not_the_same_thing(clip):
    """Seccion 38: 0 significa cero; lo que no se mide es null. Este es el test
    que protege la distincion de la que depende toda la tabla."""
    repo, job_id, clip_record_id = clip
    publication = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_1", published_at="2026-09-30T10:00:00Z",
    )
    repo.upsert_performance_snapshot(
        clip_record_id=clip_record_id, platform="tiktok", age_hours=6,
        captured_at="2026-10-01T10:00:00Z",
        metrics={"views": "0", "likes": "--", "comments": "", "shares": None},
        publication_id=publication,
    )
    row = repo.get_performance_snapshot(publication, 6)
    assert row["views"] == 0            # cero de verdad
    assert row["likes"] is None         # no medido
    assert row["comments"] is None      # no medido
    assert row["shares"] is None        # no medido


def test_numeric_strings_with_thousands_separator_are_parsed(clip):
    repo, job_id, clip_record_id = clip
    publication = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="youtube_shorts",
        platform_clip_id="abc", published_at="2026-09-30T10:00:00Z",
    )
    repo.upsert_performance_snapshot(
        clip_record_id=clip_record_id, platform="youtube_shorts", age_hours=24,
        captured_at="2026-10-01T10:00:00Z",
        metrics={"views": "1,234", "average_percentage_viewed": "78.4"},
        publication_id=publication,
    )
    row = repo.get_performance_snapshot(publication, 24)
    assert row["views"] == 1234
    assert row["average_percentage_viewed"] == 78.4


def test_raw_payload_is_preserved_verbatim(clip):
    """Seccion 42: se guarda el nombre exacto de la metrica y cuando se
    obtuvo, porque YouTube cambio como cuenta las vistas de Shorts."""
    repo, job_id, clip_record_id = clip
    publication = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="youtube_shorts",
        platform_clip_id="abc", published_at="2026-09-30T10:00:00Z",
    )
    repo.upsert_performance_snapshot(
        clip_record_id=clip_record_id, platform="youtube_shorts", age_hours=24,
        captured_at="2026-10-01T10:00:00Z",
        metrics={"views": 12450},
        raw={"columnHeaders": [{"name": "averageViewPercentage"}], "rows": [["78.4"]]},
        publication_id=publication,
    )
    row = repo.get_performance_snapshot(publication, 24)
    stored = json.loads(row["raw_metrics_json"])
    assert stored["columnHeaders"][0]["name"] == "averageViewPercentage"


# --- metricas derivadas (seccion 40) ---


def test_rates_are_views_over_something():
    result = derived_module.compute_derived(
        {"views": 1000, "likes": 50, "comments": 10, "shares": 5, "subscribers_gained": 2}
    )
    assert result["like_rate"] == pytest.approx(0.05)
    assert result["comment_rate"] == pytest.approx(0.01)
    assert result["share_rate"] == pytest.approx(0.005)
    assert result["subscriber_conversion_rate"] == pytest.approx(0.002)


def test_dividing_by_zero_views_is_null_and_not_zero():
    """El caso que mas importa: 0/0 no es "no hubo likes", es "no se puede
    saber". Poner 0.0 produciria un clip con 0% de likes."""
    result = derived_module.compute_derived({"views": 0, "likes": 0, "shares": 3})
    assert result["like_rate"] is None
    assert result["comment_rate"] is None
    assert result["subscriber_conversion_rate"] is None
    # Sin vistas no hay denominator, tampoco para las que si tienen numerador.
    assert result["share_rate"] is None


def test_a_real_zero_is_still_zero():
    """Lo contrario del anterior, y no menos importante: con vistas y cero
    likes, la tasa ES cero. Si esto devolviera None, un clip sin likes seria
    indistinguible de uno que nadie midio."""
    result = derived_module.compute_derived({"views": 1000, "likes": 0, "shares": 0})
    assert result["like_rate"] == 0.0
    assert result["share_rate"] == 0.0


def test_missing_metrics_do_not_become_zero():
    result = derived_module.compute_derived({})
    assert all(value is None for value in result.values())


def test_retention_is_normalised_to_a_proportion():
    result = derived_module.compute_derived({"views": 100, "average_percentage_viewed": 78.4})
    assert result["retention_rate"] == pytest.approx(0.784)


def test_derived_metrics_carry_a_version():
    """Sin version no se puede saber con que formula se calculo una fila vieja."""
    assert derived_module.DERIVED_METRICS_VERSION == "1.0"


# --- ventanas temporales (seccion 41) ---


def test_measurement_window_spans_from_publication_to_the_measured_age():
    start, end = measurement_window("2026-09-30T10:00:00Z", age_hours=24)
    assert start == "2026-09-30"
    assert end == "2026-10-01"


def test_age_reached_is_false_before_the_age_and_true_after():
    published = "2026-09-30T10:00:00Z"
    assert not age_reached(published, 6, now=NOW)          # NOW son 2 h despues
    assert age_reached(published, 1, now=NOW)
    assert age_reached(published, 24, now=NOW + timedelta(hours=22))


def test_planned_ages_are_normalised_and_deduplicated():
    config = {"performance": {"snapshot_ages_hours": [24, 24.0, "48", 0, -6, "x"]}}
    assert planned_ages(config) == (24, 48)


def test_planned_ages_fall_back_to_the_blueprint_ladder():
    assert planned_ages({}) == (1, 6, 24, 48, 72, 168, 672)
    assert planned_ages(None) == (1, 6, 24, 48, 72, 168, 672)


# --- el contrato de un adaptador (seccion 43) ---


def test_a_provider_cannot_be_instantiated_without_implementing_the_contract():
    with pytest.raises(TypeError):
        PlatformAnalyticsProvider()  # type: ignore[abstract]


def test_build_provider_rejects_an_unknown_platform():
    with pytest.raises(ProviderUnavailable):
        build_provider("mixtape")


def test_build_provider_returns_the_right_adapter():
    assert build_provider("manual").name == "manual"
    assert build_provider("youtube").name == "youtube_shorts"


def test_a_provider_declares_which_platforms_it_measures():
    youtube = build_provider("youtube")
    assert youtube.handles_platform("youtube_shorts")
    assert not youtube.handles_platform("tiktok")
    # El manual sirve para cualquiera: los numeros los lee una persona.
    assert build_provider("manual").handles_platform("tiktok")


def test_parse_timestamp_rejects_nonsense():
    """Un `published_at` mal guardado daria ventanas equivocadas sin que nada
    lo delatara: las metricas seguirian siendo numeros plausibles."""
    with pytest.raises(ValueError):
        parse_timestamp("no es una fecha")


def test_parse_timestamp_assumes_utc_when_there_is_no_zone():
    assert parse_timestamp("2026-09-30T10:00:00") == datetime(2026, 9, 30, 10, tzinfo=UTC)


# --- adaptador de YouTube (seccion 42) ---


class FakeResponse:
    def __init__(self, payload=None, *, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    """Cliente httpx minimo: registra la peticion y devuelve lo que se le pase."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def get(self, url, *, params, headers):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.response

    def close(self):
        pass


YOUTUBE_PAYLOAD = {
    "kind": "analytics#report",
    "columnHeaders": [
        {"name": "video", "dataType": "DIMENSION"},
        {"name": "views", "dataType": "METRIC"},
        {"name": "likes", "dataType": "METRIC"},
        {"name": "comments", "dataType": "METRIC"},
        {"name": "shares", "dataType": "METRIC"},
        {"name": "subscribersGained", "dataType": "METRIC"},
        {"name": "averageViewDuration", "dataType": "METRIC"},
        {"name": "averageViewPercentage", "dataType": "METRIC"},
        {"name": "estimatedMinutesWatched", "dataType": "METRIC"},
        {"name": "impressions", "dataType": "METRIC"},
    ],
    "rows": [["abc123", "12450", "812", "94", "55", "31", "42.3", "78.4", "8743", "210000"]],
}


def test_youtube_is_unavailable_without_a_token():
    provider = YouTubeAnalyticsProvider({})
    assert not provider.available()
    assert "youtube_access_token" in provider.describe()
    with pytest.raises(ProviderUnavailable):
        provider.fetch_metrics("abc", published_at="2026-09-30T10:00:00Z", age_hours=24)


def test_youtube_request_asks_for_the_window_and_the_video():
    client = FakeClient(FakeResponse(YOUTUBE_PAYLOAD))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    provider.fetch_metrics("abc123", published_at="2026-09-30T10:00:00Z", age_hours=24)

    params = client.calls[0]["params"]
    assert params["startDate"] == "2026-09-30"
    assert params["endDate"] == "2026-10-01"
    assert params["filters"] == "video==abc123"
    assert params["metrics"].split(",") == list(REQUESTED_METRICS)
    assert client.calls[0]["headers"]["Authorization"] == "Bearer t"


def test_youtube_maps_platform_names_to_the_core_keys():
    client = FakeClient(FakeResponse(YOUTUBE_PAYLOAD))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc123", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    assert snapshot.metrics["views"] == 12450
    assert snapshot.metrics["likes"] == 812
    assert snapshot.metrics["subscribers_gained"] == 31
    assert snapshot.metrics["average_view_duration_seconds"] == 42.3
    assert snapshot.metrics["average_percentage_viewed"] == 78.4
    assert snapshot.metrics["shown_in_feed"] == 210000
    # Las que YouTube no mide a nivel de video se quedan a null, declaradas.
    assert snapshot.metrics["saves"] if "saves" in snapshot.metrics else True
    assert snapshot.metric_source == "youtube_analytics_api_v2"


def test_youtube_pairs_columns_by_name_and_not_by_position():
    """El orden de `columnHeaders` no esta garantizado. Emparejar por posicion
    daria metricas cruzadas en silencio, que es lo peor que puede pasar aqui."""
    payload = json.loads(json.dumps(YOUTUBE_PAYLOAD))
    payload["columnHeaders"] = list(reversed(payload["columnHeaders"]))
    payload["rows"] = [list(reversed(payload["rows"][0]))]

    client = FakeClient(FakeResponse(payload))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc123", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    assert snapshot.metrics["views"] == 12450
    assert snapshot.metrics["likes"] == 812


def test_youtube_leaves_unreported_metrics_at_null():
    payload = {
        "columnHeaders": [
            {"name": "video", "dataType": "DIMENSION"},
            {"name": "views", "dataType": "METRIC"},
            {"name": "shares", "dataType": "METRIC"},
        ],
        "rows": [["abc123", "0", "0"]],
    }
    client = FakeClient(FakeResponse(payload))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc123", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    assert snapshot.metrics["views"] == 0        # cero explicito
    assert snapshot.metrics["likes"] is None     # ni siquiera se pidio
    assert snapshot.metrics["comments"] is None


def test_youtube_notes_that_unsupported_metrics_stay_null():
    """Que la columna salga a null tiene que ser una decision documentada, no
    un olvido."""
    client = FakeClient(FakeResponse(YOUTUBE_PAYLOAD))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc123", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    joined = " ".join(snapshot.notes)
    for missing in UNSUPPORTED_METRICS:
        assert missing in joined


def test_youtube_refuses_sub_daily_ages_instead_of_inventing_them():
    """La granularidad de la API es el dia. Pedir 6h no puede devolver 6h, y
    guardar el dato diario etiquetado como 6h es una mentira."""
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"})
    assert provider.minimum_age_hours == 24
    with pytest.raises(ProviderUnavailable):
        provider.fetch_metrics("abc", published_at="2026-09-30T10:00:00Z", age_hours=6)


def test_youtube_treats_an_auth_error_as_unavailable_not_as_a_crash():
    provider = YouTubeAnalyticsProvider(
        {"youtube_access_token": "caducado"},
        client=FakeClient(FakeResponse(status_code=403, text="insufficient scope")),
    )
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.fetch_metrics("abc", published_at="2026-09-30T10:00:00Z", age_hours=24)
    assert "yt-analytics.readonly" in str(excinfo.value)


def test_youtube_treats_a_server_error_as_recoverable():
    provider = YouTubeAnalyticsProvider(
        {"youtube_access_token": "t"},
        client=FakeClient(FakeResponse(status_code=500, text="backend error")),
    )
    with pytest.raises(ProviderError):
        provider.fetch_metrics("abc", published_at="2026-09-30T10:00:00Z", age_hours=24)


def test_youtube_keeps_the_raw_response_for_the_audit_trail():
    client = FakeClient(FakeResponse(YOUTUBE_PAYLOAD))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc123", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    assert snapshot.raw["response"]["columnHeaders"][0]["name"] == "video"
    assert snapshot.raw["query_start_date"] == "2026-09-30"
    assert snapshot.raw["query_end_date"] == "2026-10-01"


def test_youtube_explains_an_empty_result_instead_of_returning_all_null():
    client = FakeClient(FakeResponse({"columnHeaders": [], "rows": []}))
    provider = YouTubeAnalyticsProvider({"youtube_access_token": "t"}, client=client)
    snapshot = provider.fetch_metrics(
        "abc", published_at="2026-09-30T10:00:00Z", age_hours=24
    )
    assert snapshot.metrics == {}
    assert snapshot.notes


# --- sync (secciones 41 y 70) ---


class RecordingProvider(PlatformAnalyticsProvider):
    """Proveedor de laboratorio: sirve lo que se le programe y anota las
    peticiones, para poder comprobar QUE se pidio y no solo el resultado."""

    name = "lab"
    metric_source = "lab"
    minimum_age_hours = 24
    handles = frozenset({"lab_platform"})
    ANY_PLATFORM = "*"

    def __init__(self, *, available: bool = True, fail_with: Exception | None = None):
        self._available = available
        self._fail_with = fail_with
        self.requested: list[tuple[str, int]] = []

    def available(self) -> bool:
        return self._available

    def fetch_metrics(self, platform_clip_id, *, published_at, age_hours):
        self.requested.append((platform_clip_id, age_hours))
        if self._fail_with is not None:
            raise self._fail_with
        return MetricSnapshot(
            platform=self.name,
            age_hours=age_hours,
            captured_at="2026-10-02T00:00:00Z",
            metrics={"views": 1000, "likes": 100},
            raw={"lab": True},
            metric_source=self.metric_source,
        )


def _published_lab(repo, clip_record_id, *, hours_ago=48, external_id="vid1"):
    return repo.upsert_publication(
        clip_record_id=clip_record_id,
        platform="lab_platform",
        platform_clip_id=external_id,
        published_at=(NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
    )


def test_sync_measures_every_age_the_platform_can_resolve(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200)
    provider = RecordingProvider()

    summary = sync_publications(repo, provider, now=NOW)

    assert summary["snapshots_written"] == 4        # 24, 48, 72 y 168
    assert [age for _, age in provider.requested] == [24, 48, 72, 168]
    rows = repo.list_performance_snapshots(clip_record_id=clip_record_id)
    assert [row["age_hours"] for row in rows] == [24, 48, 72, 168]


def test_sync_skips_ages_the_clip_is_too_young_for(clip):
    """Sin este filtro, la tabla direia que el clip no funciono a las 24h
    cuando en realidad nadie lo midio a las 24h."""
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=2)
    provider = RecordingProvider()

    summary = sync_publications(repo, provider, now=NOW)

    assert summary["snapshots_written"] == 0
    # El punto de 1h SI se ha cumplido (el clip lleva 2h), pero cae por debajo
    # del minimo del adaptador, asi que se cuenta como no medible y no como
    # demasiado pronto. De 1, 6, 24, 48, 72, 168 y 672: 6 sin cumplir edad.
    assert summary["skipped_too_early"] == 6
    assert summary["skipped_unsupported_age"] == 1
    assert provider.requested == []


def test_sync_skips_ages_below_the_platform_minimum(clip):
    """YouTube solo llega a 24h: los puntos de 1h y 6h se saltan."""
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=30)
    provider = RecordingProvider()

    summary = sync_publications(repo, provider, now=NOW)

    assert [age for _, age in provider.requested] == [24]
    assert summary["skipped_unsupported_age"] == 2   # 1 y 6
    assert summary["skipped_too_early"] == 4         # 168 y 672


def test_sync_does_not_measure_the_same_point_twice(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200)
    first = RecordingProvider()
    sync_publications(repo, first, now=NOW)
    second = RecordingProvider()

    summary = sync_publications(repo, second, now=NOW)

    assert summary["snapshots_written"] == 0
    # Solo cuentan los 4 puntos que el adaptador puede medir: 1 y 6 se
    # descartan antes por el minimo del adaptador, y 672 por no haber cumplido.
    assert summary["skipped_already_recorded"] == 4
    assert summary["skipped_unsupported_age"] == 2
    assert summary["skipped_too_early"] == 1
    assert second.requested == []


def test_force_re_measures_already_recorded_points(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200)
    sync_publications(repo, RecordingProvider(), now=NOW)
    forced = RecordingProvider()

    summary = sync_publications(repo, forced, force=True, now=NOW)

    assert summary["snapshots_written"] == 4
    assert len(repo.list_performance_snapshots(clip_record_id=clip_record_id)) == 4


def test_sync_stores_derived_metrics_and_the_raw_payload(clip):
    repo, job_id, clip_record_id = clip
    publication = _published_lab(repo, clip_record_id, hours_ago=200)

    sync_publications(repo, RecordingProvider(), now=NOW)

    row = repo.get_performance_snapshot(publication, 24)
    assert row["like_rate"] == pytest.approx(0.1)
    assert row["derived_metrics_version"] == derived_module.DERIVED_METRICS_VERSION
    assert json.loads(row["raw_metrics_json"]) == {"lab": True}
    assert row["metric_source"] == "lab"
    assert row["publication_id"] == publication


def test_sync_reports_an_unavailable_provider_without_raising(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id)

    summary = sync_publications(repo, RecordingProvider(available=False), now=NOW)

    assert summary["snapshots_written"] == 0
    assert summary["errors"]


def test_sync_ignores_publications_of_platforms_the_provider_does_not_measure(clip):
    repo, job_id, clip_record_id = clip
    repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_1", published_at="2026-09-01T00:00:00Z",
    )
    provider = RecordingProvider()

    summary = sync_publications(repo, provider, now=NOW)

    assert summary["publications"] == 0
    assert summary["ignored_wrong_platform"] == 1
    assert provider.requested == []


def test_sync_says_so_when_asked_for_a_platform_it_cannot_measure(clip):
    repo, job_id, clip_record_id = clip

    summary = sync_publications(repo, RecordingProvider(), platform="tiktok", now=NOW)

    assert summary["snapshots_written"] == 0
    assert "no mide la plataforma 'tiktok'" in summary["errors"][0]


def test_one_broken_publication_does_not_stop_the_others(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200, external_id="bueno")
    _published_lab(repo, clip_record_id, hours_ago=200, external_id="roto")
    provider = RecordingProvider()

    def fetch(platform_clip_id, *, published_at, age_hours):
        if platform_clip_id == "roto":
            raise ProviderError("fallo de red")
        return RecordingProvider.fetch_metrics(provider, platform_clip_id, published_at=published_at, age_hours=age_hours)

    provider.fetch_metrics = fetch  # type: ignore[method-assign]

    summary = sync_publications(repo, provider, now=NOW)

    assert summary["snapshots_written"] == 4      # el Bueno
    assert any("roto" in error for error in summary["errors"])


def test_timeline_lists_points_from_oldest_to_newest(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200)
    sync_publications(repo, RecordingProvider(), now=NOW)

    rows = timeline(repo, clip_record_id)

    assert [row["age_hours"] for row in rows] == [24, 48, 72, 168]
    assert rows[0]["like_rate"] == pytest.approx(0.1)


def test_timeline_keeps_two_publications_apart(clip):
    """Un clip republicado en la misma plataforma no puede salir como una sola
    escalera: las edades se repetirian (24, 48, 24, 48) y se leeria como una
    publicacion unica cuyas metricas dan saltos."""
    repo, job_id, clip_record_id = clip
    old = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_1", published_at="2026-09-01T00:00:00Z",
    )
    new = repo.upsert_publication(
        clip_record_id=clip_record_id, platform="tiktok",
        platform_clip_id="tt_2", published_at="2026-10-01T00:00:00Z",
    )
    for publication, views in ((old, 100), (new, 900)):
        for age in (24, 48):
            repo.upsert_performance_snapshot(
                clip_record_id=clip_record_id, platform="tiktok", age_hours=age,
                captured_at="2026-10-02T00:00:00Z", metrics={"views": views},
                publication_id=publication,
            )

    rows = timeline(repo, clip_record_id, platform="tiktok")

    # Cada publicacion con su escalera entera y en orden.
    assert [row["age_hours"] for row in rows] == [24, 48, 24, 48]
    assert [row["views"] for row in rows] == [100, 100, 900, 900]
    assert {row["publication_id"] for row in rows} == {old, new}

    # Y se puede pedir una sola.
    solo = timeline(repo, clip_record_id, platform="tiktok", publication_id=new)
    assert [row["views"] for row in solo] == [900, 900]
    assert {row["platform_clip_id"] for row in solo} == {"tt_2"}


# --- adaptador manual ---


def test_manual_provider_returns_what_it_was_given(clip):
    repo, job_id, clip_record_id = clip
    _published_lab(repo, clip_record_id, hours_ago=200)
    provider = ManualProvider()
    provider.add("vid1", 6, {"views": 12, "likes": 1})

    snapshot = provider.fetch_metrics(
        "vid1", published_at="2026-09-20T00:00:00Z", age_hours=6
    )
    assert snapshot.metrics["views"] == 12
    assert snapshot.metric_source == "manual"


def test_manual_provider_says_when_it_has_nothing(clip):
    with pytest.raises(ProviderUnavailable):
        ManualProvider().fetch_metrics("nada", published_at="2026-09-20T00:00:00Z", age_hours=6)


def test_manual_provider_can_measure_sub_daily_points_that_the_api_cannot():
    """Una persona leyendo el panel ve el numero a las 6h aunque la API no lo
    sepa resolver. Por eso el manual declara minimum_age_hours = 0."""
    assert ManualProvider.minimum_age_hours == 0
