"""Deterministic adversarial Active State integration matrices.

The runners use canonical synthetic conversation turns through the production
continuity writer/store.  They are test harnesses, not product simulation, and
emit structural counts only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import json
import time

from benchmarks.active_state.harness import SyntheticSession


@dataclass(frozen=True)
class AdversarialReport:
    version: str
    cases: int
    checks: int
    passed: int
    failed: int
    duration_ms: float
    failure_categories: dict[str, int]


@dataclass(frozen=True)
class _Constraint:
    name: str
    establish: str
    clear: str
    field: str
    constrained: str
    restored: str


_CONSTRAINTS = (
    _Constraint("vision", "I blindfold you.", "Remove the blindfold.",
                "vision_mode", "unavailable", "available"),
    _Constraint("hearing", "I put earplugs in your ears.", "I remove the earplugs.",
                "hearing_mode", "constrained", "normal"),
    _Constraint("speech", "I cover your mouth with my hand.", "I uncover your mouth.",
                "speech_mode", "constrained", "normal"),
    _Constraint("touch", "The numbness makes it impossible to feel.", "The numbness clears.",
                "touch_mode", "unavailable", "normal"),
    _Constraint("hands", "You're carrying two boxes.", "Put the boxes down.",
                "hands_mode", "occupied", "free"),
    _Constraint("body", "Your left arm is missing.", "Your left arm is available again.",
                "left_arm_mode", "unavailable", "normal"),
    _Constraint("locomotion", "You're in a wheelchair.", "You get out of the wheelchair.",
                "locomotion_mode", "assisted", "walking"),
    _Constraint("awareness", "Go to sleep.", "Wake up.",
                "awareness_mode", "asleep", "normal"),
    _Constraint("posture", "Sit down.", "Stand up.",
                "posture_mode", "sitting", "standing"),
)


def run_capability_combinations() -> AdversarialReport:
    started = time.perf_counter()
    failures: dict[str, int] = {}
    passed = 0
    checks = 0

    def expect(condition: bool, category: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(category)

    def execute(case_id: str, callback) -> None:
        nonlocal passed
        session = SyntheticSession("adversarial-capability-" + case_id)
        try:
            callback(session)
            passed += 1
        except Exception as error:
            category = str(error) or type(error).__name__
            failures[category] = failures.get(category, 0) + 1
        finally:
            session.close()

    for first, second in combinations(_CONSTRAINTS, 2):
        def pair(session: SyntheticSession, a=first, b=second) -> None:
            session.turn(a.establish)
            session.turn(b.establish)
            effects = session.effects()
            expect(getattr(effects, a.field) == a.constrained, a.name + "_established")
            expect(getattr(effects, b.field) == b.constrained, b.name + "_established")
            session.turn(a.clear)
            effects = session.effects()
            expected_a = (
                "constrained" if a.name == "hands" and b.name == "body"
                else a.restored
            )
            expect(getattr(effects, a.field) == expected_a, a.name + "_restored")
            expect(getattr(effects, b.field) == b.constrained, b.name + "_preserved")
            session.turn(b.clear)
            expect(getattr(session.effects(), b.field) == b.restored, b.name + "_restored")
        execute(first.name + "-" + second.name, pair)

    def vision_causes(session: SyntheticSession) -> None:
        session.turn("The room is pitch black.")
        session.turn("I blindfold you.")
        expect(set(session.effects().vision_causes) == {"darkness", "blindfold"}, "vision_two_causes")
        session.turn("The lights come on.")
        expect(session.effects().vision_mode == "unavailable", "vision_first_clear")
        expect(session.effects().vision_causes == ("blindfold",), "vision_remaining_cause")
        session.turn("Remove the blindfold.")
        expect(session.effects().vision_mode == "available", "vision_final_clear")
    execute("vision-multiple-causes", vision_causes)

    def hearing_causes(session: SyntheticSession) -> None:
        session.turn("I put earplugs in your ears.")
        session.turn("The music is so loud you can't hear me.")
        expect(session.effects().hearing_mode == "unavailable", "hearing_strongest")
        session.turn("The music stops.")
        expect(session.effects().hearing_mode == "constrained", "hearing_remaining")
        session.turn("I remove the earplugs.")
        expect(session.effects().hearing_mode == "normal", "hearing_final_clear")
    execute("hearing-multiple-causes", hearing_causes)

    def speech_causes(session: SyntheticSession) -> None:
        session.turn("Your mouth is full.")
        session.turn("I cover your mouth with my hand.")
        expect(set(session.effects().speech_causes) == {"mouth contents", "user hand"}, "speech_two_causes")
        session.turn("I uncover your mouth.")
        expect(session.effects().speech_mode == "constrained", "speech_remaining")
        session.turn("Your mouth is clear now.")
        expect(session.effects().speech_mode == "normal", "speech_final_clear")
    execute("speech-multiple-causes", speech_causes)

    def maximal_composition(session: SyntheticSession) -> None:
        for constraint in _CONSTRAINTS:
            session.turn(constraint.establish)
        effects = session.effects()
        for constraint in _CONSTRAINTS:
            expect(
                getattr(effects, constraint.field) == constraint.constrained,
                "maximal_" + constraint.name,
            )
        session.restart()
        restarted = session.effects()
        expect(restarted.prompt_payload() == effects.prompt_payload(), "maximal_restart")
        for constraint in reversed(_CONSTRAINTS):
            session.turn(constraint.clear)
    execute("maximal-composed-restart", maximal_composition)

    cases = len(tuple(combinations(_CONSTRAINTS, 2))) + 4
    return AdversarialReport(
        "active-state-capability-combinations-v1", cases, checks,
        passed, cases - passed, round((time.perf_counter() - started) * 1000.0, 3),
        failures,
    )


def main() -> None:
    print(json.dumps(asdict(run_capability_combinations()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
