"""Adaptador manual: el usuario mete las metricas a mano.

No es una plataforma, pero es lo que hace que Sprint 8 sea usable el primer dia
sin configurar OAuth ni tener un solo video publicado. Las cifras se leen del
panel de la plataforma y se introducen con `performance record`.

Tambien cumple una segunda funcion menos obvia: es el adaptador de referencia
para probar el nucleo de `sync` sin mockear la red, y el unico cuya entrada se
puede contrastar a ojo con lo que dice el panel.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from src.performance.base import MetricSnapshot, PlatformAnalyticsProvider, ProviderUnavailable


class ManualProvider(PlatformAnalyticsProvider):
    """Devuelve lo que se le pasa, sin preguntar a nadie.

    `sync` lo elige explicitamente con `--provider manual`, nunca por defecto:
    un adaptador manual que se ejecuta solo no synchroniza nada, se limita a
    confirmar lo que ya se habia escrito. Por eso no se offersce como una
    plataforma mas de la lista por defecto.
    """

    name: ClassVar[str] = "manual"
    metric_source: ClassVar[str] = "manual"
    minimum_age_hours: ClassVar[int] = 0
    # Los numeros los lee una persona de donde esten: sirve para cualquiera.
    handles: ClassVar[frozenset[str]] = frozenset({"*"})

    def __init__(self, snapshots: Mapping[tuple[str, int], Mapping[str, Any]] | None = None) -> None:
        # Clave: (platform_clip_id, age_hours) -> metricas.
        self._snapshots: dict[tuple[str, int], dict[str, Any]] = {
            tuple(key): dict(value) for key, value in (snapshots or {}).items()
        }

    def available(self) -> bool:
        return True

    def add(self, platform_clip_id: str, age_hours: int, metrics: Mapping[str, Any]) -> None:
        self._snapshots[(platform_clip_id, int(age_hours))] = dict(metrics)

    def fetch_metrics(
        self, platform_clip_id: str, *, published_at: str, age_hours: int
    ) -> MetricSnapshot:
        key = (platform_clip_id, int(age_hours))
        metrics = self._snapshots.get(key)
        if metrics is None:
            raise ProviderUnavailable(
                f"no hay metricas manuales para {platform_clip_id} a las {age_hours}h"
            )
        return MetricSnapshot(
            platform=self.name,
            age_hours=int(age_hours),
            captured_at=datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            metrics=dict(metrics),
            raw={"source": self.name, "platform_clip_id": platform_clip_id, "published_at": published_at},
            metric_source=self.metric_source,
            notes=["introducido a mano"],
        )
