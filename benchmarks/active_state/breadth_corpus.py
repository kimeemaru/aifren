"""Large deterministic natural-language scene-mutation adversarial corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import time

from benchmarks.active_state.harness import SyntheticSession


@dataclass(frozen=True)
class CorpusReport:
    version: str
    cases: int
    positive_cases: int
    abstention_cases: int
    passed: int
    failed: int
    duration_ms: float
    failure_categories: dict[str, int]


_PREFIXES = (
    "", "Oh, ", "Okay, ", "Well, ", "Actually, ", "So, ",
    "By the way, ", "Oh, okay, ",
)
_ESTABLISH = (
    "I blindfold you.",
    "I cover your eyes with my hands.",
    "Your mouth is full.",
    "I cover your mouth with my hand.",
    "My hand is over your mouth.",
    "The room is pitch black.",
    "The smoke makes it hard to see.",
    "The music is so loud you can't hear me.",
    "It is too loud to hear me.",
    "The perfume makes it hard to smell.",
    "The spice makes it impossible to taste.",
    "The numbness makes it impossible to feel.",
    "I put earplugs in your ears.",
    "I cover your left ear.",
    "I cover your right ear.",
    "I cover both of your ears.",
    "Your left arm is missing.",
    "Your right arm is unavailable.",
    "Your both arms are missing.",
    "Your left wrist is handcuffed to a pole.",
    "Your right wrist is tethered to a rail.",
    "You're wearing a black dress, a red scarf, and boots.",
    "You’re wearing a blue hat and a red scarf.",
    "You've got a blue hat and a red scarf on.",
    "You're wearing a left glove and a right glove.",
    "You're wearing a pair of shoes.",
    "You're holding a cup and a book.",
    "You're holding a cup in your left hand and a book in your right hand.",
    "I'm holding a lantern.",
    "I'm carrying two boxes.",
    "You're blindfolded and carrying two boxes.",
    "A bicycle is nearby.",
    "A wheelchair is nearby.",
    "You're holding rollerblades.",
    "You're wearing rollerblades.",
    "You get on a bicycle.",
    "You mount a horse.",
    "You're in a wheelchair.",
    "You're using crutches.",
    "I'm riding a bicycle.",
    "I'm driving a car.",
    "I'm using crutches.",
    "I'm in a wheelchair.",
    "Your sparkly scrunchie is on your wrist.",
    "Your blue and purple sparkly scrunchie is on your left wrist.",
    "I put a blue hat on you.",
    "You're wearing a red hat.",
    "You're wearing a red scarf.",
    "You're holding the handcuffs.",
    "The blindfold is in your hand.",
    "The rollerblades are beside you.",
    "The earplugs are on the table.",
    "A red lantern is nearby.",
    "Sit down.",
    "Stand up.",
    "Lie down.",
)
_CLEAR = (
    (("I blindfold you.",), "Remove the blindfold."),
    (("I cover your eyes with my hands.",), "I take my hands off your eyes."),
    (("I cover your mouth with my hand.",), "I uncover your mouth."),
    (("I put earplugs in your ears.",), "I remove the earplugs."),
    (("The music is so loud you can't hear me.",), "The music stops."),
    (("It is too loud to hear me.",), "It quiets down."),
    (("The smoke makes it hard to see.",), "The smoke clears."),
    (("The room is pitch black.",), "The lights come on."),
    (("Your left wrist is handcuffed to a pole.",), "Release the left handcuff."),
    (("You're holding a cup.",), "Put the cup down."),
    (("You're holding a cup.",), "Give me the cup."),
    (("You're in a wheelchair.",), "You get out of the wheelchair."),
    (("You get on a bicycle.",), "You get off the bicycle."),
    (("You're wearing a red scarf.",), "Take off the scarf."),
    (("Your left arm is missing.",), "Your left arm is available again."),
    (("The perfume makes it hard to smell.",), "The perfume clears."),
    (("I hand you a red lantern.", "Put the red lantern down."), "Throw the red lantern away."),
)

_NEGATIVE_CONTRASTS = (
    "Is your hat red?",
    "I wonder what would happen if I put the blindfold on you.",
    "My hand is near your mouth.",
    "The music is loud.",
    "Could the music be too loud to hear me?",
    "You're not holding the handcuffs.",
    "You're not handcuffed to the pole.",
    "You aren't wearing the rollerblades.",
    "The wheelchair might be nearby.",
    "Maybe your left arm is missing.",
    "Suppose your right wrist were tethered to a rail.",
    "If I covered your left ear, what would happen?",
    "What if the room were pitch black?",
    "I said that your hat is red, but I was quoting someone.",
    "Sarcasm aside, perhaps you're holding a cup.",
    "You're wearing a hat, or maybe not.",
    "I cover you hands with my eyes.",
    "Your left wrist is near a pole.",
    "A bicycle exists somewhere.",
    "You look as if you might be sitting.",
    "Are you in a wheelchair?",
    "Did I put a blue hat on you?",
    "Don't put on the boots.",
    "Please don't hold the cup.",
    "I almost covered your mouth.",
    "I was going to blindfold you.",
    "Someone else wears a red scarf.",
)


def planned_case_count() -> int:
    """Return the stable corpus size without executing storage fixtures."""
    return (
        len(_PREFIXES) * len(_ESTABLISH)
        + len(_ESTABLISH)
        + 4 * len(_CLEAR)
        + 10 * len(_ESTABLISH)
        + 4 * len(_NEGATIVE_CONTRASTS)
    )


def run() -> CorpusReport:
    started = time.perf_counter()
    failures: dict[str, int] = {}
    passed = 0
    positive = 0
    abstentions = 0

    def execute(case_id: str, setup: tuple[str, ...], text: str, expect_change: bool) -> None:
        nonlocal passed, positive, abstentions
        session = SyntheticSession(case_id)
        try:
            for turn in setup:
                session.turn(turn)
            before = session.structural_snapshot()
            result = session.turn(text)
            after = session.structural_snapshot()
            changed = before != after
            if expect_change:
                positive += 1
                valid = result.get("state") in {"applied", "unchanged"} and changed
            else:
                abstentions += 1
                valid = not changed
            if valid:
                passed += 1
            else:
                category = "expected_mutation" if expect_change else "expected_abstention"
                failures[category] = failures.get(category, 0) + 1
        finally:
            session.close()

    case_index = 0
    for prefix in _PREFIXES:
        for statement in _ESTABLISH:
            case_index += 1
            execute(f"establish-{case_index:03d}", (), prefix + statement, True)
    for statement in _ESTABLISH:
        case_index += 1
        execute(f"emote-{case_index:03d}", (), "*" + statement + "*", True)
    for prefix in _PREFIXES[:4]:
        for setup, statement in _CLEAR:
            case_index += 1
            execute(f"clear-{case_index:03d}", setup, prefix + statement, True)

    negative_templates = (
        lambda value: "What if " + value[0].lower() + value[1:],
        lambda value: "Imagine that " + value[0].lower() + value[1:],
        lambda value: "Hypothetically, " + value,
        lambda value: '"' + value + '"',
        lambda value: value.rstrip(".") + "?",
        lambda value: "I wonder what would happen if " + value[0].lower() + value[1:],
        lambda value: "Maybe " + value[0].lower() + value[1:],
        lambda value: "Someone said, \"" + value + "\"",
        lambda value: "Not true: " + value[0].lower() + value[1:],
        lambda value: "In a hypothetical story, " + value[0].lower() + value[1:],
    )
    for template in negative_templates:
        for statement in _ESTABLISH:
            case_index += 1
            execute(f"negative-{case_index:03d}", (), template(statement), False)

    for prefix in _PREFIXES[:4]:
        for statement in _NEGATIVE_CONTRASTS:
            case_index += 1
            execute(f"contrast-{case_index:04d}", (), prefix + statement, False)

    total = positive + abstentions
    if total != planned_case_count():
        raise AssertionError("natural-language corpus cardinality drifted")
    return CorpusReport(
        "active-scene-natural-language-corpus-v2", total, positive, abstentions,
        passed, total - passed, round((time.perf_counter() - started) * 1000.0, 3),
        failures,
    )


def main() -> None:
    print(json.dumps(asdict(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
