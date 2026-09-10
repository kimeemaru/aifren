"""Small, governed contract for durable-core Memory V2 claims.

This module deliberately describes only the narrow 1.0 durable facts.  It is
not a general fact classifier, entity graph, or prompt-admission policy.
"""

from __future__ import annotations

import re


DURABLE_CORE_FACT = "durable_core_fact"
DURABLE_ASSERTION_SCOPE = "user_fact"
DURABLE_USER_EVIDENCE_ROLES = frozenset({"direct_user_statement", "user_confirmation"})
DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE = "legacy_memory_bridge"
DURABLE_EVIDENCE_ROLES = frozenset({
    *DURABLE_USER_EVIDENCE_ROLES,
    DURABLE_LEGACY_BRIDGE_EVIDENCE_ROLE,
})

# Administrative corrections are explicit user edits, but they are not
# conversation records.  These stable audit values keep them distinct from
# canonical dialogue, legacy bridges, and synthetic/system-derived evidence.
MEMORY_VIEWER_CORRECTION_EVENT_TYPE = "memory_viewer_correction"
MEMORY_VIEWER_CORRECTION_SOURCE_ORIGIN = "memory_viewer"
DURABLE_SOURCE_CLASS_VIEWER_CORRECTION = "explicit_viewer_correction"
DURABLE_SOURCE_CLASS_CONVERSATION = "canonical_conversation"
DURABLE_SOURCE_CLASS_LEGACY_BRIDGE = "legacy_v1_bridge"
DURABLE_SOURCE_CLASS_SYNTHETIC_SYSTEM = "synthetic_or_system_derived"
DURABLE_SOURCE_CLASS_OTHER = "other_retained_source"

SINGLETON_DURABLE_KEYS = frozenset({
    "identity.name",
    "address.preferred",
    "home.primary",
    "bio.occupation",
    "bio.school",
    "device.gpu",
    "device.computer",
    "pet.primary",
    "project.primary",
})
PREFERENCE_DOMAINS = frozenset({"beverage", "color", "food", "media", "game", "communication"})
RELATION_ROLES = frozenset({"sibling", "parent", "partner", "friend", "coworker", "relative"})

_PERSON_ID = re.compile(r"p-[0-9a-f]{8,32}$")
_TOPIC_KEY = re.compile(r"(?:preference|interest|possession)\.topic\.[0-9a-f]{16}$")


def validate_durable_subject_key(value: object) -> str:
    """Return a governed subject key or raise ``ValueError``.

    Person identifiers are opaque local identifiers, never display names.
    They give relationship facts stable correction identity without adding an
    entity table or relationship-state model.
    """
    if not isinstance(value, str):
        raise ValueError("durable subject_key must be text")
    key = value.strip().lower()
    if key in SINGLETON_DURABLE_KEYS:
        return key
    if key.startswith("preference.") and key.removeprefix("preference.") in PREFERENCE_DOMAINS:
        return key
    if _TOPIC_KEY.fullmatch(key):
        return key
    parts = key.split(".")
    if len(parts) == 3 and parts[0] == "relation" and parts[1] in RELATION_ROLES and _PERSON_ID.fullmatch(parts[2]):
        return key
    raise ValueError("durable subject_key is not in the governed 1.0 namespace")


def is_singleton_durable_key(subject_key: str) -> bool:
    """Preferences are singleton per approved domain as well as named slots."""
    return (
        subject_key in SINGLETON_DURABLE_KEYS
        or subject_key.startswith("preference.")
        or _TOPIC_KEY.fullmatch(subject_key) is not None
    )


def classify_durable_source(
    *, event_type: object, actor_kind: object, source_origin: object,
) -> str:
    """Return the product-facing audit class for retained durable evidence."""
    event_type = str(event_type or "")
    actor_kind = str(actor_kind or "")
    source_origin = str(source_origin or "")
    if (
        event_type == MEMORY_VIEWER_CORRECTION_EVENT_TYPE
        and source_origin == MEMORY_VIEWER_CORRECTION_SOURCE_ORIGIN
        and actor_kind == "user"
    ):
        return DURABLE_SOURCE_CLASS_VIEWER_CORRECTION
    if source_origin == "canonical_conversation" and actor_kind == "user":
        return DURABLE_SOURCE_CLASS_CONVERSATION
    if source_origin == "legacy_v1_import":
        return DURABLE_SOURCE_CLASS_LEGACY_BRIDGE
    if actor_kind == "system" or source_origin.startswith("synthetic"):
        return DURABLE_SOURCE_CLASS_SYNTHETIC_SYSTEM
    return DURABLE_SOURCE_CLASS_OTHER
