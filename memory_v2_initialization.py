"""Bounded catch-up orchestration over existing V2 owners; no new memory authority.

Ordinary startup still does one recovery page and existing idle maintenance.
An explicit initialization job can drain those same pages before a cutover.
Prepared caches are optional derived acceleration, never original state.
"""
from __future__ import annotations

from memory_v2_runtime_observation import CanonicalObservationRecovery
from memory_v2_episode_compaction import EpisodeCompactionCache, EPISODE_PURPOSE_HISTORICAL
from memory_v2_store.store import utc_now_us


def _historical_validation(cache, messages):
    return cache.validate_for_context(messages, allow_historical_recall=True)


def install_validated_episode_acceleration(target, cached, messages, *, source_unchanged=lambda: True):
    """Add an exact-source validated generation without replacing any live original.

    The caller must attest cache identity/provenance before supplying it. Both
    caches use the production validator again, including under the write lock.
    Existing valid historical generations win; conflicting IDs are never replaced.
    """
    if target.character_id != cached.character_id:
        raise ValueError('Episode acceleration belongs to another character')
    previous = target._current_generation_id() or ''
    current = _historical_validation(target, messages)
    if current.accepted and any(r.accepted and r.generation_purpose == EPISODE_PURPOSE_HISTORICAL
                                for r in current.lower_records):
        return {'state': 'unchanged', 'installed': 0}
    candidate = _historical_validation(cached, messages)
    records = tuple(r for r in candidate.records if r.accepted and
                    r.generation_id == candidate.generation_id)
    if (not candidate.accepted or not records or len(records) > 2048
            or any(r.generation_purpose != EPISODE_PURPOSE_HISTORICAL for r in records)):
        raise ValueError('Episode acceleration is not a valid historical generation')
    with target.store.transaction():
        if not source_unchanged() or (target._current_generation_id() or '') != previous:
            raise ValueError('Source or live episode generation changed during initialization')
        for record in records:
            if target.store.connection.execute(
                'SELECT 1 FROM summaries WHERE character_id=? AND summary_id=?',
                (target.character_id, record.record_id)).fetchone() is not None:
                raise ValueError('Episode acceleration cannot overwrite an existing record')
        created = utc_now_us()
        for record in records:
            row = record.row
            target.store.add_summary(target.character_id, record.record_id, record.content,
                source_count=record.source_count, provenance_state=record.provenance_state,
                generator_name=row['generator_name'], generator_version=row['generator_version'],
                legacy_metadata=dict(record.metadata), created_at_us=created,
                summary_level=record.summary_level)
            target.store.add_summary_source_range(target.character_id, record.record_id,
                record.source_start_sequence, record.source_end_sequence)
        check = _historical_validation(target, messages)
        if not check.accepted or check.generation_id != candidate.generation_id or not source_unchanged():
            raise ValueError('Installed episode generation failed production validation')
    return {'state': 'installed', 'installed': len(records), 'generation': candidate.generation_id}


def initialize_v2_derived_state(writer, conversation, *, embedding_provider,
                                maximum_pages=256, cancelled=lambda: False,
                                episode_acceleration=None):
    """Drain finite normal observer/vector pages; durable cursors own resume.

    Does not reconcile/import V1, mutate JSON, replay model turns or reinterpret
    unresolved state. No unbounded retries and no new persistent job/marker store.
    """
    if isinstance(maximum_pages, bool) or not 1 <= maximum_pages <= 4096:
        raise ValueError('Initialization page bound is invalid')
    if embedding_provider is None:
        raise ValueError('Initialization requires an identified embedding provider')
    recovery = CanonicalObservationRecovery(writer, conversation)
    recovery.embedding_provider_getter = lambda: embedding_provider
    pages = []
    embedded = 0
    for _ in range(maximum_pages):
        if cancelled():
            return {'state': 'cancelled', 'pages': len(pages), 'embedded': embedded}
        status = recovery.run_page()
        pages.append(status)
        if status.get('state') in {'failed', 'unresolved'}:
            break
        recovery.maintain_embeddings(embedding_provider)
        embedded += recovery.last_embedding_work.get('embedded', 0)
        if recovery.last_embedding_work.get('failed'):
            break
        pending = any(v.get('state') == 'pending' for v in status.get('consumers', {}).values())
        if not pending and not recovery._missing_embedding_ids(embedding_provider, limit=1):
            break
    messages = conversation.messages[:conversation._persisted_message_count]
    cache = EpisodeCompactionCache(writer.store, writer.character_id)
    acceleration = {'state': 'not_requested', 'installed': 0}
    if episode_acceleration is not None and not cancelled():
        from pathlib import Path
        import json
        def same():
            try:
                return json.loads(Path(conversation.conversation_file).read_text()) == messages
            except (OSError, ValueError):
                return False
        acceleration = install_validated_episode_acceleration(cache, episode_acceleration, messages,
                                                              source_unchanged=same)
    validation = _historical_validation(cache, messages)
    consumers = {name: dict(recovery._progress(name) or {})
                 for name in ('identity','durable','continuity','headwear','history')}
    ready = all(consumers[name].get('state') == 'complete' and
                consumers[name].get('next_index') == len(messages)
                for name in ('identity','durable','history'))
    missing_vectors = bool(recovery._missing_embedding_ids(embedding_provider, limit=1))
    valid_episodes = sum(r.accepted and r.generation_purpose == EPISODE_PURPOSE_HISTORICAL
                         for r in validation.lower_records)
    return {'state': 'ready' if ready and not missing_vectors and (valid_episodes or not messages) else 'pending',
            'pages':len(pages), 'embedded':embedded, 'missing_vectors':missing_vectors,
            'consumers':consumers, 'valid_historical_episodes':valid_episodes,
            'episode_acceleration':acceleration, 'last_observer_page':recovery.last_status}
