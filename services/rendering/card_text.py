"""Catalogo de textos de tarjetas CTA por idioma del audio fuente.

Regla del proyecto: el texto de las notificaciones (tarjetas CTA) se escribe en
el idioma del **audio original** del clip, nunca en el idioma de los subtitulos
traducidos. Los subtitulos son una pista independiente y no pueden cambiar los
overlays.

- El catalogo solo contiene textos revisados para `en` y `es`.
- Si el idioma canonico no tiene catalogo se lanza `MissingCardTranslation`;
  no existe fallback silencioso a otro idioma.
- Los nombres propios y marcas se conservan tal como aparecen en la fuente.

Los textos no cambian la geometria de la tarjeta: `cards.py` sigue usando la
misma configuracion de `card_family.json` (tamaño, posicion, tipografia y
animacion).
"""

from __future__ import annotations

from typing import Any, Dict

CATALOG_VERSION = "card_text.v1"

# Acciones soportadas por `card_family.json`.
CARD_ACTIONS = ("subscribe", "like", "comment", "share")

CARD_TEXTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "en": {
        "subscribe": {
            "title": "SUBSCRIBE",
            "subtitle": "New showdowns every week",
        },
        "like": {
            "title": "LIKE THIS",
            "subtitle": "If you enjoyed this showdown",
        },
        "comment": {
            "title": "COMMENT",
            "subtitle": "WHO WOULD WIN THIS BATTLE?",
        },
        "share": {
            "title": "SHARE",
            "subtitle": "So more people discover this showdown",
        },
    },
    "es": {
        "subscribe": {
            "title": "SUSCRÍBETE",
            "subtitle": "Nuevos enfrentamientos cada semana",
        },
        "like": {
            "title": "DA LIKE",
            "subtitle": "Si te gustó este enfrentamiento",
        },
        "comment": {
            "title": "COMENTA",
            "subtitle": "¿Quién ganaría esta batalla?",
        },
        "share": {
            "title": "COMPARTE",
            "subtitle": "Que más personas descubran esta batalla",
        },
    },
}

SUPPORTED_CARD_LANGUAGES = tuple(CARD_TEXTS)


class MissingCardTranslation(RuntimeError):
    """No existe una traduccion revisada de la tarjeta para ese idioma."""

    def __init__(self, message: str, *, details: Dict[str, Any] = None):
        super().__init__(message)
        self.details = details or {}


def has_card_translation(language: str) -> bool:
    return str(language or "") in CARD_TEXTS


def resolve_card_text(action: str, language: str) -> Dict[str, Any]:
    """Devuelve el texto revisado de `action` en `language`.

    Lanza `MissingCardTranslation` si el idioma no tiene catalogo o si la
    accion no existe. No se usa el texto de `card_family.json` como fallback:
    ese archivo historico conserva los textos en espanol y solo aporta la
    geometria y la animacion.
    """
    language = str(language or "")
    if action not in CARD_ACTIONS:
        raise MissingCardTranslation(
            f"Accion de tarjeta desconocida: {action!r}.",
            details={"action": action, "catalog_version": CATALOG_VERSION},
        )
    if language not in CARD_TEXTS:
        raise MissingCardTranslation(
            f"No hay traduccion de tarjeta revisada para el idioma {language!r}; "
            "se requiere revision humana.",
            details={
                "action": action,
                "language": language,
                "supported_languages": list(SUPPORTED_CARD_LANGUAGES),
                "catalog_version": CATALOG_VERSION,
            },
        )
    text = CARD_TEXTS[language][action]
    return {
        "action": action,
        "language": language,
        "title": text["title"],
        "subtitle": text["subtitle"],
        "source": "card_text_catalog",
        "catalog_version": CATALOG_VERSION,
    }


__all__ = [
    "CATALOG_VERSION",
    "CARD_ACTIONS",
    "CARD_TEXTS",
    "SUPPORTED_CARD_LANGUAGES",
    "MissingCardTranslation",
    "has_card_translation",
    "resolve_card_text",
]
