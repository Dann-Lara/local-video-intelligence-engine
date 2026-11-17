# Decisión: idioma de overlays y SEO derivado del audio original

**Fecha de la decisión:** 2026-10-29
**Registrada:** 2026-10-29, durante la corrección de localización del Short 7
**Tipo:** contrato editorial y de localización
**Estado:** vigente

## Objeto

Fijar de forma explícita de qué idioma dependen las notificaciones (tarjetas CTA),
los rótulos de énfasis y el paquete SEO de un Short, y separarlo del idioma de los
subtítulos.

El caso que originó la decisión es el Short 7 (`clip_0007`): audio original en
inglés, subtítulos en inglés, pero la tarjeta CTA mostraba el título en español
(`COMENTA`) porque el generador leía textos fijos de
`services/rendering/card_family.json`.

## Regla

1. La **fuente de verdad** del idioma de las notificaciones, del énfasis y del SEO
   es `source_audio_language`: el idioma del **audio original** del clip.
2. Los **subtítulos** son una pista independiente: su idioma se registra aparte y
   **nunca** decide el idioma de las tarjetas, del énfasis ni del SEO.
3. Una **traducción de subtítulos** no puede cambiar los overlays. Si el audio es
   inglés y los subtítulos están traducidos al español, las tarjetas siguen en
   inglés.
4. Si el idioma del audio es **mixto, ambiguo o sin confianza suficiente**, la
   decisión es `needs_review`: no se generan textos localizados ni SEO.
5. Si la transcripción es una **traducción** y no hay idioma de audio declarado,
   la decisión es `needs_review`; no se infiere el idioma del audio desde el texto
   traducido.
6. Si el idioma declarado del audio **contradice** la detección del fragmento, la
   decisión es `needs_review`.
7. Los textos se escriben en el idioma del audio y se traducen de forma natural
   (no literal). Los nombres propios y las marcas no se traducen.
8. La localización **no altera** geometría, posición, tamaño, colores, tipografía,
   animaciones, sincronía de tarjetas y clics, subtítulos ni sus tiempos, marco
   neón, cortes ni duración.
9. Si un elemento ya estaba en el idioma correcto, no se modifica.

## Idiomas registrados por separado

El contexto de idioma (`services/delivery/language.py`) guarda y expone, sin
mezclarlos:

| Campo | Significado |
|---|---|
| `source_audio_language` | Idioma del audio original (fuente de verdad) |
| `source_audio_language_source` | Origen del dato: `job`, `transcription_metadata` o `unknown` |
| `transcript_language` | Idioma detectado en la transcripción del fragmento |
| `subtitle_language` | Idioma detectado en los subtítulos (puede diferir) |
| `notification_language` | Idioma de las tarjetas CTA |
| `emphasis_language` | Idioma de los rótulos de énfasis |
| `overlay_language` | Idioma consolidado de overlays (= notificaciones y énfasis) |
| `seo_language` | Idioma del paquete SEO |
| `transcript_is_translation` | Marca si la transcripción es una traducción |

`notification_language`, `emphasis_language`, `overlay_language` y `seo_language`
comparten el idioma canónico del audio.

## Resolución del idioma canónico

Orden de prioridad:

1. `source_audio_language` declarado explícitamente en el job.
2. Metadato de la transcripción (ASR), solo si no es una traducción.
3. Detección segura sobre la transcripción del fragmento.

La detección valida el dato declarado. Una contradicción no se resuelve por
mayoría: se detiene con `needs_review`.

Decisiones que autorizan el uso automático: `auto_from_source_audio` y
`auto_from_audio_transcript`. La etiqueta histórica `auto_from_transcript` deja de
ser una decisión positiva.

## Catálogo de textos de tarjeta

- Los textos revisados viven en `services/rendering/card_text.py`
  (`CATALOG_VERSION = "card_text.v1"`), con entradas para `en` y `es`.
- `card_family.json` ya **no** es fuente de texto: solo aporta geometría, colores,
  tipografía y animación. Se conserva intacto como referencia histórica.
- Si el idioma canónico no tiene catálogo, se lanza `MissingCardTranslation` y el
  pipeline falla de forma controlada. **No** hay fallback silencioso a español ni
  a inglés.
- Un `title_text`/`subtitle_text` aportado por el job solo se acepta si su idioma
  detectable coincide con el canónico; si no, error `needs_review`.

## Énfasis

Los rótulos de énfasis deben ser frases **literales** de la transcripción. Si la
transcripción es una traducción y su idioma difiere del audio, no se puede
producir énfasis literal en el idioma del audio: la decisión es `needs_review`.

## SEO

El `seo.md` se genera en el mismo idioma canónico del audio. La validación
comprueba:

- `seo_language_matches_transcript` (idioma canónico),
- `seo_language_matches_overlay_language`,
- `seo_language_decision_recorded`,
- `source_audio_matches_transcript`.

Un SEO en un idioma distinto al canónico invalida el paquete.

## Aplicación al Short 7

- Audio original: inglés (declarado en el job y confirmado por la detección).
- Transcripción y subtítulos: inglés.
- Tarjeta `comment`: título `COMMENT`, subtítulo `WOULD YOU FALL FOR IT?`
  (aporte editorial en inglés, sin cambios).
- Énfasis: ya estaban en inglés (literales de la transcripción); no se tocaron.
- SEO: inglés, regenerado porque embebe nombre y SHA-256 del MP4.
- Entregable nuevo y fechado: `output/clip_0007_shorts_mvp_v2_20261029.mp4`.
  El entregable anterior (`clip_0007_shorts_mvp_20261029.*`) se conserva intacto.

## Alcance

- **No modifica** `docs/shorts_mvp_approved_standard.md`; su §3.6 describe el caso
  original en español y esta decisión lo generaliza por idioma del audio.
- **No modifica** `AGENTS.md` ni `BLUEPRINT.md`.
- **No autoriza** publicar, subir ni programar.
- **No sustituye** la aprobación humana: el nuevo manifiesto queda en
  `pending_user_review` con `human_review.approved = false`.

## Pruebas

- `tests/test_delivery_overlay_language.py`: matriz audio/subtítulos, traducción,
  catálogo ausente, override en otro idioma, SEO/overlays y geometría/tiempos.
- `tests/test_delivery_language.py`: actualizado a las nuevas etiquetas de decisión.

## Trazabilidad

- Ejecución: `work/run_20261029_overlay_language/`.
- Informe: `work/run_20261029_overlay_language/INFORME.md`.
- Hashes de referencia previos: `work/run_20261029_overlay_language/reference_hashes.json`.
