"""Performance Intelligence: publications, snapshots y adaptadores de plataforma.

Sprint 8 del blueprint (secciones 38-44). El nucleo NO habla con ninguna API:
define el contrato de un `PlatformAnalyticsProvider` y el resto lo resuelve el
core. Seccion 43: "No mezclar APIs dentro del core".

La idea de fondo de la seccion 38 es que un unico resultado final no sirve para
aprender nada: las metricas de un clip hay que medirlas en la escalera temporal
de la seccion 41 (1h, 6h, 24h... 28d) y guardar cada punto por separado, porque
un clip que hace 500 vistas el primer dia y se queda plano, y otro que hace 80
el primer dia y sigue creciendo, son casos opuestos y con un unico numero final
quedan iguales.
"""

from __future__ import annotations

from src.performance.base import (
    MetricSnapshot,
    PlatformAnalyticsProvider,
    ProviderError,
    ProviderUnavailable,
    build_provider,
)
from src.performance.derived import DERIVED_METRICS_VERSION, compute_derived
from src.performance.manual import ManualProvider

__all__ = [
    "DERIVED_METRICS_VERSION",
    "ManualProvider",
    "MetricSnapshot",
    "PlatformAnalyticsProvider",
    "ProviderError",
    "ProviderUnavailable",
    "build_provider",
    "compute_derived",
]
