"""Deteccion local del idioma para overlays (tarjetas/énfasis) y SEO.

Regla del proyecto: el idioma de los overlays y del SEO se deriva del **audio
original** del clip. La transcripcion y los subtitulos se registran por separado
porque pueden ser traducciones y nunca deben cambiar las tarjetas ni el énfasis.

- Se detecta con perfiles de palabras funcionales (stopwords), sin red, sin
  modelos y sin dependencias nuevas.
- Si la evidencia es insuficiente, mixta o ambigua, la decision es
  `needs_review` y se detiene la generacion de overlays/SEO.
- Si la transcripcion es una traduccion sin idioma de audio declarado, la
  decision es `needs_review`: no se infiere el idioma del audio desde el texto
  traducido.
- Una contradiccion entre el idioma declarado y la deteccion tambien es
  `needs_review`.
- Se registran por separado `source_audio_language`, `transcript_language`,
  `subtitle_language`, `notification_language`, `emphasis_language`,
  `overlay_language` y `seo_language`.

Los codigos de idioma son ISO 639-1 (`en`, `es`, `pt`, `fr`, `de`, `it`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Sequence

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}

SUPPORTED_LANGUAGES = tuple(LANGUAGE_NAMES)

# Decisiones que autorizan a usar el idioma canonico sin revision humana.
AUTO_LANGUAGE_DECISIONS = (
    "auto_from_source_audio",
    "auto_from_audio_transcript",
)

# Minimos para decidir sin revision humana.
MIN_STOPWORD_HITS = 8
MIN_CONFIDENCE = 0.60
MIN_COVERAGE = 0.15

_TOKEN = re.compile(r"[a-zà-öø-ÿ']+")

_STOPWORDS: Dict[str, frozenset] = {
    "en": frozenset(
        """
        the and to of is it that we you they for on with was were are be this
        have has had not but from at as our my he she his her their there what
        when who how can will just all get got out up if do did your us them
        because about into over down one two three now then than more most some
        any only also very well still even back off again right know think going
        gonna gotta okay yeah guys like want need been being would could should
        might must here where why which while after before through between both
        each few other such nor too own same dont didnt doesnt isnt arent wasnt
        werent wont wouldnt couldnt shouldnt lets thats whats youre theyre weve
        ive im
        """.split()
    ),
    "es": frozenset(
        """
        el los las del al es son era eran fue fueron ser estar está están estoy
        estamos este esta esto estos estas ese esa eso esos esas por para con sin
        sobre entre hasta desde cuando donde cómo qué quién quiénes cuál cuáles
        cuánto cuánta cuántos cuántas porque pero sino aunque mientras también
        tampoco muy más menos mucho mucha muchos muchas poco poca pocos pocas todo
        toda todos todas otro otra otros otras mismo misma le les lo su sus mi mis
        tu tus yo tú él ella nosotros vosotros ellos ellas usted ustedes hay hemos
        han había habían soy eres somos sois puedes puede pueden podemos quiero
        quiere quieren tengo tiene tienen tenemos voy va van vamos hace hacen hizo
        hicieron dijo dicen decir veo mira ahora después antes aquí ahí allí bien
        mal sí ya aún todavía siempre nunca nada algo alguien nadie cada veces día
        días año años cosa cosas gente entonces pues bueno vale chicos y
        """.split()
    ),
    "pt": frozenset(
        """
        não você vocês está estão estou estamos muito muita muitos muitas também
        porque quando onde mas porém embora enquanto até com sem sobre entre para
        pelo pela dos das uns umas aos são eram foi foram estar tem têm tinha
        tinham havia sou somos posso pode podem quero quer querem tenho temos vou
        vai vão vamos faz fazem fez disse dizem dizer vejo olha agora depois antes
        aqui aí ali já ainda sempre nunca nada algo alguém ninguém cada vezes dia
        dias ano anos coisa coisas gente então bom boa pois cara é e
        """.split()
    ),
    "fr": frozenset(
        """
        les des du au aux dans sur sous avec sans pour par entre jusqu depuis
        quand où comme qui quoi quel quelle quels quelles combien parce mais donc
        or ni car aussi très trop plus moins beaucoup peu tout toute tous toutes
        autre autres même mêmes lui leur leurs nous vous ils elles je tu on est
        sont était étaient été être avoir avons avez ont avait avaient suis es
        sommes êtes peux peut peuvent pouvons veux veut veulent vais vont allons
        fait font faisait dit disent dire vois regarde maintenant après avant ici
        là bien mal oui non déjà encore toujours jamais rien quelque chose choses
        gens alors bon ouais mec cest jai nest et ne pas
        """.split()
    ),
    "de": frozenset(
        """
        der die das den dem des ein eine einen einem einer und oder aber wenn als
        wie wo wer was warum weil dass ich du er sie wir ihr mich dich sich uns
        euch mein dein sein ihre unser euer ist sind war waren bin bist seid habe
        hast hat haben hatte hatten wird werden wurde wurden kann können konnte
        wollen muss müssen soll sollen mag möchte gehe geht gehen ging komme kommt
        kommen kam mache macht machen machte sehe sieht sehen sah sage sagt sagen
        sagte jetzt dann danach vorher hier da dort gut schlecht ja nein schon
        noch immer nie nichts etwas jemand niemand jeder mal zeit sache sachen
        leute also nicht auch nur
        """.split()
    ),
    "it": frozenset(
        """
        il lo gli le di del della dei degli delle alla agli alle nel nella nei
        nelle sul sulla senza tra fra fino dal quando dove come che chi quale
        quali quanto quanta perché però quindi anche molto molta molti molte poco
        tutto tutta tutti tutte altro altra altri altre stesso stessa ci vi suo
        sua suoi sue mio mia miei mie tuo tua tuoi tue io tu lui lei noi voi loro
        è sono erano stato essere avere ho hai ha abbiamo avete hanno avevo posso
        può possono voglio vuole vogliono vado vanno andiamo fa fanno fatto dice
        dicono dire vedere vedo guarda ora adesso dopo prima qui qua lì là bene
        male sì già ancora sempre mai niente qualcosa qualcuno nessuno ogni volta
        volte giorno giorni anno anni cosa cose gente allora buono dai
        """.split()
    ),
}


class SeoLanguageError(RuntimeError):
    """El idioma del SEO no se puede determinar o no coincide con la fuente."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


class OverlayLanguageError(RuntimeError):
    """El idioma de las tarjetas/énfasis no se puede determinar con seguridad."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


def _norm_language(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text or text in {"unknown", "und", "none", "null"}:
        return None
    return text


def tokenize(text: str) -> list:
    return _TOKEN.findall(str(text or "").lower())


def detect_language(text: str) -> Dict[str, Any]:
    """Detecta el idioma predominante de `text` con un puntaje interpretable.

    Devuelve `language` (o `None`), `confidence`, `decision`
    (`auto`/`needs_review`) y la evidencia usada (hits por idioma, cobertura,
    segundo idioma y motivo).
    """
    tokens = tokenize(text)
    total_tokens = len(tokens)
    hits = {lang: 0 for lang in _STOPWORDS}
    for token in tokens:
        for lang, words in _STOPWORDS.items():
            if token in words:
                hits[lang] += 1

    ranked = sorted(hits.items(), key=lambda item: item[1], reverse=True)
    top_lang, top_hits = ranked[0]
    second_lang, second_hits = ranked[1] if len(ranked) > 1 else (None, 0)
    total_hits = sum(hits.values())
    coverage = round(top_hits / total_tokens, 4) if total_tokens else 0.0
    purity = top_hits / total_hits if total_hits else 0.0
    margin = (top_hits - second_hits) / top_hits if top_hits else 0.0
    confidence = round(0.6 * purity + 0.4 * margin, 4) if top_hits else 0.0

    reasons = []
    if total_tokens < 6:
        reasons.append(f"texto demasiado corto ({total_tokens} tokens)")
    if top_hits < MIN_STOPWORD_HITS:
        reasons.append(
            f"evidencia insuficiente ({top_hits} palabras funcionales, "
            f"minimo {MIN_STOPWORD_HITS})"
        )
    if coverage < MIN_COVERAGE:
        reasons.append(f"cobertura baja ({coverage:.3f})")
    if confidence < MIN_CONFIDENCE:
        reasons.append(f"confianza insuficiente ({confidence:.3f})")
    if top_hits and second_hits and (top_hits - second_hits) < 3:
        reasons.append(
            f"idiomas mezclados ({top_lang}={top_hits}, {second_lang}={second_hits})"
        )

    decision = "auto" if not reasons else "needs_review"
    return {
        "language": top_lang if decision == "auto" else None,
        "candidate_language": top_lang if top_hits else None,
        "confidence": confidence,
        "decision": decision,
        "reason": "; ".join(reasons) if reasons else "idioma predominante claro",
        "tokens": total_tokens,
        "hits": hits,
        "top_hits": top_hits,
        "coverage": coverage,
        "runner_up": second_lang if second_hits else None,
        "runner_up_hits": second_hits,
    }


def _join_segment_text(segments: Iterable[Dict[str, Any]]) -> str:
    return " ".join(str(segment.get("text", "")) for segment in segments)


def build_language_context(
    segments: Sequence[Dict[str, Any]],
    *,
    source_audio_language: Optional[str] = None,
    subtitle_text: Optional[str] = None,
    source_audio_language_source: str = "unknown",
    transcript_is_translation: bool = False,
) -> Dict[str, Any]:
    """Construye el contexto canonico de idioma del fragmento final.

    Prioridad (fuente de verdad = audio original):

    1. `source_audio_language` declarado por el job.
    2. Metadato de la transcripcion (ASR), solo si no es una traduccion.
    3. Deteccion sobre la transcripcion, si es automatica y segura.

    La deteccion del audio/transcripcion valida el idioma declarado. Si hay
    contradiccion, mezcla o ambiguedad, la decision es `needs_review`.

    `subtitle_language` se detecta aparte y nunca decide overlays ni SEO.
    `notification_language`, `emphasis_language`, `overlay_language` y
    `seo_language` comparten el idioma canonico del audio.
    """
    transcript_text = _join_segment_text(segments)
    transcript = detect_language(transcript_text)
    if subtitle_text is None:
        subtitle_detection = dict(transcript)
    else:
        subtitle_detection = detect_language(subtitle_text)

    transcript_language = transcript["language"]
    declared = _norm_language(source_audio_language)
    source = str(source_audio_language_source or "unknown")

    canonical: Optional[str] = None
    decision = "needs_review"
    reason = ""

    if declared and declared not in SUPPORTED_LANGUAGES:
        reason = f"idioma del audio no soportado por el validador: {declared}"
    elif transcript_is_translation:
        if declared:
            canonical = declared
            decision = (
                "auto_from_source_audio"
                if source == "job"
                else "auto_from_audio_transcript"
            )
            reason = (
                "la transcripcion es una traduccion; el idioma se toma del "
                "audio original declarado, no del texto traducido"
            )
        else:
            reason = (
                "la transcripcion es una traduccion y no hay idioma de audio "
                "declarado; no se infiere desde el texto traducido"
            )
    elif transcript_language:
        if declared and declared != transcript_language:
            reason = (
                "el idioma declarado del audio no coincide con la deteccion "
                f"del fragmento: declarado={declared}, detectado={transcript_language}"
            )
        else:
            canonical = transcript_language
            if declared and source == "job":
                decision = "auto_from_source_audio"
                reason = (
                    "idioma del audio declarado y confirmado por la deteccion "
                    "del fragmento"
                )
            else:
                decision = "auto_from_audio_transcript"
                reason = (
                    "idioma derivado del audio/transcripcion del fragmento"
                )
    else:
        reason = (
            "audio/transcripcion mixta o ambigua: "
            + str(transcript.get("reason") or "sin evidencia suficiente")
        )

    if canonical and canonical not in SUPPORTED_LANGUAGES:
        canonical = None
        decision = "needs_review"
        reason = f"idioma del audio no soportado: {canonical}"

    # El enfasis exige texto literal de la transcripcion. Si la transcripcion
    # es una traduccion no se puede producir enfasis en el idioma del audio.
    emphasis_language = canonical if canonical and not transcript_is_translation else None
    if canonical and transcript_is_translation:
        emphasis_reason = (
            "la transcripcion es una traduccion: el enfasis literal no esta en "
            "el idioma del audio"
        )
    elif emphasis_language:
        emphasis_reason = "enfasis literal de la transcripcion en el idioma del audio"
    else:
        emphasis_reason = reason

    names = {
        "source_audio": LANGUAGE_NAMES.get(declared or ""),
        "transcript": LANGUAGE_NAMES.get(transcript_language or ""),
        "subtitle": LANGUAGE_NAMES.get(subtitle_detection["language"] or ""),
        "notification": LANGUAGE_NAMES.get(canonical or ""),
        "emphasis": LANGUAGE_NAMES.get(emphasis_language or ""),
        "overlay": LANGUAGE_NAMES.get(canonical or ""),
        "seo": LANGUAGE_NAMES.get(canonical or ""),
    }

    return {
        "source_audio_language": declared,
        "source_audio_language_source": source,
        "transcript_is_translation": bool(transcript_is_translation),
        "canonical_language": canonical,
        "canonical_language_source": source if canonical else None,
        "transcript_language": transcript_language,
        "transcript_language_confidence": transcript["confidence"],
        "subtitle_language": subtitle_detection["language"],
        "notification_language": canonical,
        "emphasis_language": emphasis_language,
        "overlay_language": canonical,
        "seo_language": canonical,
        "overlay_language_decision": decision,
        "notification_language_decision": decision,
        "emphasis_language_decision": (
            decision if emphasis_language else "needs_review"
        ),
        "seo_language_decision": decision,
        "overlay_language_reason": reason,
        "emphasis_language_reason": emphasis_reason,
        "seo_language_reason": reason,
        "names": names,
        "evidence": {
            "segments": [
                {
                    "segment_id": s.get("segment_id"),
                    "start_seconds": s.get("start_seconds"),
                    "end_seconds": s.get("end_seconds"),
                }
                for s in segments
            ],
            "declared_source_audio": declared,
            "transcript": transcript,
            "subtitles": subtitle_detection,
        },
    }


def require_seo_language(context: Dict[str, Any]) -> str:
    """Devuelve el idioma del SEO o lanza `SeoLanguageError` con la evidencia."""
    if not context:
        raise SeoLanguageError(
            "No hay contexto de idioma: no se puede generar el SEO.",
            details={"context": context},
        )
    if context.get("seo_language_decision") not in AUTO_LANGUAGE_DECISIONS:
        raise SeoLanguageError(
            "Idioma del SEO no determinado con confianza suficiente; "
            "se requiere revision humana.",
            details={"context": context},
        )
    language = context.get("seo_language")
    if language not in SUPPORTED_LANGUAGES:
        raise SeoLanguageError(
            f"Idioma del SEO no soportado: {language!r}.",
            details={"context": context},
        )
    return language


def require_overlay_language(context: Dict[str, Any]) -> str:
    """Devuelve el idioma canonico de overlays o lanza `OverlayLanguageError`."""
    if not context:
        raise OverlayLanguageError(
            "No hay contexto de idioma: no se pueden generar overlays.",
            details={"context": context},
        )
    if context.get("overlay_language_decision") not in AUTO_LANGUAGE_DECISIONS:
        raise OverlayLanguageError(
            "Idioma de las tarjetas no determinado con confianza suficiente; "
            "se requiere revision humana.",
            details={"context": context, "needs_review": True},
        )
    language = context.get("overlay_language")
    if language not in SUPPORTED_LANGUAGES:
        raise OverlayLanguageError(
            f"Idioma de overlays no soportado: {language!r}.",
            details={"context": context},
        )
    return language


def require_emphasis_language(context: Dict[str, Any]) -> str:
    """Devuelve el idioma del enfasis literal o lanza `OverlayLanguageError`."""
    if not context:
        raise OverlayLanguageError(
            "No hay contexto de idioma: no se puede generar el enfasis.",
            details={"context": context},
        )
    language = context.get("emphasis_language")
    if (
        context.get("emphasis_language_decision") not in AUTO_LANGUAGE_DECISIONS
        or language not in SUPPORTED_LANGUAGES
    ):
        raise OverlayLanguageError(
            "El enfasis no se puede escribir en el idioma del audio; "
            "se requiere revision humana.",
            details={"context": context, "needs_review": True},
        )
    return language


__all__ = [
    "AUTO_LANGUAGE_DECISIONS",
    "LANGUAGE_NAMES",
    "MIN_CONFIDENCE",
    "MIN_COVERAGE",
    "MIN_STOPWORD_HITS",
    "SUPPORTED_LANGUAGES",
    "OverlayLanguageError",
    "SeoLanguageError",
    "build_language_context",
    "detect_language",
    "require_emphasis_language",
    "require_overlay_language",
    "require_seo_language",
    "tokenize",
]
