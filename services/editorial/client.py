"""Cliente HTTP minimo para el servidor local compatible con la API de LM Studio.

IMPORTANTE: este modulo NUNCA realiza una solicitud al importarse ni al
construirse. Solo `complete()` abre la conexion, y en esta fase esa ruta no
se ejecuta (las pruebas inyectan un cliente falso). No se cargan modelos, no
se descubre el modelo real y no se envia ningun prompt al servidor.

Se usa unicamente la biblioteca estandar (`urllib`): no se anaden
dependencias.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from .errors import (
    EditorialInvalidResponseError,
    EditorialModelUnavailableError,
)


class ModelClient:
    """Protocolo implicito: cualquier objeto con `complete()` sirve."""

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:  # pragma: no cover
        raise NotImplementedError


class LMStudioClient:
    """Cliente para POST {base_url}/chat/completions."""

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout = float(timeout)

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint(),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise EditorialModelUnavailableError(
                f"LM Studio respondio HTTP {exc.code}: {body[:300]}",
                details={"status": exc.code},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EditorialModelUnavailableError(
                f"No se pudo contactar LM Studio en {self._endpoint()}: {exc}"
            ) from exc

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise EditorialInvalidResponseError(
                f"Respuesta de LM Studio no es JSON valido: {exc}"
            ) from exc
        return _extract_content(parsed)


def _extract_content(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        raise EditorialInvalidResponseError("Respuesta de LM Studio no es un objeto JSON")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise EditorialInvalidResponseError("Respuesta de LM Studio sin 'choices'")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise EditorialInvalidResponseError("Respuesta de LM Studio sin 'message'")
    content = message.get("content")
    if not isinstance(content, str) or content.strip() == "":
        raise EditorialInvalidResponseError("Respuesta de LM Studio con contenido vacio")
    return content
