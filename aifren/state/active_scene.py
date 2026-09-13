"""Deterministic sparse scene/equipment extraction for Active State.

This module recognizes only explicit current user evidence. It emits bounded
subject and relation proposals; it never writes storage or simulates unmentioned
world state.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from aifren.memory_v2_store import (
    ActiveSceneSubjectIntroduction,
    ActiveSceneSubjectReactivation,
    ActiveSceneSubjectRetirement,
    ActiveStateProposal,
    ActiveStateProposalUpdate,
)
from aifren.memory_v2_store.repository import ActiveSceneRelationRecord, ActiveStateRecord
from aifren.memory_v2_store.scene_relation_contract import SceneRelationProposal


_TRAILING = re.compile(r"[\s.!?]+$")
_UNSAFE = re.compile(
    r"^(?:what\s+if|if\s+|imagine\s+|suppose\s+|hypothetically\b|for\s+example\b|"
    r"i\s+wonder\b.{0,80}\bif\b|maybe\b|perhaps\b|"
    r"not\s+true\s*:|in\s+(?:a\s+)?hypothetical\s+(?:story|scenario)\b)",
    re.IGNORECASE,
)
_AMBIGUOUS_ITEM = re.compile(
    r"\b(?:maybe|perhaps|possibly|probably|might|may|could|would|"
    r"or\s+something|whatever|some\s+kind\s+of|"
    r"take\s+(?:it|that|them)\s+off|put\s+(?:it|that|them)\s+on)\b",
    re.I,
)
_FILLER = re.compile(
    r"(?:(?:oh|okay|ok|alright|well|actually|so|sorry|hey|look|seriously|"
    r"indeed|yep|for\s+sure|right\s+this\s+moment|"
    r"currently|now|right\s+now|here|see|listen|you\s+know|for\s+what\s+it'?s\s+worth|"
    r"just|please|go\s+(?:ahead|on)(?:\s+and)?|got\s+it|it\s+is\s+true\s+that|"
    r"just\s+so\s+you\s+know|by\s+the\s+way)[,!]?\s+)+",
    re.IGNORECASE,
)
_SAFE_TRAILING_DISCOURSE = re.compile(
    r"(?:,\s*)?(?:right\s+now|right\s+this\s+(?:second|instant)|at\s+the\s+moment|"
    r"presently|immediately|right\s+away|now|please|for\s+me|you\s+know|buddy|friend)"
    r"[.!]?\s*$",
    re.IGNORECASE,
)
_COLOR_WORDS = frozenset({
    "black", "blue", "brown", "cream", "cyan", "gold", "gray", "green", "grey",
    "orange", "pink", "purple", "red", "silver", "tan", "teal", "violet", "white", "yellow",
})
_DECORATIVE_DESCRIPTORS = frozenset({"identical", "sparkly", "striped", "patterned"})
_QUANTITIES = {"one": 1, "two": 2, "three": 3, "four": 4, "both": 2}
_REGIONS = {
    "blindfold": "eyes",
    "hat": "head", "cap": "head", "helmet": "head",
    "scarf": "neck", "necklace": "neck",
    "dress": "full_outfit", "outfit": "full_outfit",
    "shirt": "torso", "hoodie": "torso", "coat": "torso", "jacket": "torso",
    "glove": "hands", "gloves": "hands",
    "pants": "legs", "trousers": "legs", "skirt": "legs",
    "boot": "feet", "boots": "feet", "shoe": "feet", "shoes": "feet",
    "sneaker": "feet", "sneakers": "feet", "rollerblade": "feet", "rollerblades": "feet",
    "skate": "feet", "skates": "feet", "ski": "feet", "skis": "feet",
    "earplug": "ears", "earplugs": "ears", "prosthetic arm": "arms",
    "prosthetic hand": "hands", "prosthetic leg": "legs",
}
_LOCOMOTION_EQUIPMENT = {
    "rollerblade": "skating", "rollerblades": "skating",
    "skate": "skating", "skates": "skating",
    "ski": "skating", "skis": "skating",
    "prosthetic leg": "assisted",
}
_BODY_ASSISTANCE = {"prosthetic arm", "prosthetic hand"}
_SUBJECT_OCCUPANCY_PREDICATES = {
    "wearing", "worn_by", "holding", "carrying", "riding", "driving",
    "seated_in", "using", "supported_by", "near",
    "covered_by", "obstructed_by", "occupied_by",
    "attached_to", "tethered_to", "restrained_by",
}
_SINGULAR = {
    "boxes": "box", "cups": "cup", "books": "book", "apples": "apple",
    "gloves": "gloves", "boots": "boots", "shoes": "shoes", "sneakers": "sneakers",
    "rollerblades": "rollerblades", "skates": "skates", "skis": "skis",
}


@dataclass(frozen=True)
class ActiveSceneMutation:
    active_state: ActiveStateProposal | None
    relations: tuple[SceneRelationProposal, ...]
    reason: str = "deterministic"

    @property
    def proposed(self) -> bool:
        return self.active_state is not None or bool(self.relations)


@dataclass(frozen=True)
class _View:
    canonical: str
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Item:
    kind: str
    label: str
    color: str | None
    quantity: int
    set_label: str | None
    region: str | None
    descriptors: tuple[str, ...] = ()


def extract_active_scene_mutation(
    content: object,
    *,
    subjects: dict[str, dict[str, ActiveStateRecord]],
    retired_subjects: dict[str, dict[str, ActiveStateRecord]] | None = None,
    relations: Iterable[ActiveSceneRelationRecord] = (),
    profile_available: bool = False,
    reusable_subject_ids: set[str] | None = None,
    allow_loci: bool = False,
) -> ActiveSceneMutation | None:
    """Extract bounded companion scene, relation, and lifecycle updates."""
    view = _parse_view(content)
    if view is None:
        return None
    builder = _Builder(
        view, subjects, tuple(relations), retired_subjects=retired_subjects or {},
        profile_available=profile_available,
        reusable_subject_ids=reusable_subject_ids or set(),
    )
    normalized = _TRAILING.sub("", view.text).strip()
    if allow_loci and builder.parse_locus_clause(normalized):
        return builder.finish()

    # A few explicitly ordered self-corrections are resolved as one bounded
    # turn before any broad list matcher runs.  In particular, never allow a
    # later sentence such as "Actually, take it off" to become part of an
    # object's identity.  A no-op result is still meaningful here: it prevents
    # clause salvage from re-applying the superseded first clause.
    wear_then_remove = re.fullmatch(
        r"you(?:'re|\s+are)\s+wearing\s+(?P<item>(?:the\s+|your\s+|a\s+)?"
        r"[a-z][a-z'’ -]{0,55}?)(?:(?:[.!;]+|,\s+(?:but\s+)?)\s*)"
        r"(?:actually[,]?\s*)?"
        r"(?:take\s+(?:it|that)\s+off|take\s+off\s+(?:the\s+|your\s+)?[a-z][a-z'’ -]{0,55})",
        normalized, re.I,
    )
    if wear_then_remove is not None:
        builder.clear_worn_reference(
            "companion", wear_then_remove.group("item"), builder.full_span(),
        )
        return builder.finish() or ActiveSceneMutation(None, (), "ordered_noop")

    corrected_attribute = re.fullmatch(
        r"your\s+(?P<item>[a-z][a-z'’ -]{0,55}?)\s+(?:is|are)\s+(?:now\s+)?"
        r"(?P<old>[a-z]+(?:\s+and\s+[a-z]+)?)(?:[.!;,—–-]+)\s*"
        r"(?:sorry|no|actually)[,!]?\s*(?:i\s+meant\s+)?"
        r"(?P<new>[a-z]+(?:\s+and\s+[a-z]+)?)",
        normalized, re.I,
    )
    if corrected_attribute is not None:
        final_value = corrected_attribute.group("new")
        builder.set_subject_attribute(
            corrected_attribute.group("item"), final_value, builder.span(final_value),
        )
        return builder.finish() or ActiveSceneMutation(None, (), "ordered_noop")

    corrected_wear = re.fullmatch(
        r"(?:please\s+)?(?:you\s+)?put\s+on\s+(?:the\s+|your\s+|a\s+)?"
        r"(?P<old>[a-z][a-z'’ -]{0,47}?)\s*(?:—|–|-)\s*"
        r"(?:no|sorry)[,]?\s*(?:the\s+|your\s+|a\s+)?"
        r"(?P<new>[a-z][a-z'’ -]{0,47})",
        normalized, re.I,
    )
    if corrected_wear is not None:
        builder.add_worn(
            "companion", corrected_wear.group("new"),
            builder.span(corrected_wear.group("new")), replace_exclusive=True,
        )
        return builder.finish() or ActiveSceneMutation(None, (), "ordered_noop")

    negated_blindfold = re.fullmatch(
        r"you(?:'re|\s+are)\s+blindfolded[,]?\s+except\s+you(?:'re|\s+are)\s+not",
        normalized, re.I,
    )
    if negated_blindfold is not None:
        builder.clear_relations(
            "companion", cause_hint="blindfold", facet="eyes", span=builder.full_span(),
        )
        return builder.finish() or ActiveSceneMutation(None, (), "ordered_noop")

    if profile_available and re.fullmatch(
        r"(?:please\s+)?(?:change|get)\s+back\s+into\s+(?:your\s+)?(?:usual|normal)\s+clothes",
        normalized, re.I,
    ):
        builder.clear_all_actor_relations(
            "companion", {"wearing", "worn_by"}, builder.full_span(), suppress_profile=False,
        )
        builder.set_profile_mode("profile", builder.full_span())
        return builder.finish()

    # Atomic destructive replacements are recognized before generic splitting.
    replacement = re.fullmatch(
        r"(?:please\s+)?take\s+off\s+(?P<old>[^;]{1,150}?)\s+and\s+"
        r"(?:then\s+)?(?:please\s+)?put\s+on\s+(?P<new>[^;]{1,150})",
        normalized, re.IGNORECASE,
    )
    if replacement is not None:
        old_rows = builder.item_list(replacement.group("old"))
        new_rows = builder.item_list(replacement.group("new"))
        parsed_new = tuple(_parse_item(fragment) for fragment, _span in new_rows)
        exclusive = {"head", "eyes", "hands", "feet", "full_outfit"}
        new_regions = [item.region for item in parsed_new if item is not None and item.region in exclusive]
        if (not 1 <= len(old_rows) <= 4 or not 1 <= len(new_rows) <= 4
                or any(item is None for item in parsed_new)
                or len(new_regions) != len(set(new_regions))
                or any(not builder.can_replace_worn("companion", old, new_rows[0][0])
                       for old, _span in old_rows)):
            return None
        for old, span in old_rows:
            builder.clear_worn("companion", old, span)
        for (new, span), _item in zip(new_rows, parsed_new):
            builder.add_worn(
                "companion", new, span, replace_exclusive=True, force_new=True,
            )
        return builder.finish()

    replacement = re.fullmatch(
        r"(?:(?:i\s+)?(?:swap|replace|switch)\s+(?:your\s+|the\s+)?"
        r"(?P<old>[a-z][a-z -]{0,47}?)\s+(?:for|with)\s+"
        r"(?:a\s+|the\s+|your\s+)?(?P<new>[a-z][a-z -]{0,47}))",
        normalized, re.IGNORECASE,
    )
    if replacement is not None:
        old = replacement.group("old")
        new = builder.complete_replacement_item(old, replacement.group("new"))
        if new is None or not builder.can_replace_worn("companion", old, new):
            return None
        builder.clear_worn("companion", old, builder.span(old))
        builder.add_worn("companion", new, builder.span(replacement.group("new")),
                         replace_exclusive=True, force_new=True)
        return builder.finish()

    drive = re.fullmatch(
        r"you\s+(?:get|got)\s+into\s+(?P<item>(?:the\s+|a\s+)?car)\s+and\s+drive",
        normalized, re.I,
    )
    if drive is not None:
        builder.add_transport(
            "companion", drive.group("item"), "driving", "driving", builder.full_span(),
        )
        return builder.finish()

    # One explicit release plus a separate destination sentence is still one
    # atomic holder lifecycle update.
    release_destination = re.fullmatch(
        r"i\s+(?:put|set)\s+(?P<item>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,44}?)\s+down"
        r"[.!]?\s+it(?:'s|\s+is)\s+(?P<relation>on|in|at)\s+(?:the\s+|my\s+)?"
        r"(?P<location>[a-z][a-z -]{0,44})",
        normalized, re.I,
    )
    if release_destination is not None:
        builder.release_subject(
            "user", release_destination.group("item"), builder.full_span(),
            location=release_destination.group("location"),
            location_relation=release_destination.group("relation"),
        )
        return builder.finish()

    # Closed semantic clause shapes admit ordinary apposition/progressive
    # wording without turning the extractor into an open world parser. Every
    # shape still requires explicit actor, body/object and relation evidence.
    inverted_worn = re.fullmatch(
        r"your\s+(?P<items>[a-z][a-z ,'-]{1,150}?)[,;]\s+you(?:'re|\s+are)\s+"
        r"(?:currently\s+)?(?:wearing|sporting)\s+(?:them|those)", normalized, re.I,
    )
    if inverted_worn is not None:
        _add_worn_list(builder, "companion", inverted_worn.group("items"))
        return builder.finish()
    restraint_clause = re.fullmatch(
        r"(?![^\n]*\b(?:not|do\s+not|don't)\b)"
        r"(?=[^\n]{0,220}\byour\s+left\s+wrist\b)(?=[^\n]{0,220}\bpole\b)"
        r"(?=[^\n]{0,220}\b(?:handcuff|cuff|tether|restrain)[a-z]*\b)"
        r"[a-z0-9' ,;:.!—–-]+", normalized, re.I,
    )
    if restraint_clause is not None:
        builder.add_relation(
            "set", "companion", "wrists", "tethered_to", "environment", "pole",
            builder.full_span(), side="left", semantic_family="restraint",
            effect_state="constrained",
        )
        return builder.finish()
    mouth_clear_clause = re.fullmatch(
        r"(?=[^\n]{0,240}\bi(?:'m|'ve|\s+am|\s+have|\s+remove|\s+take|\s+pull|\s+move)\b)"
        r"(?=[^\n]{0,240}\bmy\s+hand\b)(?=[^\n]{0,240}\byour\s+mouth\b)"
        r"(?=[^\n]{0,240}\b(?:uncover[a-z]*|remov[a-z]*|tak(?:e|ing)|pull[a-z]*|"
        r"mov[a-z]*|off|away)\b)[a-z0-9' ,;:.!—–-]+", normalized, re.I,
    )
    if mouth_clear_clause is not None:
        builder.add_relation(
            "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
            builder.full_span(),
        )
        return builder.finish()
    mouth_cover_clause = re.fullmatch(
        r"(?![^\n]*\b(?:not|do\s+not|don't)\b)"
        r"(?=[^\n]{0,220}\bmy\s+hand\b)(?=[^\n]{0,220}\byour\s+mouth\b)"
        r"(?=[^\n]{0,220}\b(?:cover[a-z]*|over)\b)[a-z0-9' ,;:.!—–-]+",
        normalized, re.I,
    )
    if mouth_cover_clause is not None:
        builder.add_relation(
            "set", "companion", "mouth", "covered_by", "actor_part", "user hand",
            builder.full_span(), semantic_family="speech_obstruction",
        )
        return builder.finish()
    mouth_object = re.fullmatch(
        r"(?:i\s+(?:plug|stuff)\s+your\s+mouth\s+with\s+"
        r"(?P<applied>(?:the\s+|an?\s+)?[a-z][a-z' -]{0,36})|"
        r"you(?:'re|\s+are)\s+wearing\s+(?P<worn>(?:the\s+|an?\s+)?"
        r"(?:gag|[a-z][a-z' -]{0,28}\s+in\s+your\s+mouth))|"
        r"(?P<located>(?:the\s+|an?\s+)?[a-z][a-z' -]{0,32}?)\s+is\s+"
        r"(?:(?P<stuck>stuck)\s+)?in\s+your\s+mouth"
        r"(?:[.!;]+\s*you\s+can(?:'t|not)\s+talk)?|"
        r"(?P<preventing>(?:the\s+|an?\s+)?[a-z][a-z' -]{0,32}?)\s+is\s+"
        r"preventing\s+you\s+from\s+(?:speaking|talking))",
        normalized, re.I,
    )
    if mouth_object is not None:
        raw_item = next(
            value for value in (
                mouth_object.group("applied"), mouth_object.group("worn"),
                mouth_object.group("located"), mouth_object.group("preventing"),
            ) if value is not None
        )
        raw_item = re.sub(r"\s+in\s+your\s+mouth$", "", raw_item, flags=re.I)
        unavailable = bool(
            mouth_object.group("applied") or mouth_object.group("preventing")
            or mouth_object.group("stuck") or re.search(r"\bgag\b", raw_item, re.I)
            or re.search(r"can(?:'t|not)\s+talk", normalized, re.I)
        )
        builder.add_mouth_obstruction(
            raw_item, builder.span(raw_item),
            effect_state="unavailable" if unavailable else "constrained",
        )
        return builder.finish()
    blindfold_clear_clause = re.fullmatch(
        r"(?![^\n]*\b(?:not|do\s+not|don't)\b)"
        r"(?=[^\n]{0,220}\bblindfold\b)(?=[^\n]{0,220}\byour\s+eyes\b)"
        r"(?=[^\n]{0,220}\b(?:remov[a-z]*|tak[a-z]*|pull[a-z]*|lift[a-z]*|off)\b)"
        r"[a-z0-9' ,;:.!—–-]+",
        normalized, re.I,
    )
    if blindfold_clear_clause is not None:
        builder.clear_relations(
            "companion", facet="eyes", cause_hint="blindfold", span=builder.full_span(),
        )
        return builder.finish()
    blindfold_set_clause = re.fullmatch(
        r"(?![^\n]*\b(?:not|do\s+not|don't)\b)"
        r"(?=[^\n]{0,220}\bblindfold\b)(?=[^\n]{0,220}\byour\s+eyes\b)"
        r"(?=[^\n]{0,220}\b(?:put[a-z]*|got|plac[a-z]*|cover[a-z]*|across|over|upon)\b)"
        r"[a-z0-9' ,;:.!—–-]+", normalized, re.I,
    )
    if blindfold_set_clause is not None:
        builder.add_blindfold(builder.full_span())
        return builder.finish()
    blindfold_set_result = re.fullmatch(
        r"(?:blindfold\s+on\s+your\s+eyes[;,. ]+that(?:'s|\s+is)\s+what\s+i\s+did"
        r"(?:\s+now)?|your\s+eyes\s+receive\s+(?:the\s+)?blindfold\s+from\s+me"
        r"(?:\s+now)?|your\s+(?:blindfold|blindfold's)\s+(?:is\s+)?on\s+your\s+eyes)",
        normalized, re.I,
    )
    if blindfold_set_result is not None:
        builder.add_blindfold(builder.full_span())
        return builder.finish()
    handoff_clause = re.fullmatch(
        r"(?:(?:here[,]?\s+)?i\s+(?:hand|give)\s+you\s+"
        r"(?P<given>(?:this\s+|the\s+|an?\s+)?[a-z][a-z -]{0,40}?)|"
        r"(?:please\s+)?take\s+(?P<taken>(?:this\s+|that\s+|an?\s+)"
        r"[a-z][a-z -]{0,40}?))"
        r"(?:\s+from\s+me)?(?:[,;—–-].*)?", normalized, re.I,
    )
    if handoff_clause is not None:
        item = handoff_clause.group("given") or handoff_clause.group("taken")
        builder.transfer(
            "user", "companion", item, builder.span(item), introduce=True,
        )
        return builder.finish()
    handoff_result = re.fullmatch(
        r"(?:it(?:'s|\s+is)\s+(?P<item1>(?:the\s+|an?\s+)?[a-z][a-z -]{0,36}?)\s+"
        r"that\s+i\s+hand\s+you[,]?\s+meaning\s+you\s+now\s+possess\s+"
        r"(?:the\s+|an?\s+)?[a-z][a-z -]{0,36}|"
        r"you\s+hold\s+(?P<item2>(?:the\s+|an?\s+)?[a-z][a-z -]{0,36}?)\s+"
        r"(?:because|'cause)\s+i\s+hand\s+you\s+(?:the\s+|an?\s+)?"
        r"[a-z][a-z -]{0,36})",
        normalized, re.I,
    )
    if handoff_result is not None:
        item = handoff_result.group("item1") or handoff_result.group("item2")
        builder.transfer("user", "companion", item, builder.span(item), introduce=True)
        return builder.finish()
    offered_handoff = re.fullmatch(
        r"take\s+(?:this\s+|the\s+|an?\s+)?(?P<item>[a-z][a-z -]{0,36}?)\s+"
        r"i(?:'m|\s+am)\s+offering(?:[,;]\s*and\s+you\s+hold\s+(?:the\s+)?"
        r"[a-z][a-z -]{0,36})?",
        normalized, re.I,
    )
    if offered_handoff is not None:
        item = offered_handoff.group("item")
        builder.transfer("user", "companion", item, builder.span(item), introduce=True)
        return builder.finish()
    release_clause = re.fullmatch(
        r"(?:you\s+(?:gotta|need\s+to|must|should)\s+)?(?:(?:put|set)\s+"
        r"(?=[^\n]{0,100}\bdown\b)|drop\s+)(?=[^\n]{0,140}\bcup\b)"
        r"[a-z0-9' ,;.!—–-]+", normalized, re.I,
    )
    if release_clause is not None:
        builder.release_unique_holder("cup", builder.full_span())
        return builder.finish()
    release_clause = re.fullmatch(
        r"(?:your\s+hands?[,]?\s+(?:the\s+)?cup[,]?\s+(?:put|set)\s+it\s+down|"
        r"your\s+cup[;,:]\s*(?:please\s+)?(?:put|set)\s+it\s+down)"
        r"(?:\s+for\s+me)?",
        normalized, re.I,
    )
    if release_clause is not None:
        builder.release_unique_holder("cup", builder.full_span())
        return builder.finish()
    release_clause = re.fullmatch(
        r"your\s+cup\s+needs\s+to\s+be\s+(?:put|set)\s+down"
        r"(?:[;,. ]+(?:please\s+)?do\s+it)?",
        normalized, re.I,
    )
    if release_clause is not None:
        builder.release_unique_holder("cup", builder.full_span())
        return builder.finish()
    release_clause = re.fullmatch(
        r"(?:cup[,]?\s+(?:please\s+)?put\s+it\s+down[;,. ]+you\s+holdin'\s+it|"
        r"your\s+cup[,]?\s+gotta\s+be\s+(?:put|set)\s+down\s+by\s+you)",
        normalized, re.I,
    )
    if release_clause is not None:
        builder.release_unique_holder("cup", builder.full_span())
        return builder.finish()
    release_clause = re.fullmatch(
        r"your\s+hands?\s+need(?:s)?\s+to\s+(?:put|set)\s+down\s+"
        r"(?:this\s+|that\s+|the\s+|your\s+)?cup",
        normalized, re.I,
    )
    if release_clause is not None:
        builder.release_unique_holder("cup", builder.full_span())
        return builder.finish()

    # Full-turn list patterns intentionally accept natural coordinated lists,
    # but not a second sentence. Independent sentence clauses are handled by
    # Current Continuity's exact-offset salvage layer below this extractor.
    loud_compound = re.fullmatch(
        r"(?=[^\n]{0,240}\bmusic\b)"
        r"(?=[^\n]{0,240}(?:\b(?:loud|loudness|deafening[a-z]*|blasting|volume|excessive|"
        r"drown(?:ed|ing)\s+out)\b|\bbecause\s+of\s+(?:(?:the|your)\s+)?music\b))"
        r"(?=[^\n]{0,240}(?:\byou\s+(?:[a-z]+\s+){0,3}"
        r"(?:can(?:'t|\s+not)|cannot)\s+hear\s+me\b|"
        r"\byou(?:'re|\s+are)\s+not\s+hearing\s+me\b|"
        r"\byour\s+ears?\s+(?:[a-z']+\s+){0,2}(?:can(?:'t|\s+not)|cannot)\s+hear\s+me\b|"
        r"\byour\s+hearing(?:'s|\s+is)\s+shot\b|"
        r"\byour\s+hearing(?:'s|\s+is)?\s+[^\n]{0,50}?"
        r"(?:can(?:'t|\s+not)|cannot)\s+(?:hear|catch|register|pick\s+up)\s+"
        r"(?:me|my\s+words)\b))"
        r"[a-z0-9' ,;:.!-]+",
        normalized, re.I,
    )
    if loud_compound is not None:
        builder.add_environmental_sense(
            "hearing", "loud music", "unavailable", builder.full_span(),
            targets=("companion",),
        )
        return builder.finish()
    loud_clear_compound = re.fullmatch(
        r"(?=[^\n]{0,240}\bmusic(?:'s|\s+is|\s+has)?\b)"
        r"(?=[^\n]{0,240}\b(?:stopp(?:ed|ing)|ceased|ended|done|quiet(?:er|ed)?|"
        r"not\s+loud|no\s+longer\s+loud|ain't\s+loud\s+anymore|"
        r"isn't\s+bothering)\b)"
        r"[a-z0-9' ,;:.!—–-]+",
        normalized, re.I,
    )
    if loud_clear_compound is not None:
        builder.clear_environmental_sense(
            "hearing", {"music", "loud"}, builder.full_span(),
        )
        return builder.finish()
    summarized_outfit = re.fullmatch(
        r"you\s+have\s+on\s+(?P<items>[^;]{1,160})\s*;\s*"
        r"that(?:'s|\s+is)\s+what\s+you\s+wear",
        normalized, re.I,
    )
    if summarized_outfit is not None:
        _add_worn_list(builder, "companion", summarized_outfit.group("items"))
        return builder.finish()
    handoff_compound = re.fullmatch(
        r"i\s+(?:hand|give)\s+you\s+(?P<item>(?:this\s+|the\s+|an?\s+)?"
        r"[a-z][a-z -]{0,44}?)[,;]\s+(?:so\s+)?you\s+(?:now\s+|currently\s+)?"
        r"(?:hold|are\s+holding)\s+(?:it|the\s+[a-z][a-z -]{0,30})",
        normalized, re.I,
    )
    if handoff_compound is not None:
        builder.transfer(
            "user", "companion", handoff_compound.group("item"),
            builder.span(handoff_compound.group("item")), introduce=True,
        )
        return builder.finish()
    if re.search(r"[.;!?]+\s+", normalized):
        return None

    # Two clearly typed independent clauses share one atomic evidence batch.
    typed_compound = re.fullmatch(
        r"you(?:'re|\s+are)\s+wearing\s+(?P<worn>.+?)\s+and\s+"
        r"(?P<verb>holding|carrying)\s+(?P<held>.+)", normalized, re.I,
    )
    if typed_compound is not None:
        _add_worn_list(builder, "companion", typed_compound.group("worn"))
        for fragment, span in builder.item_list(typed_compound.group("held"), hand_context=True):
            builder.add_held("companion", fragment, span, predicate=typed_compound.group("verb").lower())
        return builder.finish()

    # Wearing/holding coordinated lists are one evidence-backed atomic batch.
    list_match = re.fullmatch(
        r"(?:you(?:'re|\s+are)\s+(?:(?:currently|presently|definitely)\s+)?(?:wearing|sporting)|"
        r"you\s+(?:currently\s+|presently\s+|definitely\s+)?(?:wear|have\s+on)|"
        r"you\s+(?:have\s+)?got(?:\s+on)?|"
        r"your\s+(?:current\s+)?(?:outfit|attire|ensemble)\s+"
        r"(?:is|includes|consists\s+of)|"
        r"your\s+(?:current\s+)?(?:outfit|attire|ensemble)\s+shows\s+"
        r"you(?:(?:'re|\s+are)\s+(?:wearing|sporting)|\s+(?:have|got))|"
        r"i\s+see\s+you(?:'ve|\s+have)\s+got)\s+"
        r"(?P<items>.+)",
        normalized, re.IGNORECASE,
    )
    if list_match is not None:
        items = re.sub(
            r"\s*[,;]\s*that(?:'s|\s+is)\s+what\s+you\s+wear$", "",
            list_match.group("items"), flags=re.I,
        )
        items = re.sub(
            r"\s+(?:currently\s+)?on(?:\s+(?:you|yourself|your\s+head))?$", "", items,
            flags=re.I,
        )
        items = re.sub(r"\s+going\s+with\s+your\s+look$", "", items, flags=re.I)
        _add_worn_list(builder, "companion", items)
        return builder.finish()
    regional_outfit = re.fullmatch(
        r"your\s+head\s+(?:has|sports|shows)\s+(?P<head>(?:the\s+|an?\s+)?"
        r"[a-z][a-z'’ -]{0,45})\s*,?\s+and\s+your\s+neck\s+(?:has|sports|shows)\s+"
        r"(?P<neck>(?:the\s+|an?\s+)?[a-z][a-z'’ -]{0,45})",
        normalized, re.I,
    )
    if regional_outfit is not None:
        _add_worn_list(
            builder, "companion",
            regional_outfit.group("head") + " and " + regional_outfit.group("neck"),
        )
        return builder.finish()
    worn_in = re.fullmatch(
        r"you(?:'re|\s+are)\s+in\s+(?P<items>.+\s+and\s+.+)",
        normalized, re.I,
    )
    if worn_in is not None:
        rows = builder.item_list(worn_in.group("items"))
        parsed = tuple(_parse_item(fragment) for fragment, _span in rows)
        if len(rows) >= 2 and all(item is not None and item.region is not None for item in parsed):
            _add_worn_list(builder, "companion", worn_in.group("items"))
            return builder.finish()
    list_match = re.fullmatch(
        r"you(?:'ve|\s+have)\s+got\s+(?P<items>.+?)\s+"
        r"(?:(?:currently\s+)?on(?:\s+(?:you|yourself))?|going\s+with\s+your\s+look)",
        normalized, re.IGNORECASE,
    )
    if list_match is not None:
        _add_worn_list(builder, "companion", list_match.group("items"))
        return builder.finish()
    list_match = re.fullmatch(
        r"you(?:'re|\s+are)\s+(?P<verb>holding|carrying)\s+(?P<items>.+)",
        normalized, re.IGNORECASE,
    )
    if list_match is not None:
        for fragment, span in builder.item_list(list_match.group("items"), hand_context=True):
            builder.add_held("companion", fragment, span, predicate=list_match.group("verb").lower())
        return builder.finish()
    list_match = re.fullmatch(
        r"i(?:'m|\s+am)\s+(?P<verb>holding|carrying)\s+(?P<items>.+)",
        normalized, re.IGNORECASE,
    )
    if list_match is not None:
        for fragment, span in builder.item_list(list_match.group("items"), hand_context=True):
            builder.add_held("user", fragment, span, predicate=list_match.group("verb").lower())
        return builder.finish()
    list_match = re.fullmatch(
        r"i(?:'m|\s+am)\s+wearing\s+(?P<items>.+)", normalized, re.IGNORECASE,
    )
    if list_match is not None:
        _add_worn_list(builder, "user", list_match.group("items"))
        return builder.finish()

    # Shared-subject compound: "You're blindfolded and carrying two boxes."
    compound = re.fullmatch(
        r"you(?:'re|\s+are)\s+blindfolded\s+and\s+(?:you(?:'re|\s+are)\s+)?"
        r"(?P<verb>holding|carrying)\s+(?P<items>.+)",
        normalized, re.IGNORECASE,
    )
    if compound is not None:
        builder.add_blindfold(builder.span("blindfolded"))
        for fragment, span in builder.item_list(compound.group("items"), hand_context=True):
            builder.add_held("companion", fragment, span, predicate=compound.group("verb").lower())
        return builder.finish()

    if builder.parse_single_clause(normalized):
        return builder.finish()
    return None


def _add_worn_list(builder: "_Builder", actor: str, value: str) -> None:
    rows = builder.item_list(value)
    parsed = tuple((fragment, span, _parse_item(fragment)) for fragment, span in rows)
    exclusive = {"head", "eyes", "hands", "feet", "full_outfit"}
    region_rows: dict[str, list[tuple[str, _Item]]] = {}
    for fragment, _, item in parsed:
        if item is not None and item.region in exclusive:
            region_rows.setdefault(item.region, []).append((fragment, item))
    invalid_regions = {
        region for region, items in region_rows.items()
        if len(items) > 1 and not (
            len(items) == 2
            and {_side(fragment) for fragment, _item in items} == {"left", "right"}
        )
    }
    for fragment, span, item in parsed:
        if item is None or item.region in invalid_regions:
            continue
        builder.add_worn(actor, fragment, span, replace_exclusive=True)


def _parse_view(content: object) -> _View | None:
    if not isinstance(content, str) or not content or content != content.strip():
        return None
    if content.rstrip().endswith("?"):
        # Interrogative surface forms are never authoritative scene evidence,
        # even when their inner words resemble a supported command.
        return None
    if len(content) > 800 or any(mark in content for mark in ("\n", "\r", "\t", "`")):
        return None
    start, end = 0, len(content)
    if (content.startswith("*") and content.endswith("*") and not content.startswith("**")
            and "*" not in content[1:-1]):
        start, end = 1, len(content) - 1
    # Smart apostrophes are a presentation spelling, not a semantic
    # distinction. Translate them only in this equal-length derived view so
    # every evidence offset still indexes the exact canonical user message.
    text = content[start:end].replace("’", "'")
    filler = _FILLER.match(text)
    if filler is not None:
        start += filler.end()
        text = content[start:end].replace("’", "'")
    vocative = re.match(r"(?:you|companion|buddy)\s*,\s+", text, re.I)
    if vocative is not None:
        start += vocative.end()
        text = content[start:end].replace("’", "'")
    # Bounded turn modifiers do not change a current-state assertion. Moving
    # the view boundary retains exact offsets into the untouched evidence.
    trailing = _SAFE_TRAILING_DISCOURSE.search(text)
    if trailing is not None:
        end = start + trailing.start()
        text = content[start:end].replace("’", "'").rstrip(" ,")
        end = start + len(text)
    if (not text or _UNSAFE.search(text)
            or re.search(r"\b(?:probably|possibly|might|may|could)\b", text, re.I)
            or re.search(r"\b(?:or|except)\s+maybe\s+not\b", text, re.I)
            or '"' in text or "“" in text or "”" in text):
        return None
    return _View(content, text, start, end)


class _Builder:
    def __init__(
        self,
        view: _View,
        subjects: dict[str, dict[str, ActiveStateRecord]],
        relations: tuple[ActiveSceneRelationRecord, ...],
        *,
        retired_subjects: dict[str, dict[str, ActiveStateRecord]] | None = None,
        profile_available: bool = False,
        reusable_subject_ids: set[str] | None = None,
    ) -> None:
        self.view = view
        self.subjects = subjects
        self.retired_subjects = retired_subjects or {}
        self.current_relations = relations
        self.active_subject_ids = {
            subject_id
            for relation in relations
            for subject_id in (
                relation.cause_subject_id,
                relation.target if relation.target_kind == "scene" else None,
            )
            if subject_id is not None
        }
        self.updates: list[ActiveStateProposalUpdate] = []
        self.introductions: list[ActiveSceneSubjectIntroduction] = []
        self.retirements: list[ActiveSceneSubjectRetirement] = []
        self.reactivations: list[ActiveSceneSubjectReactivation] = []
        self.relations: list[SceneRelationProposal] = []
        self._next_ref = 1
        self._search_at = view.start
        self.profile_available = bool(profile_available)
        self.reusable_subject_ids = reusable_subject_ids or set()
        self._profile_mode_set = False
        self._blocked_reason: str | None = None

    def full_span(self) -> tuple[int, int]:
        return self.view.start, self.view.end

    def span(self, fragment: str) -> tuple[int, int]:
        lowered = self.view.canonical.casefold()
        needle = str(fragment).strip().casefold()
        position = lowered.find(needle, self._search_at)
        if position < 0:
            position = lowered.find(needle, self.view.start)
        if position < 0:
            return self.full_span()
        self._search_at = position + len(needle)
        return position, position + len(needle)

    def item_list(self, value: str, *, hand_context: bool = False) -> tuple[tuple[str, tuple[int, int]], ...]:
        compact = value.strip()
        # "a box in each hand" is one two-item set, not two malformed clauses.
        if hand_context and re.fullmatch(r"(?:a\s+)?[a-z][a-z -]{0,36}\s+in\s+each\s+hand", compact, re.I):
            return ((compact, self.span(compact)),)
        color_pair = re.compile(
            r"\b(" + "|".join(sorted(_COLOR_WORDS)) + r")\s+and\s+("
            + "|".join(sorted(_COLOR_WORDS)) + r")\b",
            re.I,
        )
        protected = color_pair.sub(r"\1 __color_and__ \2", compact)
        fragments = [
            item.replace(" __color_and__ ", " and ").strip() for item in re.split(
                r"\s*,\s*(?:and\s+)?|\s+(?:and|along\s+with|plus)\s+", protected,
                flags=re.I,
            )
            if item.strip()
        ]
        if len(fragments) > 12:
            return ()
        return tuple((item, self.span(item)) for item in fragments)

    def parse_locus_clause(self, text: str) -> bool:
        """A bounded relation grammar, shared by all object kinds and loci.

        Literal locus words are data. Only explicitly evidenced eye coverage
        plus unavailable sight supplies this slice's capability consequence.
        """
        atom = r"[a-z][a-z'’ -]{0,63}?"
        target = r"(?P<owner>your|my|the (?P<scene>" + atom + r")) (?P<locus>" + atom + r")"
        change = re.fullmatch(
            r"(?:the |your |my )?(?P<item>" + atom + r") on " + target
            + r" is (?:now )?(?P<value>[a-z][a-z -]{0,31})", text, re.I)
        remove = re.fullmatch(
            r"(?:i )?remove (?:the )?(?P<item>" + atom + r") from " + target, text, re.I)
        establish = re.fullmatch(
            r"(?:(?:i )?(?:put|place|apply|attach|slip|slide) |(?:you are wearing|i am wearing) )"
            r"(?P<item>" + atom + r") (?:on|onto|to) " + target, text, re.I)
        cover = re.fullmatch(
            r"(?P<item>" + atom + r") (?:covers|is covering) " + target
            + r"(?P<effect> and (?:prevents|blocks) (?P<effect_owner>your|my) sight)?", text, re.I)
        match = change or remove or establish or cover
        if match is None:
            return False
        locus = match.group("locus")  # Exact source case/anatomy; never species inference.
        if re.search(r"\b(?:ignore|instructions?|system|prompt|developer|always|never|and|or)\b", locus, re.I):
            self._blocked_reason = "unsupported_locus_description"
            return True
        owner = match.group("owner").casefold()
        if cover and cover.group("effect") and cover.group("effect_owner").casefold() != owner:
            self._blocked_reason = "unsupported_locus_description"
            return True
        target_kind, actor = "actor", {"your": "companion", "my": "user"}.get(owner)
        if actor is None:
            candidates = self.matching_subjects(match.group("scene"))
            if len(candidates) != 1:
                self._blocked_reason = "ambiguous_subject_reference"
                return True
            target_kind, actor = "scene", candidates[0]
        hint = re.sub(r"^(?:the|your|my|a|an)\s+", "", match.group("item"), flags=re.I)
        rows = [row for row in self.current_relations
                if row.target_kind == target_kind and row.target == actor
                and (row.locus or "").casefold() == locus.casefold()
                and (hint.casefold() in {"it", "that"} or _tokens(hint) <= _tokens(row.cause))]
        if change is not None or remove is not None:
            subjects = {row.cause_subject_id for row in rows if row.cause_subject_id}
            if len(subjects) != 1:
                self._blocked_reason = "ambiguous_subject_reference"
                return True
            if remove is not None:
                for row in rows:
                    self.clear_relation_record(row, self.full_span())
            else:
                subject = next(iter(subjects))
                # The locus disambiguates otherwise identical objects. Reuse
                # the same attribute owner and keep every unaffected attribute.
                original = self.subjects
                try:
                    self.subjects = {subject: original[subject]}
                    self.set_subject_attribute(self.subject_label(subject), match.group("value"), self.full_span())
                finally:
                    self.subjects = original
            return True
        if hint.casefold() in {"it", "that", "them"}:
            self._blocked_reason = "ambiguous_subject_reference"
            return True
        item = _parse_item(match.group("item"))
        if item is None:
            self._blocked_reason = "unsupported_locus_description"
            return True
        matches = self.matching_subjects(hint)
        if len(matches) > 1 and re.match(r"(?:the|your|my)\b", match.group("item"), re.I):
            self._blocked_reason = "ambiguous_subject_reference"
            return True
        predicate = "covering" if cover else "located_on"
        if text.casefold().startswith(("you are wearing ", "i am wearing ")):
            predicate = "wearing"
        # Coatings/decorations are scene subjects, not simulated inventory or
        # single-occupancy equipment slots. Repeated exact references can reuse.
        reference, item = self.subject_for(match.group("item"), self.full_span(), actor=actor, predicate=predicate)
        self._metadata(reference, item, self.full_span())
        anchor = {"eye": "eyes", "eyes": "eyes", "hand": "hands", "hands": "hands",
                  "ear": "ears", "ears": "ears", "mouth": "mouth", "wrist": "wrists"}.get(locus.casefold())
        unavailable = bool(cover and cover.group("effect") and anchor == "eyes" and target_kind == "actor")
        self.add_relation("set", actor, anchor, predicate, "scene", item.label,
            self.full_span(), target_kind=target_kind, cause_subject_kind=item.kind,
            cause_subject_ref=reference, locus=locus,
            semantic_family="vision_obstruction" if unavailable else None,
            effect_state="unavailable" if unavailable else None)
        return True

    def parse_single_clause(self, text: str) -> bool:
        posture = re.fullmatch(
            r"(?:(?:please\s+)?(?P<command>sit\s+down|stand\s+up|lie\s+down)|"
            r"you(?:'re|\s+are)\s+(?P<state>sitting|standing|lying))",
            text, re.I,
        )
        if posture is not None:
            value = posture.group("state") or {
                "sit down": "sitting", "stand up": "standing", "lie down": "lying",
            }[posture.group("command").casefold()]
            self.set_actor_posture("companion", value, self.full_span())
            return True
        user_posture = re.fullmatch(r"i(?:'m|\s+am)\s+(sitting|standing|lying)", text, re.I)
        if user_posture is not None:
            self.set_actor_posture("user", user_posture.group(1).casefold(), self.full_span())
            return True

        unique_attribute_correction = re.fullmatch(
            r"(?:it(?:'s|\s+is)\s+(?:an?\s+)?(?P<value1>[a-z]+(?:\s+and\s+[a-z]+)?)\s+"
            r"(?P<kind1>hat|scarf|coat|dress|shirt|boots?|shoes?|sneakers?)|"
            r"the\s+(?P<kind2>hat|scarf|coat|dress|shirt|boots?|shoes?|sneakers?)\s+"
            r"(?:is|are)\s+(?P<value2>[a-z]+(?:\s+and\s+[a-z]+)?)(?:\s+now)?)",
            text, re.I,
        )
        if unique_attribute_correction is not None:
            kind = unique_attribute_correction.group("kind1") or unique_attribute_correction.group("kind2")
            value = unique_attribute_correction.group("value1") or unique_attribute_correction.group("value2")
            if self.set_subject_attribute(kind, value, self.span(value)):
                return True

        # Attribute mutation keeps stable subject identity. Replacement verbs
        # are handled separately above and deliberately create another subject.
        attribute_change = re.fullmatch(
            r"your\s+(?P<item>[a-z][a-z'’ -]{0,55}?)\s+"
            r"(?:(?:is|are)\s+(?:now\s+)?|(?:got|gets)\s+)"
            r"(?P<value>wet|dry|muddy|clean|[a-z]+(?:\s+and\s+[a-z]+)?)(?:\s+now)?",
            text, re.I,
        )
        if attribute_change is not None:
            if self.set_subject_attribute(
                attribute_change.group("item"), attribute_change.group("value"),
                self.span(attribute_change.group("value")),
            ):
                return True
        set_attribute = re.fullmatch(
            r"set\s+(?:your\s+|the\s+)?(?P<item>[a-z][a-z'’ -]{0,55}?)\s+"
            r"(?:color\s+)?to\s+(?P<value>[a-z]+(?:\s+and\s+[a-z]+)?)",
            text, re.I,
        )
        if set_attribute is not None and self.set_subject_attribute(
            set_attribute.group("item"), set_attribute.group("value"),
            self.span(set_attribute.group("value")),
        ):
            return True

        # Sparse, explicit environmental consequences. No light/sound/smoke
        # physics is inferred: the relation records exactly what the user said.
        if re.fullmatch(r"the\s+(?:room|place|area)\s+is\s+(?:pitch\s+black|completely\s+dark)", text, re.I):
            self.add_environmental_sense(
                "vision", "darkness", "unavailable", self.full_span(), targets=("user", "companion"),
            )
            return True
        if re.fullmatch(r"(?:the\s+)?smoke\s+makes\s+it\s+(?:hard|difficult)\s+to\s+see", text, re.I):
            self.add_environmental_sense(
                "vision", "smoke", "constrained", self.full_span(), targets=("user", "companion"),
            )
            return True
        loud = re.fullmatch(
            r"(?:the\s+music\s+is|it(?:'s|\s+is))\s+so\s+loud\s+(?P<actor>you|i)\s+"
            r"can(?:'t|not)\s+hear\s+(?:me|you)",
            text, re.I,
        )
        if loud is not None:
            self.add_environmental_sense(
                "hearing", "loud music", "unavailable", self.full_span(),
                targets=(("companion",) if loud.group("actor").casefold() == "you" else ("user",)),
            )
            return True
        loud_reversed = re.fullmatch(
            r"you\s+can(?:'t|not)\s+hear\s+me\s+(?:because|because\s+of|'cause|"
            r"over)\s+(?:the|this)\s+music(?:\s+is)?\s+(?:so\s+)?"
            r"(?:loud|deafening|blasting)", text, re.I,
        )
        if loud_reversed is not None:
            self.add_environmental_sense(
                "hearing", "loud music", "unavailable", self.full_span(),
                targets=("companion",),
            )
            return True
        if re.fullmatch(
            r"the\s+music\s+(?:stops|has\s+stopped|is\s+off|goes\s+quiet|"
            r"has\s+ceased|(?:has\s+)?ended|(?:has\s+)?gone\s+quiet|"
            r"quieted\s+down|isn'?t\s+loud\s+(?:anymore|now)|is\s+no\s+longer\s+loud)",
            text, re.I,
        ):
            self.clear_environmental_sense("hearing", {"music", "loud"}, self.full_span())
            return True
        if re.fullmatch(r"you\s+can\s+hear\s+me\s+again", text, re.I):
            self.clear_environmental_sense(
                "hearing", {"music", "loud", "environment"}, self.full_span(),
            )
            return True
        if re.fullmatch(r"it(?:'s|\s+is)\s+too\s+loud\s+to\s+hear\s+me", text, re.I):
            self.add_environmental_sense(
                "hearing", "loud environment", "unavailable", self.full_span(),
                targets=("companion",),
            )
            return True
        if re.fullmatch(r"it(?:'s|\s+is)\s+quiet\s+again|it\s+quiets\s+down", text, re.I):
            self.clear_environmental_sense(
                "hearing", {"loud", "environment"}, self.full_span(),
            )
            return True
        if re.fullmatch(r"(?:the\s+)?smoke\s+(?:clears|is\s+gone|has\s+cleared)", text, re.I):
            self.clear_environmental_sense("vision", {"smoke"}, self.full_span())
            return True
        if re.fullmatch(r"(?:the\s+)?lights?\s+(?:come|comes)\s+on|it\s+is\s+light\s+again", text, re.I):
            self.clear_environmental_sense("vision", {"darkness", "dark"}, self.full_span())
            return True
        environmental_clear = re.fullmatch(
            r"(?:the\s+)?(?P<cause>[a-z][a-z -]{0,40}?)\s+"
            r"(?:stops|clears|is\s+gone|has\s+stopped|has\s+cleared)",
            text, re.I,
        )
        if environmental_clear is not None:
            cause_tokens = _tokens(environmental_clear.group("cause"))
            matches = [
                relation for relation in self.current_relations
                if relation.cause_kind == "environment"
                and bool(_tokens(relation.cause) & cause_tokens)
            ]
            if matches:
                for relation in matches:
                    self.clear_relation_record(relation, self.full_span())
                return True
        explicit_sense = re.fullmatch(
            r"(?:the\s+)?(?P<cause>[a-z][a-z -]{0,40}?)\s+makes\s+it\s+"
            r"(?P<severity>hard|difficult|impossible)\s+(?:for\s+you\s+)?to\s+"
            r"(?P<sense>see|hear|smell|taste|feel)",
            text, re.I,
        )
        if explicit_sense is not None:
            sense = {
                "see": "vision", "hear": "hearing", "smell": "smell",
                "taste": "taste", "feel": "touch",
            }[explicit_sense.group("sense").casefold()]
            self.add_environmental_sense(
                sense, explicit_sense.group("cause"),
                "unavailable" if explicit_sense.group("severity").casefold() == "impossible" else "constrained",
                self.full_span(), targets=("companion",),
            )
            return True

        earplugs = re.fullmatch(
            r"(?:i\s+(?:put\s+(?:the\s+)?earplugs\s+in\s+your\s+ears|"
            r"plug\s+your\s+ears\s+with\s+(?:the\s+)?earplugs)|"
            r"there\s+are\s+earplugs\s+in\s+your\s+ears)",
            text, re.I,
        )
        if earplugs is not None:
            # The command's instrument is definite even when the optional
            # article is omitted. Reuse uniquely current held earplugs when
            # their relation changes from holding to wearing.
            self.add_sensory_equipment(
                "companion", "the earplugs", "ears", "hearing", self.full_span(),
            )
            return True
        cover_ear = re.fullmatch(
            r"i\s+cover\s+(?:(?P<both>both)(?:\s+of)?\s+)?your\s+"
            r"(?:(?P<side>left|right)\s+)?(?P<plural>ears|ear)", text, re.I,
        )
        if cover_ear is not None:
            side = cover_ear.group("side")
            if cover_ear.group("both") or cover_ear.group("plural").casefold() == "ears":
                side = "both"
            self.add_relation(
                "set", "companion", "ears", "covered_by", "actor_part", "user hand",
                self.full_span(), side=side,
                semantic_family="hearing_obstruction", effect_state="constrained",
            )
            return True
        uncover_ear = re.fullmatch(
            r"i\s+uncover\s+(?:(?P<both>both)(?:\s+of)?\s+)?your\s+"
            r"(?:(?P<side>left|right)\s+)?(?P<plural>ears|ear)", text, re.I,
        )
        if uncover_ear is not None:
            side = uncover_ear.group("side")
            if uncover_ear.group("both") or uncover_ear.group("plural").casefold() == "ears":
                side = "both"
            matches = self.current_relation_matches(
                "companion", predicates={"covered_by", "obstructed_by"}, facet="ears",
                side=side,
            )
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True
        remove_earplugs = re.fullmatch(r"(?:i\s+)?(?:remove|take\s+out)\s+(?:the\s+)?earplugs", text, re.I)
        if remove_earplugs is not None:
            self.clear_relations("companion", cause_hint="earplugs", facet="ears", span=self.full_span())
            return True

        body_state = re.fullmatch(
            r"your\s+(?:(?P<side>left|right|both)\s+)?(?P<part>arm|arms|hand|hands|leg|legs)\s+"
            r"(?:is|are)\s+(?:missing|unavailable)", text, re.I,
        )
        if body_state is not None:
            part = body_state.group("part").casefold()
            facet = "arms" if part.startswith("arm") else "hands" if part.startswith("hand") else "legs"
            side = body_state.group("side")
            if part.endswith("s") and side is None:
                side = "both"
            self.add_relation(
                "set", "companion", facet, "unavailable_due_to", "body_state",
                f"missing {part}", self.full_span(), side=side,
                semantic_family="body_unavailable", effect_state="unavailable",
            )
            return True
        body_restored = re.fullmatch(
            r"your\s+(?:(?P<side>left|right|both)\s+)?(?P<part>arm|arms|hand|hands|leg|legs)\s+"
            r"(?:is|are)\s+(?:available|restored)\s+again",
            text, re.I,
        )
        if body_restored is not None:
            part = body_restored.group("part").casefold()
            facet = "arms" if part.startswith("arm") else "hands" if part.startswith("hand") else "legs"
            matches = self.current_relation_matches(
                "companion", predicates={"unavailable_due_to"}, facet=facet,
                side=body_restored.group("side"),
            )
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True
        restraint = re.fullmatch(
            r"(?:(?P<owner>your|my)\s+)?(?:(?P<side>left|right)\s+|(?P<one>one)\s+)?"
            r"wrist(?:\s+is|'s)\s+(?:handcuffed|tethered|restrained)\s+to\s+"
            r"(?:a\s+|the\s+)?(?P<object>[a-z][a-z -]{0,40})",
            text, re.I,
        )
        if restraint is not None:
            self.add_relation(
                "set", "user" if restraint.group("owner") == "my" else "companion",
                "wrists", "tethered_to", "environment",
                restraint.group("object"), self.full_span(), side=restraint.group("side"),
                semantic_family="restraint", effect_state="constrained",
            )
            return True
        explicit_tether_consequence = re.fullmatch(
            r"(?P<actor>i|you)(?:'m|\s+am|\s+are)\s+stuck|"
            r"(?P<actor2>i|you)\s+can(?:'t|not)\s+move\s+away",
            text, re.I,
        )
        if explicit_tether_consequence is not None:
            actor_word = explicit_tether_consequence.group("actor") or explicit_tether_consequence.group("actor2")
            actor = "user" if actor_word.casefold() == "i" else "companion"
            matches = self.current_relation_matches(
                actor, predicates={"tethered_to", "restrained_by", "attached_to"},
                facet="wrists",
            )
            matches = tuple(item for item in matches if item.semantic_family == "restraint")
            if len(matches) == 1:
                item = matches[0]
                self.add_relation(
                    "set", item.target, item.facet, item.predicate,
                    item.cause_kind, item.cause, self.full_span(),
                    target_kind=item.target_kind,
                    cause_subject_ref=item.cause_subject_id, side=item.side,
                    semantic_family="restraint", effect_state="unavailable",
                )
                return True
        release_restraint = re.fullmatch(
            r"(?:i\s+)?(?:unlock|unfasten|release|remove)\s+(?:the\s+)?"
            r"(?:(?P<side>left|right)\s+)?(?:handcuff|restraint|tether)(?:\s+from\s+your\s+wrist)?",
            text, re.I,
        )
        if release_restraint is not None:
            matches = self.current_relation_matches(
                "companion", predicates={"tethered_to", "restrained_by", "attached_to"},
                facet="wrists", side=release_restraint.group("side"),
            )
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True
        if re.fullmatch(r"you(?:'re|\s+are)\s+blindfolded", text, re.I):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(r"i\s+blindfold\s+you", text, re.I):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(
            r"i(?:'m|\s+am)\s+(?:putting|placing|applying)\s+(?:the\s+)?blindfold\s+"
            r"(?:on\s+you|(?:over|to)\s+your\s+eyes)", text, re.I,
        ):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(
            r"i\s+(?:put|place|placed|apply|applied)\s+(?:this\s+|the\s+|a\s+)?blindfold\s+"
            r"(?:on\s+you|(?:on|over|across|upon)\s+your\s+eyes)", text, re.I,
        ):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(
            r"i(?:'ve|\s+have)\s+put\s+(?:this\s+|the\s+|a\s+)?blindfold\s+"
            r"(?:on\s+you|over\s+your\s+eyes)|your\s+eyes\s+are\s+(?:being\s+)?"
            r"covered\s+(?:by|with)\s+(?:this\s+|the\s+|a\s+)?blindfold",
            text, re.I,
        ):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(
            r"(?:please\s+)?(?:put\s+(?:this\s+|the\s+|a\s+)?blindfold\s+"
            r"(?:on|over|upon)\s+your\s+eyes|cover\s+your\s+eyes\s+"
            r"(?:with|using)\s+(?:this\s+|the\s+|a\s+)?blindfold|blindfold\s+yourself)",
            text, re.I,
        ):
            self.add_blindfold(self.full_span())
            return True
        if re.fullmatch(r"i\s+cover\s+your\s+eyes\s+with\s+my\s+hands", text, re.I):
            self.add_relation(
                "set", "companion", "eyes", "covered_by", "actor_part", "user hands",
                self.full_span(), semantic_family="vision_obstruction",
            )
            return True
        if re.fullmatch(
            r"i\s+(?:take|move|pull)\s+my\s+hands?\s+away\s+from\s+your\s+eyes",
            text, re.I,
        ):
            self.add_relation(
                "clear", "companion", "eyes", "covered_by", "actor_part", "user hands",
                self.full_span(),
            )
            return True
        if re.fullmatch(r"i\s+take\s+my\s+hands?\s+off\s+your\s+eyes", text, re.I):
            self.add_relation(
                "clear", "companion", "eyes", "covered_by", "actor_part", "user hands",
                self.full_span(),
            )
            return True
        if re.fullmatch(r"i\s+move\s+my\s+hands?\s+away", text, re.I):
            matches = [
                item for item in self.current_relations
                if item.target_kind == "actor" and item.target == "companion"
                and item.cause_kind == "actor_part"
                and item.cause.casefold() in {"user hand", "user hands"}
                and item.predicate in {"covered_by", "obstructed_by"}
            ]
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True
        if re.fullmatch(
            r"(?:i\s+)?(?:(?:take|took|pull|pulled)\s+"
            r"(?:that\s+|this\s+|the\s+|your\s+)?blindfold\s+off"
            r"(?:\s+(?:of\s+)?(?:you|yourself|your\s+(?:eyes|face)))?|"
            r"(?:take|took)\s+off\s+(?:that\s+|this\s+|the\s+|your\s+)?blindfold"
            r"(?:\s+from\s+(?:you|your\s+(?:eyes|face)))?|"
            r"(?:remove|removed)\s+(?:that\s+|this\s+|the\s+)?blindfold"
            r"(?:\s+from\s+(?:you|your\s+(?:eyes|face)))?|"
            r"(?:pull|lift)\s+(?:that\s+|this\s+|the\s+)?blindfold\s+off\s+"
            r"(?:you|yourself|your\s+(?:eyes|face)))",
            text, re.I,
        ):
            self.clear_relations("companion", facet="eyes", cause_hint="blindfold",
                                 span=self.full_span())
            return True
        if re.fullmatch(r"i\s+uncover\s+your\s+eyes", text, re.I):
            self.add_relation("clear", "companion", "eyes", None, None, None, self.full_span())
            return True
        if re.fullmatch(r"your\s+mouth\s+is\s+full", text, re.I):
            self.add_relation(
                "set", "companion", "mouth", "occupied_by", "state", "mouth contents",
                self.full_span(), semantic_family="speech_obstruction",
            )
            return True
        if re.fullmatch(r"your\s+mouth\s+is\s+(?:empty|clear)(?:\s+now)?", text, re.I):
            self.add_relation(
                "clear", "companion", "mouth", "occupied_by", "state", "mouth contents",
                self.full_span(),
            )
            return True
        if re.fullmatch(
            r"i(?:\s+cover|'m\s+covering|\s+am\s+covering)\s+your\s+mouth\s+"
            r"with\s+my\s+hand", text, re.I,
        ):
            self.add_relation(
                "set", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(), semantic_family="speech_obstruction",
            )
            return True
        if re.fullmatch(
            r"(?:your\s+mouth\s+is\s+(?:being\s+)?covered\s+by\s+my\s+hand|"
            r"you(?:'re|\s+are)\s+covering\s+your\s+mouth\s+with\s+my\s+hand|"
            r"covering\s+your\s+mouth\s+with\s+my\s+hand\s+is\s+what\s+"
            r"i(?:'m|\s+am)\s+doing)", text, re.I,
        ):
            self.add_relation(
                "set", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(), semantic_family="speech_obstruction",
            )
            return True
        if re.fullmatch(r"my\s+hand\s+is\s+over\s+your\s+mouth", text, re.I):
            self.add_relation(
                "set", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(), semantic_family="speech_obstruction",
            )
            return True
        if re.fullmatch(
            r"i(?:'ve|\s+have)\s+(?:got\s+)?my\s+hand\s+(?:right\s+)?over\s+"
            r"your\s+mouth|i(?:'m|\s+am)\s+using\s+my\s+hand\s+to\s+cover\s+"
            r"your\s+mouth", text, re.I,
        ):
            self.add_relation(
                "set", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(), semantic_family="speech_obstruction",
            )
            return True
        if re.fullmatch(
            r"i\s+(?:take|move|pull)\s+my\s+hand\s+away\s+from\s+your\s+mouth",
            text, re.I,
        ):
            self.add_relation(
                "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(),
            )
            return True
        if re.fullmatch(
            r"i(?:'ve|\s+have)?\s*(?:taken|pulled|moved)\s+my\s+hand\s+"
            r"(?:off|away\s+from)\s+your\s+mouth", text, re.I,
        ):
            self.add_relation(
                "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(),
            )
            return True
        if re.fullmatch(
            r"i(?:'m|\s+am)\s+(?:taking|removing|pulling|moving)\s+my\s+hand\s+"
            r"(?:off|from|away\s+from)\s+your\s+mouth", text, re.I,
        ):
            self.add_relation(
                "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(),
            )
            return True
        if re.fullmatch(
            r"i\s+remove\s+my\s+hand\s+(?:from|off)\s+your\s+mouth", text, re.I,
        ):
            self.add_relation(
                "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(),
            )
            return True
        if re.fullmatch(r"i\s+(?:take|move)\s+my\s+hand\s+off\s+your\s+mouth", text, re.I):
            self.add_relation(
                "clear", "companion", "mouth", "covered_by", "actor_part", "user hand",
                self.full_span(),
            )
            return True
        if re.fullmatch(r"i\s+uncover\s+your\s+mouth", text, re.I):
            matches = self.current_relation_matches(
                "companion", predicates={"covered_by", "obstructed_by"}, facet="mouth",
            )
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True
        if re.fullmatch(r"i\s+move\s+my\s+hand\s+away", text, re.I):
            matches = [
                item for item in self.current_relations
                if item.target_kind == "actor" and item.target == "companion"
                and item.cause_kind == "actor_part" and item.cause.casefold() == "user hand"
                and item.predicate in {"covered_by", "obstructed_by"}
            ]
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
                return True

        worn_on = re.fullmatch(
            r"your\s+(?P<item>[a-z][a-z'’ -]{0,55}?)\s+is\s+(?:worn\s+)?on\s+"
            r"your\s+(?:(?P<side>left|right)\s+)?(?P<region>wrist|arm|hand)",
            text, re.I,
        )
        if worn_on is not None:
            attachment = " ".join(filter(None, (
                worn_on.group("item"), "on your", worn_on.group("side"), worn_on.group("region"),
            )))
            self.add_worn("companion", attachment, self.span(worn_on.group("item")))
            return True

        held_in_hand = re.fullmatch(
            r"(?P<item>(?:the\s+|a\s+|an\s+)?[a-z][a-z'’ -]{0,55}?)\s+"
            r"(?:is|are)\s+in\s+your\s+(?:(?P<side>left|right)\s+)?hands?",
            text, re.I,
        )
        if held_in_hand is not None:
            hand_suffix = (
                " in your " + held_in_hand.group("side") + " hand"
                if held_in_hand.group("side") else " in one hand"
            )
            self.add_held(
                "companion", held_in_hand.group("item") + hand_suffix,
                self.span(held_in_hand.group("item")), predicate="holding",
            )
            return True

        nearby = re.fullmatch(
            r"(?:there(?:'s|\s+is)\s+)?"
            r"(?P<item>(?:a\s+|an\s+|the\s+)?[a-z][a-z -]{0,55}?)\s+"
            r"(?:(?:is\s+)?nearby|(?:is|are)\s+beside\s+you)",
            text, re.I,
        )
        if nearby is not None and _parse_item(nearby.group("item")) is not None:
            self.add_nearby_subject(nearby.group("item"), self.span(nearby.group("item")))
            return True

        located = re.fullmatch(
            r"(?P<item>(?:the\s+|a\s+|an\s+)?[a-z][a-z'’ -]{0,55}?)\s+"
            r"(?:is|are)\s+(?P<relation>on|in)\s+(?:the\s+|a\s+|an\s+)?"
            r"(?P<location>[a-z][a-z'’ -]{0,44})",
            text, re.I,
        )
        if (located is not None
                and _parse_item(located.group("item")) is not None):
            self.add_located_subject(
                located.group("item"), located.group("location"),
                located.group("relation"), self.span(located.group("item")),
            )
            return True

        pick_up = re.fullmatch(
            r"(?:(?P<user>i)\s+)?(?:pick|picked)\s+up\s+"
            r"(?P<article>the\s+|a\s+|my\s+)?(?P<item>[a-z][a-z -]{0,55})",
            text, re.I,
        )
        if pick_up is not None:
            raw_item = (pick_up.group("article") or "") + pick_up.group("item")
            self.add_held(
                "user" if pick_up.group("user") else "companion",
                raw_item, self.span(pick_up.group("item")), predicate="holding",
            )
            return True

        user_applies_wear = re.fullmatch(
            r"i\s+put\s+(?P<items>(?:the\s+|an?\s+)?[a-z][a-z'’ ,&-]{0,90}?)\s+on\s+you",
            text, re.I,
        )
        if user_applies_wear is not None:
            if re.fullmatch(
                r"(?:the\s+|an?\s+)?blindfold",
                user_applies_wear.group("items"), re.I,
            ):
                self.add_blindfold(self.full_span())
                return True
            for fragment, span in self.item_list(user_applies_wear.group("items")):
                self.add_worn("companion", fragment, span, replace_exclusive=True)
            return True
        user_applies_region = re.fullmatch(
            r"i\s+put\s+(?:the\s+|an?\s+)?(?P<item>[a-z][a-z'’ -]{0,62}?)\s+on\s+"
            r"your\s+(?:(?P<side>left|right)\s+)?(?P<region>wrist|arm|hand|head|neck)",
            text, re.I,
        )
        if user_applies_region is not None:
            attachment = " ".join(filter(None, (
                user_applies_region.group("item"), "on your",
                user_applies_region.group("side"), user_applies_region.group("region"),
            )))
            self.add_worn("companion", attachment, self.span(user_applies_region.group("item")))
            return True
        wear = re.fullmatch(
            r"(?:please\s+)?(?:you\s+)?put\s+on\s+(?:the\s+|your\s+|a\s+)?(?P<item>.+)",
            text, re.I,
        )
        if wear is not None:
            self.add_worn("companion", wear.group("item"), self.span(wear.group("item")),
                          replace_exclusive=True)
            return True
        user_wear = re.fullmatch(
            r"i\s+(?:put|have)\s+on\s+(?:the\s+|my\s+|a\s+)?(?P<item>.+)", text, re.I,
        )
        if user_wear is not None:
            self.add_worn("user", user_wear.group("item"), self.span(user_wear.group("item")),
                          replace_exclusive=True)
            return True
        companion_not_wearing = re.fullmatch(
            r"you\s+(?:aren't|are\s+not)\s+wearing\s+(?:any\s+|the\s+|an?\s+)?"
            r"(?P<item>[a-z][a-z -]{0,62})", text, re.I,
        )
        if companion_not_wearing is not None:
            self.clear_worn(
                "companion", companion_not_wearing.group("item"),
                self.span(companion_not_wearing.group("item")),
            )
            return True
        user_take_off = re.fullmatch(
            r"i\s+(?:take|took)\s+off\s+(?:the\s+|my\s+)?(?P<item>[a-z][a-z -]{0,62})",
            text, re.I,
        )
        if user_take_off is not None:
            self.clear_worn("user", user_take_off.group("item"), self.span(user_take_off.group("item")))
            return True
        user_take_off_reverse = re.fullmatch(
            r"i\s+(?:take|took)\s+(?P<item>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,62}?)\s+off"
            r"(?:\s+because\s+it\s+(?:got|was)\s+wet)?",
            text, re.I,
        )
        if user_take_off_reverse is not None:
            self.clear_worn_reference(
                "user", user_take_off_reverse.group("item"), self.full_span(),
            )
            return True
        user_take_off_observed = re.fullmatch(
            r"i\s+take\s+off\s+(?:the\s+|my\s+)?(?P<item>[a-z][a-z -]{0,48}?)"
            r"\s+that\s+i(?:'m|\s+am)\s+wearing",
            text, re.I,
        )
        if user_take_off_observed is not None:
            self.clear_worn(
                "user", user_take_off_observed.group("item"),
                self.span(user_take_off_observed.group("item")),
            )
            return True
        if re.fullmatch(r"i(?:'m|\s+am)\s+not\s+wearing\s+anything", text, re.I):
            matches = self.current_relation_matches("user", predicates={"wearing", "worn_by"})
            if len(matches) == 1:
                self.clear_relation_record(matches[0], self.full_span())
            return True
        take_off = re.fullmatch(
            r"(?:please\s+)?take\s+off\s+(?P<item>everything\s+you(?:'re|\s+are)\s+wearing|"
            r"(?:the\s+|your\s+)?[a-z][a-z -]{0,62})",
            text, re.I,
        )
        if take_off is not None:
            target = take_off.group("item")
            if target.casefold().startswith("everything"):
                self.clear_all_actor_relations("companion", {"wearing", "worn_by"}, self.full_span())
            else:
                self.clear_worn("companion", target, self.span(target))
            return True

        user_release_action = re.fullmatch(
            r"i\s+(?:(?:put|set)\s+(?P<placed>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,44}?)"
            r"(?:\s+down)?(?:\s+(?P<relation>on|in|at)\s+(?:the\s+|my\s+)?"
            r"(?P<location>[a-z][a-z -]{0,44}))?|"
            r"(?:drop|dropped)\s+(?P<dropped>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,44})|"
            r"let\s+go\s+of\s+(?P<released>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,44})|"
            r"leave\s+(?P<left>it|that|(?:the\s+|my\s+)?[a-z][a-z -]{0,44})\s+there)",
            text, re.I,
        )
        if user_release_action is not None:
            item = next(
                value for value in (
                    user_release_action.group("placed"), user_release_action.group("dropped"),
                    user_release_action.group("released"), user_release_action.group("left"),
                ) if value is not None
            )
            self.release_subject(
                "user", item, self.full_span(),
                location=user_release_action.group("location"),
                location_relation=user_release_action.group("relation"),
            )
            return True
        release_text = re.sub(r"\s+from\s+your\s+hand$", "", text, flags=re.I)
        holder_release_reverse = re.fullmatch(
            r"(?:please\s+)?(?:you\s+(?:gotta|need\s+to|must|should)\s+)?"
            r"(?:put|set)\s+down\s+(?:the\s+|your\s+|that\s+|this\s+)?"
            r"(?P<item>it|that|[a-z][a-z -]{0,32}?)"
            r"(?:\s+you(?:'re|\s+are)\s+(?:currently\s+)?(?:holding|grasping)|"
            r"\s+that(?:'s|\s+is)\s+in\s+your\s+grasp|"
            r"\s+you(?:'ve|\s+have)\s+got)?",
            release_text, re.I,
        )
        if holder_release_reverse is not None:
            self.release_unique_holder(holder_release_reverse.group("item"), self.full_span())
            return True
        holder_release = re.fullmatch(
            r"(?:please\s+)?(?:you\s+(?:gotta|need\s+to|must|should)\s+)?"
            r"(?:(?:put|set)\s+(?:the\s+|both\s+|your\s+|that\s+|this\s+)?"
            r"(?P<placed>it|that|[a-z][a-z -]{0,44}?)(?:\s+down)?"
            r"(?:\s+(?P<relation>on|in|at)\s+(?:the\s+)?(?P<location>[a-z][a-z -]{0,44}))?|"
            r"drop\s+(?:the\s+)?(?P<dropped>it|that|[a-z][a-z -]{0,44})|"
            r"let\s+go\s+of\s+(?:the\s+)?(?P<released>it|that|[a-z][a-z -]{0,44})|"
            r"leave\s+(?:the\s+)?(?P<left>it|that|[a-z][a-z -]{0,44})\s+there)",
            release_text, re.I,
        )
        if holder_release is not None:
            item = next(
                value for value in (
                    holder_release.group("placed"), holder_release.group("dropped"),
                    holder_release.group("released"), holder_release.group("left"),
                ) if value is not None
            )
            released = self.release_unique_holder(
                item, self.full_span(), location=holder_release.group("location"),
                location_relation=holder_release.group("relation"),
            )
            if not released and holder_release.group("location") is not None:
                self.locate_subject(
                    item, holder_release.group("location"),
                    holder_release.group("relation"), self.full_span(),
                )
            return True
        user_release = re.fullmatch(
            r"i(?:'m|\s+am)\s+no\s+longer\s+holding\s+(?P<item>it|that|(?:the\s+)?[a-z][a-z -]{0,44})",
            text, re.I,
        )
        if user_release is not None:
            self.clear_held_reference("user", user_release.group("item"), self.full_span())
            return True
        transfer = re.fullmatch(
            r"(?:please\s+)?(?:give|hand)\s+me\s+(?:the\s+)?(?P<item>.+)", text, re.I,
        )
        if transfer is not None:
            self.transfer("companion", "user", transfer.group("item"), self.full_span())
            return True
        transfer = re.fullmatch(
            r"(?:i\s+(?:hand|give)\s+you|here[,]?\s+you\s+take|"
            r"(?:please\s+)?take)\s+(?P<item>(?:this\s+|the\s+|an?\s+)?.+)",
            text, re.I,
        )
        if transfer is not None:
            self.transfer("user", "companion", transfer.group("item"), self.full_span(), introduce=True)
            return True
        retire = re.fullmatch(
            r"(?:please\s+)?throw\s+(?:the\s+)?(?P<item>[a-z][a-z -]{0,48})\s+away",
            text, re.I,
        )
        if retire is not None:
            self.retire_named(retire.group("item"), self.full_span())
            return True
        if re.fullmatch(r"i\s+(?:put\s+(?:it|that)\s+away|threw\s+(?:it|that)\s+away|got\s+rid\s+of\s+(?:it|that))", text, re.I):
            self.retire_unique(self.full_span())
            return True

        companion_bicycle = re.fullmatch(
            r"you\s+get\s+on\s+(?P<item>(?:a\s+|the\s+)?bicycle)", text, re.I,
        )
        if companion_bicycle is not None:
            self.add_transport(
                "companion", companion_bicycle.group("item"), "riding", "cycling", self.full_span(),
            )
            return True
        user_bicycle = re.fullmatch(
            r"i(?:'m|\s+am)\s+riding\s+(?P<item>(?:a\s+|the\s+)?(?:bicycle|bike))",
            text, re.I,
        )
        if user_bicycle is not None:
            self.add_transport(
                "user", user_bicycle.group("item"), "riding", "cycling", self.full_span(),
            )
            return True
        user_car = re.fullmatch(
            r"i(?:'m|\s+am)\s+driving\s+(?P<item>(?:a\s+|the\s+)?car)", text, re.I,
        )
        if user_car is not None:
            self.add_transport(
                "user", user_car.group("item"), "driving", "driving", self.full_span(),
            )
            return True
        if re.fullmatch(r"i(?:'m|\s+am)\s+using\s+crutches", text, re.I):
            self.add_transport("user", "crutches", "using", "assisted", self.full_span())
            return True
        user_wheelchair = re.fullmatch(
            r"i(?:'m|\s+am)\s+in\s+(?P<item>(?:a\s+|the\s+)?wheelchair)", text, re.I,
        )
        if user_wheelchair is not None:
            self.add_transport(
                "user", user_wheelchair.group("item"), "seated_in", "assisted", self.full_span(),
            )
            return True
        companion_horse = re.fullmatch(
            r"you\s+(?:mount|get\s+on)\s+(?P<item>(?:a\s+|the\s+)?horse)", text, re.I,
        )
        if companion_horse is not None:
            self.add_transport(
                "companion", companion_horse.group("item"), "riding", "riding", self.full_span(),
            )
            return True
        if re.fullmatch(r"you(?:'re|\s+are)\s+using\s+crutches", text, re.I):
            self.add_transport("companion", "crutches", "using", "assisted", self.full_span())
            return True
        companion_wheelchair = re.fullmatch(
            r"you(?:'re|\s+are)\s+in\s+(?P<item>(?:a\s+|the\s+)?wheelchair)", text, re.I,
        )
        if companion_wheelchair is not None:
            self.add_transport(
                "companion", companion_wheelchair.group("item"), "seated_in", "assisted",
                self.full_span(),
            )
            return True
        dismount = re.fullmatch(
            r"you\s+(?:get\s+off|dismount)\s+(?:the\s+|your\s+)?(?P<item>bicycle|bike|horse)|"
            r"you\s+get\s+out\s+of\s+(?:the\s+|a\s+)?(?P<car>car)|"
            r"you\s+stop\s+using\s+(?P<aid>crutches)|"
            r"you\s+get\s+out\s+of\s+(?:the\s+|your\s+)?(?P<chair>wheelchair)",
            text, re.I,
        )
        if dismount is not None:
            item = dismount.group("item") or dismount.group("car") or dismount.group("aid") or dismount.group("chair")
            self.clear_transport("companion", item, self.full_span())
            return True
        user_dismount = re.fullmatch(
            r"i\s+(?:get\s+off|dismount)\s+(?:the\s+|my\s+)?(?P<item>bicycle|bike)|"
            r"i\s+get\s+out\s+of\s+(?:the\s+|a\s+)?(?P<car>car)|"
            r"i\s+stop\s+using\s+(?P<aid>crutches)|"
            r"i\s+get\s+out\s+of\s+(?:the\s+|my\s+)?(?P<chair>wheelchair)",
            text, re.I,
        )
        if user_dismount is not None:
            item = (user_dismount.group("item") or user_dismount.group("car")
                    or user_dismount.group("aid") or user_dismount.group("chair"))
            self.clear_transport("user", item, self.full_span())
            return True
        return False

    def add_blindfold(self, span: tuple[int, int]) -> bool:
        if len(self.matching_subjects("blindfold")) > 1:
            # An article-less action may introduce a blindfold when none is
            # current, but it must not guess among multiple existing objects.
            self._blocked_reason = "ambiguous_subject_reference"
            return False
        # The action names a semantically definite instrument even though the
        # surface omits an article. Reuse one uniquely current blindfold (for
        # example, one already held) instead of cloning it when its relation
        # changes from holding to covering.
        ref, item = self.subject_for(
            "the blindfold", span, actor="companion", predicate="wearing",
        )
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, "companion", _SUBJECT_OCCUPANCY_PREDICATES, "covered_by", span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", "companion", "eyes", "covered_by", "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
            semantic_family="vision_obstruction",
        )
        return True

    def add_worn(
        self, actor: str, raw_item: str, span: tuple[int, int], *,
        replace_exclusive: bool = False, force_new: bool = False,
    ) -> None:
        item = _parse_item(raw_item)
        if item is None:
            return
        if actor == "companion":
            self.set_profile_mode("suppressed", span)
        side = _side(raw_item)
        if replace_exclusive and item.region in {"head", "eyes", "hands", "feet", "full_outfit"}:
            current = self.current_relation_matches(
                actor, predicates={"wearing", "worn_by"}, facet=item.region,
            )
            for relation in current:
                if (side is None or relation.side is None
                        or relation.side in {side, "both"}):
                    self.clear_relation_record(relation, span)
        ref, item = self.subject_for(
            raw_item, span, actor=actor, predicate="wearing", force_new=force_new,
        )
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, actor, _SUBJECT_OCCUPANCY_PREDICATES, "wearing", span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span, side=side)
        semantic = (
            _LOCOMOTION_EQUIPMENT.get(item.kind)
            or ("body_assistance" if item.kind in _BODY_ASSISTANCE else None)
        )
        self.add_relation(
            "set", actor, item.region, "wearing", "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
            semantic_family=semantic, quantity=item.quantity, side=side,
        )

    def add_sensory_equipment(
        self, actor: str, raw_item: str, facet: str, sense: str, span: tuple[int, int],
    ) -> bool:
        if len(self.matching_subjects(raw_item)) > 1:
            self._blocked_reason = "ambiguous_subject_reference"
            return False
        ref, item = self.subject_for(raw_item, span, actor=actor, predicate="wearing")
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, actor, _SUBJECT_OCCUPANCY_PREDICATES, "wearing", span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", actor, facet, "wearing", "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
            semantic_family=f"{sense}_obstruction", effect_state="constrained",
        )
        return True

    def add_mouth_obstruction(
        self, raw_item: str, span: tuple[int, int], *, effect_state: str,
    ) -> bool:
        """Attach one explicit object to the existing speech capability lane."""
        if len(self.matching_subjects(raw_item)) > 1:
            self._blocked_reason = "ambiguous_subject_reference"
            return False
        definite = raw_item if re.match(r"\s*(?:the|your)\b", raw_item, re.I) else "the " + raw_item
        ref, item = self.subject_for(
            definite, span, actor="companion", predicate="occupied_by",
        )
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, "companion", _SUBJECT_OCCUPANCY_PREDICATES, "occupied_by", span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", "companion", "mouth", "occupied_by", "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
            semantic_family="speech_obstruction", effect_state=effect_state,
        )
        return True

    def add_environmental_sense(
        self, sense: str, cause: str, effect_state: str, span: tuple[int, int], *,
        targets: tuple[str, ...],
    ) -> None:
        facet = {
            "vision": "eyes", "hearing": "ears", "smell": "nose",
            "taste": "tongue", "touch": "skin",
        }[sense]
        predicate = "unavailable_due_to" if effect_state == "unavailable" else "obstructed_by"
        for actor in targets:
            self.add_relation(
                "set", actor, facet, predicate, "environment", cause.strip(), span,
                semantic_family=f"{sense}_obstruction", effect_state=effect_state,
            )

    def clear_environmental_sense(
        self, sense: str, cause_tokens: set[str], span: tuple[int, int],
    ) -> None:
        family = f"{sense}_obstruction"
        matches = [
            relation for relation in self.current_relations
            if relation.cause_kind == "environment"
            and relation.semantic_family == family
            and bool(_tokens(relation.cause) & cause_tokens)
        ]
        for relation in matches:
            self.clear_relation_record(relation, span)

    def set_subject_attribute(
        self, hint: str, raw_value: str, span: tuple[int, int],
    ) -> bool:
        matches = self.matching_subjects(hint)
        if len(matches) != 1:
            return False
        value = raw_value.strip().casefold()
        if value in {"wet", "dry"}:
            attribute, normalized = "wet", "true" if value == "wet" else "false"
        elif value in {"muddy", "clean"}:
            attribute, normalized = "stain", "mud" if value == "muddy" else "clean"
        elif all(
            token in _COLOR_WORDS or token == "and"
            for token in value.split()
        ) and any(token in _COLOR_WORDS for token in value.split()):
            attribute, normalized = "color", value
        else:
            attribute, normalized = "condition", value
        self.updates.append(ActiveStateProposalUpdate(
            operation="set", value=normalized, excerpt_start_cp=span[0], excerpt_end_cp=span[1],
            target_kind="scene", target_ref=matches[0], attribute=attribute,
        ))
        if attribute == "color":
            # Relations are canonical for ownership/capability, while their
            # bounded cause label is a human-facing projection of the stable
            # subject. Keep that projection in the same atomic mutation as a
            # color change so prompts, direct queries, and Unity cannot retain
            # a stale "blue hat" after the subject becomes red.
            subject_id = matches[0]
            kind = self.subjects[subject_id].get("kind")
            if kind is not None:
                condition = self.subjects[subject_id].get("condition")
                side = self.subjects[subject_id].get("side")
                descriptor = (
                    condition.value if condition is not None
                    and condition.value in _DECORATIVE_DESCRIPTORS else ""
                )
                label = " ".join(part for part in (
                    normalized, side.value if side is not None else "", descriptor, kind.value,
                ) if part)
                for relation in self.current_relations:
                    if relation.cause_subject_id != subject_id or relation.cause == label:
                        continue
                    self.clear_relation_record(relation, span)
                    self.add_relation(
                        "set", relation.target, relation.facet, relation.predicate,
                        relation.cause_kind, label, span,
                        target_kind=relation.target_kind, side=relation.side,
                        cause_subject_kind=kind.value, cause_subject_ref=subject_id,
                        semantic_family=relation.semantic_family,
                        quantity=relation.quantity, effect_state=relation.effect_state,
                        locus=relation.locus,
                    )
        return True

    def complete_replacement_item(self, old_hint: str, new_value: str) -> str | None:
        parsed = _parse_item(new_value)
        if parsed is not None and parsed.region is not None:
            return new_value
        matches = self.matching_subjects(old_hint)
        if len(matches) != 1:
            return None
        old_kind = self.subjects[matches[0]].get("kind")
        if old_kind is None:
            return None
        compact = new_value.strip()
        if re.fullmatch(r"[a-z]+(?:\s+and\s+[a-z]+)?\s+one", compact, re.I):
            compact = re.sub(r"\s+one$", "", compact, flags=re.I)
            return f"{compact} {old_kind.value}"
        return new_value if _parse_item(new_value) is not None else None

    def add_held(
        self, actor: str, raw_item: str, span: tuple[int, int], *, predicate: str,
    ) -> None:
        item = _parse_item(raw_item, hand_context=True)
        if item is None:
            return
        side = _side(raw_item)
        ref, item = self.subject_for(raw_item, span, actor=actor, predicate=predicate)
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, actor, _SUBJECT_OCCUPANCY_PREDICATES, predicate, span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span, side=side)
        self.add_relation(
            "set", actor, "hands", predicate, "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
            semantic_family="hand_occupancy", quantity=item.quantity, side=side,
        )

    def add_transport(
        self, actor: str, raw_item: str, predicate: str, semantic: str, span: tuple[int, int],
    ) -> None:
        ref, item = self.subject_for(raw_item, span, actor=actor, predicate=predicate)
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, actor, _SUBJECT_OCCUPANCY_PREDICATES, predicate, span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", actor, None, predicate, "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref, semantic_family=semantic,
        )

    def clear_transport(self, actor: str, hint: str, span: tuple[int, int]) -> None:
        matches = self.current_relation_matches(
            actor, hint, {"riding", "driving", "seated_in", "using", "supported_by"},
        )
        if len(matches) == 1:
            self.clear_relation_record(matches[0], span)

    def add_nearby_subject(self, raw_item: str, span: tuple[int, int]) -> None:
        ref, item = self.subject_for(raw_item, span, actor="companion", predicate="near")
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, "companion", _SUBJECT_OCCUPANCY_PREDICATES, "near", span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", "companion", None, "near", "scene", item.label, span,
            cause_subject_kind=item.kind, cause_subject_ref=ref,
        )

    def add_located_subject(
        self, raw_item: str, location: str, relation: str,
        span: tuple[int, int],
    ) -> None:
        """Establish one sparse subject at one explicit non-simulated location."""
        ref, item = self.subject_for(
            raw_item, span, actor="companion", predicate="located_" + relation.casefold(),
        )
        if ref in self.subjects:
            self.clear_subject_ownership(
                ref, "companion", _SUBJECT_OCCUPANCY_PREDICATES,
                "located_" + relation.casefold(), span,
            )
            self.clear_subject_locations(ref, span)
        self._metadata(ref, item, span)
        self.add_relation(
            "set", ref, None,
            "located_in" if relation.casefold() == "in" else "located_on",
            "location", location.strip(), span, target_kind="scene",
        )

    def subject_for(
        self, raw_item: str, span: tuple[int, int], *, actor: str, predicate: str,
        force_new: bool = False,
    ) -> tuple[str, _Item]:
        item = _parse_item(raw_item, hand_context=predicate in {"holding", "carrying"})
        if item is None:
            # Callers prevalidate; this bounded fallback is never instruction text.
            item = _Item("object", "object", None, 1, None, None)
        matches = [] if force_new else self.matching_subjects(item.label)
        current_ids = {
            relation.cause_subject_id for relation in self.current_relations
            if relation.target == actor and relation.predicate == predicate
        }
        same_relation_matches = [
            subject_id for subject_id in matches if subject_id in current_ids
        ]
        definite = re.match(r"\s*(?:the|your|both)\b", raw_item, re.I) is not None
        exact_reusable = [
            subject_id for subject_id in same_relation_matches
            if _tokens(self.subject_label(subject_id)) == _tokens(item.label)
        ]
        if len(exact_reusable) == 1:
            return exact_reusable[0], item
        if definite and len(same_relation_matches) == 1:
            return same_relation_matches[0], item
        # A uniquely identified wearable/equipment subject keeps its identity
        # across removal and re-wear. Generic held objects remain conservative:
        # a new indefinite "a cup" is not silently merged with an old cup.
        if (len(matches) == 1 and (definite or item.color is not None)
                and (matches[0] in self.active_subject_ids
                     or matches[0] in self.reusable_subject_ids)):
            return matches[0], item
        retired_matches = (
            [] if force_new
            else self.matching_subjects(item.label, subjects=self.retired_subjects)
        )
        if len(retired_matches) == 1 and (definite or item.color is not None):
            retired_id = retired_matches[0]
            self.reactivations.append(ActiveSceneSubjectReactivation(retired_id, *span))
            self.subjects[retired_id] = self.retired_subjects[retired_id]
            return retired_id, item
        reference = f"item_{self._next_ref}"
        self._next_ref += 1
        self.introductions.append(ActiveSceneSubjectIntroduction(
            reference, item.kind, *self.full_span(),
            identity_strength=(
                "distinct" if item.color is not None or item.descriptors or definite else "generic"
            ),
        ))
        return reference, item

    def _metadata(
        self, reference: str, item: _Item, span: tuple[int, int], *, side: str | None = None,
    ) -> None:
        for attribute, value in (
            ("color", item.color),
            ("condition", " ".join(item.descriptors) if item.descriptors else None),
            ("region", item.region),
            ("quantity", str(item.quantity) if item.quantity > 1 else None),
            ("set_label", item.set_label),
            ("side", side),
        ):
            if value is not None:
                self.updates.append(ActiveStateProposalUpdate(
                    operation="set", value=value, excerpt_start_cp=span[0], excerpt_end_cp=span[1],
                    target_kind="scene", target_ref=reference, attribute=attribute,
                ))

    def matching_subjects(
        self, hint: str, *, subjects: dict[str, dict[str, ActiveStateRecord]] | None = None,
    ) -> list[str]:
        hint_tokens = _tokens(hint)
        matches = []
        for subject_id, attributes in (subjects if subjects is not None else self.subjects).items():
            kind = attributes.get("kind")
            color = attributes.get("color")
            condition = attributes.get("condition")
            side = attributes.get("side")
            label = " ".join(value.value for value in (color, side, condition, kind) if value is not None)
            if hint_tokens and hint_tokens <= _tokens(label):
                matches.append(subject_id)
        if subjects is None:
            active_ids = {
                subject_id
                for relation in self.current_relations
                for subject_id in (
                    relation.cause_subject_id,
                    relation.target if relation.target_kind == "scene" else None,
                )
                if subject_id is not None
            }
            active_matches = [subject_id for subject_id in matches if subject_id in active_ids]
            if active_matches:
                return active_matches
        return matches

    def subject_label(self, subject_id: str) -> str:
        attributes = self.subjects.get(subject_id, {})
        return " ".join(
            value.value for value in (
                attributes.get("color"), attributes.get("side"),
                attributes.get("condition"), attributes.get("kind"),
            ) if value is not None
        )

    def current_relation_matches(
        self,
        actor: str,
        hint: str | None = None,
        predicates: set[str] | None = None,
        facet: str | None = None,
        side: str | None = None,
    ) -> list[ActiveSceneRelationRecord]:
        hint_tokens = _tokens(hint)
        rows = []
        for relation in self.current_relations:
            if relation.target_kind != "actor" or relation.target != actor:
                continue
            if predicates is not None and relation.predicate not in predicates:
                continue
            if facet is not None and relation.facet != facet:
                continue
            if side is not None and relation.side not in {side, "both"}:
                continue
            # A qualified reference is only a match when every meaningful
            # qualifier is present.  Token intersection made "red scarf"
            # also resolve to a red hat in accumulated scenes, turning an
            # otherwise unambiguous atomic replacement into an abstention.
            if hint_tokens and not hint_tokens <= _tokens(relation.cause):
                continue
            rows.append(relation)
        return rows

    def clear_relations(
        self, actor: str, *, cause_hint: str | None = None,
        facet: str | None = None, span: tuple[int, int],
    ) -> None:
        matches = self.current_relation_matches(actor, cause_hint, facet=facet)
        if len(matches) == 1:
            self.clear_relation_record(matches[0], span)
        elif cause_hint is None:
            self.add_relation("clear", actor, facet, None, None, None, span)

    def clear_worn(self, actor: str, hint: str, span: tuple[int, int]) -> None:
        if actor == "companion":
            self.set_profile_mode("suppressed", span)
        clean = re.sub(r"^(?:the|your)\s+", "", hint.strip(), flags=re.I)
        matches = self.current_relation_matches(actor, clean, {"wearing", "worn_by"})
        if len(matches) == 1:
            self.clear_relation_record(matches[0], span)

    def can_replace_worn(self, actor: str, old_hint: str, new_item: str) -> bool:
        """Require both halves of an explicit destructive replacement."""
        if _parse_item(new_item) is None:
            return False
        clean = re.sub(r"^(?:the|your)\s+", "", old_hint.strip(), flags=re.I)
        return len(self.current_relation_matches(
            actor, clean, {"wearing", "worn_by"},
        )) == 1

    def clear_worn_reference(self, actor: str, hint: str, span: tuple[int, int]) -> None:
        clean = hint.strip().casefold()
        matches = self.current_relation_matches(
            actor, None if clean in {"it", "that"} else clean,
            {"wearing", "worn_by"},
        )
        if len(matches) == 1:
            self.clear_relation_record(matches[0], span)

    def clear_held_reference(self, actor: str, hint: str, span: tuple[int, int]) -> None:
        clean = hint.strip().casefold()
        matches = self.current_relation_matches(
            actor, None if clean in {"it", "that"} else clean,
            {"holding", "carrying"},
        )
        if len(matches) == 1:
            self.clear_relation_record(matches[0], span)

    def clear_relation_record(
        self, item: ActiveSceneRelationRecord, span: tuple[int, int],
    ) -> None:
        self.add_relation(
            "clear", item.target, item.facet, item.predicate, item.cause_kind, item.cause,
            span, target_kind=item.target_kind,
            cause_subject_ref=item.cause_subject_id, side=item.side,
            locus=item.locus,
        )

    def clear_actor_slot(
        self, actor: str, predicates: set[str], facet: str, span: tuple[int, int],
    ) -> None:
        for item in self.current_relation_matches(actor, predicates=predicates, facet=facet):
            self.clear_relation_record(item, span)

    def clear_all_actor_relations(
        self, actor: str, predicates: set[str], span: tuple[int, int], *,
        suppress_profile: bool = True,
    ) -> None:
        if actor == "companion" and suppress_profile and predicates & {"wearing", "worn_by"}:
            self.set_profile_mode("suppressed", span)
        for item in self.current_relation_matches(actor, predicates=predicates):
            self.clear_relation_record(item, span)

    def clear_subject_ownership(
        self,
        subject_id: str,
        actor: str,
        predicates: set[str],
        establishing_predicate: str,
        span: tuple[int, int],
    ) -> None:
        """Prevent one stable subject from acquiring two current owners."""
        for relation in self.current_relations:
            if relation.cause_subject_id != subject_id or relation.predicate not in predicates:
                continue
            if relation.target == actor and relation.predicate == establishing_predicate:
                continue
            self.clear_relation_record(relation, span)

    def clear_subject_locations(
        self, subject_id: str, span: tuple[int, int],
    ) -> None:
        """A sparse subject has at most one current explicit location."""
        for relation in self.current_relations:
            if (relation.target_kind == "scene" and relation.target == subject_id
                    and relation.predicate in {"located_on", "located_in"}):
                self.clear_relation_record(relation, span)

    def set_profile_mode(self, mode: str, span: tuple[int, int]) -> None:
        if not self.profile_available or self._profile_mode_set:
            return
        self.updates.append(ActiveStateProposalUpdate(
            operation="set", value=mode,
            excerpt_start_cp=span[0], excerpt_end_cp=span[1],
            target_kind="actor", target_ref="companion",
            attribute="profile_scene_mode",
        ))
        self._profile_mode_set = True

    def set_actor_posture(self, actor: str, value: str, span: tuple[int, int]) -> None:
        self.updates.append(ActiveStateProposalUpdate(
            operation="set", value=value,
            excerpt_start_cp=span[0], excerpt_end_cp=span[1],
            target_kind="actor", target_ref=actor, attribute="posture",
        ))

    def release_subject(
        self, actor: str, hint: str, span: tuple[int, int], *,
        location: str | None = None, location_relation: str | None = None,
    ) -> bool:
        """Close one current holder and optionally establish a destination."""
        compact_hint = hint.strip().casefold()
        matches = self.current_relation_matches(
            actor, None if compact_hint in {"it", "that"} else hint,
            {"holding", "carrying"},
        )
        if len(matches) != 1:
            return False
        held = matches[0]
        self.clear_relation_record(held, span)
        if location and held.cause_subject_id:
            self.clear_subject_locations(held.cause_subject_id, span)
            self.add_relation(
                "set", held.cause_subject_id, None,
                "located_in" if str(location_relation or "").casefold() == "in" else "located_on",
                "location",
                location.strip(), span, target_kind="scene",
            )
        return True

    def release_unique_holder(
        self, hint: str, span: tuple[int, int], *,
        location: str | None = None, location_relation: str | None = None,
    ) -> bool:
        clean = hint.strip().casefold()
        hint_value = None if clean in {"it", "that"} else hint
        matches = []
        for actor in ("companion", "user"):
            matches.extend(self.current_relation_matches(
                actor, hint_value, {"holding", "carrying"},
            ))
        if len(matches) != 1:
            return False
        return self.release_subject(
            matches[0].target, hint, span, location=location,
            location_relation=location_relation,
        )

    def locate_subject(
        self, hint: str, location: str, location_relation: str | None,
        span: tuple[int, int],
    ) -> bool:
        clean = re.sub(r"^(?:the|your|my)\s+", "", hint.strip(), flags=re.I)
        if clean.casefold() in {"it", "that"}:
            return False
        matches = self.matching_subjects(clean)
        if len(matches) != 1:
            return False
        subject_id = matches[0]
        self.clear_subject_locations(subject_id, span)
        self.add_relation(
            "set", subject_id, None,
            "located_in" if str(location_relation or "").casefold() == "in" else "located_on",
            "location", location.strip(), span, target_kind="scene",
        )
        return True

    def put_down_reference(
        self, actor: str, hint: str, location: str, relation: str,
        span: tuple[int, int],
    ) -> None:
        clean = hint.strip().casefold()
        matches = self.current_relation_matches(
            actor, None if clean in {"it", "that"} else clean,
            {"holding", "carrying"},
        )
        if len(matches) != 1 or matches[0].cause_subject_id is None:
            return
        held = matches[0]
        self.release_subject(
            actor, hint, span, location=location, location_relation=relation,
        )

    def transfer(
        self, source: str, target: str, hint: str, span: tuple[int, int], *, introduce: bool = False,
    ) -> None:
        matches = self.current_relation_matches(source, hint, {"holding", "carrying"})
        if len(matches) == 1:
            held = matches[0]
            self.clear_relation_record(held, span)
            self.add_relation(
                "set", target, "hands", "holding", "scene", held.cause, span,
                cause_subject_kind=(
                    self.subjects.get(str(held.cause_subject_id), {}).get("kind").value
                    if held.cause_subject_id is not None
                    and self.subjects.get(str(held.cause_subject_id), {}).get("kind") is not None
                    else held.cause
                ),
                cause_subject_ref=held.cause_subject_id,
                semantic_family="hand_occupancy", quantity=held.quantity or 1,
                side=held.side,
            )
            return
        if introduce:
            self.add_held(target, hint, span, predicate="holding")

    def retire_named(self, hint: str, span: tuple[int, int]) -> None:
        matches = self.matching_subjects(hint)
        if len(matches) == 1:
            self.retirements.append(ActiveSceneSubjectRetirement(matches[0], *span))

    def retire_unique(self, span: tuple[int, int]) -> None:
        if len(self.subjects) == 1:
            self.retirements.append(ActiveSceneSubjectRetirement(next(iter(self.subjects)), *span))

    def add_relation(
        self,
        operation: str,
        target: str,
        facet: str | None,
        predicate: str | None,
        cause_kind: str | None,
        cause: str | None,
        span: tuple[int, int],
        *,
        target_kind: str = "actor",
        side: str | None = None,
        cause_subject_kind: str | None = None,
        cause_subject_ref: str | None = None,
        semantic_family: str | None = None,
        quantity: int | None = None,
        effect_state: str | None = None,
        locus: str | None = None,
    ) -> None:
        self.relations.append(SceneRelationProposal(
            operation, target, facet, predicate, cause_kind, cause, cause_subject_kind,
            span[0], span[1], target_kind=target_kind, side=side,
            cause_subject_ref=cause_subject_ref, semantic_family=semantic_family,
            quantity=quantity, effect_state=effect_state, locus=locus,
        ))
        # Backward-compatible current-scene mirrors keep existing snapshot/UI
        # consumers working. Relations remain authoritative for capability
        # derivation and lifecycle; both mutations are applied atomically.
        if cause_subject_ref is not None and target_kind == "actor":
            attribute = (
                "worn_by" if predicate in {"wearing", "worn_by"}
                else "held_by" if predicate in {"holding", "carrying"}
                else None
            )
            if attribute is not None:
                self.updates.append(ActiveStateProposalUpdate(
                    operation=operation,
                    value=target if operation == "set" else None,
                    excerpt_start_cp=span[0] if operation == "set" else None,
                    excerpt_end_cp=span[1] if operation == "set" else None,
                    target_kind="scene", target_ref=cause_subject_ref,
                    attribute=attribute,
                ))
        if target_kind == "scene" and predicate in {"located_on", "located_in"}:
            self.updates.append(ActiveStateProposalUpdate(
                operation=operation,
                value=cause if operation == "set" else None,
                excerpt_start_cp=span[0] if operation == "set" else None,
                excerpt_end_cp=span[1] if operation == "set" else None,
                target_kind="scene", target_ref=target, attribute="location",
            ))

    def finish(self) -> ActiveSceneMutation | None:
        if not (self.updates or self.introductions or self.retirements
                or self.reactivations or self.relations):
            return (
                ActiveSceneMutation(None, (), self._blocked_reason)
                if self._blocked_reason is not None else None
            )
        # A transfer/replacement may clear and then set the same compatibility
        # mirror inside one atomic turn. The relation batch preserves both
        # lifecycle operations; the singleton mirror needs only the final
        # value and the governed proposal contract intentionally forbids two
        # writes to one slot.
        coalesced_reversed: list[ActiveStateProposalUpdate] = []
        seen: set[tuple[object, ...]] = set()
        for update in reversed(self.updates):
            identity = (
                update.target_kind, update.target_ref, update.attribute,
                update.slot if update.target_kind == "slot" else None,
            )
            if identity in seen:
                continue
            seen.add(identity)
            coalesced_reversed.append(update)
        updates = tuple(reversed(coalesced_reversed))
        proposal = (
            ActiveStateProposal(
                updates, tuple(self.introductions), tuple(self.retirements),
                tuple(self.reactivations),
            )
            if updates or self.introductions or self.retirements or self.reactivations else None
        )
        return ActiveSceneMutation(proposal, tuple(self.relations))


def _parse_item(value: str, *, hand_context: bool = False) -> _Item | None:
    compact = _TRAILING.sub("", " ".join(str(value or "").strip().split()))
    if not compact or len(compact) > 72 or _AMBIGUOUS_ITEM.search(compact):
        return None
    explicit_region = None
    attachment = re.search(
        r"\s+on\s+(?:(?:the|your)\s+)?(?:(?:left|right)\s+)?"
        r"(wrist|head|neck|torso|waist|arm|leg|foot|hand)$",
        compact, re.I,
    )
    if attachment is not None:
        explicit_region = {
            "wrist": "wrists", "arm": "arms", "leg": "legs", "foot": "feet",
            "hand": "hands", "head": "head", "neck": "neck", "torso": "torso",
            "waist": "waist",
        }[attachment.group(1).casefold()]
        compact = compact[:attachment.start()].rstrip()
    compact = re.sub(
        r"\s+in\s+(?:(?:each|one|both|(?:(?:the|your)\s+)?(?:left|right))\s+hands?|"
        r"(?:the\s+other|other)(?:\s+hand)?)$",
        "", compact, flags=re.I,
    )
    quantity = 1
    set_label = None
    pair = re.match(r"(?:a\s+)?pair\s+of\s+", compact, re.I)
    if pair is not None:
        quantity, set_label, compact = 2, "pair", compact[pair.end():]
    else:
        quantity_match = re.match(r"(one|two|three|four|both|\d{1,2})\s+", compact, re.I)
        if quantity_match is not None:
            token = quantity_match.group(1).casefold()
            quantity = _QUANTITIES.get(token, int(token) if token.isdigit() else 1)
            compact = compact[quantity_match.end():]
            set_label = "set" if quantity > 1 else None
        else:
            compact = re.sub(
                r"^(?:an?|the|this|that|your|my|matching)\s+", "", compact, flags=re.I,
            )
    if hand_context and re.search(r"\bin\s+each\s+hand\b", value, re.I):
        quantity, set_label = 2, "set"
    words = compact.split()
    side_descriptor = ""
    if words and words[0].casefold() in {"left", "right"}:
        side_descriptor = words.pop(0).casefold()
    color = None
    if (len(words) >= 3 and words[0].casefold() in _COLOR_WORDS
            and words[1].casefold() == "and" and words[2].casefold() in _COLOR_WORDS):
        color = " ".join((words.pop(0), words.pop(0), words.pop(0))).casefold()
    elif words and words[0].casefold() in _COLOR_WORDS:
        color = words.pop(0).casefold()
    elif len(words) >= 2 and words[0].casefold() in {"dark", "light"} and words[1].casefold() in _COLOR_WORDS:
        color = (words.pop(0) + " " + words.pop(0)).casefold()
    if not words:
        return None
    descriptors: list[str] = []
    while words and words[0].casefold() in _DECORATIVE_DESCRIPTORS:
        descriptors.append(words.pop(0).casefold())
    if not words:
        return None
    kind = " ".join(words).casefold()
    kind = _SINGULAR.get(kind, kind)
    if not re.fullmatch(r"[a-z][a-z'’ -]{0,47}", kind):
        return None
    if quantity > 16:
        return None
    if quantity == 1 and kind in {
        "gloves", "boots", "shoes", "sneakers", "rollerblades", "skates", "skis",
    }:
        quantity, set_label = 2, "pair"
    region = explicit_region or _REGIONS.get(kind)
    label = " ".join(part for part in (
        color or "", side_descriptor, " ".join(descriptors), kind,
    ) if part)
    return _Item(kind, label, color, quantity, set_label, region, tuple(descriptors))


def _side(value: str) -> str | None:
    lowered = value.casefold()
    if "each hand" in lowered or "both hands" in lowered:
        return "both"
    if re.search(r"\bleft\b", lowered):
        return "left"
    if re.search(r"\bright\b", lowered):
        return "right"
    return None


def _tokens(value: object) -> set[str]:
    return {
        _SINGULAR.get(token, token)
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in {"a", "an", "the", "your", "my", "both", "pair", "of"}
    }
