"""Pruebas de parseo y seleccion editorial (Fase 3).

Cadena de confianza: el modelo propone, el codigo valida. Estas pruebas
verifican que se rechaza todo lo que no este respaldado por la fuente, que
los limites temporales los calcula el codigo y que la deduplicacion y el
ordenamiento son deterministas. No se llama a LM Studio.

Ejecutar con:
    python -m unittest tests.test_editorial_analysis -v
"""

import itertools
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas.contracts import (  # noqa: E402
    ClipProposal,
    ReviewStatus,
    TranscriptSegment,
    ValidationError,
)
from schemas.editorial import EditorialCandidate, EditorialSignal, SignalStatus  # noqa: E402
from services.editorial.config import EditorialConfig  # noqa: E402
from services.editorial.errors import EditorialInvalidResponseError  # noqa: E402
from services.editorial.parse import (  # noqa: E402
    extract_json_object,
    parse_model_response,
)
from services.editorial.selection import (  # noqa: E402
    apply_total_limit,
    deduplicate,
    is_duplicate,
    overlap_ratio,
    rank_candidates,
    text_similarity,
)


def seg(sid, start, end, text):
    return TranscriptSegment(
        segment_id=sid, start_seconds=start, end_seconds=end, text=text
    )


def make_segments():
    return [
        seg("seg_0001", 0.0, 30.0, "texto uno comun"),
        seg("seg_0002", 30.0, 60.0, "texto dos comun"),
        seg("seg_short", 55.0, 60.0, "corto texto"),
        seg("ctx_0001", 60.0, 70.0, "contexto posterior"),
    ]


def counter_factory():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return f"clip_{state['n']:04d}"

    return factory, state


def candidate_response(clip_id_refs, *, evidence=None, title="Titulo", confidence=0.8,
                       signals=None, duration_ok=True, context_dependency="low"):
    candidate = {
        "segment_ids": clip_id_refs,
        "title": title,
        "hook": "gancho",
        "main_idea": "idea",
        "ending": "cierre",
        "editorial_reason": "razon",
        "evidence_text": evidence if evidence is not None else "texto uno comun",
        "context_dependency": context_dependency,
        "confidence": confidence,
    }
    if signals is not None:
        candidate["signals"] = signals
    return json.dumps({"candidates": [candidate]})


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.config = EditorialConfig()
        self.segments = make_segments()
        self.by_id = {s.segment_id: s for s in self.segments}
        self.body_ids = ["seg_0001", "seg_0002"]

    def _parse(self, raw, body_ids=None):
        factory, _ = counter_factory()
        return parse_model_response(
            raw,
            source_asset_id="asset-1",
            window_id="win_0001",
            body_ids=body_ids or self.body_ids,
            segments_by_id=self.by_id,
            config=self.config,
            duration_seconds=60.0,
            clip_id_factory=factory,
        )

    def test_valid_response_computes_times_from_segments(self):
        candidates, rejected = self._parse(candidate_response(["seg_0001", "seg_0002"]))
        self.assertEqual(rejected, [])
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.proposal.start_seconds, 0.0)
        self.assertEqual(c.proposal.end_seconds, 60.0)
        self.assertEqual(c.duration_seconds, 60.0)
        self.assertEqual(c.proposal.review_status, ReviewStatus.PENDING)
        self.assertEqual(c.proposal.transcript_refs, ["seg_0001", "seg_0002"])
        self.assertEqual(c.proposal.clip_id, "clip_0001")

    def test_hallucinated_timestamps_are_ignored(self):
        raw = json.dumps({
            "candidates": [{
                "segment_ids": ["seg_0001"],
                "title": "T",
                "start_seconds": 999,
                "end_seconds": 1000,
                "evidence_text": "texto uno comun",
            }]
        })
        candidates, _ = self._parse(raw)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].proposal.start_seconds, 0.0)
        self.assertEqual(candidates[0].proposal.end_seconds, 30.0)

    def test_context_segment_rejected(self):
        _, rejected = self._parse(candidate_response(["ctx_0001"]))
        self.assertEqual(rejected[0]["reason"], "segment_out_of_body")

    def test_unknown_segment_rejected(self):
        _, rejected = self._parse(candidate_response(["seg_9999"]))
        self.assertEqual(rejected[0]["reason"], "segment_out_of_body")

    def test_evidence_not_found_rejected(self):
        _, rejected = self._parse(candidate_response(["seg_0001"], evidence="frase inventada"))
        self.assertEqual(rejected[0]["reason"], "evidence_not_found")

    def test_missing_title_rejected(self):
        _, rejected = self._parse(candidate_response(["seg_0001"], title="   "))
        self.assertEqual(rejected[0]["reason"], "missing_title")

    def test_duration_out_of_range_rejected(self):
        _, rejected = self._parse(
            candidate_response(["seg_short"], evidence="corto texto"),
            body_ids=["seg_short"],
        )
        self.assertEqual(rejected[0]["reason"], "duration_out_of_range")

    def test_duration_exception_accepts_with_warning(self):
        config = EditorialConfig(allow_duration_exception=True)
        factory, _ = counter_factory()
        candidates, rejected = parse_model_response(
            candidate_response(["seg_short"], evidence="corto texto"),
            source_asset_id="asset-1",
            window_id="win_0001",
            body_ids=["seg_short"],
            segments_by_id=self.by_id,
            config=config,
            duration_seconds=60.0,
            clip_id_factory=factory,
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].proposal.warnings)
        self.assertTrue(candidates[0].limitations)

    def test_clip_id_not_consumed_by_rejection(self):
        raw = json.dumps({
            "candidates": [
                {"segment_ids": ["ctx_0001"], "title": "mala", "evidence_text": "x"},
                {"segment_ids": ["seg_0001", "seg_0002"], "title": "buena",
                 "evidence_text": "texto uno comun texto dos comun"},
            ]
        })
        candidates, rejected = self._parse(raw)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].proposal.clip_id, "clip_0001")
        self.assertEqual(rejected[0]["reason"], "segment_out_of_body")

    def test_window_limit_caps_candidates(self):
        config = EditorialConfig(max_candidates_per_window=1)
        many = {"candidates": [
            {"segment_ids": ["seg_0001", "seg_0002"], "title": "a",
             "evidence_text": "texto uno comun texto dos comun", "confidence": 0.5},
            {"segment_ids": ["seg_0001", "seg_0002"], "title": "b",
             "evidence_text": "texto uno comun texto dos comun", "confidence": 0.4},
        ]}
        factory, _ = counter_factory()
        candidates, rejected = parse_model_response(
            json.dumps(many),
            source_asset_id="asset-1",
            window_id="win_0001",
            body_ids=self.body_ids,
            segments_by_id=self.by_id,
            config=config,
            duration_seconds=60.0,
            clip_id_factory=factory,
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(any(r["reason"] == "window_limit" for r in rejected))

    def test_observed_signal_requires_verifiable_evidence(self):
        good = candidate_response(
            ["seg_0001", "seg_0002"],
            signals=[{"name": "hook", "status": "observed",
                      "evidence": "texto uno comun", "note": ""}],
        )
        candidates, _ = self._parse(good)
        self.assertEqual(candidates[0].signals[0].status, SignalStatus.OBSERVED)

        bad = candidate_response(
            ["seg_0001", "seg_0002"],
            signals=[{"name": "hook", "status": "observed",
                      "evidence": "evidencia falsa", "note": ""}],
        )
        candidates, _ = self._parse(bad)
        self.assertEqual(candidates[0].signals[0].status, SignalStatus.UNKNOWN)

    def test_empty_candidates_is_valid(self):
        candidates, rejected = self._parse(json.dumps({"candidates": []}))
        self.assertEqual(candidates, [])
        self.assertEqual(rejected, [])

    def test_invalid_json_raises(self):
        with self.assertRaises(EditorialInvalidResponseError):
            self._parse("no hay json aqui")

    def test_extract_json_from_code_fence(self):
        raw = "```json\n{\"candidates\": []}\n```"
        self.assertEqual(extract_json_object(raw), {"candidates": []})


class SelectionTests(unittest.TestCase):
    def _make(self, clip_id, start, end, *, title, confidence, evidence=None):
        proposal = ClipProposal(
            clip_id=clip_id,
            source_asset_id="asset-1",
            start_seconds=start,
            end_seconds=end,
            title=title,
            confidence=confidence,
            transcript_refs=["seg_0001"],
        )
        return EditorialCandidate(
            proposal=proposal,
            duration_seconds=end - start,
            evidence_text=evidence if evidence is not None else title,
            originated_from_window="win_0001",
        )

    def test_text_similarity_bounds(self):
        self.assertEqual(text_similarity("hola mundo", "hola mundo"), 1.0)
        self.assertEqual(text_similarity("hola", "mundo"), 0.0)

    def test_overlap_ratio(self):
        a = self._make("clip_0001", 0, 30, title="a", confidence=0.5)
        b = self._make("clip_0002", 15, 45, title="b", confidence=0.5)
        self.assertAlmostEqual(overlap_ratio(a, b), 0.5)

    def test_dedupe_keeps_higher_confidence(self):
        weak = self._make("clip_0001", 0, 30, title="idea repetida", confidence=0.3)
        strong = self._make("clip_0002", 5, 35, title="idea repetida", confidence=0.9)
        kept, removed = deduplicate(
            [weak, strong], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].proposal.clip_id, "clip_0002")
        self.assertIn("clip_0001", kept[0].superseded_candidate_ids)
        self.assertEqual(removed[0]["clip_id"], "clip_0001")

    def test_non_overlapping_not_deduped(self):
        a = self._make("clip_0001", 0, 30, title="uno", confidence=0.5)
        b = self._make("clip_0002", 30, 60, title="dos", confidence=0.5)
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    def test_rank_is_deterministic_priority(self):
        c1 = self._make("clip_0001", 0, 30, title="a", confidence=0.4)
        c2 = self._make("clip_0002", 30, 60, title="b", confidence=0.9)
        c3 = self._make("clip_0003", 60, 90, title="c", confidence=0.9)
        ranked = rank_candidates([c1, c2, c3])
        self.assertEqual([c.clip_id for c in ranked], ["clip_0002", "clip_0003", "clip_0001"])
        self.assertEqual([c.rank for c in ranked], [1, 2, 3])

    def test_total_limit(self):
        candidates = [
            self._make(f"clip_{i:04d}", i * 20, i * 20 + 20, title=f"t{i}", confidence=0.5)
            for i in range(5)
        ]
        kept, removed = apply_total_limit(candidates, max_total=2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(removed), 3)
        self.assertTrue(all(r["reason"] == "total_limit" for r in removed))


class SelectionPolicyTests(unittest.TestCase):
    """Regresiones P1 (orden/no transitividad), P2 (ganador) y P3 (similitud).

    Reproducen los defectos detectados en la auditoria de Fase 3:
    - P1: el greedy dependia del orden y dejaba escapar duplicados.
    - P2: el ganador se elegia solo por la confianza del modelo.
    - P3: la similitud incluia la evidencia (fusionaba propuestas distintas)
      y no cubria la parafrasis del mismo momento. Politica B1: el intervalo
      identico ya no basta; exige tambien evidencia literal similar.
    """

    def _make(
        self,
        clip_id,
        start,
        end,
        *,
        title="Titulo",
        confidence=0.5,
        evidence="evidencia literal",
        refs=("seg_0001",),
        hook="",
        main_idea="",
        ending="",
        editorial_reason="",
    ):
        proposal = ClipProposal(
            clip_id=clip_id,
            source_asset_id="asset-1",
            start_seconds=start,
            end_seconds=end,
            title=title,
            hook=hook,
            main_idea=main_idea,
            ending=ending,
            editorial_reason=editorial_reason,
            confidence=confidence,
            transcript_refs=list(refs),
        )
        return EditorialCandidate(
            proposal=proposal,
            duration_seconds=end - start,
            evidence_text=evidence,
            originated_from_window="win_0001",
        )

    @staticmethod
    def _ids(candidates):
        return sorted(c.proposal.clip_id for c in candidates)

    @staticmethod
    def _removed_ids(removed):
        return sorted(str(item["clip_id"]) for item in removed)

    def _invariant(self, factories, **kwargs):
        """Ejecuta todas las permutaciones y exige resultados identicos."""
        results = []
        for order in itertools.permutations(range(len(factories))):
            candidates = [factories[i]() for i in order]
            kept, removed = deduplicate(candidates, **kwargs)
            results.append((self._ids(kept), self._removed_ids(removed)))
        first = results[0]
        for result in results:
            self.assertEqual(result, first)
        return first

    # ------------------------------------------------------------------
    # P1
    # ------------------------------------------------------------------

    def test_dedup_order_independent_all_permutations(self):
        def a():
            return self._make("clip_0001", 0, 30, title="idea unica", confidence=0.3)

        def b():
            return self._make("clip_0002", 0, 30, title="idea unica", confidence=0.9)

        def c():
            return self._make("clip_0003", 0, 30, title="idea unica", confidence=0.6)

        kept_ids, removed_ids = self._invariant(
            [a, b, c], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(kept_ids, ["clip_0002"])
        self.assertEqual(removed_ids, ["clip_0001", "clip_0003"])

    def test_non_transitive_chain_does_not_merge(self):
        # A~B y B~C pero A!~C: solo se retira el dominado (B).
        def a():
            return self._make("clip_0001", 0, 30, title="alfa beta gamma", confidence=0.9)

        def b():
            return self._make(
                "clip_0002", 2, 32, title="alfa beta gamma delta", confidence=0.5
            )

        def c():
            return self._make(
                "clip_0003", 4, 34,
                title="alfa beta gamma delta epsilon", confidence=0.9,
            )

        kept_ids, removed_ids = self._invariant(
            [a, b, c], overlap_ratio_threshold=0.5, text_similarity_threshold=0.7
        )
        self.assertEqual(kept_ids, ["clip_0001", "clip_0003"])
        self.assertEqual(removed_ids, ["clip_0002"])

        # Trazabilidad: el sobreviviente A registra a B como reemplazado.
        kept, _ = deduplicate(
            [a(), b(), c()],
            overlap_ratio_threshold=0.5,
            text_similarity_threshold=0.7,
        )
        winner = next(c for c in kept if c.proposal.clip_id == "clip_0001")
        self.assertEqual(winner.superseded_candidate_ids, ["clip_0002"])

    def test_monotone_chain_extreme_survives_all_permutations(self):
        # A > B > C con A~B, B~C y A!~C. El extremo C no debe caer por la
        # cadena: sobreviven A y C; B lo reemplaza A (duplicado directo).
        def a():
            return self._make("clip_0001", 0, 30, title="alfa beta gamma", confidence=0.9)

        def b():
            return self._make(
                "clip_0002", 2, 32, title="alfa beta gamma delta", confidence=0.7
            )

        def c():
            return self._make(
                "clip_0003", 4, 34,
                title="alfa beta gamma delta epsilon", confidence=0.5,
            )

        kept_ids, removed_ids = self._invariant(
            [a, b, c], overlap_ratio_threshold=0.5, text_similarity_threshold=0.7
        )
        self.assertEqual(kept_ids, ["clip_0001", "clip_0003"])
        self.assertEqual(removed_ids, ["clip_0002"])

        kept, removed = deduplicate(
            [a(), b(), c()],
            overlap_ratio_threshold=0.5,
            text_similarity_threshold=0.7,
        )
        winner = next(c for c in kept if c.proposal.clip_id == "clip_0001")
        self.assertEqual(winner.superseded_candidate_ids, ["clip_0002"])
        self.assertEqual(
            removed,
            [
                {
                    "reason": "duplicate",
                    "detail": "clip_0002 reemplazado por clip_0001",
                    "clip_id": "clip_0002",
                }
            ],
        )

    def test_winner_is_direct_duplicate(self):
        # Todo ganador registrado debe ser duplicado DIRECTO del descartado,
        # nunca un simple "mas preferible" alcanzado por la cadena.
        def a():
            return self._make("clip_0001", 0, 30, title="alfa beta gamma", confidence=0.9)

        def b():
            return self._make(
                "clip_0002", 2, 32, title="alfa beta gamma delta", confidence=0.7
            )

        def c():
            return self._make(
                "clip_0003", 4, 34,
                title="alfa beta gamma delta epsilon", confidence=0.5,
            )

        kept, removed = deduplicate(
            [a(), b(), c()],
            overlap_ratio_threshold=0.5,
            text_similarity_threshold=0.7,
        )
        survivor_ids = {x.proposal.clip_id for x in kept}
        fresh = {"clip_0001": a(), "clip_0002": b(), "clip_0003": c()}
        for entry in removed:
            winner_id = str(entry["detail"]).split("reemplazado por ")[1]
            self.assertIn(winner_id, survivor_ids)
            self.assertTrue(
                is_duplicate(
                    fresh[winner_id],
                    fresh[str(entry["clip_id"])],
                    overlap_ratio_threshold=0.5,
                    text_similarity_threshold=0.7,
                )
            )

    def test_duplicate_does_not_escape_by_order(self):
        # Caso de la auditoria: el greedy dejaba [Z, Y] en un orden.
        def x():
            return self._make("clip_0001", 0, 30, title="alfa beta gamma", confidence=0.5)

        def y():
            return self._make(
                "clip_0002", 2, 32, title="alfa beta gamma delta", confidence=0.9
            )

        def z():
            return self._make(
                "clip_0003", 4, 34,
                title="alfa beta gamma delta epsilon", confidence=0.7,
            )

        kept_ids, removed_ids = self._invariant(
            [x, y, z], overlap_ratio_threshold=0.5, text_similarity_threshold=0.7
        )
        self.assertEqual(kept_ids, ["clip_0002"])
        self.assertEqual(removed_ids, ["clip_0001", "clip_0003"])

    def test_removed_keep_references_and_reasons(self):
        weak = self._make("clip_0001", 0, 30, title="misma idea", confidence=0.2)
        strong = self._make("clip_0002", 0, 30, title="misma idea", confidence=0.8)
        kept, removed = deduplicate(
            [weak, strong], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 1)
        entry = removed[0]
        self.assertEqual(entry["reason"], "duplicate")
        self.assertEqual(entry["clip_id"], "clip_0001")
        self.assertIn("clip_0002", entry["detail"])
        self.assertIn("clip_0001", kept[0].superseded_candidate_ids)

    def test_ties_resolved_deterministically(self):
        # Identicos salvo el clip_id: gana el clip_id menor en cualquier orden.
        def one():
            return self._make("clip_0001", 0, 30, title="empate", confidence=0.5)

        def two():
            return self._make("clip_0002", 0, 30, title="empate", confidence=0.5)

        kept_ids, removed_ids = self._invariant(
            [one, two], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(kept_ids, ["clip_0001"])
        self.assertEqual(removed_ids, ["clip_0002"])

    # ------------------------------------------------------------------
    # P2
    # ------------------------------------------------------------------

    def test_more_complete_beats_higher_confidence(self):
        complete = self._make(
            "clip_0001", 0, 30, title="propuesta completa", confidence=0.3,
            hook="gancho", main_idea="idea", ending="cierre", editorial_reason="razon",
        )
        empty = self._make("clip_0002", 0, 30, title="propuesta completa", confidence=0.9)

        def _complete():
            return self._make(
                "clip_0001", 0, 30, title="propuesta completa", confidence=0.3,
                hook="gancho", main_idea="idea", ending="cierre",
                editorial_reason="razon",
            )

        def _empty():
            return self._make(
                "clip_0002", 0, 30, title="propuesta completa", confidence=0.9
            )

        kept_ids, _ = self._invariant(
            [_complete, _empty],
            overlap_ratio_threshold=0.5,
            text_similarity_threshold=0.8,
        )
        self.assertEqual(kept_ids, ["clip_0001"])

        kept, removed = deduplicate(
            [complete, empty],
            overlap_ratio_threshold=0.5,
            text_similarity_threshold=0.8,
        )
        self.assertEqual(kept[0].proposal.clip_id, "clip_0001")
        self.assertEqual(removed[0]["clip_id"], "clip_0002")

    def test_invalid_never_wins_over_valid(self):
        invalid = self._make(
            "clip_0001", 0, 30, title="sin referencias", confidence=0.9,
            hook="gancho", main_idea="idea", ending="cierre",
            editorial_reason="razon", refs=(),
        )
        valid = self._make(
            "clip_0002", 0, 30, title="con referencias", confidence=0.1,
            refs=("seg_0001",),
        )
        kept, removed = deduplicate(
            [invalid, valid], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual([c.proposal.clip_id for c in kept], ["clip_0002"])
        self.assertEqual(removed[0]["clip_id"], "clip_0001")

    def test_confidence_used_after_completeness(self):
        # Misma completitud: decide la confianza.
        def high():
            return self._make(
                "clip_0001", 0, 30, title="misma idea", confidence=0.9,
                hook="h", main_idea="m",
            )

        def low():
            return self._make(
                "clip_0002", 0, 30, title="misma idea", confidence=0.4,
                hook="h", main_idea="m",
            )

        kept_ids, _ = self._invariant(
            [high, low], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(kept_ids, ["clip_0001"])

    # ------------------------------------------------------------------
    # P3
    # ------------------------------------------------------------------

    def test_paraphrase_same_span_deduped(self):
        # Mismo intervalo y redaccion distinta, pero la MISMA evidencia literal
        # respalda que es el mismo momento (politica B1).
        evidence = "el mercado sube rapido al abrir la sesion de hoy"
        a = self._make(
            "clip_0001", 0, 30, title="el error que todos cometen",
            evidence=evidence, confidence=0.5, refs=("seg_0001",),
        )
        b = self._make(
            "clip_0002", 0, 30, title="una confusion muy comun",
            evidence=evidence, confidence=0.6, refs=("seg_0002",),
        )
        self.assertLess(text_similarity(a.proposal.title, b.proposal.title), 0.8)
        self.assertGreaterEqual(
            text_similarity(a.evidence_text, b.evidence_text), 0.8
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual([c.proposal.clip_id for c in kept], ["clip_0002"])
        self.assertEqual(removed[0]["clip_id"], "clip_0001")

    def test_shared_evidence_distinct_approaches_kept(self):
        shared = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce"
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            hook="gancho financiero", evidence=shared, confidence=0.5,
        )
        b = self._make(
            "clip_0002", 10, 40, title="receta de cocina",
            hook="gancho culinario", evidence=shared, confidence=0.5,
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(self._ids(kept), ["clip_0001", "clip_0002"])
        self.assertEqual(removed, [])

    def test_temporally_close_but_independent_kept(self):
        a = self._make("clip_0001", 0, 30, title="mercado financiero")
        b = self._make("clip_0002", 2, 32, title="receta de cocina")
        self.assertGreaterEqual(overlap_ratio(a, b), 0.5)
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    def test_clear_duplicates_deduped(self):
        def a():
            return self._make(
                "clip_0001", 0, 30, title="el secreto del exito",
                hook="h", main_idea="m", confidence=0.4,
            )

        def b():
            return self._make(
                "clip_0002", 0, 30, title="el secreto del exito",
                hook="h", main_idea="m", confidence=0.7,
            )

        kept_ids, removed_ids = self._invariant(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(kept_ids, ["clip_0002"])
        self.assertEqual(removed_ids, ["clip_0001"])

    def test_ambiguous_preserved(self):
        # Similitud por debajo del umbral y spans distintos: se conservan ambos.
        a = self._make("clip_0001", 0, 30, title="clave del marketing digital")
        b = self._make("clip_0002", 4, 34, title="marketing digital avanzado")
        sim = text_similarity(a.proposal.title, b.proposal.title)
        self.assertLess(sim, 0.8)
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    def test_same_interval_distinct_approaches_kept(self):
        # Mismo intervalo, enfoques editoriales distintos y evidencia distinta:
        # el intervalo identico NO basta (politica B1), se conservan ambos.
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            evidence="uno dos tres cuatro", confidence=0.5,
        )
        b = self._make(
            "clip_0002", 0, 30, title="receta de cocina",
            evidence="siete ocho nueve diez", confidence=0.6,
        )
        self.assertLess(text_similarity(a.proposal.title, b.proposal.title), 0.8)
        self.assertLess(text_similarity(a.evidence_text, b.evidence_text), 0.8)
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(self._ids(kept), ["clip_0001", "clip_0002"])
        self.assertEqual(removed, [])

    def test_same_interval_evidence_below_threshold_kept(self):
        # Mismo intervalo y evidencia parcialmente compartida por debajo del
        # umbral: caso ambiguo, se conservan ambos.
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            evidence="uno dos tres cuatro", confidence=0.5,
        )
        b = self._make(
            "clip_0002", 0, 30, title="receta de cocina",
            evidence="uno dos cinco seis", confidence=0.6,
        )
        self.assertLess(text_similarity(a.evidence_text, b.evidence_text), 0.8)
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])


class SimilarityEdgeCaseTests(unittest.TestCase):
    """Casos limite de similitud textual y de la regla B1.

    Distingue casos ALCANZABLES por las interfaces publicas de casos que las
    validaciones del contrato vuelven INALCANZABLES. No define reglas
    editoriales nuevas ni cambia umbrales: fija el comportamiento actual del
    contrato para estas entradas.
    """

    def _make(self, clip_id, start, end, *, title, evidence):
        proposal = ClipProposal(
            clip_id=clip_id,
            source_asset_id="asset-1",
            start_seconds=start,
            end_seconds=end,
            title=title,
            confidence=0.5,
            transcript_refs=["seg_0001"],
        )
        return EditorialCandidate(
            proposal=proposal,
            duration_seconds=end - start,
            evidence_text=evidence,
            originated_from_window="win_0001",
        )

    # -- text_similarity puro -----------------------------------------

    def test_text_similarity_empty_vs_empty_is_one(self):
        # Contrato de text_similarity: dos conjuntos de palabras vacios se
        # consideran iguales (1.0); un texto vacio frente a uno no vacio es 0.0.
        self.assertEqual(text_similarity("", ""), 1.0)
        self.assertEqual(text_similarity("", "hola"), 0.0)
        self.assertEqual(text_similarity("hola", ""), 0.0)

    def test_empty_evidence_is_unreachable_at_candidate_level(self):
        # EditorialCandidate exige evidence_text no vacio (ni solo espacios),
        # por lo que "evidencia vacia" NO es alcanzable construyendo
        # candidatos por la via publica normal. Se prueba la inalcanzabilidad.
        with self.assertRaises(ValidationError):
            self._make("clip_0001", 0, 30, title="titulo", evidence="")
        with self.assertRaises(ValidationError):
            self._make("clip_0002", 0, 30, title="titulo", evidence="   ")

    def test_text_similarity_punctuation_only_has_no_word_tokens(self):
        # La tokenizacion ignora la puntuacion: dos textos solo-puntuacion
        # tienen ambos conjunto vacio de palabras => similitud 1.0.
        self.assertEqual(text_similarity("...", "!!!"), 1.0)

    def test_punctuation_only_evidence_same_span_is_duplicate(self):
        # ALCANZABLE: evidence_text solo-puntuacion pasa la validacion (no
        # vacio) y, con el mismo intervalo, B1 la trata como evidencia
        # identica (ambos conjuntos de palabras vacios). Comportamiento
        # actual coherente con text_similarity, aunque de baja severidad.
        a = self._make("clip_0001", 0, 30, title="mercado alcista", evidence="...")
        b = self._make("clip_0002", 0, 30, title="receta de cocina", evidence="!!!")
        self.assertLess(text_similarity(a.proposal.title, b.proposal.title), 0.8)
        self.assertTrue(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 1)

    # -- palabras identicas frente a disjuntas ------------------------

    def test_identical_evidence_words_same_span_dedupes(self):
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            evidence="alfa beta gamma delta",
        )
        b = self._make(
            "clip_0002", 0, 30, title="receta de cocina",
            evidence="alfa beta gamma delta",
        )
        self.assertEqual(text_similarity(a.evidence_text, b.evidence_text), 1.0)
        self.assertTrue(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )

    def test_disjoint_evidence_words_same_span_kept(self):
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            evidence="alfa beta gamma delta",
        )
        b = self._make(
            "clip_0002", 0, 30, title="receta de cocina",
            evidence="uno dos tres cuatro",
        )
        self.assertEqual(text_similarity(a.evidence_text, b.evidence_text), 0.0)
        self.assertFalse(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    # -- evidencia similar pero no identica ---------------------------

    def test_evidence_similar_above_threshold_dedupes(self):
        # 9 palabras comunes de 11 en la union => 9/11 ~= 0.818 >= 0.8.
        a = self._make(
            "clip_0001", 0, 30, title="tema alfa",
            evidence="w1 w2 w3 w4 w5 w6 w7 w8 w9 w10",
        )
        b = self._make(
            "clip_0002", 0, 30, title="tema beta",
            evidence="w2 w3 w4 w5 w6 w7 w8 w9 w10 w11",
        )
        sim = text_similarity(a.evidence_text, b.evidence_text)
        self.assertGreaterEqual(sim, 0.8)
        self.assertLess(sim, 1.0)
        self.assertTrue(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )

    def test_evidence_similar_below_threshold_kept(self):
        # 8 palabras comunes de 12 en la union => 8/12 ~= 0.667 < 0.8.
        a = self._make(
            "clip_0001", 0, 30, title="tema alfa",
            evidence="w1 w2 w3 w4 w5 w6 w7 w8 w9 w10",
        )
        b = self._make(
            "clip_0002", 0, 30, title="tema beta",
            evidence="w3 w4 w5 w6 w7 w8 w9 w10 w11 w12",
        )
        sim = text_similarity(a.evidence_text, b.evidence_text)
        self.assertLess(sim, 0.8)
        self.assertFalse(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])

    # -- mismo intervalo, evidencia identica, enfoques distintos ------

    def test_same_interval_identical_evidence_distinct_approaches_merged(self):
        # LIMITACION CONOCIDA (B1): con el mismo intervalo, la evidencia
        # literal identica basta para declarar duplicidad aunque el enfoque
        # editorial difiera (regla 2). Se fija aqui el comportamiento actual
        # como limitacion aceptada, no como comportamiento deseado.
        a = self._make(
            "clip_0001", 0, 30, title="mercado alcista",
            evidence="texto citado de la fuente",
        )
        b = self._make(
            "clip_0002", 0, 30, title="receta de cocina",
            evidence="texto citado de la fuente",
        )
        self.assertLess(text_similarity(a.proposal.title, b.proposal.title), 0.8)
        self.assertTrue(
            is_duplicate(
                a, b, overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
            )
        )
        kept, removed = deduplicate(
            [a, b], overlap_ratio_threshold=0.5, text_similarity_threshold=0.8
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
