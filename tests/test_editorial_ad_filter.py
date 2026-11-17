"""Pruebas del filtro anti-anuncios editorial (v3).

Cubren los casos exigidos por el usuario:

1. anuncio de videojuego (Call of Duty: Mobile) -> promotional / reject
2. anuncio de libro ("The Most Dangerous Games") -> promotional / reject
3. patrocinio explicito -> sponsored_or_integrated / reject
4. mencion incidental de marca sin promocion -> incidental_mention / aceptable
5. evidencia publicitaria ambigua -> uncertain / needs_review
6. corte dentro de un anuncio (solo una parte de la CTA) -> promotional / reject
7. candidato limpio -> none / accept

Las pruebas usan la transcripcion REAL de los dos anuncios conocidos del
proyecto (fixtures negativos) y no llaman a LM Studio ni a la red.

Ejecutar con:
    python -m unittest tests.test_editorial_ad_filter -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import ClipProposal, ReviewStatus, TranscriptSegment  # noqa: E402
from schemas.editorial import (  # noqa: E402
    AdClassification,
    EditorialCandidate,
    EditorialDecision,
)
from services.editorial.config import EditorialConfig  # noqa: E402
from services.editorial.parse import (  # noqa: E402
    detect_promotional_evidence,
    parse_model_response,
)
from services.editorial.selection import (  # noqa: E402
    content_render_blockers,
    is_content_eligible,
    is_render_eligible,
    render_blockers,
)

# Texto literal de la fuente real (projects/d3d5157c.../transcription.json).
COD_CTA = (
    "and if you want to keep playing, hop into cod mobile right after this video. "
    "scan this qr code or click the link in the description to download call of "
    "duty mobile today"
)

BOOK_CTA = (
    "i wrote a brand new book with james patterson called the most dangerous games. "
    "buy the book and enter for a chance to win one million dollars. go wherever "
    "you buy books and check it out"
)

SPONSOR_CTA = (
    "this video is sponsored by a vpn service. use code example to get a discount "
    "on your first year"
)

INCIDENTAL = (
    "we escaped from one hundred real cops and then we played a call of duty lobby "
    "with my friends for the rest of the night"
)

CLEAN = (
    "me and my four friends just escaped from one hundred real cops. we have to "
    "survive for twenty four hours without getting caught again"
)


def seg(sid, start, end, text):
    return TranscriptSegment(
        segment_id=sid, start_seconds=start, end_seconds=end, text=text
    )


def counter_factory():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return f"clip_{state['n']:04d}"

    return factory


def raw_candidate(
    refs,
    *,
    evidence,
    ad_classification=None,
    ad_evidence="",
    decision=None,
    reason="",
    start_ok=True,
    end_ok=True,
    boundary=True,
):
    candidate = {
        "segment_ids": refs,
        "title": "Titulo editorial",
        "hook": "gancho",
        "main_idea": "idea",
        "ending": "cierre",
        "editorial_reason": "razon",
        "evidence_text": evidence,
        "context_dependency": "low",
        "confidence": 0.8,
    }
    if ad_classification is not None:
        candidate["ad_classification"] = ad_classification
    if ad_evidence:
        candidate["ad_evidence"] = ad_evidence
    if decision is not None:
        candidate["editorial_decision"] = decision
    if reason:
        candidate["editorial_decision_reason"] = reason
    if boundary:
        candidate["boundary_evaluation"] = {
            "start": "inicio de frase",
            "end": "cierre de idea",
            "start_ok": start_ok,
            "end_ok": end_ok,
            "notes": "",
        }
    return {"candidates": [candidate]}


def make_parse(segments, body_ids):
    by_id = {s.segment_id: s for s in segments}

    def _parse(payload, *, config=None):
        return parse_model_response(
            json.dumps(payload),
            source_asset_id="asset-1",
            window_id="win_0001",
            body_ids=body_ids,
            segments_by_id=by_id,
            config=config or EditorialConfig(),
            duration_seconds=max(s.end_seconds for s in segments),
            clip_id_factory=counter_factory(),
        )

    return _parse


class GameAdTests(unittest.TestCase):
    """Caso 1: anuncio de Call of Duty: Mobile."""

    def setUp(self):
        self.segments = [
            seg("seg_0210", 0.0, 18.0, "so what are you waiting for"),
            seg("seg_0211", 18.0, 36.0, COD_CTA),
        ]
        self.parse = make_parse(self.segments, ["seg_0210", "seg_0211"])

    def test_model_that_calls_it_organic_is_overruled_by_literal_cta(self):
        candidates, rejected = self.parse(
            raw_candidate(
                ["seg_0210", "seg_0211"],
                evidence="so what are you waiting for",
                ad_classification="none",
                decision="accept",
                reason="parece contenido",
            )
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.PROMOTIONAL.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)
        self.assertIn("download call of duty mobile today", candidate.ad_evidence)
        self.assertFalse(is_content_eligible(candidate))

    def test_reported_promotional_is_preserved_and_rejected(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_0210", "seg_0211"],
                evidence="so what are you waiting for",
                ad_classification="promotional",
                ad_evidence="scan this qr code",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.PROMOTIONAL.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)
        self.assertIn("Fragmento publicitario", candidate.editorial_decision_reason)


class BookAdTests(unittest.TestCase):
    """Caso 2: promocion del libro coescrito con James Patterson."""

    def setUp(self):
        self.segments = [
            seg("seg_0319", 0.0, 17.0, "i wrote a brand new book with james patterson"),
            seg("seg_0320", 17.0, 34.0, BOOK_CTA),
        ]
        self.parse = make_parse(self.segments, ["seg_0319", "seg_0320"])

    def test_book_cta_is_promotional_even_without_model_label(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_0319", "seg_0320"],
                evidence="i wrote a brand new book with james patterson",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.PROMOTIONAL.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)
        self.assertTrue(candidate.ad_evidence)
        self.assertTrue(any("buy the book" in item.lower() for item in [candidate.ad_evidence]))


class SponsorshipTests(unittest.TestCase):
    """Caso 3: tramo patrocinado explicito."""

    def setUp(self):
        self.segments = [
            seg("seg_sp1", 0.0, 20.0, "before we continue"),
            seg("seg_sp2", 20.0, 42.0, SPONSOR_CTA),
        ]
        self.parse = make_parse(self.segments, ["seg_sp1", "seg_sp2"])

    def test_sponsorship_never_passes_to_render(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_sp1", "seg_sp2"],
                evidence="before we continue",
                ad_classification="sponsored_or_integrated",
                ad_evidence="this video is sponsored by a vpn service",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(
            candidate.ad_classification,
            AdClassification.SPONSORED_OR_INTEGRATED.value,
        )
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)
        self.assertFalse(is_content_eligible(candidate))


class IncidentalMentionTests(unittest.TestCase):
    """Caso 4: marca mencionada como parte de la historia, sin CTA."""

    def setUp(self):
        self.segments = [
            seg("seg_inc1", 0.0, 16.0, "we escaped from one hundred real cops"),
            seg("seg_inc2", 16.0, 38.0, INCIDENTAL),
        ]
        self.parse = make_parse(self.segments, ["seg_inc1", "seg_inc2"])

    def test_incidental_mention_can_be_accepted(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_inc1", "seg_inc2"],
                evidence="we escaped from one hundred real cops",
                ad_classification="incidental_mention",
                decision="accept",
                reason="La marca aparece como parte de la historia.",
            )
        )
        candidate = candidates[0]
        self.assertEqual(
            candidate.ad_classification, AdClassification.INCIDENTAL_MENTION.value
        )
        self.assertEqual(candidate.editorial_decision, EditorialDecision.ACCEPT.value)
        self.assertTrue(is_content_eligible(candidate))
        # Sin aprobacion humana nunca es elegible para render final.
        self.assertFalse(is_render_eligible(candidate))
        self.assertIn("review_status=pending", render_blockers(candidate))


class AmbiguousEvidenceTests(unittest.TestCase):
    """Caso 5: evidencia publicitaria ambigua o no verificable."""

    def setUp(self):
        self.segments = [
            seg("seg_amb1", 0.0, 18.0, "then we tried a new game for a while"),
            seg("seg_amb2", 18.0, 36.0, "it was fun but i am not sure what it was called"),
        ]
        self.parse = make_parse(self.segments, ["seg_amb1", "seg_amb2"])

    def test_missing_classification_defaults_to_uncertain_and_review(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_amb1", "seg_amb2"],
                evidence="then we tried a new game for a while",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.UNCERTAIN.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.NEEDS_REVIEW.value)
        self.assertFalse(is_content_eligible(candidate))

    def test_non_literal_ad_evidence_is_discarded_and_downgraded(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_amb1", "seg_amb2"],
                evidence="then we tried a new game for a while",
                ad_classification="promotional",
                ad_evidence="compra ahora con descuento",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_evidence, "")
        self.assertEqual(candidate.ad_classification, AdClassification.UNCERTAIN.value)
        self.assertIn(candidate.editorial_decision, (
            EditorialDecision.REJECT.value,
            EditorialDecision.NEEDS_REVIEW.value,
        ))
        self.assertFalse(is_content_eligible(candidate))

    def test_promotional_without_evidence_is_downgraded_to_uncertain(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_amb1", "seg_amb2"],
                evidence="then we tried a new game for a while",
                ad_classification="promotional",
                ad_evidence="",
                decision="reject",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.UNCERTAIN.value)
        self.assertFalse(is_content_eligible(candidate))


class CutInsideAdTests(unittest.TestCase):
    """Caso 6: el clip empieza o termina dentro de un anuncio."""

    def setUp(self):
        self.segments = [
            seg("seg_cut1", 0.0, 14.0, "so what are you waiting for"),
            seg("seg_cut2", 14.0, 30.0, "scan this qr code"),
            seg("seg_cut3", 30.0, 48.0, "download call of duty mobile today"),
        ]
        self.parse = make_parse(
            self.segments, ["seg_cut1", "seg_cut2", "seg_cut3"]
        )

    def test_partial_ad_is_still_promotional(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_cut1", "seg_cut2"],
                evidence="so what are you waiting for",
                ad_classification="none",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.PROMOTIONAL.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)
        self.assertFalse(is_content_eligible(candidate))

    def test_ad_tail_inside_candidate_is_detected(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_cut2", "seg_cut3"],
                evidence="scan this qr code",
                ad_classification="incidental_mention",
                decision="accept",
                reason="",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.PROMOTIONAL.value)
        self.assertFalse(is_content_eligible(candidate))


class CleanCandidateTests(unittest.TestCase):
    """Caso 7: candidato limpio, sin publicidad."""

    def setUp(self):
        self.segments = [
            seg("seg_0016", 0.0, 17.0, "me and my four friends just escaped from one hundred real cops"),
            seg("seg_0017", 17.0, 34.0, CLEAN),
        ]
        self.parse = make_parse(self.segments, ["seg_0016", "seg_0017"])

    def test_clean_candidate_is_content_eligible_but_not_render_eligible(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_0016", "seg_0017"],
                evidence="me and my four friends just escaped from one hundred real cops",
                ad_classification="none",
                decision="accept",
                reason="Reto autoconclusivo sin promocion.",
            )
        )
        candidate = candidates[0]
        self.assertEqual(candidate.ad_classification, AdClassification.NONE.value)
        self.assertEqual(candidate.editorial_decision, EditorialDecision.ACCEPT.value)
        self.assertEqual(content_render_blockers(candidate), [])
        self.assertTrue(is_content_eligible(candidate))
        self.assertFalse(is_render_eligible(candidate))

    def test_accept_with_invalid_boundary_is_downgraded(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_0016", "seg_0017"],
                evidence="me and my four friends just escaped from one hundred real cops",
                ad_classification="none",
                decision="accept",
                reason="",
                end_ok=False,
            )
        )
        candidate = candidates[0]
        self.assertEqual(
            candidate.editorial_decision, EditorialDecision.NEEDS_REVIEW.value
        )
        self.assertIn("limites_no_validados", content_render_blockers(candidate))

    def test_missing_boundary_evaluation_is_not_accepted(self):
        candidates, _ = self.parse(
            raw_candidate(
                ["seg_0016", "seg_0017"],
                evidence="me and my four friends just escaped from one hundred real cops",
                ad_classification="none",
                decision="accept",
                reason="",
                boundary=False,
            )
        )
        candidate = candidates[0]
        self.assertEqual(
            candidate.editorial_decision, EditorialDecision.NEEDS_REVIEW.value
        )


class ContractGateTests(unittest.TestCase):
    """El contrato impide construir un `accept` publicitario por accidente."""

    def _candidate(self, ad_classification, decision):
        proposal = ClipProposal(
            clip_id="clip_9000",
            source_asset_id="asset-1",
            start_seconds=0.0,
            end_seconds=30.0,
            title="Titulo",
            confidence=0.9,
            transcript_refs=["seg_0001"],
        )
        return EditorialCandidate(
            proposal=proposal,
            duration_seconds=30.0,
            evidence_text="texto",
            originated_from_window="win_0001",
            ad_classification=ad_classification,
            editorial_decision=decision,
            boundary_evaluation={"start_ok": True, "end_ok": True},
        )

    def test_promotional_accept_is_forced_to_reject(self):
        candidate = self._candidate("promotional", "accept")
        self.assertEqual(candidate.editorial_decision, EditorialDecision.REJECT.value)

    def test_uncertain_accept_is_forced_to_needs_review(self):
        candidate = self._candidate("uncertain", "accept")
        self.assertEqual(
            candidate.editorial_decision, EditorialDecision.NEEDS_REVIEW.value
        )

    def test_legacy_candidate_defaults_are_conservative(self):
        proposal = ClipProposal(
            clip_id="clip_9001",
            source_asset_id="asset-1",
            start_seconds=0.0,
            end_seconds=30.0,
            title="Titulo",
            confidence=0.9,
            transcript_refs=["seg_0001"],
        )
        candidate = EditorialCandidate(
            proposal=proposal,
            duration_seconds=30.0,
            evidence_text="texto",
            originated_from_window="win_0001",
        )
        self.assertEqual(candidate.ad_classification, AdClassification.UNCERTAIN.value)
        self.assertEqual(
            candidate.editorial_decision, EditorialDecision.NEEDS_REVIEW.value
        )
        self.assertFalse(is_content_eligible(candidate))

    def test_serialization_round_trip_keeps_new_fields(self):
        candidate = self._candidate("none", "accept")
        restored = EditorialCandidate.from_dict(
            json.loads(json.dumps(candidate.to_dict()))
        )
        self.assertEqual(restored.ad_classification, AdClassification.NONE.value)
        self.assertEqual(restored.editorial_decision, EditorialDecision.ACCEPT.value)
        self.assertEqual(restored.to_dict(), candidate.to_dict())


class PromotionalDetectorTests(unittest.TestCase):
    """El detector local reconoce CTAs literales y no inventa otras."""

    def test_detects_real_call_of_duty_cta(self):
        found = detect_promotional_evidence(COD_CTA)
        self.assertTrue(found)
        joined = " | ".join(found).lower()
        self.assertIn("qr code", joined)
        self.assertIn("link in the description", joined)

    def test_detects_real_book_cta(self):
        found = detect_promotional_evidence(BOOK_CTA)
        self.assertIn("buy the book", [item.lower() for item in found])

    def test_clean_text_has_no_detections(self):
        self.assertEqual(detect_promotional_evidence(CLEAN), [])

    def test_incidental_mention_has_no_detections(self):
        self.assertEqual(detect_promotional_evidence(INCIDENTAL), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
