"""YouTube como primer adaptador (seccion 42).

Advertencia honesta sobre el alcance de este modulo: la PETICION y el
PARSEO estan escritos contra la YouTube Analytics API v2 documentada
(`reports.query`) y cubiertos por tests con la respuesta simulada, pero NO se
han podido ejecutar contra la API real desde aqui: hace falta un canal de
OAuth con el scope `yt-analytics.readonly` y una cuenta con Shorts publicados.
Lo que esta verificado es el contrato (parametros, nombres de metrica, forma
de la respuesta); lo que no lo esta es la mano de esa cuenta concreta. La
primera vez que se use contra el canal real hay que mirar el `raw` que se
guarda, que es justo para eso: se escribe la respuesta tal cual la devuelve
YouTube para poder depurar sin volver a pedirla.

Dos cosas que la seccion 42 obliga a tener presentes y que este modulo NO
intenta disimular:

1. YouTube cambio como cuenta las vistas publicas de Shorts y mantiene
   `engaged views` en Analytics. Por eso se guarda el nombre exacto de cada
   metrica y la fecha: un `views` de hoy y un `views` de hace seis meses pueden
   no ser la misma cifra aunque se llamen igual.

2. La granularidad de la API es el DIA. No puede resolver 1h ni 6h. En vez de
   devolver un numero para esos puntos, este adaptador declara
   `minimum_age_hours = 24` y `sync` se los salta: la seccion 41 pide guardar
   unicamente lo disponible, y un 1h inventado a partir de un dato diario es
   justo el tipo de mentira que luego aparece como conclusion en un informe.

OJO tambien con el efecto 24h: la ventana es "del dia de la publicacion al
dia en que cumple 24h", en dias naturales. Un clip publicado a las 23:00 tiene
en su ventana de 24h una hora real de datos, y uno publicado a las 01:00 tiene
veintitres. YouTube no ofrece una forma de corregirlo, asi que se documenta en
vez de inventar un ajuste.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from src.performance.base import (
    MetricSnapshot,
    PlatformAnalyticsProvider,
    ProviderError,
    ProviderUnavailable,
    measurement_window,
    to_number,
)

REPORTS_ENDPOINT = "https://youtubeanalytics.googleapis.com/v2/reports"

#: Metricas que se piden a YouTube, en el orden en que se piden. Se piden las
#: de la seccion 39 que esta plataforma SÍ mide a nivel de video.
#:
#: Lo que se pide de menos es deliberado. Pedir una metrica que la API no
#: reconoce devuelve un 400 para la consulta COMPLETA, no solo para esa
#: metrica, asi que ampliar esta lista exige comprobar antes que existe.
REQUESTED_METRICS: tuple[str, ...] = (
    "views",
    "likes",
    "comments",
    "shares",
    "subscribersGained",
    "averageViewDuration",
    "averageViewPercentage",
    "estimatedMinutesWatched",
    "impressions",
)

#: Metrica de la plataforma -> clave del nucleo (seccion 39). Los nombres de
#: la derecha son los que entiende `compute_derived` y los que tienen columna
#: en `performance_snapshots`, de modo que comparar un clip de YouTube con uno
#: de TikTok no exige saber de donde salio cada numero.
METRIC_MAP: dict[str, str] = {
    "views": "views",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
    "averageViewDuration": "average_view_duration_seconds",
    "averageViewPercentage": "average_percentage_viewed",
    "impressions": "shown_in_feed",
}

#: Metricas de la seccion 39 que esta plataforma NO expone a nivel de video.
#: Se listan en vez de omitirse en silencio para que quede escrito por que la
#: columna sale a NULL, que es distinto de "no se midio" (seccion 38).
#:
#:  - unique_viewers: es una DIMENSION en Analytics, no una metrica de video.
#:  - completion_rate: YouTube no la publica; se aproxima con
#    averageViewPercentage, y el nucleo lo guarda en su propia columna para no
#    disfrazar una aproximacion de un dato de la plataforma.
#:  - saves / followers_gained / profile_visits: no existen en la API.
#:  - traffic_source / audience_source: son dimensiones; exigiran una segunda
#:    consulta agrupada. Fuera del MVP, y declarado aqui para que la omision
#:    sea una decision y no un olvido.
UNSUPPORTED_METRICS: dict[str, str] = {
    "unique_viewers": "es una dimension, no una metrica de video",
    "completion_rate": "no se publica; se aproxima con averageViewPercentage",
    "saves": "no existe en la API",
    "followers_gained": "no existe; en YouTube los seguidores son suscriptores",
    "profile_visits": "no existe a nivel de video",
    "traffic_source": "es una dimension; requiere otra consulta agrupada",
    "audience_source": "es una dimension; requiere otra consulta agrupada",
}


class YouTubeAnalyticsProvider(PlatformAnalyticsProvider):
    """Lee metricas de Shorts desde YouTube Analytics API v2."""

    name: ClassVar[str] = "youtube_shorts"
    metric_source: ClassVar[str] = "youtube_analytics_api_v2"
    minimum_age_hours: ClassVar[int] = 24
    # `youtube` tambien se acepta porque es como lo escribe la gente, pero el
    # nombre canonico en la base de datos es `youtube_shorts`.
    handles: ClassVar[frozenset[str]] = frozenset({"youtube_shorts", "youtube"})

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        settings = dict(config or {})
        self._channel_id: str | None = settings.get("youtube_channel_id") or None
        self._token: str | None = settings.get("youtube_access_token") or None
        self._timeout: float = float(settings.get("timeout_seconds", 30.0))
        # Inyectable para poder probar el parseo sin red.
        self._client = client

    def available(self) -> bool:
        """Comprueba solo configuracion, sin hacer peticiones.

        Falta el token en el 99% de las instalaciones nuevas, y avisar de eso
        antes de recorrer 40 publicaciones es la diferencia entre un mensaje
        util y cuarenta errores repetidos.
        """
        return bool(self._token)

    def describe(self) -> str:
        if not self.available():
            return (
                f"{self.name} (NO disponible: falta youtube_access_token. "
                "Se obtiene con el scope yt-analytics.readonly)"
            )
        return super().describe()

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise ProviderUnavailable(
                "falta youtube_access_token (seccion 42: scope yt-analytics.readonly)"
            )
        return {"Authorization": f"Bearer {self._token}"}

    def build_request_params(self, platform_clip_id: str, *, published_at: str, age_hours: int) -> dict[str, str]:
        """Parametros exactos de la consulta. Aislado para poder testearlos."""
        start_date, end_date = measurement_window(published_at, age_hours)
        filters = f"video=={platform_clip_id}"
        if self._channel_id:
            filters += f",channel=={self._channel_id}"
        return {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "metrics": ",".join(REQUESTED_METRICS),
            "dimensions": "video",
            "filters": filters,
        }

    def fetch_metrics(
        self, platform_clip_id: str, *, published_at: str, age_hours: int
    ) -> MetricSnapshot:
        if age_hours < self.minimum_age_hours:
            # No es un error de configuracion sino una decision: se documenta
            # en la nota del snapshot para que quede constancia de que el
            # punto se salto a proposito y no por olvido.
            raise ProviderUnavailable(
                f"{self.name} no puede resolver {age_hours}h: la granularidad de la "
                "API es el dia (seccion 41)"
            )

        params = self.build_request_params(
            platform_clip_id, published_at=published_at, age_hours=age_hours
        )
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.get(
                REPORTS_ENDPOINT, params=params, headers=self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"fallo de red hacia YouTube Analytics: {exc}") from exc
        finally:
            if self._client is None:
                client.close()

        if response.status_code in (401, 403):
            # 403 aqui casi siempre es el scope que falta o un token caducado,
            # no cuota agotada. Se distingue el texto porque la accion es otra:
            # renovar el token.
            raise ProviderUnavailable(
                f"YouTube rechazo la peticion ({response.status_code}): revisa el scope "
                f"yt-analytics.readonly y que el token no haya caducado. "
                f"Respuesta: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"YouTube Analytics devolvio {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"respuesta no JSON de YouTube: {response.text[:200]}") from exc

        metrics, notes = self._parse(payload, platform_clip_id)
        return MetricSnapshot(
            platform=self.name,
            age_hours=int(age_hours),
            captured_at=datetime.now(tz=UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            metrics=metrics,
            # Seccion 42: la respuesta tal cual, con sus nombres de metrica.
            # Es lo que permite, dentro de seis meses, saber que un `views` de
            # hoy no es comparable con uno de antes del cambio de conteo.
            raw={
                "provider": self.metric_source,
                "queried_video_id": platform_clip_id,
                "query_start_date": params["startDate"],
                "query_end_date": params["endDate"],
                "response": payload,
            },
            metric_source=self.metric_source,
            notes=notes,
        )

    def _parse(
        self, payload: Mapping[str, Any], platform_clip_id: str
    ) -> tuple[dict[str, Any], list[str]]:
        """Convierte la respuesta de la API en las claves del nucleo.

        YouTube devuelve los valores como CADENAS dentro de `rows`, y el
        nombre de cada columna vive aparte, en `columnHeaders`. El orden de las
        columnas NO esta garantizado, asi que se emparejan por nombre y no por
        posicion: dar por hecho el orden es el tipo de bug que solo aparece
        cuando Google reordena algo.
        """
        headers: Sequence[Mapping[str, Any]] = payload.get("columnHeaders") or []
        names = [str(header.get("name", "")) for header in headers]
        rows: Sequence[Sequence[Any]] = payload.get("rows") or []
        notes: list[str] = []

        if not rows:
            notes.append(
                "YouTube no devolvio filas: el video no tiene datos en esa ventana "
                "(puede ser recien subido, o no ser del canal indicado)"
            )
            return {}, notes

        row = rows[0]
        by_metric = {
            name: value
            for name, value in zip(names, row, strict=False)
            if name and name != "video"
        }
        if len(row) != len(names):
            notes.append(
                f"respuesta desalineada: {len(names)} columnas y {len(row)} valores"
            )

        metrics: dict[str, Any] = {}
        for source_name, target_name in METRIC_MAP.items():
            # Se normaliza aqui y no se deja la cadena tal cual: `metrics` es
            # el contrato del nucleo y tiene que llevar numeros. Las cadenas
            # originales siguen disponibles en `raw`.
            metrics[target_name] = to_number(by_metric.get(source_name))

        if not any(value is not None for value in metrics.values()):
            notes.append(
                "YouTube devolvio la fila vacia: todas las metricas quedan a null "
                "(seccion 38: null es 'no medido', nunca 0)"
            )
        for missing, reason in UNSUPPORTED_METRICS.items():
            notes.append(f"{missing}: null, {reason}")
        return metrics, notes
