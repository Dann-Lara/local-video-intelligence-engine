"""Carga de prompts desde disco y versionado por contenido (Blueprint, seccion 54).

Los prompts NO viven en el codigo. Cada etapa tiene sus ficheros en `prompts/`:
una parte de sistema (la instruccion estable) y una de usuario (la plantilla
con los huecos de datos). Editar el prompt es editar un `.txt` legible, sin
tocar Python y sin riesgo de romper el modulo.

La version se deriva del CONTENIDO (sha256 del system + user, 12 hex), no de un
`PROMPT_VERSION = "1.0"` escrito a mano. Esa constante manual era el problema:
nadie la actualizaba al retocar el prompt, asi que dos ejecuciones con prompt
distinto.shareaban version y la telemetria no podia distinguir un cambio de
instruccion de una fluctuacion del modelo. Con el hash no hace falta acordarse
de subir un numero: si el texto cambio, la version cambio.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

PROMPTS_DIRNAME = "prompts"
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class PromptNotFoundError(RuntimeError):
    """El prompt no esta en disco. Es un fallo de instalacion, no de datos."""


def prompts_dir(base: Path | str | None = None) -> Path:
    """Directorio de prompts: el del repo, salvo que se indique otro."""
    if base is not None:
        return Path(base)
    # src/core/prompts.py -> raiz del proyecto
    return Path(__file__).resolve().parents[2] / PROMPTS_DIRNAME


def load(part: str, *, stage: str, base: Path | str | None = None) -> str:
    """Lee `prompts/<stage>.<part>.txt` (part = "system" | "user")."""
    path = prompts_dir(base) / f"{stage}.{part}.txt"
    if not path.is_file():
        raise PromptNotFoundError(
            f"falta el prompt {path.name} (seccion 54: los prompts van en ficheros)"
        )
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise PromptNotFoundError(f"el prompt {path.name} esta vacio")
    return text


def parts(stage: str, *, base: Path | str | None = None) -> tuple[str, str]:
    """Devuelve (system, user) del stage."""
    return load("system", stage=stage, base=base), load("user", stage=stage, base=base)


def version(stage: str, *, base: Path | str | None = None) -> str:
    """Version del prompt, derivada del texto: cambia si cambia una letra.

    Sin cache a proposito. Una cache por stage servia la version vieja para
    siempre en el mismo proceso, con lo que editar el prompt durante una
    sesion no cambiaba la version y la invalidacion de cache dejo de funcionar:
    el fallo mas caro posible en un sistema que se apoya en ella. Leer dos
    ficheros de 2 KB unas pocas veces por ejecucion no es un coste.
    """
    system, user = parts(stage, base=base)
    digest = hashlib.sha256((system + "\x00" + user).encode("utf-8")).hexdigest()
    return digest[:12]


def render(stage: str, part: str, /, **values: Any) -> str:
    """Rellena los huecos `{nombre}` de la plantilla con `values`."""
    return load(part, stage=stage).format(**values)


def placeholders(stage: str, part: str = "user") -> set[str]:
    """Nombres de hueco que la plantilla espera. Lo usan los tests."""
    return set(_PLACEHOLDER.findall(load(part, stage=stage)))
