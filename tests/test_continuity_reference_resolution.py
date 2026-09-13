"""Full-pipeline sequence matrix for bounded continuity reference resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import (
    MemoryV2Repository,
    OpenThreadProposal,
    OpenThreadProposalOperation,
)


GPU_OPENINGS = (
    "I'm waiting for my GPU.",
    "I'm still waiting on that graphics card.",
    "Waiting on the GPU delivery.",
    "I'm awaiting my video card.",
)
GPU_RECONFIRMATIONS = (
    "Still waiting on that GPU.",
    "I'm awaiting the graphics card.",
    "Yep, still waiting for my GPU.",
    "I'm still waiting on the card.",
    "That graphics card still hasn't arrived.",
    "The GPU still hasn't shown up.",
)
RECEIPT_FORMS = (
    "It finally arrived.",
    "It came today.",
    "It showed up.",
    "Oh, it finally showed up. I'll go get it.",
    "I got it.",
    "Yeah, I got it now.",
    "Finally got the thing.",
    "It was delivered.",
)
CANCELLATION_FORMS = (
    "Forget that.",
    "Cancel it.",
    "Drop that plan.",
    "Actually I'm not doing that anymore.",
)
DIRECT_RP_FORMS = (
    "Why don't we pretend we're hanging out in Silvervale for a while?",
    "Why don't we pretend we're in Silvervale?",
    "why dont we roleplay in silvervale",
    "Why don't we role play that we're in Silvervale?",
    "Why don't we start an RP in Silvervale?",
    "Why don't we pretend we're adventurers for a bit?",
    "Why don't we pretend we're on a spaceship?",
    "why dont we do a scenario in pokemon",
)
RP_ENACTMENTS = (
    "Let's roleplay.",
    "Let's do an RP.",
    "Can we roleplay?",
    "Could we start an RP?",
    "Okay, let's start a scenario.",
)
RP_LABELS = ("Silvervale.", "Pokemon.")
ADVERSARIAL_SINGLE_TURNS = (
    "I'm gonna mess around and wait up for a bit.",
    "Wait up, I forgot my keys.",
    "What if we lived in Silvervale?",
    "If we roleplayed in Pokemon, what would happen?",
    "Pokemon is a roleplaying game.",
    "The movie asks why we don't pretend we're astronauts.",
    'She said "Why don\'t we roleplay in Silvervale?"',
    '"Let\'s roleplay."',
    "I got it.",
    "It showed up.",
    "Finally got the thing.",
    "Still nothing.",
    "Forget that.",
    "The graphics card is fast.",
    "My GPU driver needs an update.",
    "Why don't we discuss Silvervale?",
    "Why don't we watch Pokemon?",
    "Suppose we pretended we were on Mars.",
    "Imagine that we're in Silvervale.",
)


class ContinuityHarness:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.character_id = str(uuid.uuid4())
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.rows: list[dict[str, object]] = []
        self.base = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        self.writer: MemoryV2ShadowWriter
        self.repository: MemoryV2Repository
        self._open_writer()

    def _open_writer(self) -> None:
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id, display_name="Synthetic",
            memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(
            self.character_id, "Synthetic", legacy_config_key="characters/default",
        )

    def close(self) -> None:
        self.writer.close()
        self.temp.cleanup()

    def restart(self) -> None:
        self.writer.close()
        self._open_writer()

    def turn(self, text: str, *, assistant: str = "Synthetic response.") -> dict[str, object]:
        scope = self.repository.active_truth_scope(self.character_id)
        provenance = {"kind": scope.kind, "scope_id": scope.truth_scope_id}
        index = len(self.rows)
        message = {
            "role": "user", "content": text,
            "timestamp": (self.base + timedelta(minutes=index)).isoformat(),
            "truth_scope": provenance,
        }
        self.rows.extend((message, {
            "role": "assistant", "content": assistant,
            "timestamp": (self.base + timedelta(minutes=index, seconds=1)).isoformat(),
            "truth_scope": provenance,
        }))
        self.conversation_file.write_text(json.dumps(self.rows), encoding="utf-8")
        return self.writer.observe_canonical_user_continuity(
            message, conversation_index=index, conversation_file=self.conversation_file,
        )

    def threads(self, *, scope_id: str | None = None):
        return self.repository.list_open_threads(
            self.character_id, truth_scope_id=scope_id,
        ).threads

    def evidence(self, thread_id: str):
        return self.repository.list_open_thread_evidence(self.character_id, thread_id)

    def seed_duplicate_waiting_threads(self) -> tuple[str, str]:
        scope = self.repository.active_truth_scope(self.character_id)
        identifiers = []
        for offset, (content, description) in enumerate((
            ("I'm waiting for my graphics card.", "waiting for graphics card"),
            ("I'm waiting for my GPU.", "waiting for GPU"),
        ), start=1):
            event_id = str(uuid.uuid4())
            self.writer.store.add_event(
                self.character_id, event_id, offset,
                event_type="canonical_user_message", actor_kind="user",
                recorded_at_us=1_700_000_000_000_000 + offset,
                temporal_precision="instant", content_text=content,
                source_origin="synthetic_fixture", source_reference=f"fixture#{offset}",
            )
            update = self.writer.store.apply_open_thread_proposal(
                self.character_id,
                OpenThreadProposal((OpenThreadProposalOperation(
                    "open", f"new{offset}", 0, len(content), "waiting", "user", description,
                ),)),
                evidence_event_id=event_id, truth_scope_id=scope.truth_scope_id,
            )
            identifiers.append(str(update[0]["thread_id"]))
        return identifiers[0], identifiers[1]


class FullPipelineContinuityReferenceTests(unittest.TestCase):
    def _harness(self) -> ContinuityHarness:
        return ContinuityHarness()

    def test_gpu_alias_cross_product_reconfirms_one_record(self):
        for opening in GPU_OPENINGS:
            for reconfirmation in GPU_RECONFIRMATIONS:
                with self.subTest(opening=opening, reconfirmation=reconfirmation):
                    harness = self._harness()
                    first = harness.turn(opening)
                    self.assertEqual("applied", first["state"])
                    original = harness.threads()[0]
                    original_description = original.description
                    second = harness.turn(reconfirmation)
                    self.assertEqual("applied", second["state"])
                    self.assertIn("reconfirm_thread", second["extraction_intents"])
                    current = harness.threads()
                    self.assertEqual(1, len(current))
                    self.assertEqual(original.thread_id, current[0].thread_id)
                    self.assertEqual(original_description, current[0].description)
                    evidence = harness.evidence(original.thread_id)
                    self.assertEqual(("thread_open", "thread_reconfirm"), tuple(item.evidence_role for item in evidence))
                    self.assertEqual((opening, reconfirmation), tuple(item.content for item in evidence))
                    harness.close()

    def test_action_scope_and_restart_boundaries(self):
        action_pairs = (
            ("I'm waiting for my GPU to arrive.", "I'm waiting for the GPU driver install to finish."),
            ("I'm waiting for my graphics card.", "I plan to install the graphics driver."),
            ("I plan to install Plex.", "I'm probably going to replace Plex."),
            ("I'm waiting for the monitor delivery.", "I plan to repair the monitor."),
        )
        for first_text, second_text in action_pairs:
            with self.subTest(boundary="action", first=first_text, second=second_text):
                harness = self._harness()
                harness.turn(first_text)
                harness.turn(second_text)
                self.assertEqual(2, len(harness.threads()))
                harness.close()

        for opening in GPU_OPENINGS:
            with self.subTest(boundary="restart", opening=opening):
                harness = self._harness()
                harness.turn(opening)
                identifier = harness.threads()[0].thread_id
                harness.restart()
                harness.turn("Still waiting on that graphics card.")
                self.assertEqual((identifier,), tuple(item.thread_id for item in harness.threads()))
                self.assertEqual(2, len(harness.evidence(identifier)))
                harness.close()

        for label in ("Silvervale", "Pokemon", "a spaceship", "a haunted mansion"):
            with self.subTest(boundary="scope", label=label):
                harness = self._harness()
                real_id = harness.repository.active_truth_scope(harness.character_id).truth_scope_id
                harness.turn("I'm waiting for my GPU.")
                real_thread = harness.threads()[0]
                harness.turn(f"Let's roleplay that we're in {label}.")
                scenario_id = harness.repository.active_truth_scope(harness.character_id).truth_scope_id
                harness.turn("I'm waiting for my graphics card.")
                scenario_thread = harness.threads()[0]
                self.assertNotEqual(real_thread.thread_id, scenario_thread.thread_id)
                self.assertEqual(1, len(harness.threads(scope_id=real_id)))
                self.assertEqual(1, len(harness.threads(scope_id=scenario_id)))
                harness.close()

    def test_receipt_coreference_with_and_without_intervening_turns(self):
        for phrase in RECEIPT_FORMS:
            for intervening in ((), ("The weather is nice.", "I made some tea.")):
                with self.subTest(phrase=phrase, intervening=intervening):
                    harness = self._harness()
                    harness.turn("I'm waiting for my graphics card.")
                    identifier = harness.threads()[0].thread_id
                    for text in intervening:
                        harness.turn(text)
                    result = harness.turn(phrase)
                    self.assertEqual("applied", result["state"])
                    self.assertIn("resolve_thread", result["extraction_intents"])
                    self.assertEqual((), harness.threads())
                    record = harness.repository.get_open_thread(harness.character_id, identifier)
                    self.assertEqual("resolved", record.status)
                    self.assertEqual("thread_resolve", harness.evidence(identifier)[-1].evidence_role)
                    harness.close()

    def test_reconfirmation_cancellation_and_ambiguity(self):
        for phrase in ("Still nothing.", "Nothing yet.", "No sign yet.", "Yeah, still nothing."):
            with self.subTest(operation="reconfirm", phrase=phrase):
                harness = self._harness()
                harness.turn("I'm waiting for my GPU.")
                identifier = harness.threads()[0].thread_id
                harness.turn(phrase)
                self.assertEqual((identifier,), tuple(item.thread_id for item in harness.threads()))
                self.assertEqual("thread_reconfirm", harness.evidence(identifier)[-1].evidence_role)
                harness.close()

        for phrase in CANCELLATION_FORMS:
            with self.subTest(operation="cancel", phrase=phrase):
                harness = self._harness()
                harness.turn("I'm probably going to install Plex.")
                identifier = harness.threads()[0].thread_id
                result = harness.turn(phrase)
                self.assertEqual("applied", result["state"])
                self.assertEqual((), harness.threads())
                self.assertEqual("cancelled", harness.repository.get_open_thread(harness.character_id, identifier).status)
                harness.close()

        ambiguous = RECEIPT_FORMS[:4] + CANCELLATION_FORMS
        for phrase in ambiguous:
            with self.subTest(operation="ambiguous", phrase=phrase):
                harness = self._harness()
                harness.turn("I'm waiting for my GPU.")
                harness.turn("I'm waiting for my RAM.")
                result = harness.turn(phrase)
                self.assertEqual("ignored", result["state"])
                self.assertEqual(2, len(harness.threads()))
                harness.close()

    def test_existing_semantic_duplicates_resolve_as_one_logical_target(self):
        for phrase in RECEIPT_FORMS[:4]:
            with self.subTest(phrase=phrase):
                harness = self._harness()
                first_id, second_id = harness.seed_duplicate_waiting_threads()
                result = harness.turn(phrase)
                self.assertEqual("applied", result["state"])
                self.assertEqual(2, result["open_thread_updates"])
                self.assertEqual((), harness.threads())
                self.assertEqual("resolved", harness.repository.get_open_thread(harness.character_id, first_id).status)
                self.assertEqual("resolved", harness.repository.get_open_thread(harness.character_id, second_id).status)
                harness.close()

    def test_correction_evolves_derived_identity_without_rewriting_description(self):
        corrections = (
            "No, I meant the graphics card.",
            "Actually, I meant my graphics card.",
            "No I meant the GPU.",
            "Actually I meant that video card.",
        )
        for correction in corrections:
            with self.subTest(correction=correction):
                harness = self._harness()
                opening = "I'm waiting for my display adapter."
                harness.turn(opening)
                original = harness.threads()[0]
                harness.turn(correction)
                harness.turn("Still waiting on the GPU delivery.")
                current = harness.threads()
                self.assertEqual(1, len(current))
                self.assertEqual(original.thread_id, current[0].thread_id)
                self.assertEqual("waiting for my display adapter", current[0].description)
                self.assertEqual(
                    (opening, correction, "Still waiting on the GPU delivery."),
                    tuple(item.content for item in harness.evidence(original.thread_id)),
                )
                harness.close()

    def test_rp_direct_and_split_discourse(self):
        for phrase in DIRECT_RP_FORMS:
            with self.subTest(kind="direct", phrase=phrase):
                harness = self._harness()
                result = harness.turn(phrase)
                self.assertEqual("applied", result["state"])
                self.assertEqual("scenario", harness.repository.active_truth_scope(harness.character_id).kind)
                harness.close()

        for enactment in RP_ENACTMENTS:
            for label in RP_LABELS:
                with self.subTest(kind="enactment_then_label", enactment=enactment, label=label):
                    harness = self._harness()
                    first = harness.turn(enactment)
                    self.assertEqual("ignored", first["state"])
                    second = harness.turn(label)
                    self.assertEqual("applied", second["state"])
                    self.assertEqual(label.rstrip("."), harness.repository.active_truth_scope(harness.character_id).label)
                    harness.close()

        for label in RP_LABELS:
            for enactment in RP_ENACTMENTS[:2]:
                with self.subTest(kind="label_then_enactment", enactment=enactment, label=label):
                    harness = self._harness()
                    self.assertEqual("ignored", harness.turn(label)["state"])
                    self.assertEqual("applied", harness.turn(enactment)["state"])
                    self.assertEqual(label.rstrip("."), harness.repository.active_truth_scope(harness.character_id).label)
                    harness.close()

        correction_sequences = (
            ("Let's do an RP.", "No, I meant Silvervale."),
            ("Silvervale.", "Okay, let's role play that."),
            ("Silvervale.", "Yeah, let's do that one."),
            ("uh lets roleplay", "okay silvervale"),
        )
        for first_text, second_text in correction_sequences:
            with self.subTest(kind="correction_or_reference", first=first_text, second=second_text):
                harness = self._harness()
                harness.turn(first_text)
                result = harness.turn(second_text)
                self.assertEqual("scenario", harness.repository.active_truth_scope(harness.character_id).kind)
                self.assertEqual("applied", result["state"])
                harness.close()

    def test_rp_discourse_rebuilds_after_restart_and_stops_at_scope_boundary(self):
        harness = self._harness()
        self.assertEqual("ignored", harness.turn("Let's roleplay.")["state"])
        harness.restart()
        self.assertEqual("applied", harness.turn("Silvervale.")["state"])
        self.assertEqual("Silvervale", harness.repository.active_truth_scope(harness.character_id).label)
        harness.close()

        harness = self._harness()
        self.assertEqual("ignored", harness.turn("Silvervale.")["state"])
        harness.turn("Let's roleplay that we're in Pokemon.")
        self.assertEqual("scenario", harness.repository.active_truth_scope(harness.character_id).kind)
        harness.turn("Back to real life.")
        self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
        result = harness.turn("Let's roleplay.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
        harness.close()

    def test_real_player_rp_repair_sequence_uses_the_full_bounded_user_frame(self):
        harness = self._harness()
        harness.turn("Let's roleplay that we're in Silvervale.")
        harness.turn("Back to reality.")
        self.assertEqual("ignored", harness.turn("So I want to role play something.")["state"])
        for misheard in (
            "Again secure again",
            "Again, so, kill.",
            "Sorry, I said get so killed again.",
        ):
            with self.subTest(misheard=misheard):
                self.assertEqual("ignored", harness.turn(misheard)["state"])
                self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)

        result = harness.turn("sorry I meant silvervale again. you misheard")
        self.assertEqual("applied", result["state"])
        self.assertEqual(("enter_scenario",), result["extraction_intents"])
        self.assertTrue(result["scope_changed"])
        scope = harness.repository.active_truth_scope(harness.character_id)
        self.assertEqual("scenario", scope.kind)
        self.assertEqual("Silvervale", scope.label)
        harness.close()

    def test_rp_desire_declaration_and_deictic_forms_use_structural_grammar(self):
        direct_forms = (
            "We are roleplaying Silvervale.",
            "okay so we are roleplaying silvervale",
            "I want to roleplay in Silvervale.",
            "We want to role play in Silvervale.",
        )
        for text in direct_forms:
            with self.subTest(kind="direct", text=text):
                harness = self._harness()
                result = harness.turn(text)
                self.assertEqual("applied", result["state"])
                self.assertEqual("silvervale", harness.repository.active_truth_scope(harness.character_id).label.casefold())
                harness.close()

        split_forms = (
            ("I want to roleplay something.", "Silvervale."),
            ("Silvervale.", "okay so we're rping there now"),
        )
        for first, second in split_forms:
            with self.subTest(kind="split", first=first, second=second):
                harness = self._harness()
                self.assertEqual("ignored", harness.turn(first)["state"])
                self.assertEqual("applied", harness.turn(second)["state"])
                self.assertEqual("Silvervale", harness.repository.active_truth_scope(harness.character_id).label)
                harness.close()

    def test_rp_desire_and_declaration_neighbors_abstain(self):
        negatives = (
            "I want to play a roleplaying game.",
            "I want to talk about roleplaying.",
            "We are discussing roleplaying games.",
            "I want to talk about Silvervale.",
            "They are roleplaying Silvervale.",
            "My friends want to roleplay Silvervale.",
            "We are not roleplaying Silvervale.",
        )
        for text in negatives:
            with self.subTest(text=text):
                harness = self._harness()
                self.assertEqual("ignored", harness.turn(text)["state"])
                self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
                harness.close()

        harness = self._harness()
        self.assertEqual("ignored", harness.turn("Silvervale.")["state"])
        self.assertEqual("ignored", harness.turn("I want to talk about roleplaying.")["state"])
        self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
        harness.close()

        harness = self._harness()
        self.assertEqual("ignored", harness.turn("I want to roleplay something.")["state"])
        for unrelated in ("My GPU arrived.", "I made some tea.", "The weather is pleasant."):
            self.assertEqual("ignored", harness.turn(unrelated)["state"])
        self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
        harness.close()

    def test_adversarial_sequences_abstain_without_authoritative_mutation(self):
        for text in ADVERSARIAL_SINGLE_TURNS:
            with self.subTest(kind="single", text=text):
                harness = self._harness()
                result = harness.turn(text)
                self.assertEqual("ignored", result["state"])
                self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
                self.assertEqual((), harness.threads())
                harness.close()

        stale_or_ambiguous = (
            ("Let's roleplay.", (
                "The weather is pleasant today.", "I made a cup of tea.",
                "My desk needs some cleaning.", "The window is open now.",
                "I found an old notebook.", "This music sounds very calm.",
            ), "Silvervale."),
            ("Silvervale.", ("Pokemon.",), "Okay, let's roleplay that."),
            ("Let's discuss roleplaying games.", ("Silvervale.",), "Yeah, that one."),
        )
        for first, middle, last in stale_or_ambiguous:
            with self.subTest(kind="stale_or_ambiguous", first=first, last=last):
                harness = self._harness()
                harness.turn(first)
                for text in middle:
                    harness.turn(text)
                result = harness.turn(last)
                self.assertEqual("ignored", result["state"])
                self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
                harness.close()

        harness = self._harness()
        harness.turn("Sounds good.", assistant="Let's roleplay in Silvervale.")
        result = harness.turn("Okay, let's do that.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("real_world", harness.repository.active_truth_scope(harness.character_id).kind)
        harness.close()

    def test_matrix_contains_at_least_one_hundred_full_pipeline_sequences(self):
        declared = (
            len(GPU_OPENINGS) * len(GPU_RECONFIRMATIONS)
            + 4 + len(GPU_OPENINGS) + 4
            + len(RECEIPT_FORMS) * 2
            + 4 + len(CANCELLATION_FORMS) + 8
            + 4 + 4
            + len(DIRECT_RP_FORMS)
            + len(RP_ENACTMENTS) * len(RP_LABELS)
            + len(RP_LABELS) * 2 + 4
            + len(ADVERSARIAL_SINGLE_TURNS) + 3 + 1
        )
        self.assertEqual(125, declared)


class ReferenceProvenanceTests(unittest.TestCase):
    def test_reconfirmation_keeps_exact_source_hashes_and_restart_resolution(self):
        harness = ContinuityHarness()
        self.addCleanup(harness.close)
        opening = "I'm waiting for my graphics card."
        reconfirmation = "So I'm waiting for that GPU."
        harness.turn(opening)
        identifier = harness.threads()[0].thread_id
        harness.turn(reconfirmation)
        evidence = harness.evidence(identifier)
        rows = harness.writer.store.connection.execute(
            """SELECT e.content_text, ce.excerpt_start_cp, ce.excerpt_end_cp, ce.excerpt_hash
                 FROM claim_evidence ce JOIN events e
                   ON e.character_id=ce.character_id AND e.event_id=ce.event_id
                WHERE ce.character_id=? AND ce.claim_id=? ORDER BY ce.created_at_us""",
            (harness.character_id, identifier),
        ).fetchall()
        self.assertEqual(2, len(evidence))
        for row in rows:
            excerpt = row["content_text"][row["excerpt_start_cp"]:row["excerpt_end_cp"]]
            self.assertEqual(hashlib.sha256(excerpt.encode("utf-8")).hexdigest(), row["excerpt_hash"])
        canonical_before = json.loads(harness.conversation_file.read_text(encoding="utf-8"))
        harness.restart()
        harness.turn("It finally arrived.")
        canonical_after = json.loads(harness.conversation_file.read_text(encoding="utf-8"))
        self.assertEqual(canonical_before, canonical_after[:len(canonical_before)])
        record = harness.repository.get_open_thread(harness.character_id, identifier)
        self.assertEqual("resolved", record.status)
        self.assertEqual(("thread_open", "thread_reconfirm", "thread_resolve"), tuple(
            item.evidence_role for item in harness.evidence(identifier)
        ))


if __name__ == "__main__":
    unittest.main()
