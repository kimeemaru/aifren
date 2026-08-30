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
