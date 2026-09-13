"""Ordered speech preparation for early text or one already-committed reply.

The existing queue owns bounded production/backpressure and synthesis recovery.
Providers retain PCM playback/cancellation. Committed mode requires the explicit
owned-continuous capability; legacy providers keep their existing speak surface.
"""

from __future__ import annotations

import os
import re
from queue import Empty, Full, Queue
import threading
import time
from typing import Any, Callable
from dataclasses import dataclass

from aifren.runtime.development_flight_recorder import development_flight_recorder


_PERFORMANCE_TIMING = os.environ.get("AIFREN_PERFORMANCE_TIMING", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _timing_log(message: str) -> None:
    if _PERFORMANCE_TIMING:
        print(f"[AIFren Timing] {message}")


@dataclass(frozen=True)
class SynthesisRecoveryResult:
    succeeded: bool
    prepared: object | None = None
    category: str = "none"
    cancelled: bool = False
    cpu_fallback_used: bool = False


@dataclass(frozen=True)
class CommittedSpeechUnitPolicy:
    """Native-work bounds for later units, not dialogue-length limits.

    The two opening units retain their established policy. Later units should
    not grow merely because playback has begun: an in-flight native inference
    cannot be interrupted until its next yield. The default keeps later work
    near a sentence/clause while preserving exact words and whitespace.
    """

    minimum: int = 48
    preferred_maximum: int = 96
    hard_maximum: int = 120

    def __post_init__(self) -> None:
        if not 1 <= self.minimum <= self.preferred_maximum <= self.hard_maximum:
            raise ValueError("invalid committed TTS work bounds")


class TtsSynthesisResourceManager:
    """One lossless synthesis retry/CPU policy for streamed and direct speech."""

    def __init__(
        self,
        tts: Any,
        *,
        cancelled: threading.Event,
        provider_generation_active: Callable[[], bool] | None = None,
        preparation_owner_current: Callable[[], bool] | None = None,
    ) -> None:
        self._tts = tts
        self._cancelled = cancelled
        self._provider_generation_active = provider_generation_active
        self._preparation_owner_current = preparation_owner_current

    def is_set(self) -> bool:
        """Cancellation signal passed into a cooperative provider preparation."""
        return (self._cancelled.is_set() or
                (self._preparation_owner_current is not None and
                 not self._preparation_owner_current()))

    @staticmethod
    def failure_category(error: Exception, provider_active: bool) -> str:
        error_type = type(error)
        name = error_type.__name__.casefold()
        qualified = f"{error_type.__module__}.{error_type.__qualname__}".casefold()
        hierarchy = " ".join(
            f"{item.__module__}.{item.__qualname__}".casefold()
            for item in error_type.__mro__
        )
        detail = str(error).casefold()[:240]
        if (isinstance(error, (MemoryError, TimeoutError))
                or "outofmemory" in name
                or "outofmemory" in qualified
                or "outofmemory" in hierarchy):
            return "transient_resource"
        if isinstance(error, OSError):
            return "transient_io"
        resource_detail = any(
            token in detail
            for token in (
                "cuda", "hip", "rocm", "accelerator", "out of memory",
                "memory alloc", "memory pressure", "resource", "temporar", "busy",
            )
        )
        if resource_detail:
            return "concurrent_provider_resource" if provider_active else "transient_resource"
        return "unrecognized"

    def accelerator_backend(self) -> str:
        """Describe the torch accelerator without making telemetry correctness NVIDIA-only."""
        device = str(getattr(self._tts, "device", "") or "").casefold()
        if device == "cpu":
            return "cpu"
        try:
            import torch
            if getattr(getattr(torch, "version", None), "hip", None):
                return "hip_rocm"
            if getattr(getattr(torch, "version", None), "cuda", None):
                return "cuda"
        except Exception:
            pass
        if "hip" in device or "rocm" in device:
            return "hip_rocm"
        if "cuda" in device:
            return "torch_accelerator"
        return device or "unknown"

    def provider_active(self) -> bool:
        try:
            return bool(self._provider_generation_active and self._provider_generation_active())
        except Exception:
            return False

    def wait_for_provider_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while not self.is_set() and self.provider_active() and time.monotonic() < deadline:
            if self._cancelled.wait(0.05):
                return False
        return not self.is_set()

    def _fallback_to_cpu(self, unit_index: int, category: str, direct: bool) -> bool:
        fallback = getattr(self._tts, "fallback_to_cpu_after_resource_failure", None)
        if not callable(fallback) or self.is_set():
            return False
        development_flight_recorder().mark(
            "tts_cpu_fallback_start", chunk_index=unit_index, direct=direct,
            category=category, retry_scheduled=True,
            accelerator_backend=self.accelerator_backend(),
        )
        started_at = time.monotonic()
        try:
            switched = bool(fallback())
        except Exception as error:
            development_flight_recorder().mark(
                "tts_cpu_fallback_result", chunk_index=unit_index, direct=direct,
                succeeded=False, exception_class=type(error).__name__[:80],
                category=self.failure_category(error, False),
                accelerator_backend=self.accelerator_backend(),
                duration_ms=(time.monotonic() - started_at) * 1000.0,
            )
            return False
        development_flight_recorder().mark(
            "tts_cpu_fallback_result", chunk_index=unit_index, direct=direct,
            succeeded=switched,
            accelerator_backend=self.accelerator_backend(),
            duration_ms=(time.monotonic() - started_at) * 1000.0,
        )
        return switched and not self.is_set()

    def prepare(self, text: str, *, unit_index: int = 0, direct: bool = False) -> SynthesisRecoveryResult:
        prepare = getattr(self._tts, "prepare_stream_chunk", None)
        if not callable(prepare):
            # Legacy providers synthesize inside speak().  Preserve that ordered
            # playback path; resource recovery is available only to providers
            # that expose the prepare/start boundary.
            return SynthesisRecoveryResult(True, prepared=text, category="prepare_unsupported")
        cpu_fallback_used = False
        last_category = "unrecognized"
        # Explicit provider capability; dynamic Mock/extension attributes must
        # not silently replace an existing prepare implementation.
        cancellable_prepare = (getattr(self._tts, "prepare_cancellable_stream_chunk")
                               if callable(getattr(type(self._tts), "prepare_cancellable_stream_chunk", None))
                               else None)
        for attempt in (1, 2, 3):
            if self.is_set():
                return SynthesisRecoveryResult(False, category="cancelled", cancelled=True)
            provider_active = self.provider_active()
            try:
                prepared = (cancellable_prepare(text, cancelled=self)
                            if callable(cancellable_prepare) else prepare(text))
                if self.is_set():
                    return SynthesisRecoveryResult(False, category="cancelled", cancelled=True,
                                                   cpu_fallback_used=cpu_fallback_used)
                if attempt > 1:
                    development_flight_recorder().mark(
                        "tts_synthesis_retry_result", chunk_index=unit_index, direct=direct,
                        succeeded=True, provider_generating=provider_active,
                        provider_generation_active=provider_active,
                        cpu_fallback_used=cpu_fallback_used,
                        accelerator_backend=self.accelerator_backend(),
                    )
                return SynthesisRecoveryResult(
                    True, prepared=prepared, cpu_fallback_used=cpu_fallback_used,
                )
            except Exception as error:
                if self.is_set():
                    return SynthesisRecoveryResult(False, category="cancelled", cancelled=True,
                                                   cpu_fallback_used=cpu_fallback_used)
                last_category = self.failure_category(error, provider_active)
                wait_retry = attempt == 1 and last_category != "unrecognized"
                cpu_retry = False
                if (attempt == 2
                        and last_category in {"transient_resource", "concurrent_provider_resource"}):
                    cpu_retry = self._fallback_to_cpu(unit_index, last_category, direct)
                retry = wait_retry or cpu_retry
                cpu_fallback_used = cpu_fallback_used or cpu_retry
                cancelled_now = self.is_set()
                development_flight_recorder().mark(
                    "tts_synthesis_failure", chunk_index=unit_index, direct=direct,
                    attempt=attempt, exception_class=type(error).__name__[:80],
                    category=last_category, provider_generating=provider_active,
                    provider_generation_active=provider_active,
                    retry_planned=retry, retry_scheduled=retry,
                    cpu_fallback_used=cpu_fallback_used,
                    accelerator_backend=self.accelerator_backend(),
                )
                if cancelled_now:
                    return SynthesisRecoveryResult(
                        False, category="cancelled", cancelled=True,
                        cpu_fallback_used=cpu_fallback_used,
                    )
                if not retry:
                    if attempt > 1:
                        development_flight_recorder().mark(
                            "tts_synthesis_retry_result", chunk_index=unit_index, direct=direct,
                            succeeded=False, provider_generating=provider_active,
                            provider_generation_active=provider_active,
                            exception_class=type(error).__name__[:80], category=last_category,
                            cpu_fallback_used=cpu_fallback_used,
                            accelerator_backend=self.accelerator_backend(),
                        )
                    return SynthesisRecoveryResult(
                        False, category=last_category,
                        cpu_fallback_used=cpu_fallback_used,
                    )
                development_flight_recorder().mark(
                    "tts_synthesis_retry_wait", chunk_index=unit_index, direct=direct,
                    provider_generating=provider_active,
                    provider_generation_active=provider_active,
                    retry_scheduled=True, cpu_fallback_used=cpu_fallback_used,
                    accelerator_backend=self.accelerator_backend(),
                )
                if wait_retry and provider_active and not self.wait_for_provider_idle():
                    return SynthesisRecoveryResult(
                        False, category="cancelled", cancelled=True,
                        cpu_fallback_used=cpu_fallback_used,
                    )
        return SynthesisRecoveryResult(False, category=last_category, cpu_fallback_used=cpu_fallback_used)


class StreamingSpeechQueue:
    """Bounded TTS dispatcher; committed mode never consumes provider deltas.

    ``committed_text`` is the complete, already-validated spoken projection.
    Constructor callbacks must already belong to its service turn. The short
    ``owner_current`` predicate must be nonblocking and reflect speech lifetime,
    not whether the completed provider turn is still generating. Call ``cancel``
    to retire queued/preparing/playing work, then optionally ``join`` outside
    service/PTT locks. ``outcome`` and ``playback_id`` are diagnostics only.
    ``on_owned_failure(queue, reason)`` supersedes the legacy reason-only
    callback when supplied. Cleanup may already have retired the service's
    queue reference, so failure publication must use this exact queue identity.
    """

    _STOP = object()

    def __init__(
        self,
        tts: Any,
        *,
        max_chunks: int = 4,
        on_failure: Callable[[str], None] | None = None,
        on_owned_failure: Callable[["StreamingSpeechQueue", str], None] | None = None,
        on_chunk_submitted: Callable[[str, str, int], None] | None = None,
        on_chunk_starting: Callable[[str, str, int], None] | None = None,
        on_complete: Callable[["StreamingSpeechQueue"], None] | None = None,
        provider_generation_active: Callable[[], bool] | None = None,
        committed_text: str | None = None,
        owner_current: Callable[[], bool] | None = None,
        committed_unit_policy: CommittedSpeechUnitPolicy | None = None,
    ):
        self._tts = tts
        self._queue: Queue[object] = Queue(maxsize=max(1, int(max_chunks)))
        self._prepared: Queue[object] = Queue(maxsize=max(1, int(max_chunks)))
        self._overflow_lock = threading.Lock()
        self._overflow_item: tuple | None = None
        self._cancelled = threading.Event()
        self._closed = threading.Event()
        self._on_failure = on_failure
        self._on_owned_failure = on_owned_failure
        self._on_chunk_submitted = on_chunk_submitted
        self._on_chunk_starting = on_chunk_starting
        self._on_complete = on_complete
        self._provider_generation_active = provider_generation_active
        self._committed_text = committed_text
        self._committed_unit_policy = committed_unit_policy or CommittedSpeechUnitPolicy()
        self._owner_current = owner_current
        self._failed = threading.Event()
        self._owned_continuous = getattr(type(tts), "supports_owned_continuous_stream", False) is True
        if committed_text is not None and not self._owned_continuous:
            raise ValueError("Committed speech requires owned continuous playback support")
        self._resource_manager = TtsSynthesisResourceManager(
            tts, cancelled=self._cancelled,
            provider_generation_active=provider_generation_active,
            preparation_owner_current=lambda: not self._failed.is_set() and
                (self._owner_current is None or self._owner_current()),
        )
        self._next_chunk_index = 0
        self.playback_id: int | None = None
        self.outcome = "pending"
        self._feeder_thread = None
        self._synthesis_thread = threading.Thread(target=self._synthesize, name="aifren-tts-synthesis", daemon=True)
        self._playback_thread = threading.Thread(target=self._play, name="aifren-tts-playback", daemon=True)
        self._synthesis_thread.start()
        self._playback_thread.start()
        if committed_text is not None:
            self._feeder_thread = threading.Thread(
                target=self._feed_committed, name="aifren-tts-committed-feed", daemon=True,
            )
            self._feeder_thread.start()

    @staticmethod
    def committed_units(
        text: str, *, policy: CommittedSpeechUnitPolicy | None = None,
    ) -> tuple[str, ...]:
        """Exact admitted speech with bounded native work throughout the reply.

        These are synthesis work limits, not a truncation or response-length
        policy. The first two units keep the already-validated opening ceiling;
        later units have a smaller bound to reduce obsolete inference drain.
        An indivisible long token is retained whole rather than split into new
        spoken words. No dialogue/emote interpretation happens a second time.
        """
        from aifren.tts.chunker import SpeechChunker
        if not text.strip():
            return ()
        opening = SpeechChunker(minimum=48, preferred_maximum=120,
                                hard_maximum=160, preserve_whitespace=True)
        first_units = (opening.feed(text) + opening.finish())[:2]
        # The opening must cover preparation of its successor. Jumping
        # directly from one short sentence to a much larger unit caused a
        # measured first-boundary underrun on CPU despite ample later runway.
        # Keep the next unit modest too; do not delay initial playback or add
        # another synthesis worker to mask that scheduling mismatch.
        opening_characters = sum(len(unit) for unit in first_units)
        policy = policy or CommittedSpeechUnitPolicy()
        later = SpeechChunker(minimum=policy.minimum,
                              preferred_maximum=policy.preferred_maximum,
                              hard_maximum=policy.hard_maximum, preserve_whitespace=True)
        units = first_units + later.feed(text[opening_characters:]) + later.finish()
        # The chunker's emergency character boundary can bisect one very long
        # lexical token. Rejoin that boundary; preserve exact text/word offsets.
        result = []
        for unit in units:
            if result and (not unit.strip() or
                           (not result[-1][-1].isspace() and not unit[0].isspace())):
                result[-1] += unit
            else:
                result.append(unit)
        return tuple(result)

    def _feed_committed(self) -> None:
        units = self.committed_units(str(self._committed_text or ""),
                                     policy=self._committed_unit_policy)
        word_offset = 0
        try:
            for index, unit in enumerate(units):
                if self._resource_manager.is_set() or self._failed.is_set():
                    return
                metadata = {
                    "committed_stream": True,
                    "complete_text": str(self._committed_text or ""),
                    "chunk_text": unit,
                    "sequence": index,
                    "word_offset": word_offset,
                    "word_count": len(re.findall(r"\S+", unit)),
                    "final_chunk": index == len(units) - 1,
                }
                item = (index, unit, unit, time.monotonic(), False, metadata)
                # A finite committed reply can apply actual backpressure.
                # Unlike early provider deltas, it never needs overflow text
                # coalescing to keep a generation socket draining.
                while not self._resource_manager.is_set() and not self._failed.is_set():
                    with self._overflow_lock:
                        if self._resource_manager.is_set():
                            return
                        try:
                            self._queue.put_nowait(item)
                            queued = True
                        except Full:
                            queued = False
                    if queued:
                        break
                    self._cancelled.wait(0.01)
                else:
                    return
                word_offset += metadata["word_count"]
        finally:
            self._closed.set()

    def submit(self, text: str, *, presentation_text: str | None = None) -> bool:
        """Queue a complete natural-language chunk without reordering it."""
        if self._committed_text is not None:
            return False
        spoken = str(text)
        presentation = str(presentation_text if presentation_text is not None else text)
        if self._closed.is_set() or self._cancelled.is_set() or not spoken.strip():
            return False
        submitted_at = time.monotonic()
        chunk_index = self._next_chunk_index
        item = (
            chunk_index,
            spoken,
            presentation,
            submitted_at,
            self._on_chunk_submitted is not None,
        )
        announced = False
        with self._overflow_lock:
            # Cancellation/draining and nonblocking enqueue are one boundary.
            if self._cancelled.is_set() or self._closed.is_set():
                return False
            if self._overflow_item is None:
                try:
                    self._queue.put_nowait(item)
                    self._next_chunk_index += 1
                    announced = True
                except Full:
                    self._overflow_item = item[:-1] + (False,)
                    self._next_chunk_index += 1
            else:
                old_index, old_spoken, old_presentation, old_at, _ = self._overflow_item
                self._overflow_item = (
                    old_index, self._join_units(old_spoken, spoken),
                    self._join_units(old_presentation, presentation), old_at, False,
                )
        if announced and self._on_chunk_submitted is not None:
            self._on_chunk_submitted(spoken, presentation, chunk_index)
        development_flight_recorder().mark(
            "tts_synthesis_queued" if announced else "tts_synthesis_coalesced",
            chunk_index=chunk_index, queue_depth=self._queue.qsize(),
            pending_jobs=self.pending_jobs, characters=len(spoken), words=len(spoken.split()),
        )
        return True

    @property
    def pending_jobs(self) -> int:
        with self._overflow_lock:
            overflow = 1 if self._overflow_item is not None else 0
        return self._queue.qsize() + self._prepared.qsize() + overflow

    @property
    def owns_continuous_playback(self) -> bool:
        """Cancellation targets this utterance, never a later playback owner."""
        return self._owned_continuous

    @staticmethod
    def _join_units(first: str, second: str) -> str:
        if not first:
            return second
        if not second:
            return first
        separator = "" if first[-1].isspace() or second[0].isspace() else " "
        return first + separator + second

    def close(self) -> None:
        if self._committed_text is not None:
            return  # The finite feeder closes after all exact units are queued.
        if self._closed.is_set():
            return
        self._closed.set()

    def cancel(self) -> None:
        with self._overflow_lock:
            # Old turn cleanup can call cancel again after a replacement has
            # begun playback. Only the first retirement owns provider.stop().
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            self._closed.set()
            self.outcome = "cancelled"
            self._overflow_item = None
            for pending in (self._queue, self._prepared):
                while True:
                    try:
                        pending.get_nowait()
                        pending.task_done()
                    except Empty:
                        break
        stop = (lambda: self._tts.abort_prepared_stream(self.playback_id)) if self._owned_continuous else getattr(self._tts, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def join(self, timeout: float | None = None) -> None:
        if self._feeder_thread is not None:
            self._feeder_thread.join(timeout)
        self._synthesis_thread.join(timeout)
        self._playback_thread.join(timeout)

    def _report_failure(self, reason: str) -> None:
        if self._on_owned_failure is not None:
            self._on_owned_failure(self, reason)
        elif self._on_failure is not None:
            self._on_failure(reason)

    def _provider_active(self) -> bool:
        return self._resource_manager.provider_active()

    def _wait_for_provider_idle(self, timeout: float = 30.0) -> bool:
        return self._resource_manager.wait_for_provider_idle(timeout)

    def _abort_speech_sequence(self, reason: str) -> None:
        development_flight_recorder().mark(
            "tts_synthesis_sequence_failure", reason=reason,
            coherent_sequence_failure=True,
        )
        self._failed.set()
        self.outcome = "failed"
        self._closed.set()
        if self._owned_continuous:
            self._tts.abort_prepared_stream(self.playback_id)
        self._report_failure(reason)
        with self._overflow_lock:
            self._overflow_item = None
        while True:
            try:
                queued = self._queue.get_nowait()
                self._queue.task_done()
                if queued is self._STOP:
                    break
            except Empty:
                break
        for pending in (self._prepared,):
            while True:
                try:
                    pending.get_nowait()
                    pending.task_done()
                except Empty:
                    break
        self._put_prepared(self._STOP)

    def _synthesize(self) -> None:
        while not self._resource_manager.is_set():
            try:
                item = self._queue.get(timeout=0.10)
            except Empty:
                with self._overflow_lock:
                    item = self._overflow_item
                    self._overflow_item = None
                if item is None and self._closed.is_set() and self._queue.empty():
                    self._put_prepared(self._STOP)
                    return
                if item is None:
                    continue
                from_queue = False
            else:
                from_queue = True
            try:
                if self._cancelled.is_set():
                    return
                try:
                    chunk_index, spoken, presentation, submitted_at, announced = item[:5]
                    metadata = item[5] if len(item) > 5 else None
                    if not announced and self._on_chunk_submitted is not None:
                        self._on_chunk_submitted(spoken, presentation, chunk_index)
                    synthesis_started_at = time.monotonic()
                    development_flight_recorder().mark(
                        "tts_synthesis_job_start", chunk_index=chunk_index,
                        queue_depth=self._queue.qsize(), pending_jobs=self.pending_jobs,
                        characters=len(spoken), words=len(spoken.split()), active_jobs=1,
                    )
                    _timing_log(
                        "TTS chunk synthesis begin; "
                        f"chunk={chunk_index}; queue_wait={synthesis_started_at - submitted_at:.3f}s"
                    )
                    recovery = self._resource_manager.prepare(
                        spoken, unit_index=chunk_index, direct=False,
                    )
                    if not recovery.succeeded:
                        if recovery.cancelled:
                            return
                        self._abort_speech_sequence("tts_synthesis_sequence_failed")
                        return
                    prepared = recovery.prepared
                    synthesis_ready_at = time.monotonic()
                    development_flight_recorder().mark(
                        "tts_synthesis_job_end", chunk_index=chunk_index,
                        duration_ms=(synthesis_ready_at - synthesis_started_at) * 1000.0,
                        queue_depth=self._queue.qsize(), pending_jobs=self.pending_jobs,
                        active_jobs=0,
                    )
                    _timing_log(
                        "TTS chunk synthesis ready; "
                        f"chunk={chunk_index}; synthesis={synthesis_ready_at - synthesis_started_at:.3f}s"
                    )
                    prepared_item = (
                        chunk_index, spoken, presentation, prepared, submitted_at, synthesis_ready_at, metadata
                    )
                    if not self._put_prepared(prepared_item):
                        return
                except Exception as error:
                    development_flight_recorder().mark(
                        "tts_synthesis_worker_failure", exception_class=type(error).__name__[:80],
                    )
                    self._abort_speech_sequence("tts_synthesis_sequence_failed")
                    return
            finally:
                if from_queue:
                    self._queue.task_done()

    def _put_prepared(self, item: object) -> bool:
        while not self._cancelled.is_set():
            with self._overflow_lock:
                if self._cancelled.is_set():
                    return False
                try:
                    self._prepared.put_nowait(item)
                except Full:
                    queued = False
                else:
                    queued = True
            if queued:
                if item is not self._STOP:
                    development_flight_recorder().mark(
                        "tts_pcm_queued", chunk_index=int(item[0]),
                        queue_depth=self._prepared.qsize(), pending_jobs=self.pending_jobs,
                    )
                return True
            self._cancelled.wait(0.05)  # Never hold a dispatch/PTT lock while waiting.
        return False

    def _play(self) -> None:
        continuous_started = False
        begin_continuous = getattr(self._tts, "begin_prepared_stream", None)
        append_continuous = getattr(self._tts, "append_prepared_stream", None)
        finish_continuous = getattr(self._tts, "finish_prepared_stream", None)
        try:
            while not self._resource_manager.is_set():
                try:
                    item = self._prepared.get(timeout=0.10)
                except Empty:
                    if self._closed.is_set() and not self._synthesis_thread.is_alive() and self._prepared.empty():
                        return
                    continue
                try:
                    if item is self._STOP or self._resource_manager.is_set():
                        if self._failed.is_set():
                            return
                        if continuous_started and not self._resource_manager.is_set() and callable(finish_continuous):
                            if self._owned_continuous:
                                finish_continuous(stream_id=self.playback_id)
                            else:
                                finish_continuous()
                            finished = getattr(self._tts, "playback_finished", None)
                            if finished is not None:
                                while not finished.wait(0.05):
                                    if self._resource_manager.is_set():
                                        return
                            if self._owned_continuous:
                                outcome = self._tts.continuous_stream_outcome(self.playback_id)
                                if outcome == "failed":
                                    self._failed.set()
                                    self.outcome = "failed"
                                    self._report_failure("tts_playback_failed")
                                    return
                                if outcome == "cancelled":
                                    self.outcome = "cancelled"
                                    return
                        if not self._resource_manager.is_set():
                            self.outcome = "completed"
                        return
                    try:
                        chunk_index, spoken, presentation, prepared, submitted_at, synthesis_ready_at, metadata = item
                        dispatch_at = time.monotonic()
                        _timing_log(
                            "TTS chunk playback dispatch; "
                            f"chunk={chunk_index}; ready_wait={dispatch_at - synthesis_ready_at:.3f}s; "
                            f"submit_to_dispatch={dispatch_at - submitted_at:.3f}s"
                        )
                        on_started = (
                            (lambda s=spoken, p=presentation, i=chunk_index:
                                self._on_chunk_starting(s, p, i))
                            if self._on_chunk_starting is not None else None
                        )
                        if callable(begin_continuous) and callable(append_continuous):
                            if not continuous_started:
                                if self._owned_continuous:
                                    started = begin_continuous(
                                        prepared, on_started=on_started,
                                        cancelled=self._resource_manager, metadata=metadata,
                                    )
                                    if started is not False:
                                        self.playback_id = int(started)
                                else:
                                    started = begin_continuous(prepared, on_started=on_started)
                                continuous_started = started is not False
                            else:
                                if self._owned_continuous:
                                    started = append_continuous(
                                        prepared, on_started=on_started,
                                        stream_id=self.playback_id,
                                        cancelled=self._resource_manager, metadata=metadata,
                                    )
                                else:
                                    started = append_continuous(prepared, on_started=on_started)
                        else:
                            if on_started is not None:
                                on_started()
                            start_prepared = getattr(self._tts, "start_prepared_chunk", None)
                            started = start_prepared(prepared) if callable(start_prepared) else self._tts.speak(spoken)
                        if started is False:
                            if not self._resource_manager.is_set():
                                self._abort_speech_sequence("tts_chunk_failed")
                            return
                        if self._resource_manager.is_set():
                            if self._owned_continuous:
                                self._tts.abort_prepared_stream(self.playback_id)
                            return
                        if not continuous_started:
                            # Legacy providers retain ordered per-chunk playback.
                            finished = getattr(self._tts, "playback_finished", None)
                            if started is not False and finished is not None:
                                while not finished.wait(0.05):
                                    if self._resource_manager.is_set():
                                        return
                    except Exception:
                        if not self._resource_manager.is_set():
                            self._abort_speech_sequence("tts_chunk_exception")
                        return
                finally:
                    self._prepared.task_done()
        finally:
            if self._resource_manager.is_set() and self.outcome != "failed":
                self.outcome = "cancelled"
                if self._owned_continuous:
                    self._tts.abort_prepared_stream(self.playback_id)
            if self._on_complete is not None:
                try:
                    self._on_complete(self)
                except Exception:
                    pass
