"""Generacion y validacion del `seo.md` listo para YouTube.

Principios:

- El idioma del paquete SEO se deriva del idioma canonico del **audio original**
  del fragmento (`language_context`), nunca de una preferencia fija, de los
  subtitulos traducidos ni de una traduccion automatica.
- Los overlays (tarjetas y énfasis) usan ese mismo idioma canonico.
- Todos los campos copiables (titulo, alternativas, descripcion, hashtags,
  etiquetas, comentario fijado y miniatura) y los encabezados del documento
  usan ese mismo idioma.
- El SEO se deriva de la transcripcion real y del entregable medido.
- No se inventan hechos, cifras ni promesas de resultado.
- Se separan requisitos oficiales, recomendaciones editoriales e hipotesis.
- Cada cita literal del SEO se verifica contra los segmentos fuente.
- Cada cifra del titulo/descripcion/comentario debe existir en la fuente.

La validacion es independiente de la generacion: recibe el markdown final y lo
contrasta con la transcripcion, el MP4 y el contexto de idioma. Asi se detectan
ediciones manuales que introduzcan datos no respaldados o cambien de idioma.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.delivery.language import (
    AUTO_LANGUAGE_DECISIONS,
    LANGUAGE_NAMES,
    MIN_STOPWORD_HITS,
    SUPPORTED_LANGUAGES,
    SeoLanguageError,
    detect_language,
)

MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
MAX_HASHTAGS_DISPLAYED = 3
MAX_HASHTAGS_TOTAL = 60

SECTION_KEYS = (
    "deliverable",
    "title",
    "description",
    "hashtags",
    "tags",
    "pinned",
    "thumbnail",
    "facts",
    "sources",
    "state",
)

# Etiquetas por idioma. Los encabezados en espanol son identicos a la version
# anterior para no romper SEO historicos ni las pruebas existentes.
LABELS: Dict[str, Dict[str, Any]] = {
    "es": {
        "doc_title": "SEO del Short {clip_id}",
        "intro": (
            "Generado a partir de la transcripcion real y del MP4 medido. "
            "No se publica automaticamente: la publicacion y la aprobacion son humanas."
        ),
        "sections": {
            "deliverable": "1. Archivo entregable",
            "title": "2. Titulo",
            "description": "3. Descripcion",
            "hashtags": "4. Hashtags",
            "tags": "5. Etiquetas",
            "pinned": "6. Comentario fijado",
            "thumbnail": "7. Miniatura",
            "facts": "8. Verificacion de datos",
            "sources": "9. Fuentes y requisitos",
            "state": "10. Estado",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Duracion",
        "language": "Idioma",
        "audience": "Audiencia",
        "title": "Titulo",
        "alternatives": "Alternativas",
        "tags": "Etiquetas",
        "thumbnail_text": "Texto",
        "status": "Estado",
        "generated": "Generado",
        "transcript_language": "Idioma de la transcripcion",
        "transcript_confidence": "Confianza de la transcripcion",
        "subtitle_language": "Idioma de los subtitulos",
        "source_audio_language": "Idioma del audio original",
        "notification_language": "Idioma de las notificaciones",
        "emphasis_language": "Idioma del enfasis",
        "overlay_language": "Idioma de los overlays",
        "seo_language": "Idioma del SEO",
        "seo_decision": "Decision de idioma del SEO",
        "fact_claim": "Cita literal de la fuente",
        "fact_segment": "Segmento",
        "source_class": "Clasificacion",
        "source_url": "Fuente",
        "source_date": "Fecha",
        "source_claim": "Afirmacion",
    },
    "en": {
        "doc_title": "SEO for Short {clip_id}",
        "intro": (
            "Generated from the real transcript and the measured MP4. "
            "It is not published automatically: publishing and approval are human."
        ),
        "sections": {
            "deliverable": "1. Deliverable file",
            "title": "2. Title",
            "description": "3. Description",
            "hashtags": "4. Hashtags",
            "tags": "5. Tags",
            "pinned": "6. Pinned comment",
            "thumbnail": "7. Thumbnail",
            "facts": "8. Data verification",
            "sources": "9. Sources and requirements",
            "state": "10. Status",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Duration",
        "language": "Language",
        "audience": "Audience",
        "title": "Title",
        "alternatives": "Alternatives",
        "tags": "Tags",
        "thumbnail_text": "Text",
        "status": "Status",
        "generated": "Generated",
        "transcript_language": "Transcript language",
        "transcript_confidence": "Transcript confidence",
        "subtitle_language": "Subtitle language",
        "source_audio_language": "Source audio language",
        "notification_language": "Notification language",
        "emphasis_language": "Emphasis language",
        "overlay_language": "Overlay language",
        "seo_language": "SEO language",
        "seo_decision": "SEO language decision",
        "fact_claim": "Literal quote from the source",
        "fact_segment": "Segment",
        "source_class": "Classification",
        "source_url": "Source",
        "source_date": "Date",
        "source_claim": "Claim",
    },
    "pt": {
        "doc_title": "SEO do Short {clip_id}",
        "intro": (
            "Gerado a partir da transcricao real e do MP4 medido. "
            "Nao e publicado automaticamente: a publicacao e a aprovacao sao humanas."
        ),
        "sections": {
            "deliverable": "1. Arquivo entregavel",
            "title": "2. Titulo",
            "description": "3. Descricao",
            "hashtags": "4. Hashtags",
            "tags": "5. Etiquetas",
            "pinned": "6. Comentario fixado",
            "thumbnail": "7. Miniatura",
            "facts": "8. Verificacao de dados",
            "sources": "9. Fontes e requisitos",
            "state": "10. Estado",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Duracao",
        "language": "Idioma",
        "audience": "Publico",
        "title": "Titulo",
        "alternatives": "Alternativas",
        "tags": "Etiquetas",
        "thumbnail_text": "Texto",
        "status": "Estado",
        "generated": "Gerado",
        "transcript_language": "Idioma da transcricao",
        "transcript_confidence": "Confianca da transcricao",
        "subtitle_language": "Idioma das legendas",
        "source_audio_language": "Idioma do audio original",
        "notification_language": "Idioma das notificacoes",
        "emphasis_language": "Idioma da enfase",
        "overlay_language": "Idioma dos overlays",
        "seo_language": "Idioma do SEO",
        "seo_decision": "Decisao de idioma do SEO",
        "fact_claim": "Citacao literal da fonte",
        "fact_segment": "Segmento",
        "source_class": "Classificacao",
        "source_url": "Fonte",
        "source_date": "Data",
        "source_claim": "Afirmacao",
    },
    "fr": {
        "doc_title": "SEO du Short {clip_id}",
        "intro": (
            "Genere a partir de la transcription reelle et du MP4 mesure. "
            "Il n'est pas publie automatiquement : la publication et "
            "l'approbation sont humaines."
        ),
        "sections": {
            "deliverable": "1. Fichier livre",
            "title": "2. Titre",
            "description": "3. Description",
            "hashtags": "4. Hashtags",
            "tags": "5. Etiquettes",
            "pinned": "6. Commentaire epingle",
            "thumbnail": "7. Miniature",
            "facts": "8. Verification des donnees",
            "sources": "9. Sources et exigences",
            "state": "10. Etat",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Duree",
        "language": "Langue",
        "audience": "Public",
        "title": "Titre",
        "alternatives": "Alternatives",
        "tags": "Etiquettes",
        "thumbnail_text": "Texte",
        "status": "Etat",
        "generated": "Genere",
        "transcript_language": "Langue de la transcription",
        "transcript_confidence": "Confiance de la transcription",
        "subtitle_language": "Langue des sous-titres",
        "source_audio_language": "Langue de l'audio d'origine",
        "notification_language": "Langue des notifications",
        "emphasis_language": "Langue de la mise en avant",
        "overlay_language": "Langue des overlays",
        "seo_language": "Langue du SEO",
        "seo_decision": "Decision de langue du SEO",
        "fact_claim": "Citation litterale de la source",
        "fact_segment": "Segment",
        "source_class": "Classification",
        "source_url": "Source",
        "source_date": "Date",
        "source_claim": "Affirmation",
    },
    "de": {
        "doc_title": "SEO fur den Short {clip_id}",
        "intro": (
            "Erstellt aus dem echten Transkript und dem gemessenen MP4. "
            "Wird nicht automatisch veroffentlicht: Veroffentlichung und "
            "Freigabe erfolgen durch Menschen."
        ),
        "sections": {
            "deliverable": "1. Lieferdatei",
            "title": "2. Titel",
            "description": "3. Beschreibung",
            "hashtags": "4. Hashtags",
            "tags": "5. Tags",
            "pinned": "6. Angepinnter Kommentar",
            "thumbnail": "7. Vorschaubild",
            "facts": "8. Datenprufung",
            "sources": "9. Quellen und Anforderungen",
            "state": "10. Status",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Dauer",
        "language": "Sprache",
        "audience": "Zielgruppe",
        "title": "Titel",
        "alternatives": "Alternativen",
        "tags": "Tags",
        "thumbnail_text": "Text",
        "status": "Status",
        "generated": "Erstellt",
        "transcript_language": "Transkriptsprache",
        "transcript_confidence": "Konfidenz des Transkripts",
        "subtitle_language": "Sprache der Untertitel",
        "source_audio_language": "Sprache des Originalaudios",
        "notification_language": "Sprache der Benachrichtigungen",
        "emphasis_language": "Sprache der Hervorhebung",
        "overlay_language": "Sprache der Overlays",
        "seo_language": "Sprache des SEO",
        "seo_decision": "Entscheidung zur SEO-Sprache",
        "fact_claim": "Wortliches Zitat aus der Quelle",
        "fact_segment": "Segment",
        "source_class": "Klassifikation",
        "source_url": "Quelle",
        "source_date": "Datum",
        "source_claim": "Aussage",
    },
    "it": {
        "doc_title": "SEO dello Short {clip_id}",
        "intro": (
            "Generato dalla trascrizione reale e dall'MP4 misurato. "
            "Non viene pubblicato automaticamente: pubblicazione e "
            "approvazione sono umane."
        ),
        "sections": {
            "deliverable": "1. File consegnato",
            "title": "2. Titolo",
            "description": "3. Descrizione",
            "hashtags": "4. Hashtag",
            "tags": "5. Tag",
            "pinned": "6. Commento fissato",
            "thumbnail": "7. Miniatura",
            "facts": "8. Verifica dei dati",
            "sources": "9. Fonti e requisiti",
            "state": "10. Stato",
        },
        "mp4": "MP4",
        "sha": "SHA-256",
        "duration": "Durata",
        "language": "Lingua",
        "audience": "Pubblico",
        "title": "Titolo",
        "alternatives": "Alternative",
        "tags": "Tag",
        "thumbnail_text": "Testo",
        "status": "Stato",
        "generated": "Generato",
        "transcript_language": "Lingua della trascrizione",
        "transcript_confidence": "Confidenza della trascrizione",
        "subtitle_language": "Lingua dei sottotitoli",
        "source_audio_language": "Lingua dell'audio originale",
        "notification_language": "Lingua delle notifiche",
        "emphasis_language": "Lingua dell'enfasi",
        "overlay_language": "Lingua degli overlay",
        "seo_language": "Lingua del SEO",
        "seo_decision": "Decisione sulla lingua del SEO",
        "fact_claim": "Citazione letterale dalla fonte",
        "fact_segment": "Segmento",
        "source_class": "Classificazione",
        "source_url": "Fonte",
        "source_date": "Data",
        "source_claim": "Affermazione",
    },
}

# Compatibilidad: la version en espanol es la de referencia historica.
REQUIRED_SECTIONS = tuple(LABELS["es"]["sections"][key] for key in SECTION_KEYS)

FORBIDDEN_PROMISES = (
    # Espanol
    "garantizado",
    "garantiza",
    "garantizamos",
    "viral",
    "viralizar",
    "exito asegurado",
    "éxito asegurado",
    "seguro que",
    "vas a ganar",
    "ganaras",
    "ganarás",
    "milagro",
    # Ingles
    "guaranteed",
    "guarantees",
    "go viral",
    "guaranteed success",
    "you will earn",
    "you'll earn",
    "make money",
    "easy money",
    "no risk",
    "miracle",
    # Portugues
    "garantido",
    "garantia",
    "sucesso garantido",
    "vai ganhar",
    # Frances
    "garanti",
    "garantie",
    "succes assure",
    "tu vas gagner",
    # Aleman
    "garantiert",
    "garantie",
    "erfolg garantiert",
    # Italiano
    "garantito",
    "garanzia",
    "successo garantito",
)

_WORD = re.compile(r"[0-9a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+")
_NUMBER = re.compile(r"\d[\d.,]*")
_QUOTED = re.compile(r"[\"“”']([^\"“”']{3,})[\"“”']")
_LANG_COMMENT = re.compile(r"<!--\s*seo_language:\s*([a-z]{2})\s*-->")
_DECISION_COMMENT = re.compile(r"<!--\s*seo_language_decision:\s*([a-z_]+)\s*-->")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def normalizar_texto(text: str) -> str:
    """Normalizacion usada para comparar citas y cifras (sin acentos)."""
    return _norm(text)


def _section(markdown: str, heading: str) -> str:
    """Devuelve el cuerpo de una seccion `## <heading>`."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown)
    return match.group(1).strip() if match else ""


def _field(text: str, label: str) -> str:
    match = re.search(
        r"^\*\*" + re.escape(label) + r":\*\*\s*(.+)$", text, re.MULTILINE
    )
    return match.group(1).strip() if match else ""


def _bullets(text: str) -> List[str]:
    return [
        line[2:].strip()
        for line in text.splitlines()
        if line.startswith("- ")
    ]


def _table_rows(text: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def transcript_index(
    segments: Iterable[Dict[str, Any]],
) -> Dict[str, str]:
    """Mapa segment_id -> texto, para verificar citas literales."""
    return {s["segment_id"]: s.get("text", "") for s in segments}


def _transcript_all_text(segments: Iterable[Dict[str, Any]]) -> str:
    return _norm(" ".join(s.get("text", "") for s in segments))


def declared_language(markdown: str) -> Optional[str]:
    """Idioma declarado en la cabecera del documento, si existe."""
    match = _LANG_COMMENT.search(markdown)
    return match.group(1) if match else None


def _labels_for(
    markdown: str, language_context: Optional[Dict[str, Any]]
) -> tuple:
    """Devuelve (idioma, etiquetas) para leer/validar un SEO concreto."""
    declared = declared_language(markdown)
    context_language = None
    if language_context:
        context_language = language_context.get("seo_language")
    language = context_language or declared or "es"
    if language not in LABELS:
        raise SeoLanguageError(
            f"Idioma del SEO no soportado: {language!r}.",
            details={"language": language},
        )
    if context_language and declared and declared != context_language:
        raise SeoLanguageError(
            "El SEO declara un idioma distinto al canonico del audio: "
            f"declarado={declared!r}, audio={context_language!r}.",
            details={"declared": declared, "context": language_context},
        )
    return language, LABELS[language]


def _seo_language(job: Dict[str, Any], language_context: Optional[Dict[str, Any]]) -> str:
    seo = job["seo"]
    if language_context:
        decision = language_context.get("seo_language_decision")
        if decision not in AUTO_LANGUAGE_DECISIONS:
            raise SeoLanguageError(
                "Idioma del SEO no determinado con confianza suficiente; "
                "se requiere revision humana.",
                details={"context": language_context},
            )
        language = language_context.get("seo_language")
        if language not in LABELS:
            raise SeoLanguageError(
                f"Idioma del SEO no soportado: {language!r}.",
                details={"context": language_context},
            )
        declared = seo.get("language")
        if declared and declared != language:
            raise SeoLanguageError(
                "El SEO del job esta escrito en un idioma distinto al del "
                f"audio original: job={declared!r}, audio={language!r}.",
                details={"job_language": declared, "context": language_context},
            )
        return language

    # Compatibilidad con jobs historicos sin contexto: se respeta el idioma
    # declarado en el job (por defecto espanol).
    language = seo.get("language", "es")
    if language not in LABELS:
        raise SeoLanguageError(
            f"Idioma del SEO no soportado: {language!r}.",
            details={"language": language},
        )
    return language


def _context_rows(context: Dict[str, Any], labels: Dict[str, Any]) -> List[str]:
    transcript = context.get("transcript_language")
    source_audio = context.get("source_audio_language")
    overlay = context.get("overlay_language")
    notification = context.get("notification_language")
    emphasis = context.get("emphasis_language")
    seo_language = context.get("seo_language")

    def named(value: Optional[str]) -> str:
        return (
            f"{LANGUAGE_NAMES.get(value or '', value or 'unknown')} "
            f"({value or 'unknown'})"
        )

    return [
        f"**{labels['source_audio_language']}:** {named(source_audio)}",
        f"**{labels['transcript_language']}:** {named(transcript)}",
        f"**{labels['transcript_confidence']}:** "
        f"{context.get('transcript_language_confidence')}",
        f"**{labels['subtitle_language']}:** "
        f"{named(context.get('subtitle_language'))}",
        f"**{labels['notification_language']}:** {named(notification)}",
        f"**{labels['emphasis_language']}:** {named(emphasis)}",
        f"**{labels['overlay_language']}:** {named(overlay)}",
        f"**{labels['seo_language']}:** {named(seo_language)}",
        f"**{labels['seo_decision']}:** {context.get('seo_language_decision')}",
    ]


def build_seo(
    job: Dict[str, Any],
    deliverable: Dict[str, Any],
    *,
    language_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Construye el markdown SEO a partir del job y del entregable medido.

    Si se recibe `language_context`, el idioma del paquete (textos y
    encabezados) es obligatoriamente `language_context["seo_language"]`.
    """
    seo = job["seo"]
    candidate = job["candidate"]
    clip_id = candidate["clip_id"]
    title = seo["title"]
    alternatives = seo.get("alt_titles", [])
    description = seo["description"].strip()
    hashtags = seo.get("hashtags", [])
    tags = seo.get("tags", [])
    pinned = seo["pinned_comment"].strip()
    thumbnail = seo["thumbnail_text"]
    audience = seo.get("audience", "not_made_for_kids")
    facts = seo.get("facts", [])
    sources = seo.get("sources", [])
    notes = seo.get("notes", "")

    language = _seo_language(job, language_context)
    labels = LABELS[language]
    sections = labels["sections"]

    lines: List[str] = []
    lines.append(f"# {labels['doc_title'].format(clip_id=clip_id)}")
    lines.append("")
    lines.append(f"<!-- seo_language: {language} -->")
    if language_context:
        lines.append(
            "<!-- seo_language_decision: "
            f"{language_context.get('seo_language_decision')} -->"
        )
    lines.append("")
    lines.append(f"> {labels['intro']}")
    lines.append("")

    lines.append(f"## {sections['deliverable']}")
    lines.append("")
    lines.append(f"**{labels['mp4']}:** `{deliverable['file_name']}`")
    lines.append(f"**{labels['sha']}:** `{deliverable['sha256']}`")
    lines.append(
        f"**{labels['duration']}:** {deliverable['duration_seconds']:.3f} s"
    )
    lines.append(
        f"**{labels['language']}:** {LANGUAGE_NAMES.get(language, language)} "
        f"({language})"
    )
    lines.append(f"**{labels['audience']}:** {audience}")
    if language_context:
        lines.extend(_context_rows(language_context, labels))
    lines.append("")

    lines.append(f"## {sections['title']}")
    lines.append("")
    lines.append(f"**{labels['title']}:** {title}")
    lines.append("")
    lines.append(f"**{labels['alternatives']}:**")
    for i, alt in enumerate(alternatives, start=1):
        lines.append(f"{i}. {alt}")
    lines.append("")

    lines.append(f"## {sections['description']}")
    lines.append("")
    lines.append(description)
    lines.append("")

    lines.append(f"## {sections['hashtags']}")
    lines.append("")
    for tag in hashtags:
        lines.append(f"- {tag}")
    lines.append("")

    lines.append(f"## {sections['tags']}")
    lines.append("")
    lines.append(f"**{labels['tags']}:** {', '.join(tags)}")
    lines.append("")

    lines.append(f"## {sections['pinned']}")
    lines.append("")
    lines.append(pinned)
    lines.append("")

    lines.append(f"## {sections['thumbnail']}")
    lines.append("")
    lines.append(f"**{labels['thumbnail_text']}:** {thumbnail}")
    lines.append("")

    lines.append(f"## {sections['facts']}")
    lines.append("")
    lines.append(f"| {labels['fact_claim']} | {labels['fact_segment']} |")
    lines.append("|---|---|")
    for fact in facts:
        lines.append(f"| {fact['claim']} | {fact['segment_id']} |")
    lines.append("")

    lines.append(f"## {sections['sources']}")
    lines.append("")
    lines.append(
        f"| {labels['source_class']} | {labels['source_url']} | "
        f"{labels['source_date']} | {labels['source_claim']} |"
    )
    lines.append("|---|---|---|---|")
    for source in sources:
        lines.append(
            f"| {source['classification']} | {source['url']} | "
            f"{source['checked_on']} | {source['claim']} |"
        )
    lines.append("")

    lines.append(f"## {sections['state']}")
    lines.append("")
    lines.append(f"**{labels['status']}:** pending_user_review")
    lines.append(f"**{labels['generated']}:** {date.today().isoformat()}")
    if notes:
        lines.append("")
        lines.append(notes)
    lines.append("")
    return "\n".join(lines)


def _field_language_issues(
    field_name: str, text: str, expected: str
) -> List[str]:
    """Comprueba que un campo copiable este en el idioma esperado.

    Los textos sin evidencia suficiente (titulos cortos, hashtags, nombres
    propios) se consideran neutrales y no bloquean.
    """
    if not str(text or "").strip():
        return []
    detection = detect_language(text)
    top_hits = int(detection["top_hits"])
    candidate = detection["candidate_language"]
    runner_up_hits = int(detection["runner_up_hits"])
    expected_hits = int(detection["hits"].get(expected, 0))

    if top_hits >= 5 and candidate and candidate != expected:
        return [
            f"{field_name}: parece {candidate} ({top_hits} palabras funcionales) "
            f"y no {expected}."
        ]
    if (
        top_hits >= MIN_STOPWORD_HITS
        and runner_up_hits >= 4
        and (top_hits - runner_up_hits) < 3
    ):
        return [
            f"{field_name}: mezcla de idiomas "
            f"({expected}={expected_hits}, "
            f"{detection['runner_up']}={runner_up_hits})."
        ]
    return []


def validate_seo(
    markdown: str,
    *,
    segments: Sequence[Dict[str, Any]],
    deliverable: Dict[str, Any],
    verified_sources: Optional[Iterable[str]] = None,
    language_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Valida el markdown final contra la fuente y el entregable.

    Devuelve `{valid, errors, warnings, checks, extracted}`. `valid` solo es
    True si no hay errores. Las advertencias no bloquean pero se registran.
    """
    errors: List[str] = []
    warnings: List[str] = []
    checks: Dict[str, bool] = {}

    language, labels = _labels_for(markdown, language_context)
    sections = labels["sections"]
    expected_language = None
    if language_context:
        expected_language = language_context.get("seo_language")

    for key in SECTION_KEYS:
        heading = sections[key]
        checks[f"section:{heading}"] = bool(_section(markdown, heading))
        if not _section(markdown, heading):
            errors.append(f"Falta la seccion obligatoria: {heading}")

    title_section = _section(markdown, sections["title"])
    title = _field(title_section, labels["title"])
    checks["title_present"] = bool(title)
    if not title:
        errors.append("El titulo esta vacio.")
    checks["title_within_100"] = len(title) <= MAX_TITLE_CHARS
    if len(title) > MAX_TITLE_CHARS:
        errors.append(
            f"El titulo supera {MAX_TITLE_CHARS} caracteres ({len(title)})."
        )

    alt_lines = [
        line.split(". ", 1)[1].strip()
        for line in title_section.splitlines()
        if re.match(r"^\d+\.\s+", line)
    ]
    checks["two_alternatives"] = len(alt_lines) >= 2
    if len(alt_lines) < 2:
        errors.append("Se requieren dos titulos alternativos.")
    for i, alt in enumerate(alt_lines, start=1):
        if len(alt) > MAX_TITLE_CHARS:
            errors.append(f"La alternativa {i} supera {MAX_TITLE_CHARS} caracteres.")

    description = _section(markdown, sections["description"])
    checks["description_present"] = bool(description)
    checks["description_within_5000"] = len(description) <= MAX_DESCRIPTION_CHARS
    if not description:
        errors.append("La descripcion esta vacia.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(
            f"La descripcion supera {MAX_DESCRIPTION_CHARS} caracteres "
            f"({len(description)})."
        )
    checks["description_within_youtube_shorts"] = len(description) > 0

    hashtags = _bullets(_section(markdown, sections["hashtags"]))
    checks["hashtags_present"] = bool(hashtags)
    checks["hashtags_max_3"] = len(hashtags) <= MAX_HASHTAGS_DISPLAYED
    checks["hashtags_max_60"] = len(hashtags) <= MAX_HASHTAGS_TOTAL
    bad_hashtags = [
        tag for tag in hashtags
        if not tag.startswith("#") or " " in tag or len(tag) < 2
    ]
    checks["hashtags_well_formed"] = not bad_hashtags
    if not hashtags:
        errors.append("Faltan hashtags.")
    if len(hashtags) > MAX_HASHTAGS_DISPLAYED:
        errors.append(
            f"Se muestran {len(hashtags)} hashtags; el maximo visible es "
            f"{MAX_HASHTAGS_DISPLAYED}."
        )
    if len(hashtags) > MAX_HASHTAGS_TOTAL:
        errors.append(f"Se superan los {MAX_HASHTAGS_TOTAL} hashtags.")
    if bad_hashtags:
        errors.append(f"Hashtags mal formados: {bad_hashtags}")

    tags_section = _section(markdown, sections["tags"])
    tags_raw = _field(tags_section, labels["tags"])
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    checks["tags_present"] = bool(tags)
    if not tags:
        errors.append("Faltan etiquetas.")

    pinned = _section(markdown, sections["pinned"])
    checks["pinned_present"] = bool(pinned.strip())
    if not pinned.strip():
        errors.append("Falta el comentario fijado.")

    thumbnail = _field(_section(markdown, sections["thumbnail"]), labels["thumbnail_text"])
    checks["thumbnail_present"] = bool(thumbnail)
    if not thumbnail:
        errors.append("Falta el texto de miniatura.")

    facts_section = _section(markdown, sections["facts"])
    fact_rows = _table_rows(facts_section)
    fact_rows = [r for r in fact_rows if len(r) >= 2 and r[1].startswith("seg_")]
    checks["facts_present"] = bool(fact_rows)
    if not fact_rows:
        errors.append("No hay citas verificables en la tabla de datos.")

    index = transcript_index(segments)
    all_text = _transcript_all_text(segments)
    bad_facts: List[str] = []
    for claim, segment_id in [(r[0], r[1]) for r in fact_rows]:
        source_text = index.get(segment_id)
        if source_text is None:
            bad_facts.append(f"{claim!r} referencia {segment_id} inexistente")
            continue
        literal = claim.strip().strip('"“”')
        if _norm(literal) not in _norm(source_text):
            bad_facts.append(f"{claim!r} no es literal de {segment_id}")
    checks["facts_literal"] = not bad_facts
    if bad_facts:
        errors.extend(f"Evidencia no verificable: {item}" for item in bad_facts)

    sources_section = _section(markdown, sections["sources"])
    source_rows = _table_rows(sources_section)
    source_rows = [r for r in source_rows if len(r) >= 4 and r[1].startswith("http")]
    checks["sources_present"] = len(source_rows) >= 1
    if not source_rows:
        errors.append("Faltan fuentes oficiales verificadas.")

    allowed_classifications = {
        "official_requirement",
        "editorial_recommendation",
        "hypothesis",
    }
    bad_sources: List[str] = []
    for row in source_rows:
        classification, url, checked_on, _claim = row[:4]
        if classification not in allowed_classifications:
            bad_sources.append(f"clasificacion desconocida {classification!r}")
        if not url.startswith("https://"):
            bad_sources.append(f"URL no HTTPS: {url}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", checked_on):
            bad_sources.append(f"fecha invalida para {url}: {checked_on}")
        if verified_sources is not None and not _url_verified(url, verified_sources):
            bad_sources.append(f"URL no verificada en esta ejecucion: {url}")
    checks["sources_well_formed"] = not bad_sources
    if bad_sources:
        errors.extend(f"Fuente invalida: {item}" for item in bad_sources)

    # Fidelidad: citas entre comillas y cifras.
    quoted = [q for q in _QUOTED.findall(description + "\n" + pinned) if len(q) > 3]
    bad_quotes = [q for q in quoted if _norm(q) not in all_text]
    checks["quotes_literal"] = not bad_quotes
    if bad_quotes:
        errors.extend(
            f"Cita no respaldada por la transcripcion: {q!r}" for q in bad_quotes
        )

    numeric_text = "\n".join([title, description, pinned])
    source_digits = _digits(all_text)
    missing_numbers = sorted({
        number for number in _NUMBER.findall(numeric_text)
        if len(_digits(number)) >= 1 and _digits(number) not in source_digits
    })
    checks["numbers_backed"] = not missing_numbers
    if missing_numbers:
        errors.append(
            "Cifras sin respaldo en la fuente: " + ", ".join(missing_numbers)
        )

    lowered = _norm(title + "\n" + description + "\n" + pinned)
    promises = [p for p in FORBIDDEN_PROMISES if p in lowered]
    checks["no_result_promises"] = not promises
    if promises:
        errors.append(f"Promesas de resultado no permitidas: {promises}")

    deliverable_file = deliverable["file_name"]
    deliverable_sha = deliverable["sha256"]
    checks["mp4_name_matches"] = deliverable_file in markdown
    checks["mp4_sha_matches"] = deliverable_sha in markdown
    if not checks["mp4_name_matches"]:
        errors.append("El SEO no menciona el nombre real del MP4.")
    if not checks["mp4_sha_matches"]:
        errors.append("El SEO no incluye el SHA-256 real del MP4.")

    state_section = _section(markdown, sections["state"])
    checks["pending_review_declared"] = "pending_user_review" in state_section
    if not checks["pending_review_declared"]:
        errors.append("El SEO debe declarar el estado pending_user_review.")

    deliverable_section = _section(markdown, sections["deliverable"])
    checks["language_declared"] = bool(
        _field(deliverable_section, labels["language"])
    )
    checks["audience_declared"] = bool(_field(deliverable_section, labels["audience"]))
    if not checks["language_declared"]:
        errors.append("Falta declarar el idioma.")
    if not checks["audience_declared"]:
        errors.append("Falta declarar la audiencia (made for kids o no).")

    # Idioma: el documento declara el mismo idioma que la transcripcion y todos
    # los campos copiables estan en ese idioma (sin mezclar).
    declared = declared_language(markdown)
    checks["seo_language_declared_in_header"] = declared == language
    if declared != language:
        errors.append(
            "La cabecera del SEO declara un idioma distinto al esperado: "
            f"{declared!r} != {language!r}."
        )
    if expected_language is not None:
        checks["seo_language_matches_transcript"] = language == expected_language
        if language != expected_language:
            errors.append(
                "El idioma del SEO no coincide con el idioma canonico del audio: "
                f"{language!r} != {expected_language!r}."
            )
        expected_overlay = language_context.get("overlay_language")
        checks["seo_language_matches_overlay_language"] = (
            expected_overlay is None or language == expected_overlay
        )
        if not checks["seo_language_matches_overlay_language"]:
            errors.append(
                "El idioma del SEO no coincide con el de los overlays: "
                f"{language!r} != {expected_overlay!r}."
            )
        expected_decision = language_context.get("seo_language_decision")
        decision = _DECISION_COMMENT.search(markdown)
        checks["seo_language_decision_recorded"] = bool(
            decision
            and expected_decision in AUTO_LANGUAGE_DECISIONS
            and decision.group(1) == expected_decision
        )
        if not checks["seo_language_decision_recorded"]:
            errors.append(
                "El SEO no registra la decision de idioma esperada "
                f"({expected_decision})."
            )
        checks["source_audio_matches_transcript"] = (
            language_context.get("source_audio_language")
            == language_context.get("transcript_language")
        )

    language_issues: List[str] = []
    language_issues += _field_language_issues("titulo", title, language)
    for i, alt in enumerate(alt_lines, start=1):
        language_issues += _field_language_issues(f"alternativa {i}", alt, language)
    language_issues += _field_language_issues("descripcion", description, language)
    language_issues += _field_language_issues("comentario fijado", pinned, language)
    language_issues += _field_language_issues(
        "miniatura", thumbnail, language
    )
    language_issues += _field_language_issues(
        "etiquetas", ", ".join(tags), language
    )
    checks["language_fields_consistent"] = not language_issues
    if language_issues:
        errors.extend(f"Idioma inconsistente: {item}" for item in language_issues)

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "language": {
            "seo_language": language,
            "expected_language": expected_language,
            "declared_in_header": declared,
            "decision": (
                _DECISION_COMMENT.search(markdown).group(1)
                if _DECISION_COMMENT.search(markdown)
                else None
            ),
        },
        "extracted": {
            "title": title,
            "alt_titles": alt_lines,
            "description_chars": len(description),
            "hashtags": hashtags,
            "tags": tags,
            "facts": len(fact_rows),
            "sources": len(source_rows),
        },
    }


def _url_verified(url: str, verified_sources: Iterable[str]) -> bool:
    verified = {v.rstrip("/") for v in verified_sources}
    return url.rstrip("/") in verified


__all__ = [
    "LABELS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_HASHTAGS_DISPLAYED",
    "MAX_TITLE_CHARS",
    "REQUIRED_SECTIONS",
    "SECTION_KEYS",
    "build_seo",
    "declared_language",
    "transcript_index",
    "validate_seo",
]
