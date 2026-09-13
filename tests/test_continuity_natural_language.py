"""Large synthetic matrix for bounded natural-language continuity proposals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aifren.state.continuity_intent import interpret_continuity_intents
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter
from aifren.memory_v2_store import MemoryV2Repository
from aifren.memory_v2_store.repository import ActiveStateRecord, OpenThreadRecord, TruthScopeRecord


def _scope(kind: str, label: str, identifier: str) -> TruthScopeRecord:
    return TruthScopeRecord(identifier, "character", kind, label, "active", 1, 1)


REAL_WORLD = _scope("real_world", "Real world", "scope-real")
POKEMON = _scope("scenario", "Pokemon world", "scope-pokemon")
GENSOKYO = _scope("scenario", "Silvervale", "scope-silvervale")


def _activity(value: str) -> ActiveStateRecord:
    return ActiveStateRecord(
        "activity-current", "character", "actor.user.activity", value, "active",
        1, None, 1, (), "scope-real",
    )


def _thread(identifier: str, kind: str, description: str) -> OpenThreadRecord:
    return OpenThreadRecord(
        identifier, "character", kind, "user", description, None, "open",
        1, 1, None, (), "scope-real",
    )


GPU = _thread("thread-gpu", "waiting", "waiting for GPU delivery")
RAM = _thread("thread-ram", "waiting", "waiting for RAM shipment")
PLEX = _thread("thread-plex", "plan_or_intention", "set Plex up")


class NaturalLanguageIntentMatrixTests(unittest.TestCase):
    def _intent(
        self, text: str, *, scope: TruthScopeRecord = REAL_WORLD,
        scenarios: tuple[TruthScopeRecord, ...] = (POKEMON, GENSOKYO),
        activity: ActiveStateRecord | None = None,
        threads: tuple[OpenThreadRecord, ...] = (),
    ) -> tuple[str, ...]:
        decision = interpret_continuity_intents(
            text, active_scope=scope, scenario_scopes=scenarios,
            current_activity=activity, current_threads=threads,
        )
        return tuple(item.intent for item in decision.intents)

    def test_clear_paraphrase_matrix(self):
        fillers = ("", "Okay, ", "yeah ")
        entries = tuple(prefix + phrase for prefix in fillers for phrase in (
            "let's roleplay that we're in Silvervale.",
            "let's role play where we're on a spaceship.",
            "let's do an RP where we are on Mars.",
            "let's pretend we're fantasy adventurers for a while.",
            "let's start a role play in Pokemon!",
            "pretend we're explorers on an alien planet.",
        )) + (
            "Can we roleplay that we're in Silvervale?",
            "Could we do an RP where we're on a space station?",
            "Let's roleplay as wandering detectives.",
            "Let's pretend that we're in a haunted mansion.",
        )
        exits = tuple(prefix + phrase for prefix in fillers for phrase in (
            "let's go back to real life.",
            "let's return to the real world.",
            "let's leave the Pokemon world.",
            "I'm done with the RP.",
            "let's stop roleplaying.",
            "back to reality.",
            "let's leave this scenario.",
        )) + (
            "Can we stop this roleplay?",
            "Could we return to real life?",
            "End the current RP, please.",
        )
        reentries = (
            "Let's resume the Silvervale roleplay.",
            "Let's continue our Silvervale scenario.",
            "Go back to the Silvervale RP.",
            "Can we resume our Silvervale roleplay?",
            "Let's reenter the Silvervale scenario.",
            "Let's continue our roleplay.",
        )
        activity_starts = (
            "I'm gonna play Noita for a bit.", "Going to play Noita now.",
            "I will play Noita for a while.", "I'm off to play Noita.",
            "I am playing Noita now.", "I'm about to watch Alien.",
            "Going to watch The Expanse for a bit.", "I will watch Arrival now.",
            "I'm installing Arch Linux right now.", "Going to install the GPU driver.",
            "I will install those updates now.", "Going to bed now.",
            "I'm heading to bed.", "I am going to sleep now.",
            "I'm heading out.", "Going out for a bit.", "I'm going out now.",
            "I'm working now.", "I am working for a while.",
        )
        activity_clears = (
            ("I'm done playing.", "playing Noita"),
            ("I stopped playing.", "playing Noita"),
            ("I quit playing Noita.", "playing Noita"),
            ("I'm finished playing the game.", "playing Noita"),
            ("I'm no longer playing Noita.", "playing Noita"),
            ("I stopped watching the movie.", "watching Alien"),
            ("I'm done watching Alien.", "watching Alien"),
            ("I finished watching that show.", "watching The Expanse"),
            ("I finished installing it.", "installing the GPU driver"),
            ("I completed installing the GPU driver.", "installing the GPU driver"),
            ("I'm back.", "away"), ("Back now.", "away"),
            ("I'm back home.", "working"), ("I'm back now.", "sleeping"),
        )
        waiting_opens = (
            "Still waiting on that GPU.", "I'm awaiting the GPU delivery.",
            "Waiting on my replacement keyboard.", "I await the package.",
            "I'm waiting for the repair technician.", "Still awaiting my refund.",
            "yeah, waiting on that monitor.", "Okay, I'm waiting on a response.",
        )
        plans = (
            "I'm probably going to upgrade the RAM.", "I plan to install Plex.",
            "I intend to replace the case fans.", "I might try to repair the keyboard.",
            "I'm thinking about upgrading the SSD.", "We should configure backups.",
            "I decided to reorganize the desk.", "I'm thinking about replacing the router.",
        )
        reconfirms = (
            "Still waiting on that GPU.", "I'm still awaiting the GPU delivery.",
            "Yep, still waiting for my GPU.", "Okay, waiting on the GPU still.",
            "I'm awaiting that GPU.", "Still waiting for the delivery.",
        )
        resolutions = (
            "The GPU finally arrived.", "My GPU got delivered.",
            "The GPU showed up.", "It finally arrived.",
            "I finished setting Plex up.", "I completed the Plex setup.",
            "Finished configuring Plex.", "Plex is set up.",
        )
        cancellations = (
            "Forget the Plex thing.", "Cancel the Plex plan.",
            "Drop the Plex setup.", "Actually I'm not doing Plex anymore.",
            "I'm not doing that anymore.", "Forget that plan.",
        )

        for text in entries:
            with self.subTest(group="entry", text=text):
                self.assertEqual(("enter_scenario",), self._intent(text))
        for text in exits:
            with self.subTest(group="exit", text=text):
                self.assertEqual(("exit_scenario",), self._intent(text, scope=POKEMON))
        for text in reentries:
            with self.subTest(group="reentry", text=text):
                scenarios = (GENSOKYO,) if "our roleplay" in text else (POKEMON, GENSOKYO)
                self.assertEqual(("reenter_scenario",), self._intent(text, scenarios=scenarios))
        for text in activity_starts:
            with self.subTest(group="activity_start", text=text):
                self.assertEqual(("start_activity",), self._intent(text))
                self.assertEqual(("change_activity",), self._intent(text, activity=_activity("playing Quake")))
        for text, current in activity_clears:
            with self.subTest(group="activity_clear", text=text):
                expected = "return_from_activity" if current in {"away", "working", "sleeping"} else "clear_activity"
                self.assertEqual((expected,), self._intent(text, activity=_activity(current)))
        for text in waiting_opens:
            with self.subTest(group="waiting_open", text=text):
                self.assertEqual(("open_waiting_thread",), self._intent(text))
        for text in plans:
            with self.subTest(group="plan_open", text=text):
                self.assertEqual(("open_plan_thread",), self._intent(text))
        for text in reconfirms:
            with self.subTest(group="reconfirm", text=text):
                self.assertEqual(("reconfirm_thread",), self._intent(text, threads=(GPU,)))
        for text in resolutions:
            with self.subTest(group="resolve", text=text):
                threads = (GPU,) if "GPU" in text or text.startswith("It ") else (PLEX,)
                self.assertEqual(("resolve_thread",), self._intent(text, threads=threads))
        for text in cancellations:
            with self.subTest(group="cancel", text=text):
                self.assertEqual(("cancel_thread",), self._intent(text, threads=(PLEX,)))

        positive_count = (
            len(entries) + len(exits) + len(reentries) + (2 * len(activity_starts))
            + len(activity_clears) + len(waiting_opens) + len(plans)
            + len(reconfirms) + len(resolutions) + len(cancellations)
        )
        self.assertEqual(140, positive_count)

    def test_adversarial_abstention_matrix(self):
        ordinary_negatives = (
            "Pokemon has a weird world.",
            "This novel is set in Silvervale.", "I like roleplaying games.",
            "We discussed an RP campaign yesterday.", "The movie is on a spaceship.",
            "I died while playing the game.", "Noita is a fantasy world.",
            "That tabletop scenario sounds fun.", "Roleplay mechanics can be complicated.",
            "My friend runs a Pokemon campaign.", "The astronauts pretend in the movie.",
            "The book asks us to imagine Mars.", "I read about adventurers.",
        )
        fiction_activities = ("I'm playing Noita.", "I'm watching Alien.")
        hypothetical_or_quoted = (
            "What if we lived on Mars?", "If we were in Silvervale, what would happen?",
            "Imagine we're adventurers.", "Suppose we roleplayed on a ship.",
            "Hypothetically, let's say we lived in Pokemon.",
            'She said "let\'s roleplay in Silvervale."',
            '"Let\'s go back to real life."', "Quote: let's stop roleplaying.",
            "How about a story set in Silvervale?", "What is roleplaying?",
            "Let's not roleplay in Silvervale.", "Don't stop this roleplay.",
            "Actually, no, let's not enter an RP.", "I thought about roleplaying on Mars.",
        )
        activity_negatives = (
            "I was playing Noita yesterday.", "I might play Noita later.",
            "I'm not playing Noita.", "I never watch Alien.",
            "I used to install Linux often.", "Tomorrow I will watch Alien.",
            "Next week I will install Plex.", "My occupation is game developer.",
            "I work as a shrine maiden.", "Playing games can be relaxing.",
            "Are you playing Noita?", "Should I watch Alien?",
            "I won't play Noita now.", "I was going to play Noita, but not now.",
            "I play Noita every weekend.", "I watch Alien every year.",
            "I install software for work.", "I played Noita earlier.",
        )
        lifecycle_without_referent = (
            "I'm done.", "It arrived.", "Forget it.", "Cancel that.",
            "I finished.", "Let's leave there.", "Back.",
            "Actually, not anymore.", "That is done.",
            "The GPU did not arrive.", "Don't forget the Plex plan.",
            "Did it arrive", "Has the GPU arrived", "I wonder if it arrived",
            "Maybe the GPU arrived.", "I think the GPU arrived.",
            "Perhaps I finished Plex.",
            "Let's continue our roleplay.",
        )
        scenario_context_negatives = (
            "Let's go back to real life.", "Back to reality.",
            "Let's leave this movie discussion.", "Let's leave the Pokemon world.",
        )
        mismatched_lifecycle = (
            "I stopped watching Alien.", "I finished installing it.",
            "I'm done working.", "I quit playing Quake.",
        )

        for text in ordinary_negatives + hypothetical_or_quoted + activity_negatives + lifecycle_without_referent:
            with self.subTest(group="ordinary_abstain", text=text):
                self.assertEqual((), self._intent(text))
        for text in fiction_activities:
            with self.subTest(group="activity_not_scenario", text=text):
                intents = self._intent(text)
                self.assertEqual(("start_activity",), intents)
                self.assertNotIn("enter_scenario", intents)
        for text in scenario_context_negatives:
            with self.subTest(group="scenario_context", text=text):
                self.assertEqual((), self._intent(text, scope=REAL_WORLD))
        for text in mismatched_lifecycle:
            with self.subTest(group="mismatched_activity", text=text):
                self.assertEqual((), self._intent(text, activity=_activity("playing Noita")))
        for text in ("It arrived.", "The package arrived.", "Forget that plan.", "I'm done."):
            with self.subTest(group="multiple_thread_ambiguity", text=text):
                self.assertEqual((), self._intent(text, threads=(GPU, RAM)))
        self.assertEqual(
            77,
            len(ordinary_negatives) + len(hypothetical_or_quoted) + len(activity_negatives)
            + len(fiction_activities) + len(lifecycle_without_referent) + len(scenario_context_negatives)
            + len(mismatched_lifecycle) + 4,
        )


class NaturalLanguageMultiTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.character_id = str(uuid.uuid4())
        self.memory_file = self.root / "memories.json"
        self.memory_file.write_text("[]", encoding="utf-8")
        self.conversation_file = self.root / "conversation.json"
        self.rows: list[dict[str, object]] = []
        self.base = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        self.writer = MemoryV2ShadowWriter(
            self.root, character_id=self.character_id, display_name="Synthetic",
            memory_file=self.memory_file,
        )
        self.repository = MemoryV2Repository(self.writer.store)
        self.repository.ensure_character(self.character_id, "Synthetic", legacy_config_key="characters/default")

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def _turn(self, text: str) -> dict[str, object]:
        index = len(self.rows)
        message = {
            "role": "user", "content": text,
            "timestamp": (self.base + timedelta(minutes=index)).isoformat(),
        }
        self.rows.extend((message, {
            "role": "assistant", "content": "Synthetic response.",
            "timestamp": (self.base + timedelta(minutes=index, seconds=1)).isoformat(),
        }))
        self.conversation_file.write_text(json.dumps(self.rows), encoding="utf-8")
        return self.writer.observe_canonical_user_continuity(
            message, conversation_index=index, conversation_file=self.conversation_file,
        )

    def _scope(self) -> TruthScopeRecord:
        return self.repository.active_truth_scope(self.character_id)

    def _activity(self) -> ActiveStateRecord | None:
        return self.repository.lookup_actor_state(self.character_id, "user", "activity").state

    def _threads(self) -> tuple[OpenThreadRecord, ...]:
        return self.repository.list_open_threads(self.character_id).threads

    def test_required_conversational_sequences(self):
        first = self._turn("Let's role play that we're in Silvervale.")
        self.assertEqual("applied", first["state"])
        self.assertEqual("scenario", self._scope().kind)
        self._turn("Okay, let's go back to real life.")
        self.assertEqual("real_world", self._scope().kind)
        reentered = self._turn("Can we resume our Silvervale roleplay?")
        self.assertEqual("semantic", reentered["extraction_method"])
        self.assertEqual("Silvervale", self._scope().label)
        self._turn("Back to reality.")

        started = self._turn("I'm gonna play Noita.")
        self.assertEqual("semantic", started["extraction_method"])
        self.assertEqual("applied", started["extraction_outcome"])
        self.assertEqual("playing Noita", self._activity().value)
        death = self._turn("I died again.")
        self.assertEqual("ignored", death["state"])
        self.assertEqual("abstained", death["extraction_outcome"])
        self.assertEqual("playing Noita", self._activity().value)
        self._turn("I'm done playing.")
        self.assertIsNone(self._activity())

        self._turn("I'm waiting on my GPU.")
        self._turn("The weather is nice.")
        self._turn("It finally arrived.")
        self.assertEqual((), self._threads())

        self._turn("I'm awaiting my GPU.")
        self._turn("I'm awaiting my RAM.")
        ambiguous = self._turn("It arrived.")
        self.assertEqual("ignored", ambiguous["state"])
        self.assertEqual(2, len(self._threads()))

        self._turn("Let's start a role play in Pokemon.")
        self.assertEqual("Pokemon", self._scope().label)
        self._turn("Let's leave the Pokemon world.")
        self.assertEqual("real_world", self._scope().kind)

        # The existing deterministic activity grammar applies the activity;
        # ordinary discussion of leaving it must never toggle truth scope.
        self._turn("I'm watching Alien.")
        result = self._turn("Let's leave this movie discussion.")
        self.assertEqual("ignored", result["state"])
        self.assertEqual("real_world", self._scope().kind)

        self._turn("Let's pretend we're on Mars.")
        self.assertEqual("scenario", self._scope().kind)
        self._turn("Back to reality.")
        self.assertEqual("real_world", self._scope().kind)
        hypothetical = self._turn("What if we lived on Mars?")
        self.assertEqual("ignored", hypothetical["state"])
        self.assertEqual("real_world", self._scope().kind)


if __name__ == "__main__":
    unittest.main()
