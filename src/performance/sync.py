"""Orquestador de `performance sync` (secciones 38, 41 y 70).

Recorre las publicaciones, decide que puntos de la escalera temporal se pueden
medir HOY, pide las metricas a un adaptador y escribe un snapshot por punto.

La logica interesante esta casi toda en DOS filtros, y ambos estan puestos para
preferir no tener dato antes que tener un dato falso:

1. `age_reached`: un clip de dos horas no tiene punto de 24h. Escribirlo
   llenaria la escalera con mediciones de "ahora" etiquetadas como "a las 24
   horas", y dentro de tres meses esa tabla diria que el clip no funciono a las
   24h cuando en realidad nadie lo midio a las 24h.

2. `provider.minimum_age_hours`: YouTube solo resuelve granularidad diaria, así
   que los puntos de 1h y 6h se saltan en vez de rellenarse.

Lo que no hace: ni inventar metricas, ni promediar puntos que faltan, ni dar por
bueno un `0` donde la plataforma no CONTESTO. La seccion 41 es explicita ("no
asumir que todas las plataformas proporcionaran todos los puntos, guardar
unicamente lo disponible") y la 38 prohibe el `0` disfrazado de dato.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from src.core import config as config_module
from src.core.db import JobRepository, utc_now
from src.performance.base import (
    PlatformAnalyticsProvider,
    ProviderError,
    ProviderUnavailable,
    age_reached,
    build_provider,
    parse_timestamp,
)
from src.performance.derived import DERIVED_METRICS_VERSION, compute_derived

#: Escalera temporal por defecto (seccion 41). La config puede cambiarla.
DEFAULT_AGES: tuple[int, ...] = (1, 6, 24, 48, 72, 168, 672)


def planned_ages(config: Mapping[str, Any] | None = None) -> tuple[int, ...]:
    """La escalera de edades configurada, normalizada y sin repetir.

    Lee `performance.snapshot_ages_hours` del config del proyecto y no un
    diccionario suelto: la escalera es una decision de negocio y tenerla en dos
    sitios garantiza que se usan dos.

    Se normaliza a enteros positivos y sin duplicados porque van a acabar
    formando parte de la clave UNIQUE de cada snapshot: un 24 y un 24.0 deben
    ser el mismo punto, no dos filas compitiendo por la misma clave.
    """
    raw = config_module.get(dict(config or {}), "performance.snapshot_ages_hours", None)
    if not raw:
        return DEFAULT_AGES
    ages: set[int] = set()
    for value in raw:
        try:
            age = int(value)
        except (TypeError, ValueError):
            continue
        if age > 0:
            ages.add(age)
    return tuple(sorted(ages)) if ages else DEFAULT_AGES


def sync_publications(
    repo: JobRepository,
    provider: PlatformAnalyticsProvider,
    *,
    config: Mapping[str, Any] | None = None,
    job_id: str | None = None,
    platform: str | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mide todas las publicaciones pendientes y devuelve un resumen.

    `force` vuelve a pedir los puntos ya registrados. Sin el, un snapshot
    existente se salta: la escalera de la seccion 41 se rellena UNA vez por
    punto, y repreguntar el punto de 24h un mes despues daria el acumulado de
    un mes etiquetado como 24 horas.

    Un fallo en una publicacion no corta el resto: se anota en `errors` y se
    sigue. Un token caducado deberia dejar sincronizadas las demas
    publicaciones, no impedir el trabajo de la tarde.
    """
    reference = now or datetime.now(tz=UTC)
    ages = planned_ages(config)
    candidates = repo.list_publications(job_id=job_id, platform=platform)

    summary: dict[str, Any] = {
        "provider": provider.name,
        "metric_source": provider.metric_source,
        "planned_ages_hours": list(ages),
        "publications": 0,
        "ignored_wrong_platform": 0,
        "snapshots_written": 0,
        "skipped_already_recorded": 0,
        "skipped_too_early": 0,
        "skipped_unsupported_age": 0,
        "errors": [],
        "written": [],
    }

    if platform is not None and not provider.handles_platform(platform):
        # Fallar aqui y no mas tarde, con un mensaje util. Si se deja pasar, lo
        # que sale es un 404 de YouTube por cada publicacion de TikTok, y el
        # mensaje no dice nada de plataformas.
        summary["errors"].append(
            f"el adaptador '{provider.name}' no mide la plataforma '{platform}'; "
            f"cubre: {', '.join(sorted(provider.handles)) or 'ninguna'}"
        )
        return summary

    # Descarta publicaciones de plataformas que este adaptador no mide, en
    # lugar de pedirlas y acumular errores de 404.
    publications = [
        publication
        for publication in candidates
        if provider.handles_platform(str(publication["platform"]))
    ]
    summary["publications"] = len(publications)
    summary["ignored_wrong_platform"] = len(candidates) - len(publications)

    if not provider.available():
        summary["errors"].append(f"{provider.name} no disponible: {provider.describe()}")
        return summary

    for publication in publications:
        clip_record_id = str(publication["clip_record_id"])
        publication_id = str(publication["id"])
        published_at = str(publication["published_at"])
        already = repo.recorded_ages(publication_id)

        for age in ages:
            if age in already and not force:
                summary["skipped_already_recorded"] += 1
                continue
            if not age_reached(published_at, age, now=reference):
                summary["skipped_too_early"] += 1
                continue
            if age < provider.minimum_age_hours:
                summary["skipped_unsupported_age"] += 1
                continue
            try:
                snapshot = provider.fetch_metrics(
                    str(publication["platform_clip_id"]),
                    published_at=published_at,
                    age_hours=age,
                )
            except ProviderUnavailable as exc:
                summary["errors"].append(
                    f"{publication['platform_clip_id']} @{age}h: {exc}"
                )
                break  # si el proveedor no puede, no insistir con el resto de edades
            except ProviderError as exc:
                summary["errors"].append(
                    f"{publication['platform_clip_id']} @{age}h: {exc}"
                )
                continue

            derived = compute_derived(snapshot.metrics)
            snapshot_id = repo.upsert_performance_snapshot(
                clip_record_id=clip_record_id,
                platform=str(publication["platform"]),
                age_hours=age,
                captured_at=snapshot.captured_at or utc_now(),
                publication_id=str(publication["id"]),
                platform_clip_id=str(publication["platform_clip_id"]),
                metrics=snapshot.metrics,
                derived=derived,
                metric_source=snapshot.metric_source or provider.metric_source,
                derived_metrics_version=DERIVED_METRICS_VERSION,
                raw=snapshot.raw,
            )
            summary["snapshots_written"] += 1
            summary["written"].append(
                {
                    "snapshot_id": snapshot_id,
                    "clip_record_id": clip_record_id,
                    "platform": publication["platform"],
                    "platform_clip_id": publication["platform_clip_id"],
                    "age_hours": age,
                    "notes": snapshot.notes,
                }
            )

    return summary


def timeline(
    repo: JobRepository,
    clip_record_id: str,
    *,
    platform: str | None = None,
    publication_id: str | None = None,
) -> list[dict[str, Any]]:
    """Los snapshots de un clip en orden cronologico, listos para mostrar.

    Devuelve filas planas y no las columnas crudas porque la forma de leerlas
    es siempre la misma (edad, vistas, engagement, derivadas) y meter eso en la
    CLI en cada sitio seria repetir el mismo `if` por las columnas.

    Cada fila lleva su `publication_id` y `platform_clip_id`. Sin ellos, un clip
    republicado en la misma plataforma daria una tabla con edades repetidas
    (24, 48, 24, 48) indistinguible de una publicacion cuyas metricas saltan.
    """
    rows: Sequence[Mapping[str, Any]] = repo.list_performance_snapshots(
        clip_record_id=clip_record_id, platform=platform, publication_id=publication_id
    )
    return [
        {
            "publication_id": row["publication_id"],
            "platform_clip_id": row["platform_clip_id"],
            "platform": row["platform"],
            "age_hours": row["age_hours"],
            "views": row["views"],
            "likes": row["likes"],
            "comments": row["comments"],
            "shares": row["shares"],
            "subscribers_gained": row["subscribers_gained"],
            "average_view_duration_seconds": row["average_view_duration_seconds"],
            "average_percentage_viewed": row["average_percentage_viewed"],
            "like_rate": row["like_rate"],
            "comment_rate": row["comment_rate"],
            "share_rate": row["share_rate"],
            "subscriber_conversion_rate": row["subscriber_conversion_rate"],
            "captured_at": row["captured_at"],
            "metric_source": row["metric_source"],
        }
        for row in rows
    ]


def age_hours_now(published_at: str, *, now: datetime | None = None) -> float:
    """Cuantas horas lleva publicado un clip, para el proximo punto posible."""
    reference = now or datetime.now(tz=UTC)
    return round((reference - parse_timestamp(published_at)).total_seconds() / 3600.0, 2)
