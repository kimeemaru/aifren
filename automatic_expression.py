"""Optional local CPU facial proposals over final, accepted assistant prose.

This is presentation, never evidence, state, or dialogue validation. The caller
owns publication/character/scope identity and must recheck the opaque token in
the callback. Loading and inference never download anything. Installation is an
explicit, hash-checked operation: ``python automatic_expression.py --download``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Callable
import urllib.request

from dialogue_semantics import DialogueSpanKind, parse_dialogue


MODEL_ID = "cardiffnlp/twitter-roberta-base-emotion-latest"
MODEL_REVISION = "415620c4fbc8bd82b82b9fd46642fcec6519d537"
MODEL_LICENSE = "MIT"
MODEL_FILES = {
    "model_quantized.onnx": (125860185, "c8aa675dd76487879ecabafb3e485d46c9929c2ba6da6a6cbd865a3bb21b4104"),
    "tokenizer.json": (2108688, "3045f84d35d20dfe74630b9c0f7f98d83295915f9d064d0dd9dc73b858ba4bfc"),
    "config.json": (1222, "f6b78c643f09761faf9022b6ef8093d7d592ea8379c27208249f2985b67a792c"),
}
MODEL_SOURCE_FILES = {
    "model.safetensors": (498640508, "a26c4ec370ca24cf95a0d6a9a2cc4aa3ae637f6c1e87170b30d17994d01c023d"),
}
from runtime_layout import resource_path

DEFAULT_MODEL_DIR = resource_path("models/expression/cardiff-emotion-415620c4")
MAX_INPUT_CHARACTERS = 4096
MAX_INPUT_TOKENS = 192
MIN_SCORE = .75
MIN_SEPARATION = .15
CONFLICTING_SCORE = .20
LABELS = ("anger", "anticipation", "disgust", "fear", "joy", "love", "optimism",
          "pessimism", "sadness", "surprise", "trust")
# Other labels, low scores and mixed tone preserve the current face. Multi-label
# scores are not normalized probabilities of an appropriate avatar performance.
EXPRESSION_LABELS = {"anger": "angry", "joy": "happy", "sadness": "sad", "surprise": "surprised"}


@dataclass(frozen=True)
class ExpressionInput:
    text: str = field(default="", repr=False)
    reason: str = "ready"


@dataclass(frozen=True)
class ExpressionProposal:
    emotion: str
    intensity: float
    label: str
    score: float


@dataclass(frozen=True)
class ExpressionResult:
    proposal: ExpressionProposal | None = None
    reason: str = "uncertain"
    input_characters: int = 0
    input_tokens: int = 0
    inference_ms: float = 0.0
    scores: tuple[tuple[str, float], ...] = ()

    def diagnostics(self) -> dict:
        """Bounded, content-free diagnostics; not calibrated truth confidence."""
        return {
            "reason": self.reason, "input_characters": self.input_characters,
            "input_tokens": self.input_tokens, "inference_ms": round(self.inference_ms, 3),
            "emotion": self.proposal.emotion if self.proposal else "none",
            "label": self.proposal.label if self.proposal else "none",
            "score": round(self.proposal.score, 4) if self.proposal else None,
        }


_CONTROL = re.compile(r"<\||\|>|\[/?(?:system|assistant|user|instruction)\]|(?:^|\n)\s*(?:system|assistant|user)\s*:", re.I)
_REPORTING = re.compile(
    r"\b(?:I|you|we|they|he|she)\s+(?:remember(?:ed)?|recalled|said|told|wrote|quoted)\b"
    r"|\b(?:the|a)\s+(?:source|archive|record|transcript|quotation)\s+(?:says|said|states|reads)\b",
    re.I,
)


def project_expression_input(dialogue: str) -> ExpressionInput:
    """Conservative surface exclusion, not a general temporal/semantic parser.

    Quotes, code, report scaffolding and control-looking data make the entire
    candidate ineligible. Removing just their words could reverse negation or
    splice a new proposition. Current action spans are separately owned by the
    existing RP compatibility path and never become classifier instructions.
    The caller excludes authoritative memory cores before this function.
    """
    if not isinstance(dialogue, str) or not dialogue.strip():
        return ExpressionInput(reason="empty")
    if len(dialogue) > MAX_INPUT_CHARACTERS:
        return ExpressionInput(reason="character_bound")
    if _CONTROL.search(dialogue):
        return ExpressionInput(reason="control_data")
    if any(c in dialogue for c in ('`', '{', '}', '"', '“', '”', '«', '»')):
        return ExpressionInput(reason="quoted_or_code")
    for index, character in enumerate(dialogue):
        if character not in "'‘’":
            continue
        # Apostrophes within words are contractions, not quotation delimiters.
        if not (index > 0 and index + 1 < len(dialogue)
                and dialogue[index - 1].isalnum() and dialogue[index + 1].isalnum()):
            return ExpressionInput(reason="quoted_or_code")
    if _REPORTING.search(dialogue):
        return ExpressionInput(reason="reported_content")
    text = "".join(" " if span.kind == DialogueSpanKind.EMOTE else span.text
                   for span in parse_dialogue(dialogue))
    if "*" in text:
        return ExpressionInput(reason="incomplete_markup")
    text = " ".join(text.split()).strip()
    if not text or not any(c.isalpha() for c in text):
        return ExpressionInput(reason="no_prose")
    return ExpressionInput(text=text)


def select_expression(scores: dict[str, float]) -> tuple[ExpressionProposal | None, str]:
    """Map only a clear top emotion; uncertain/mixed/neutral means no change."""
    if set(scores) != set(LABELS) or any(
            isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or value < 0 or value > 1
            for value in scores.values()):
        return None, "invalid_scores"
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    label, score = ordered[0]
    if label == "neutral":
        return None, "neutral_no_change"
    if label not in EXPRESSION_LABELS:
        return None, "unmapped_tone"
    if score < MIN_SCORE:
        return None, "uncertain"
    # Independent multi-label scores need not sum to one. A clear top label
    # can coexist with a meaningful opposite-valence score even with a large
    # rank margin. Preserve no-change for that measured mixed-tone case. These
    # are model classes, not a scan for positive/negative words in dialogue.
    opposite = ("sadness", "anger") if label == "joy" else (
        ("joy",) if label in {"sadness", "anger"} else ()
    )
    if any(scores[other] >= CONFLICTING_SCORE for other in opposite):
        return None, "mixed_tone"
    # Cardiff is multi-label: optimism can coexist with explicit joy. It
    # cannot create a face on its own, but does not compete as another facial
    # target. Retain separation between mapped faces and the opposition veto.
    competitor = max((scores[other] for other in EXPRESSION_LABELS if other != label), default=0.0)
    if score - competitor < MIN_SEPARATION:
        return None, "mixed_tone"
    # Modest, repeatable intensity is presentation policy. Scores are not a
    # measure of how far to deform a face, and are not persisted as mood.
    return ExpressionProposal(EXPRESSION_LABELS[label], .45, label, float(score)), "proposed"


def _file_valid(path: Path, expected: tuple[int, str]) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected[0]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == expected[1]
    except OSError:
        return False


def _download_files(model_dir, files, *, cancel_event=None) -> list[str]:
    """Explicit bounded download of pinned data files, never remote code.

    A verified file is retained. An interrupted/invalid transfer removes only
    its own temporary file and cannot replace the last verified model file.
    """
    directory = Path(model_dir)
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for name, expected in files.items():
        target = directory / name
        if _file_valid(target, expected):
            continue
        if target.is_symlink():
            raise ValueError("Expression model cache contains a symbolic link")
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
        descriptor, temporary_name = tempfile.mkstemp(prefix=".expression-download-", dir=directory)
        temporary = Path(temporary_name)
        started = time.monotonic()
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as destination:
                with urllib.request.urlopen(url, timeout=30) as response:
                    while True:
                        if ((cancel_event is not None and cancel_event.is_set())
                                or time.monotonic() - started > 180):
                            raise TimeoutError("Expression model download cancelled or timed out")
                        block = response.read(64 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > expected[0]:
                            raise ValueError("Expression model exceeds pinned file size")
                        digest.update(block)
                        destination.write(block)
                if size != expected[0] or digest.hexdigest() != expected[1]:
                    raise ValueError("Expression model download failed identity verification")
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, target)
            installed.append(name)
        finally:
            temporary.unlink(missing_ok=True)
    return installed


def install_model(model_dir: Path | str = DEFAULT_MODEL_DIR, *, cancel_event=None) -> dict:
    """Explicit pinned installation and isolated CPU conversion, never on a turn.

    Downloaded safetensors are inert weights. A local built-in transformer class
    produces the checked int8 graph. No remote Python/custom ops are installed.
    Existing verified graphs are retained; failed conversion cannot replace one.
    """
    directory = Path(model_dir)
    installed = _download_files(directory, {k: v for k, v in MODEL_FILES.items()
                                           if k != "model_quantized.onnx"}, cancel_event=cancel_event)
    expected = MODEL_FILES.get("model_quantized.onnx")
    target = directory / "model_quantized.onnx"
    if expected is not None and not _file_valid(target, expected):
        import shutil
        import subprocess
        import sys
        if target.is_symlink():
            raise ValueError("Expression model cache contains a symbolic link")
        installed += _download_files(directory, MODEL_SOURCE_FILES, cancel_event=cancel_event)
        with tempfile.TemporaryDirectory(prefix=".expression-build-", dir=directory) as work:
            work = Path(work)
            # Only immutable verified input data enters the isolated converter.
            for name in (*MODEL_SOURCE_FILES, "config.json"):
                shutil.copyfile(directory / name, work / name)
            command = [sys.executable, str(Path(__file__).with_name("expression_model_export.py")), str(work)]
            with (work / "conversion.log").open("w") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                started = time.monotonic()
                try:
                    while process.poll() is None:
                        if ((cancel_event is not None and cancel_event.is_set())
                                or time.monotonic() - started > 180):
                            raise TimeoutError("Expression conversion cancelled or timed out")
                        time.sleep(.1)
                    if process.returncode or not _file_valid(work / target.name, expected):
                        raise ValueError("Expression conversion did not match the reviewed graph; check installer dependencies")
                    os.replace(work / target.name, target)
                finally:
                    if process.poll() is None:
                        process.terminate()  # The exact installer-owned process only.
                        process.wait(timeout=10)
    return {"model": MODEL_ID, "revision": MODEL_REVISION, "license": MODEL_LICENSE,
            "downloaded_files": installed, "bytes": sum(item[0] for item in MODEL_FILES.values())}


class CpuExpressionClassifier:
    """One local ONNX encoder; only explicit installation has network access."""

    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR):
        self.model_dir = Path(model_dir)
        self._session = None
        self._tokenizer = None
        self._state = "not_loaded"
        self.load_ms = 0.0

    def status(self) -> dict:
        detail = {
            "not_loaded": "Optional CPU expression model is not loaded.",
            "loading": "Loading the optional CPU expression model.",
            "ready": "Ready on CPU",
            "model_missing_or_invalid": "Optional expression model files are missing or invalid.",
            "dependency_unavailable": "Optional CPU expression dependencies are unavailable.",
            "model_load_failed": "Optional CPU expression model could not be loaded.",
        }.get(self._state, "Optional CPU expressions are unavailable.")
        return {"state": self._state, "ready": self._state == "ready", "detail": detail,
                "model": MODEL_ID, "revision": MODEL_REVISION,
                "device": "cpu", "threads": 1, "load_ms": round(self.load_ms, 3),
                "model_bytes": MODEL_FILES["model_quantized.onnx"][0]}

    def load(self) -> bool:
        if self._session is not None:
            return True
        self._state = "loading"
        start = time.monotonic()
        if not all(_file_valid(self.model_dir / name, expected) for name, expected in MODEL_FILES.items()):
            self._state = "model_missing_or_invalid"
            return False
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            configuration = json.loads((self.model_dir / "config.json").read_text())
            if tuple(configuration["id2label"][str(i)] for i in range(len(LABELS))) != LABELS:
                raise ValueError("Unexpected expression labels")
            tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
            tokenizer.no_padding()
            tokenizer.no_truncation()
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(str(self.model_dir / "model_quantized.onnx"),
                                          sess_options=options, providers=["CPUExecutionProvider"])
            if session.get_providers() != ["CPUExecutionProvider"]:
                raise ValueError("Unexpected expression execution provider")
            self._session, self._tokenizer = session, tokenizer
            self._state = "ready"
            return True
        except (ImportError, ModuleNotFoundError):
            self._state = "dependency_unavailable"
            return False
        except Exception:
            self._state = "model_load_failed"
            return False
        finally:
            self.load_ms = (time.monotonic() - start) * 1000

    def classify(self, dialogue: str) -> ExpressionResult:
        projected = project_expression_input(dialogue)
        if projected.reason != "ready":
            return ExpressionResult(reason=projected.reason)
        if self._session is None or self._tokenizer is None:
            return ExpressionResult(reason="not_ready")
        start = time.monotonic()
        token_count = 0
        try:
            import numpy as np
            tokens = self._tokenizer.encode(projected.text)
            token_count = len(tokens.ids)
            if token_count > MAX_INPUT_TOKENS:
                return ExpressionResult(reason="token_bound", input_characters=len(projected.text),
                                        input_tokens=token_count)
            values = self._session.run(None, {
                "input_ids": np.array([tokens.ids], dtype=np.int64),
                "attention_mask": np.array([tokens.attention_mask], dtype=np.int64),
            })[0]
            if values.shape != (1, len(LABELS)) or not np.all(np.isfinite(values)):
                return ExpressionResult(reason="invalid_logits", input_characters=len(projected.text),
                                        input_tokens=token_count)
            probabilities = 1 / (1 + np.exp(-np.clip(values[0], -30, 30)))
            scores = {label: float(probabilities[i]) for i, label in enumerate(LABELS)}
            proposal, reason = select_expression(scores)
            return ExpressionResult(proposal, reason, len(projected.text), token_count,
                                    (time.monotonic() - start) * 1000, tuple(scores.items()))
        except Exception:
            return ExpressionResult(reason="inference_failed", input_characters=len(projected.text),
                                    input_tokens=token_count,
                                    inference_ms=(time.monotonic() - start) * 1000)


class AutomaticExpressionWorker:
    """One in-flight job, no backlog, no publication authority of its own.

    Disabling/cancelling invalidates the job epoch. An in-flight bounded CPU
    encoder is allowed to finish; its result cannot revive a retired request.
    The callback must separately validate its published-response owner token.
    """

    def __init__(self, *, enabled: bool = False, classifier=None, on_status=None):
        self.classifier = classifier if classifier is not None else CpuExpressionClassifier()
        self._lock = threading.Lock()
        self._thread = None
        self._epoch = 0
        self._enabled = False
        self._closed = False
        self._last_reason = "disabled"
        self._offered = self._completed = self._late = self._busy = 0
        self._on_status = on_status
        if enabled:
            self.set_enabled(True)

    def status(self) -> dict:
        with self._lock:
            return {**self.classifier.status(), "enabled": self._enabled,
                    "busy": self._thread is not None, "last_reason": self._last_reason,
                    "offered": self._offered, "completed": self._completed,
                    "late_drops": self._late, "busy_skips": self._busy}

    def status_text(self) -> str:
        status = self.status()
        prefix = "" if status["enabled"] else "Off. "
        return prefix + str(status.get("detail", "Optional CPU expressions."))

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Automatic expressions require true or false")
        with self._lock:
            if self._closed:
                return
            self._enabled = enabled
            self._epoch += 1
            if not enabled:
                self._last_reason = "disabled"
                return
            if self._thread is not None or self.classifier.status()["state"] == "ready":
                return
            self._last_reason = "loading"
            thread = threading.Thread(target=self._load, name="aifren-expression-load", daemon=True)
            self._thread = thread
            thread.start()

    def _load(self) -> None:
        try:
            ready = self.classifier.load()
        except Exception:
            ready = False
        with self._lock:
            self._thread = None
            self._last_reason = ("disabled" if self._closed or not self._enabled
                                 else "ready" if ready else "unavailable")
            notify = self._on_status if not self._closed else None
        if notify is not None:
            try:
                notify(self.status_text())
            except Exception:
                pass  # Readiness presentation cannot break worker ownership.

    def offer(self, final_prose: str, ownership_token, callback: Callable,
              *, deadline_seconds: float = 2.0) -> bool:
        if not callable(callback):
            raise ValueError("Automatic expression callback is required")
        if not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 5:
            raise ValueError("Automatic expression deadline must be finite and at most five seconds")
        # Cheap exclusion happens before acquiring/starting the CPU worker.
        projected = project_expression_input(final_prose)
        with self._lock:
            if self._closed or not self._enabled:
                self._last_reason = "disabled"
                return False
            if projected.reason != "ready":
                self._last_reason = projected.reason
                return False
            if self._thread is not None:
                self._busy += 1
                self._last_reason = "busy"
                return False
            if self.classifier.status()["state"] != "ready":
                self._last_reason = "not_ready"
                return False
            epoch = self._epoch
            self._offered += 1
            thread = threading.Thread(target=self._run,
                                      args=(projected.text, ownership_token, callback, epoch,
                                            time.monotonic() + deadline_seconds),
                                      name="aifren-expression", daemon=True)
            self._thread = thread
            thread.start()
            return True

    def _run(self, text, ownership_token, callback, epoch, deadline):
        try:
            result = self.classifier.classify(text)
        except Exception:
            result = ExpressionResult(reason="inference_failed")
        with self._lock:
            valid = (self._enabled and not self._closed and epoch == self._epoch
                     and time.monotonic() <= deadline)
            self._completed += 1
            self._last_reason = result.reason if valid else "late_or_cancelled"
            if not valid:
                self._late += 1
        if valid:
            try:
                callback(ownership_token, result)
            except Exception:
                with self._lock:
                    self._last_reason = "callback_failed"
        with self._lock:
            self._thread = None

    def cancel_pending(self) -> None:
        with self._lock:
            self._epoch += 1

    def close(self, timeout: float = 0.0) -> None:
        with self._lock:
            self._closed = True
            self._enabled = False
            self._epoch += 1
            thread = self._thread
        if thread is not None and thread is not threading.current_thread() and timeout > 0:
            thread.join(min(5.0, timeout))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Install the optional pinned CPU expression model.")
    parser.add_argument("--download", action="store_true", help="Explicitly download and verify the pinned model")
    args = parser.parse_args()
    if args.download:
        print(json.dumps(install_model(), indent=2))
    else:
        print(json.dumps(CpuExpressionClassifier().status(), indent=2))
