"""Metricas derivadas (seccion 40).

La seccion 40 pide guardar las CRUDAS y las DERIVADAS, y avisa de que las
formulas pueden cambiar. De ahi dos decisiones:

1. Se guarda la version de la formula junto a cada fila
   (`derived_metrics_version`). Cuando la formula cambie, las filas viejas
   siguen siendo legibles: se sabe con que criterio se calcularon.
2. Ninguna derivada borra ni recalcula una cruda, y el nucleo guarda tambien
   el JSON completo de las derivadas, no solo las cuatro que hoy tienen
   columna. Anadir una quinta formula no obliga a migrar la base de datos.

Y la regla que atraviesa todo el modulo: una tasa que no se puede calcular es
`None`, NUNCA cero. `0/0` no es "no hubo likes", es "no se puede saber". La
seccion 38 lo dice al pie de la letra: usar `0` cuando el valor es desconocido
convierte un dato ausente en un dato falso, y un falso a cero es indistinguible
de un resultado real malo. Es el tipo de error que no se detecta hasta que
alguien saca una conclusion equivocada de la tabla.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.performance.base import to_number

#: Version de las formulas. Cambiar esto obliga a subirla: es lo que permite
#: distinguir dos filas calculadas con criterios distintos.
DERIVED_METRICS_VERSION = "1.0"

#: Las derivadas que ademas tienen columna propia en `performance_snapshots`,
#: para poder consultarlas con SQL sin ir al JSON.
COLUMN_BACKED_DERIVED = (
    "like_rate",
    "comment_rate",
    "share_rate",
    "subscriber_conversion_rate",
)


def _rate(numerator: Any, denominator: Any) -> float | None:
    """Division segura que devuelve None en vez de 0 o una excepcion.

    Tres casos que parecen el mismo y no lo son:
      - falta el numerador  -> None: la plataforma no mide esa metrica.
      - falta el denominador -> None: no se puede normalizar sin base.
      - denominador 0       -> None: indefinido. Escribir 0.0 aqui produciria
        un clip con 0% de likes, conclusion que la data no sostiene.
    """
    num = to_number(numerator)
    den = to_number(denominator)
    if num is None or den is None or den == 0:
        return None
    return round(num / den, 6)


def compute_derived(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Calcula las derivadas de la seccion 40 a partir de las metricas crudas.

    Trabaja con las claves del nucleo (`views`, `likes`, ...), no con las del
    adaptador: por eso el nucleo puede comparar clips de plataformas distintas,
    que es justo lo que dira el Sprint 10 cuando haya datos suficientes.

    Acepta strings porque las APIs devuelven `"12450"` y `"0.065"`; los tipos
    se normalizan con `to_number` una vez en lugar de en cada punto de uso.
    """
    views = to_number(metrics.get("views"))
    return {
        "like_rate": _rate(metrics.get("likes"), views),
        "comment_rate": _rate(metrics.get("comments"), views),
        "share_rate": _rate(metrics.get("shares"), views),
        "subscriber_conversion_rate": _rate(metrics.get("subscribers_gained"), views),
        # Derivada de retencion, no de interaccion: un 78% de visto medio con
        # 2000 vistas dice mas del destino que 200 likes. La seccion 39 la
        # lista en "Retention" y aqui se normaliza a proporcion si la
        # plataforma la dio como porcentaje.
        "retention_rate": _fraction(to_number(metrics.get("average_percentage_viewed"))),
    }


def _fraction(percentage: float | None) -> float | None:
    """Convierte un porcentaje a proporcion, o None si no lo hay."""
    if percentage is None:
        return None
    return round(percentage / 100.0, 6)
