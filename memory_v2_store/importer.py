"""Synthetic fixture importer for the isolated Memory V2 shadow store."""

from __future__ import annotations

import uuid

from .store import MemoryV2Store, parse_timestamp_us


SYNTHETIC_CHARACTER_NAMESPACE = uuid.UUID("2bb9ab3c-183a-4d28-9739-725be7cf2721")


def fixture_character_id(fixture_version: str, fixture_character_id: str) -> str:
    """Deterministically map a fixture label to a valid stable UUID."""
    return str(uuid.uuid5(SYNTHETIC_CHARACTER_NAMESPACE, f"{fixture_version}:{fixture_character_id}"))


def import_fixture(store: MemoryV2Store, fixture) -> dict[str, str]:
    """Import only benchmark fixture objects; never reads application persistence."""
    character_map = {}
    for event in fixture.events:
        if event.character_id in character_map:
            continue
        character_id = fixture_character_id(fixture.version, event.character_id)
        character_map[event.character_id] = character_id
        store.create_character(character_id, event.character_id, metadata={"fixture_version": fixture.version})

    events_by_id = {}
    for sequence, event in enumerate(fixture.events, start=1):
        character_id = character_map[event.character_id]
        recorded = parse_timestamp_us(event.recorded_at)
        store.add_event(
            character_id, event.event_id, sequence, event_type=event.event_type,
            actor_kind="user" if event.event_type == "message" else "system",
            recorded_at_us=recorded, occurred_from_us=parse_timestamp_us(event.valid_from),
            occurred_to_us=parse_timestamp_us(event.valid_to), temporal_precision="instant",
            content_text=event.content, source_origin="synthetic_benchmark",
        )
        events_by_id[event.event_id] = event

    claim_by_id = {claim.claim_id: claim for claim in fixture.claims}
    for claim in fixture.claims:
        source_events = [events_by_id[event_id] for event_id in claim.source_event_ids]
        created_at = min(parse_timestamp_us(event.recorded_at) for event in source_events)
        valid_from = parse_timestamp_us(claim.valid_from) or created_at
        store.add_claim(
            character_map[claim.character_id], claim.claim_id, claim_type=claim.category,
            assertion_scope="shared_episode" if claim.category in {"episode", "relationship", "running_joke"} else "user_fact",
            content=claim.content, importance=claim.importance, confidence=1.0,
            valid_from_us=valid_from, valid_to_us=parse_timestamp_us(claim.valid_to),
            temporal_precision="instant", provenance_state="complete", created_at_us=created_at,
        )
        for event in source_events:
            store.attach_evidence(character_map[claim.character_id], claim.claim_id, event.event_id)

    for claim in fixture.claims:
        character_id = character_map[claim.character_id]
        source_event = events_by_id[claim.source_event_ids[-1]]
        status_at = parse_timestamp_us(source_event.recorded_at)
        if claim.superseded_by:
            successor = claim_by_id[claim.superseded_by]
            status_at = parse_timestamp_us(events_by_id[successor.source_event_ids[0]].recorded_at)
        elif claim.status == "expired":
            status_at = parse_timestamp_us(claim.valid_to) or parse_timestamp_us(source_event.valid_to) or status_at
        store.add_status(character_id, claim.claim_id, claim.status, source_event_id=source_event.event_id, created_at_us=status_at)
        if claim.superseded_by:
            successor_event = events_by_id[successor.source_event_ids[0]]
            store.add_relation(character_id, claim.claim_id, claim.superseded_by, "supersedes", created_at_us=parse_timestamp_us(successor_event.recorded_at))
            store.connection.execute(
                "UPDATE claims SET valid_to_us = ? WHERE character_id = ? AND claim_id = ? AND valid_to_us IS NULL",
                (parse_timestamp_us(successor_event.recorded_at), character_id, claim.claim_id),
            )
    return character_map
