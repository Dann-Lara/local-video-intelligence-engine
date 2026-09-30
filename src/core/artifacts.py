"""Escritura atomica y validada de artefactos JSON (blueprint secciones 14, 15, 60).

Un artefacto a medio escribir es peor que uno inexistente. Si el proceso muere
durante el `write`, un `open(path, "w")` deja un JSON truncado; el paso siguiente
lo lee, falla con un error de sintaxis incomprensible y el diagnostico real
("se quedo sin memoria en mitad de la transcripcion") se pierde para siempre.

El patron es siempre el mismo:

1. Se escribe en un temporal en el MISMO directorio, para que el renombrado
   sea atomico (mismo volumen, mismo sistema de ficheros).
2. Se hace `flush` + `fsync`, para que los datos esten en disco antes de que el
   nombre exista.
3. Se valida contra el schema del artefacto.
4. Se hace `os.replace`, que en Windows y en POSIX sustituye de forma atomica.

El orden de 3 y 4 importa: validar antes de renombrar garantiza que nunca exista
un `transcript.json` invalido en el workspace, ni siquiera durante un instante.

`transcript.json` es inmutable por diseno (seccion 14). `overwrite=True` existe
para la recuperacion explicita desde el modo `qc` de la seccion 66, no para que
una reejecucion sobrescriba trabajo bueno por accidente.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core import invariants, schemas

DEFAULT_INDENT = 2


class ArtifactError(RuntimeError):
    """No se pudo escribir un artefacto."""


class ArtifactExistsError(ArtifactError):
    """El artefacto ya existe y es inmutable."""


class ArtifactInvalidError(ArtifactError):
    """El contenido no cumple el schema. No se escribe nada."""


@dataclass(frozen=True)
class ArtifactInfo:
    path: Path
    schema_name: str
    size_bytes: int
    sha256: str


def dumps(document: Any, *, indent: int = DEFAULT_INDENT) -> str:
    """Serializa igual en todas las etapas.

    `ensure_ascii=False` es obligatorio: los transcripts contienen acentos y
    alfabetos no latinos, y escaparlos a `\\uXXXX` duplica el tamano y hace el
    fichero ilegible para cualquier persona que lo abra.
    """
    return json.dumps(document, ensure_ascii=False, indent=indent) + "\n"


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate(schema_name: str, document: Any) -> None:
    """Valida schema + invariantes de negocio. Lanza si algo no cuadra."""
    errors = schemas.iter_errors(schema_name, document)
    if errors:
        raise ArtifactInvalidError(
            f"el documento no cumple '{schema_name}':\n  - " + "\n  - ".join(errors[:10])
        )

    # Los invariantes de negocio (rangos de tiempo, coherencia de duraciones,
    # section 38) no se pueden expresar en JSON Schema.
    business = invariants.invariant_errors(schema_name, document)
    if business:
        raise ArtifactInvalidError(
            f"el documento incumple los invariantes de '{schema_name}':\n  - "
            + "\n  - ".join(business[:10])
        )


def write_json(
    path: Path | str,
    document: Any,
    *,
    schema_name: str,
    overwrite: bool = True,
    validate_document: bool = True,
) -> ArtifactInfo:
    """Escribe JSON de forma atomica y validada.

    Devuelve un `ArtifactInfo` con el sha256 DEL CONTENIDO ESCRITO, que es lo
    que se registra en la tabla `artifacts` de la base de datos y lo que permite
    a la etapa siguiente comprobar que no esta leyendo otra cosa.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not overwrite:
        raise ArtifactExistsError(
            f"ya existe {target.name} y no se permite sobrescribir. "
            "Los artefactos de origen son inmutables (seccion 14); "
            "usa --overwrite para regenerarlos de forma explicita."
        )

    if validate_document:
        validate(schema_name, document)

    text = dumps(document)

    # En el mismo directorio: `os.replace` solo es atomico dentro del mismo
    # volumen. Un temporal en %TEMP% cruzaria de unidad y lo ruinaria.
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        # Un temporal huerfano no rompe nada, pero ensucia el workspace y puede
        # confundir a un `ls` durante la depuracion.
        temporary.unlink(missing_ok=True)
        raise

    return ArtifactInfo(
        path=target,
        schema_name=schema_name,
        size_bytes=len(text.encode("utf-8")),
        sha256=_sha256(text),
    )


def read_json(path: Path | str, *, schema_name: str | None = None) -> Any:
    """Lee un artefacto. Opcionalmente lo valida al leerlo.

    Los artefactos inmutables se validan al leer: si alguien los edito a mano,
    el fallo tiene que saltar en la etapa que los consume, no tres etapas mas
    abajo con un `KeyError` en un campo que ya no existe.
    """
    target = Path(path)
    if not target.is_file():
        raise ArtifactError(f"no existe el artefacto: {target}")

    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{target.name} no es un JSON legible: {exc}") from exc

    if schema_name is not None:
        validate(schema_name, document)
    return document


def read_or_none(path: Path | str, *, schema_name: str | None = None) -> Any | None:
    """Como `read_json`, pero `None` si el fichero no existe.

    Para las etapas con idempotencia (seccion 51): si el artefacto ya esta,
    se reutiliza; si no, se produce.
    """
    target = Path(path)
    if not target.is_file():
        return None
    return read_json(target, schema_name=schema_name)


def sha256_of_file(path: Path | str, *, chunk_bytes: int = 1024 * 1024) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(chunk_bytes):
            digest.update(block)
    return digest.hexdigest()
