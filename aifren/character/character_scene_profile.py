"""Validated lower-authority default scene facts from a character profile."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from aifren.memory_v2_store import MemoryV2Repository


PROFILE_CACHE_KEY = "aifren_derived_default_scene_v1"
PROFILE_VERSION = 1
_REGIONS = frozenset({
    "head", "eyes", "ears", "nose", "mouth", "tongue", "neck", "torso",
    "full_outfit", "arms", "wrists", "hands", "skin", "waist", "legs", "feet",
    "full_body",
})
_COLORS = frozenset({
    "black", "blue", "brown", "cream", "cyan", "gold", "gray", "green", "grey",
    "orange", "pink", "purple", "red", "silver", "tan", "teal", "violet", "white", "yellow",
})
_REGION_BY_KIND = {
    "blindfold": "eyes", "hat": "head", "cap": "head", "helmet": "head",
    "scarf": "neck", "necklace": "neck", "dress": "full_outfit", "outfit": "full_outfit",
    "shirt": "torso", "hoodie": "torso", "coat": "torso", "jacket": "torso",
    "glove": "hands", "gloves": "hands", "pants": "legs", "trousers": "legs",
    "skirt": "legs", "boot": "feet", "boots": "feet", "shoe": "feet", "shoes": "feet",
    "sneaker": "feet", "sneakers": "feet", "rollerblade": "feet", "rollerblades": "feet",
    "skate": "feet", "skates": "feet", "ski": "feet", "skis": "feet",
}
_SAFE_LABEL = re.compile(r"[a-z][a-z'’ -]{0,63}", re.I)


@dataclass(frozen=True)
class ProfileWornItem:
    label: str
    kind: str
    region: str
    color: str | None = None
    quantity: int = 1
    set_label: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "label": self.label,
            "kind": self.kind,
            "region": self.region,
            **({"color": self.color} if self.color else {}),
            **({"quantity": self.quantity} if self.quantity > 1 else {}),
            **({"set_label": self.set_label} if self.set_label else {}),
        }


@dataclass(frozen=True)
class CharacterSceneProfile:
    worn_items: tuple[ProfileWornItem, ...]
    source: str
    fingerprint: str
    version: int = PROFILE_VERSION

    def cache_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "source": self.source,
            "fingerprint": self.fingerprint,
            "worn": [item.payload() for item in self.worn_items],
        }


def derive_character_scene_profile(
    character: object,
    character_prompt: object = "",
) -> CharacterSceneProfile | None:
    """Prefer explicit structure; conservatively derive one stable wear form."""
    mapping = character if isinstance(character, dict) else {}
    structured = mapping.get("default_scene")
    items: tuple[ProfileWornItem, ...] = ()
    source = ""
    if isinstance(structured, dict):
        raw_items = structured.get("worn", structured.get("worn_items"))
        if isinstance(raw_items, list) and 1 <= len(raw_items) <= 12:
            parsed = tuple(_parse_item(item) for item in raw_items)
            if all(item is not None for item in parsed):
                items = tuple(item for item in parsed if item is not None)
                source = "structured_character_profile"
    if not items:
        personality = _personality_section(character_prompt)
        match = re.search(
            r"(?:^|[.!?]\s+)(?:they|she|he|the\s+companion|[A-Z][a-z]{1,24})\s+"
            r"(?:normally|usually)\s+wears\s+(?P<items>[^.!?]{2,180})[.!?]",
            personality,
        )
        if match is None:
            match = re.search(r"\b(?:normally|usually)\s+wears\s+(?P<items>[^.!?]{2,180})[.!?]", personality, re.I)
        if match is None:
            match = re.search(
                r"\bdefault\s+outfit\s+is\s+(?P<items>[^.!?]{2,180})[.!?]",
                personality, re.I,
            )
        if match is not None:
            fragments = _split_items(match.group("items"))
            parsed = tuple(_parse_item(item) for item in fragments)
            if fragments and len(fragments) <= 8 and all(item is not None for item in parsed):
                items = tuple(item for item in parsed if item is not None)
                source = "derived_stable_personality_form"
    if not items:
        return None
    canonical = json.dumps([item.payload() for item in items], sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256((source + "\0" + canonical).encode("utf-8")).hexdigest()
    return CharacterSceneProfile(items, source, fingerprint)


def cache_character_scene_profile(
    repository: MemoryV2Repository,
    character_id: str,
    profile: CharacterSceneProfile | None,
) -> None:
    """Cache versioned derived profile facts in character metadata, never Active State."""
    row = repository.store.connection.execute(
        "SELECT metadata_json FROM characters WHERE character_id=?", (character_id,),
    ).fetchone()
    if row is None:
        return
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    desired = profile.cache_payload() if profile is not None else None
    if desired is None:
        metadata.pop(PROFILE_CACHE_KEY, None)
    else:
        metadata[PROFILE_CACHE_KEY] = desired
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with repository.store.transaction():
        repository.store.connection.execute(
            "UPDATE characters SET metadata_json=? WHERE character_id=?",
            (encoded, character_id),
        )


def cached_character_scene_profile(
    repository: MemoryV2Repository,
    character_id: str,
) -> CharacterSceneProfile | None:
    row = repository.store.connection.execute(
        "SELECT metadata_json FROM characters WHERE character_id=?", (character_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
        raw = metadata.get(PROFILE_CACHE_KEY) if isinstance(metadata, dict) else None
        if not isinstance(raw, dict) or raw.get("version") != PROFILE_VERSION:
            return None
        raw_items = raw.get("worn")
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 12:
            return None
        items = tuple(_parse_cached_item(item) for item in raw_items)
        if any(item is None for item in items):
            return None
        source = str(raw.get("source") or "")
        fingerprint = str(raw.get("fingerprint") or "")
        if source not in {"structured_character_profile", "derived_stable_personality_form"}:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            return None
        return CharacterSceneProfile(tuple(item for item in items if item is not None), source, fingerprint)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def effective_profile_worn_items(
    repository: MemoryV2Repository,
    character_id: str,
) -> tuple[ProfileWornItem, ...]:
    """Resolve profile defaults beneath scoped explicit relation history."""
    profile = cached_character_scene_profile(repository, character_id)
    if profile is None:
        return ()
    mode_state = repository.lookup_actor_state(
        character_id, "companion", "profile_scene_mode",
    ).state
    mode = mode_state.value if mode_state is not None else None
    if mode == "suppressed":
        return ()
    current = repository.list_actor_relations(
        character_id, "companion", {"wearing", "worn_by"}, limit=32,
    )
    if mode == "profile":
        occupied = {item.facet for item in current if item.facet}
        return tuple(item for item in profile.worn_items if item.region not in occupied)

    scope_id = repository.active_truth_scope(character_id).truth_scope_id
    history_rows = repository.store.connection.execute(
        """SELECT DISTINCT facet FROM active_scene_relations
             WHERE character_id=? AND truth_scope_id=? AND target_kind='actor'
               AND target_actor='companion' AND predicate IN ('wearing','worn_by')""",
        (character_id, scope_id),
    ).fetchall()
    explicitly_governed_regions = {str(row["facet"]) for row in history_rows if row["facet"] is not None}
    explicitly_governed_regions.update(item.facet for item in current if item.facet)
    return tuple(
        item for item in profile.worn_items
        if item.region not in explicitly_governed_regions
    )


def profile_context_payload(items: Iterable[ProfileWornItem]) -> dict[str, object]:
    return {
        "provenance": "character_profile",
        "authority": "baseline",
        "normally_worn": [item.label for item in items],
    }


def _parse_item(value: object) -> ProfileWornItem | None:
    if isinstance(value, str):
        compact = " ".join(value.strip().split()).casefold()
        compact = re.sub(r"^(?:an?|the)\s+", "", compact)
        words = compact.split()
        color = words.pop(0) if words and words[0] in _COLORS else None
        kind = " ".join(words)
        region = _REGION_BY_KIND.get(kind)
        if region is None or _SAFE_LABEL.fullmatch(kind) is None:
            return None
        quantity = 2 if kind in {"gloves", "boots", "shoes", "sneakers", "rollerblades", "skates", "skis"} else 1
        label = f"{color} {kind}" if color else kind
        return ProfileWornItem(label, kind, region, color, quantity, "pair" if quantity == 2 else None)
    if not isinstance(value, dict) or set(value) - {"kind", "color", "region", "quantity", "set_label"}:
        return None
    kind = " ".join(str(value.get("kind") or "").strip().split()).casefold()
    color = " ".join(str(value.get("color") or "").strip().split()).casefold() or None
    region = str(value.get("region") or _REGION_BY_KIND.get(kind) or "").strip().casefold()
    quantity = value.get("quantity", 1)
    set_label = str(value.get("set_label") or "").strip().casefold() or None
    if (_SAFE_LABEL.fullmatch(kind) is None or region not in _REGIONS
            or color is not None and color not in _COLORS
            or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 16
            or set_label not in {None, "pair", "set"}):
        return None
    label = f"{color} {kind}" if color else kind
    return ProfileWornItem(label, kind, region, color, quantity, set_label)


def _parse_cached_item(value: object) -> ProfileWornItem | None:
    if not isinstance(value, dict) or set(value) - {"label", "kind", "color", "region", "quantity", "set_label"}:
        return None
    parsed = _parse_item({key: item for key, item in value.items() if key != "label"})
    if parsed is None or value.get("label") != parsed.label:
        return None
    return parsed


def _split_items(value: str) -> tuple[str, ...]:
    compact = re.sub(r"^(?:an?|the)\s+", "", " ".join(value.strip().split()), flags=re.I)
    return tuple(
        fragment.strip() for fragment in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", compact)
        if fragment.strip()
    )


def _personality_section(value: object) -> str:
    text = str(value or "")[:20000]
    marker = "CHARACTER PERSONALITY:"
    end = "CHARACTER CONSISTENCY:"
    if marker in text:
        text = text.split(marker, 1)[1]
        if end in text:
            text = text.split(end, 1)[0]
    return text[:6000]
