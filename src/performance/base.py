"""El contrato de un proveedor de metricas (secciones 42 y 43).

La seccion 43 propone `fetch_metrics(clip_id)` y lo firma como interface con
`pass`. Se mantiene el nombre, pero la firma real necesita dos cosas mas, y las
dos sonjolables de evitar:

- `published_at`: las metricas de una plataforma son ACUMULADAS desde la
  subida, no desde que se pregunta. Sin la fecha de publicacion no se puede
  pedir "cuanto lleva el clip a las 24h", que es justo lo que pide la seccion
  41. Preguntando hoy a un video de tres meses sale el total de tres meses
  como si fuera su primer dia.
- `age_hours`: el punto de la escalera que se quiere medir.

Las dos son inevitables: sin ellas la cifra que se guarda no es la que dice
decir.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar


class ProviderError(RuntimeError):
    """Fallo recuperable hablando con la plataforma (red, 403, cuota).

    Se distingue de `ProviderUnavailable` porque quien llama trata las dos
    cosas de forma distinta: un error de red se puede reintentar, la falta de
    credenciales no.
    """


class ProviderUnavailable(ProviderError):
    """El proveedor no se puede usar: faltan credenciales o no esta configurado.

    No es un fallo del pipeline. `sync` lo trata como "este adaptador hoy no
    puede", no como error, para que un token caducado no impida registrar a
    mano lo que se haya leido de la web de la plataforma.
    """


@dataclass(frozen=True)
class MetricSnapshot:
    """Una foto de las metricas de un clip en un momento concreto.

    `metrics` son las metricas de la seccion 39 ya renombradas a las claves del
    nucleo (`views`, `likes`, ...), con `None` donde la plataforma no mide.

    `raw` es la respuesta de la plataforma SIN tocar. Se guarda porque la
    seccion 42 pide conservar el nombre exacto de cada metrica y la fecha en
    que se obtuvo, y porque la 40 prohibe borrar los valores originales: si en
    un mes YouTube renombra `averageViewPercentage`, el dato viejo solo
    sobrevive si se guardo tal cual.
    """

    platform: str
    age_hours: int
    captured_at: str
    metrics: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)
    metric_source: str = ""
    notes: list[str] = field(default_factory=list)


def to_number(value: Any) -> float | None:
    """Convierte a numero, conservando el null de "no lo sé" (seccion 38).

    Existe porque TODAS las APIs de analitica devuelven los numeros como
    cadenas: `"12450"`, `"78.4"`, `"1,234"`. Sin esta normalizacion,
    `MetricSnapshot.metrics` llevaria `"12450"` dentro y cada consumidor
    tendria que acordarse de convertirlo, que es exactamente como se cuela un
    bug de comparacion de strings en una comparacion de cifras.

    Lo que NO convierte es None a 0. Un `"0"` se queda en 0.0 porque es un
    cero de verdad; un `"--"`, un `""` o un `None` se quedan en None porque
    significan "esta plataforma no mide esto". Confundir ambos es el error que
    la seccion 38 prohibe expressly.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("%", "")
        if text in ("", "--", "null", "None", "N/A", "n/a"):
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: str) -> datetime:
    """Interpreta un timestamp ISO 8601 y lo pasa a UTC con tz.

    Acepta la `Z` que usa `db.utc_now()` y los `+00:00` de Python. Rechaza lo
    que no se pueda interpretar en lugar de adivinar: un `published_at` mal
    guardado daria unas ventanas temporales equivocadas que nadie detectaria,
    porque las metricas seguirian siendo numeros plausibles.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"timestamp no interpretable: {value!r}") from exc
    if parsed.tzinfo is None:
        # Sin zona horaria no se sabe que instante es, y de eso depende el
        # reparto entre las ventanas de la seccion 41.
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def measurement_window(published_at: str, age_hours: int) -> tuple[str, str]:
    """Ventana `(start_date, end_date)` para medir un clip de `age_hours`.

    Devuelve FECHAS en ISO, no instantes, porque es lo que aceptan las APIs de
    analitica: casi todas redondean el dato al dia. YouTube, por ejemplo, no
    puede resolver 1h ni 6h aunque se le pidan, y por eso el adaptador declara
    `minimum_age_hours` y `sync` no le pide esos puntos en vez de guardar un
    numero que en realidad mide 24 horas.

    El rango es del dia de la publicacion al dia en que se cumple la edad. Es
    acumulativo: incluye el dia de la subida entero, que es lo que hace
    comparables las ventanas entre clips.
    """
    start = parse_timestamp(published_at)
    end = start + timedelta(hours=age_hours)
    return start.date().isoformat(), end.date().isoformat()


def age_reached(published_at: str, age_hours: int, *, now: datetime | None = None) -> bool:
    """¿Ha pasado ya `age_hours` desde la publicacion?

    Sin esto, `sync` de una instalacion recien puesta escribiria el punto de
    168h de un clip de dos horas con las metricas del momento, y esa fila
    quedaria como si fuera una medida real de una semana.
    """
    reference = now or datetime.now(tz=UTC)
    elapsed = reference - parse_timestamp(published_at)
    return elapsed >= timedelta(hours=age_hours)


class PlatformAnalyticsProvider(ABC):
    """Lo que tiene que saber hacer un adaptador de plataforma (seccion 43).

    Es una clase abstracta y no un `Protocol` a proposito: se quiere que anadir
    TikTok o Instagram sea escribir una clase, no cumplir una forma.
    """

    #: Identificador de la plataforma (`youtube_shorts`, `tiktok`, ...).
    name: ClassVar[str] = "abstract"

    #: De donde salieron los numeros, para poder distinguir una lectura de la
    #: API de una lectura hecha a mano.
    metric_source: ClassVar[str] = "abstract"

    #: Edad minima en horas que la plataforma puede medir de verdad. Los
    #: puntos menores se saltan; se registra menos, pero lo que se registra es
    #: cierto (seccion 41: "guardar unicamente lo disponible").
    minimum_age_hours: ClassVar[int] = 0

    #: Las plataformas que este adaptador sabe medir. Vacio = ninguna.
    #:
    #: Hace falta porque "que publicaciones mira `sync`" y "quien mide esas
    #: publicaciones" son dos cosas distintas, y confundirlas produce basura
    #: silenciosa: si hay una publicacion de TikTok y el proveedor por defecto
    #: es YouTube, `sync` iria a pedirle a YouTube las metricas de un video de
    #: TikTok, y el id no existiria alla. Sin este filtro, el unico sintoma es
    #: un 404 por publicacion y ningun aviso de que el problema era de
    #: plataforma.
    handles: ClassVar[frozenset[str]] = frozenset()

    #: Valor comodin para un adaptador que sirve para cualquier plataforma
    #: (el manual: los numeros los lee una persona de donde sea).
    ANY_PLATFORM: ClassVar[str] = "*"

    def handles_platform(self, platform: str) -> bool:
        key = platform.strip().lower()
        return self.ANY_PLATFORM in self.handles or key in self.handles

    @abstractmethod
    def available(self) -> bool:
        """¿Este adaptador puede usarse ahora mismo, sin tocar nada mas?

        No debe hacer llamadas de red: es la comprobacion de "estan las
        credenciales puestas", que se hace antes de recorrer las
        publicaciones para poder avisar de una vez en vez de fallar en el
        primero de cuarenta clips.
        """

    @abstractmethod
    def fetch_metrics(self, platform_clip_id: str, *, published_at: str, age_hours: int) -> MetricSnapshot:
        """Mide un clip. Lanza `ProviderUnavailable` si no se puede usar."""

    def describe(self) -> str:
        """Texto para el usuario: que plataforma es y si esta lista."""
        state = "disponible" if self.available() else "NO disponible"
        return f"{self.name} ({state}, metric_source={self.metric_source})"


def build_provider(name: str, config: Mapping[str, Any] | None = None) -> PlatformAnalyticsProvider:
    """Devuelve el adaptador pedido por nombre.

    Se construye aqui y no en el nucleo de `sync` para que anadir una
    plataforma sea tocar esta funcion y su modulo, y no el orquestador.
    """
    key = name.strip().lower()
    if key == "manual":
        from src.performance.manual import ManualProvider

        return ManualProvider()
    if key in ("youtube", "youtube_shorts"):
        from src.performance.youtube import YouTubeAnalyticsProvider

        return YouTubeAnalyticsProvider(config or {})
    raise ProviderUnavailable(
        f"plataforma '{name}' sin adaptador. Disponibles: youtube, manual "
        "(seccion 43: cada plataforma nueva es un modulo nuevo)"
    )


def dumps_raw(raw: Mapping[str, Any]) -> str:
    """Serializa el payload crudo de forma estable y sin fallar.

    `default=str` a proposito: si la plataforma devuelve algo raro (una fecha
    anidada, por ejemplo) se guarda su representacion en texto en vez de
    perder el snapshot entero por un TypeError al serializar.
    """
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
