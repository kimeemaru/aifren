"""Guarded historical episode rebuilding for disposable V2 evaluation clones.

Canonical conversation records remain the source authority.  Historical
occurrences prove the staged archive was indexed under the current provenance
contract, but neither those occurrences nor an episode summary establish that
their propositions are presently true.  Publication and validation remain
owned by :class:`EpisodeCompactionCache`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from aifren.continuity.memory_v2_episode_compaction import (
    EPISODE_PURPOSE_HISTORICAL,
    EPISODE_SOURCE_HISTORICAL,
    EpisodeCompactionCache,
    EpisodeCompactor,
    EpisodeRebuildReport,
    RECENT_EXCHANGES_TO_KEEP,
    deterministic_episode_boundaries,
)
from aifren.continuity.memory_v2_historical_evidence import (
    HISTORICAL_EVIDENCE_POLICY_VERSION,
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_RECORDS,
    _records_digest,
    resolve_historical_evidence,
    validate_staged_disposable_target,
)
from aifren.continuity.memory_v2_shadow_writer import MemoryV2ShadowWriter


class HistoricalEpisodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoricalEpisodeRebuildReport:
    generation_id: str
    published: bool
    idempotent: bool
    episode_count: int
    valid_episode_count: int
    prior_episode_count: int
    prior_valid_episode_count: int
    preserved_prior_episode_count: int
    source_record_count: int
    source_coverage_count: int
    rejected_range_count: int
    unknown_scope_episode_count: int
    real_world_episode_count: int
    scenario_episode_count: int
    lower_episode_dependency_count: int
    missing_historical_occurrence_count: int
    orphan_source_range_count: int
    duplicate_source_range_count: int
    integrity_ok: bool
    runtime_context_admitted: bool
    rebuild_duration_ms: float


class HistoricalEpisodeRebuilder:
    """Publish one cache-validated generation only into an attested clone."""

    def __init__(
        self,
        writer: MemoryV2ShadowWriter,
        conversation_file: str | Path,
        *,
        confirm_staged_disposable: bool = False,
    ) -> None:
        if confirm_staged_disposable is not True:
            raise HistoricalEpisodeError(
                "historical episode rebuilding requires staged-disposable confirmation",
            )
        self.writer = writer
        self.store = writer.store
        self.character_id = writer.character_id
        target = validate_staged_disposable_target(
            writer.application_dir,
            self.character_id,
            writer.database_path,
            conversation_file,
        )
        self.conversation_file = Path(conversation_file).resolve()
        expected_attestation = (
            str(writer.application_dir), self.character_id, str(target),
            str(self.conversation_file),
        )
        if getattr(writer, "_historical_evidence_attestation", None) != expected_attestation:
            raise HistoricalEpisodeError(
                "staged episode writer was not opened through the historical factory",
            )
        self.cache = EpisodeCompactionCache(self.store, self.character_id)

    def _frozen_archive(self) -> tuple[bytes, list[Mapping[str, object]]]:
        raw = self.conversation_file.read_bytes()
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise HistoricalEpisodeError("canonical archive exceeds historical rebuild bound")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HistoricalEpisodeError("canonical archive is malformed") from error
        if not isinstance(value, list) or len(value) > MAX_ARCHIVE_RECORDS:
            raise HistoricalEpisodeError("canonical archive shape exceeds historical rebuild bound")
        if any(not isinstance(record, Mapping) for record in value):
            raise HistoricalEpisodeError("canonical archive contains a malformed record")
        records = list(value)
        checkpoint = self.store.connection.execute(
            "SELECT * FROM historical_evidence_checkpoints WHERE character_id=?",
            (self.character_id,),
        ).fetchone()
        archive_digest = hashlib.sha256(raw).hexdigest()
        if (
            checkpoint is None
            or str(checkpoint["policy_version"]) != HISTORICAL_EVIDENCE_POLICY_VERSION
            or str(checkpoint["state"]) != "complete"
            or int(checkpoint["next_index"]) != len(records)
            or int(checkpoint["source_record_count"]) != len(records)
            or str(checkpoint["source_archive_digest"]) != archive_digest
            or str(checkpoint["source_prefix_digest"]) != _records_digest(records, len(records))
        ):
            raise HistoricalEpisodeError(
                "historical evidence index is incomplete or no longer matches canonical history",
            )
        return raw, records

    def _validate_occurrence_index(
        self,
        records: Sequence[Mapping[str, object]],
    ) -> int:
        scope_ids = {
            str(row[0]) for row in self.store.connection.execute(
                "SELECT truth_scope_id FROM truth_scopes WHERE character_id=?",
                (self.character_id,),
            ).fetchall()
        }
        rows = self.store.connection.execute(
            """SELECT * FROM historical_evidence
                 WHERE character_id=? ORDER BY canonical_index, claim_id""",
            (self.character_id,),
        ).fetchall()
        by_index: dict[int, object] = {}
        for row in rows:
            index = int(row["canonical_index"])
            if index in by_index:
                raise HistoricalEpisodeError("historical occurrence index has duplicate sources")
            by_index[index] = row
        expected_count = 0
        missing_count = 0
        for index, _record in enumerate(records):
            decision = resolve_historical_evidence(records, index, valid_scope_ids=scope_ids)
            if not decision.accepted:
                continue
            expected_count += 1
            evidence = decision.evidence
            assert evidence is not None
            row = by_index.get(index)
            if row is None:
                missing_count += 1
                continue
            if (
                str(row["canonical_record_id"]) != evidence.canonical_record_id
                or str(row["speaker_role"]) != evidence.speaker_role
                or str(row["scope_state"]) != evidence.scope_state
                or (str(row["truth_scope_id"]) if row["truth_scope_id"] is not None else None)
                   != evidence.truth_scope_id
                or str(row["source_content_sha256"]) != evidence.source_content_sha256
            ):
                raise HistoricalEpisodeError(
                    "historical occurrence provenance disagrees with canonical history",
                )
        if len(rows) != expected_count:
            raise HistoricalEpisodeError("historical occurrence index has orphan sources")
        return missing_count

    def _summary_count(self) -> int:
        return int(self.store.connection.execute(
            "SELECT COUNT(*) FROM summaries WHERE character_id=? AND summary_level='episode_compaction'",
            (self.character_id,),
        ).fetchone()[0])

    def _report(
        self,
        records: Sequence[Mapping[str, object]],
        *,
        prior_count: int,
        prior_valid_count: int,
        missing_occurrences: int,
        rebuild: EpisodeRebuildReport | None,
        idempotent: bool,
    ) -> HistoricalEpisodeRebuildReport:
        validation = self.cache.validate_for_context(
            records, allow_historical_recall=True,
        )
        if (
            not validation.accepted
            or validation.source_authority != EPISODE_SOURCE_HISTORICAL
            or validation.generation_purpose != EPISODE_PURPOSE_HISTORICAL
        ):
            raise HistoricalEpisodeError(
                f"shared episode validator rejected rebuilt generation: {validation.reason}",
            )
        current = tuple(value for value in validation.lower_records if value.accepted)
        scopes = Counter(value.scope_state for value in current)
        runtime = self.cache.validate_for_context(records)
        range_rows = self.store.connection.execute(
            """SELECT r.summary_id, COUNT(*) AS count_value
                 FROM summary_source_ranges r JOIN summaries s
                   ON s.character_id=r.character_id AND s.summary_id=r.summary_id
                WHERE r.character_id=? AND s.summary_level='episode_compaction'
                GROUP BY r.summary_id""",
            (self.character_id,),
        ).fetchall()
        duplicate_ranges = sum(1 for row in range_rows if int(row["count_value"]) != 1)
        orphan_ranges = int(self.store.connection.execute(
            """SELECT COUNT(*) FROM summary_source_ranges r LEFT JOIN summaries s
                   ON s.character_id=r.character_id AND s.summary_id=r.summary_id
                 WHERE r.character_id=? AND s.summary_id IS NULL""",
            (self.character_id,),
        ).fetchone()[0])
        integrity = str(self.store.connection.execute("PRAGMA integrity_check").fetchone()[0]) == "ok"
        coverage = max(
            (int(value.source_end_index_exclusive or 0) for value in current), default=0,
        )
        boundaries = deterministic_episode_boundaries(
            records, valid_scope_ids=set(self.cache._known_truth_scope_ids()),
        )
        eligible_coverage = boundaries[-1].end_index_exclusive if boundaries else 0
        return HistoricalEpisodeRebuildReport(
            generation_id=validation.generation_id,
            published=bool(rebuild.published) if rebuild is not None else False,
            idempotent=idempotent,
            episode_count=len(current),
            valid_episode_count=len(current),
            prior_episode_count=prior_count,
            prior_valid_episode_count=prior_valid_count,
            preserved_prior_episode_count=self._summary_count() - len(current),
            source_record_count=len(records),
            source_coverage_count=coverage,
            rejected_range_count=int(
                coverage != eligible_coverage
                or len(records) - coverage > RECENT_EXCHANGES_TO_KEEP * 2
            ),
            unknown_scope_episode_count=scopes["unknown_scope"],
            real_world_episode_count=scopes["real_world"],
            scenario_episode_count=scopes["scenario"],
            lower_episode_dependency_count=sum(
                len(value.lower_episode_ids) for value in validation.era_records if value.accepted
            ),
            missing_historical_occurrence_count=missing_occurrences,
            orphan_source_range_count=orphan_ranges,
            duplicate_source_range_count=duplicate_ranges,
            integrity_ok=integrity,
            runtime_context_admitted=runtime.accepted,
            rebuild_duration_ms=float(rebuild.duration_ms) if rebuild is not None else 0.0,
        )

    def rebuild(self, compactor: EpisodeCompactor) -> HistoricalEpisodeRebuildReport:
        before_bytes, records = self._frozen_archive()
        missing_occurrences = self._validate_occurrence_index(records)
        boundaries = deterministic_episode_boundaries(
            records, valid_scope_ids=set(self.cache._known_truth_scope_ids()),
        )
        if not boundaries:
            raise HistoricalEpisodeError(
                "canonical history cannot form a validated historical source prefix",
            )
        prior_count = self._summary_count()
        prior_validation = self.cache.validate_for_context(
            records, allow_historical_recall=True,
        )
        prior_valid_count = sum(value.accepted for value in prior_validation.lower_records)
        if (
            prior_validation.accepted
            and prior_validation.source_authority == EPISODE_SOURCE_HISTORICAL
            and prior_validation.generation_purpose == EPISODE_PURPOSE_HISTORICAL
        ):
            return self._report(
                records, prior_count=prior_count, prior_valid_count=prior_valid_count,
                missing_occurrences=missing_occurrences, rebuild=None, idempotent=True,
            )
        rebuild = self.cache.rebuild(
            records,
            compactor,
            source_authority=EPISODE_SOURCE_HISTORICAL,
            generation_purpose=EPISODE_PURPOSE_HISTORICAL,
            preserve_prior_generations=True,
        )
        if self.conversation_file.read_bytes() != before_bytes:
            raise HistoricalEpisodeError("canonical archive changed during episode rebuild")
        return self._report(
            records, prior_count=prior_count, prior_valid_count=prior_valid_count,
            missing_occurrences=missing_occurrences, rebuild=rebuild, idempotent=False,
        )
