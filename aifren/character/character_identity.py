"""Stable identifiers shared by legacy-character migration boundaries."""

from __future__ import annotations

import uuid


LEGACY_CHARACTER_NAMESPACE = uuid.UUID("f4da4317-6e5f-43ad-9f2b-a17f4d2b0c58")


def default_legacy_character_id() -> str:
    """The one deterministic identity for pre-registry single-character data."""
    return str(uuid.uuid5(LEGACY_CHARACTER_NAMESPACE, "production:characters/default"))
