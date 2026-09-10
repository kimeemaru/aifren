"""Small ordered speech queue for streamed assistant text.

It deliberately contains no provider logic: Audio8, Kokoro, and future local
engines all expose the same ``speak``/``stop`` surface.  The worker keeps text
generation independent from audio playback while preserving source order.
"""

from __future__ import annotations

import os
from queue import Empty, Full, Queue
import threading
import time
from typing import Any, Callable
from dataclasses import dataclass

from development_flight_recorder import development_flight_recorder


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
    """Bounded, ordered TTS dispatcher with lossless resource recovery."""

    _STOP = object()

    def __init__(
        self,
        tts: Any,
        *,
        max_chunks: int = 4,
        on_failure: Callable[[str], None] | None = None,
        on_chunk_submitted: Callable[[str, str, int], None] | None = None,
        on_chunk_starting: Callable[[str, str, int], None] | None = None,
        on_complete: Callable[["StreamingSpeechQueue"], None] | None = None,
        provider_generation_active: Callable[[], bool] | None = None,
    ):
        self._tts = tts
        self._queue: Queue[object] = Queue(maxsize=max(1, int(max_chunks)))
        self._prepared: Queue[object] = Queue(maxsize=max(1, int(max_chunks)))
        self._overflow_lock = threading.Lock()
        self._overflow_item: tuple | None = None
        self._cancelled = threading.Event()
        self._closed = threading.Event()
        self._on_failure = on_failure
        self._on_chunk_submitted = on_chunk_submitted
        self._on_chunk_starting = on_chunk_starting
        self._on_complete = on_complete
        self._provider_generation_active = provider_generation_active
        self._resource_manager = TtsSynthesisResourceManager(
            tts, cancelled=self._cancelled,
            provider_generation_active=provider_generation_active,
        )
        self._failed = threading.Event()
        self._next_chunk_index = 0
        self._synthesis_thread = threading.Thread(target=self._synthesize, name="aifren-tts-synthesis", daemon=True)
        self._playback_thread = threading.Thread(target=self._play, name="aifren-tts-playback", daemon=True)
        self._synthesis_thread.start()
        self._playback_thread.start()

    def submit(self, text: str, *, presentation_text: str | None = None) -> bool:
        """Queue a complete natural-language chunk without reordering it."""
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

    @staticmethod
    def _join_units(first: str, second: str) -> str:
        if not first:
            return second
        if not second:
            return first
        separator = "" if first[-1].isspace() or second[0].isspace() else " "
        return first + separator + second

    def close(self) -> None:
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
            self._overflow_item = None
            for pending in (self._queue, self._prepared):
                while True:
                    try:
                        pending.get_nowait()
                        pending.task_done()
                    except Empty:
                        break
        stop = getattr(self._tts, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def join(self, timeout: float | None = None) -> None:
        self._synthesis_thread.join(timeout)
        self._playback_thread.join(timeout)

    def _report_failure(self, reason: str) -> None:
        if self._on_failure is not None:
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
        self._closed.set()
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
        self._put_prepared(self._STOP)

    def _synthesize(self) -> None:
        while not self._cancelled.is_set():
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
                    chunk_index, spoken, presentation, submitted_at, announced = item
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
                        chunk_index, spoken, presentation, prepared, submitted_at, synthesis_ready_at
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
            while not self._cancelled.is_set():
                try:
                    item = self._prepared.get(timeout=0.10)
                except Empty:
                    if self._closed.is_set() and not self._synthesis_thread.is_alive() and self._prepared.empty():
                        return
                    continue
                try:
                    if item is self._STOP or self._cancelled.is_set():
                        if continuous_started and not self._cancelled.is_set() and callable(finish_continuous):
                            finish_continuous()
                            finished = getattr(self._tts, "playback_finished", None)
                            if finished is not None:
                                finished.wait()
                        return
                    try:
                        chunk_index, spoken, presentation, prepared, submitted_at, synthesis_ready_at = item
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
                                started = begin_continuous(prepared, on_started=on_started)
                                continuous_started = started is not False
                            else:
                                started = append_continuous(prepared, on_started=on_started)
                        else:
                            if on_started is not None:
                                on_started()
                            start_prepared = getattr(self._tts, "start_prepared_chunk", None)
                            started = start_prepared(prepared) if callable(start_prepared) else self._tts.speak(spoken)
                        if started is False:
                            self._report_failure("tts_chunk_failed")
                        if not continuous_started:
                            # Legacy providers retain ordered per-chunk playback.
                            finished = getattr(self._tts, "playback_finished", None)
                            if started is not False and finished is not None:
                                finished.wait()
                    except Exception:
                        self._report_failure("tts_chunk_exception")
                finally:
                    self._prepared.task_done()
        finally:
            if self._on_complete is not None:
                try:
                    self._on_complete(self)
                except Exception:
                    pass
