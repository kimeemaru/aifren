"""Provider-neutral OpenAI-compatible chat transport for online or local models."""

from __future__ import annotations

from collections.abc import Iterator
import secrets
import threading
from typing import Any, Callable

from openai import OpenAI

from llm.unavailable import ModelTransportError


class OpenAICompatibleLLM:
    """Small transport adapter; memory and context stay outside this class."""

    # llama.cpp reserves UINT32_MAX as its random-seed sentinel.  Supplying a
    # concrete value below it keeps the request explicit and portable across
    # the current OpenAI-compatible server boundary.
    _MAX_EXPLICIT_SEED = (1 << 32) - 2
    _SAMPLING_FIELDS = frozenset({
        "temperature", "top_p", "top_k", "min_p", "presence_penalty", "repeat_penalty",
    })
    _EXTRA_BODY_SAMPLING_FIELDS = frozenset({"top_k", "min_p", "repeat_penalty"})

    def __init__(self, *, api_key: str | None, base_url: str, model: str,
                 context_budget_chars: int | None = None,
                 context_capacity_tokens: int | None = None,
                 context_operating_target_tokens: int | None = None,
                 local_tokenizer: bool = False,
                 fresh_request_seeds: bool = False,
                 seed_source: Callable[[], int] | None = None,
                 sampling_preset: str = "",
                 sampling_options: dict[str, int | float] | None = None,
                 companion_memory_realization: bool = False,
                 local_ordinary_dialogue: bool = False,
                 local_presentation: bool = False,
                 application_policy_role: str = "user") -> None:
        if not str(base_url).strip():
            raise RuntimeError("An OpenAI-compatible endpoint is required.")
        if not str(model).strip():
            raise RuntimeError("A model name is required when discovery has not selected one.")
        self.base_url = str(base_url).rstrip("/") + "/"
        self.model = str(model).strip()
        self.context_budget_chars = int(context_budget_chars) if context_budget_chars else None
        self.context_capacity_tokens = int(context_capacity_tokens) if context_capacity_tokens else None
        self.context_operating_target_tokens = context_operating_target_tokens
        self.local_tokenizer = bool(local_tokenizer)
        self.fresh_request_seeds = bool(fresh_request_seeds)
        self.companion_memory_realization = bool(companion_memory_realization)
        # Explicit local experiment opt-in, never inferred from endpoint/model
        # names. Normal and online factories remain off pending paired review.
        self.local_ordinary_dialogue = bool(local_ordinary_dialogue)
        self.local_presentation = bool(local_presentation)
        if application_policy_role not in {"user", "system"}:
            raise ValueError("Unsupported application policy role")
        self.application_policy_role = application_policy_role
        self.last_response_diagnostics = {}
        options = dict(sampling_options or {})
        unknown = set(options).difference(self._SAMPLING_FIELDS)
        if unknown:
            raise ValueError("unsupported compatible sampling field: " + sorted(unknown)[0])
        self.sampling_preset = str(sampling_preset or "").strip()
        self.sampling_options = options
        self._seed_source = seed_source or (
            lambda: secrets.randbelow(self._MAX_EXPLICIT_SEED + 1)
        )
        self.client = OpenAI(api_key=str(api_key or "local-no-key"), base_url=self.base_url)
        self._stream_lock = threading.Lock()
        self._active_stream = None

    def count_context_tokens(self, text: str) -> int | None:
        """Installed llama.cpp tokenizer; content only, no inference/model load.

        Opt-in only for the configured local adapter. Unknown online services
        are never probed. Planner owns timeout fallback and framing reserve.
        """
        if not self.local_tokenizer:
            return None
        endpoint = self.base_url.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        response = self.client._client.post(
            endpoint + "/extras/tokenize/count",
            json={"model": self.model, "input": text},
            headers=self.client.auth_headers, timeout=0.75,
        )
        response.raise_for_status()
        count = response.json().get("count")
        return count if isinstance(count, int) and not isinstance(count, bool) else None

    def _messages(self, messages: list[dict[str, Any]], character_prompt: str) -> list[dict[str, str]]:
        # Only the application prefix has a declared template capability.
        # Data/context and canonical roles are never promoted by this adapter.
        # Canonical timestamps and truth-scope IDs are persistence provenance,
        # never ordinary companion prompt fields.
        projected = [
            {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
            for item in messages
            if isinstance(item, dict)
        ]
        delivery = ""
        if self.application_policy_role == "system" and self.local_presentation:
            from conversation_style import separate_owned_delivery_policy
            character_prompt, delivery = separate_owned_delivery_policy(character_prompt)
        if delivery and projected and projected[-1]["role"] == "user":
            # The installed template supports system messages. The single
            # application delivery preference follows historical examples but
            # precedes the intact current user turn. No data changes role.
            projected.insert(len(projected) - 1, {"role": "system", "content": delivery})
        elif delivery:
            character_prompt += delivery
        return [{"role": self.application_policy_role, "content": character_prompt}, *projected]

    def _record_response_diagnostics(self, response) -> None:
        usage = getattr(response, "usage", None)
        choices = getattr(response, "choices", ()) or ()
        result = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int) and not isinstance(value, bool):
                result[key] = value
        if choices:
            reason = getattr(choices[0], "finish_reason", None)
            if isinstance(reason, str):
                result["finish_reason"] = reason[:48]
        self.last_response_diagnostics.update(result)

    def new_request_seed(self) -> int | None:
        """Return one explicit seed for a local request, or no override."""
        if not self.fresh_request_seeds:
            return None
        seed = int(self._seed_source())
        if not 0 <= seed <= self._MAX_EXPLICIT_SEED:
            raise ValueError("local request seed is outside llama.cpp's explicit uint32 range")
        return seed

    def request_sampling_metadata(self) -> dict[str, str | int | float]:
        if not self.sampling_options:
            return {}
        return {"sampling_preset": self.sampling_preset, **self.sampling_options}

    def _request(self, messages, character_prompt, *, seed: int | None, stream: bool,
                 max_output_tokens: int | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages, character_prompt),
        }
        extra_body: dict[str, int | float] = {}
        for key, value in self.sampling_options.items():
            if key in self._EXTRA_BODY_SAMPLING_FIELDS:
                extra_body[key] = value
            else:
                request[key] = value
        if extra_body:
            # The OpenAI SDK method does not declare llama.cpp extensions.
            # ``extra_body`` merges these exact keys into the JSON request.
            request["extra_body"] = extra_body
        if stream:
            request["stream"] = True
        if max_output_tokens is not None:
            if isinstance(max_output_tokens, bool) or not 1 <= int(max_output_tokens) <= 256:
                raise ValueError("bounded output tokens must be from 1 to 256")
            request["max_tokens"] = int(max_output_tokens)
        if seed is not None:
            request["seed"] = int(seed)
        return request

    def generate(self, messages, character_prompt, *, seed: int | None = None) -> str:
        self.last_response_diagnostics = {}
        try:
            if seed is None:
                seed = self.new_request_seed()
            request = self._request(messages, character_prompt, seed=seed, stream=False)
            response = self.client.chat.completions.create(
                **request,
            )
        except Exception as error:
            raise ModelTransportError("The configured model is unavailable. Check Settings > Model.") from error
        self._record_response_diagnostics(response)
        return str(response.choices[0].message.content or "")

    def generate_bounded(
        self, messages, character_prompt, *, max_output_tokens: int, seed: int | None = None,
    ) -> str:
        """Generate one small governed proposal with a transport-level cap."""
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) \
                or not 1 <= max_output_tokens <= 256:
            raise ValueError("bounded output tokens must be from 1 to 256")
        self.last_response_diagnostics = {}
        try:
            if seed is None:
                seed = self.new_request_seed()
            request = self._request(
                messages, character_prompt, seed=seed, stream=False,
                max_output_tokens=max_output_tokens,
            )
            response = self.client.chat.completions.create(**request)
        except Exception as error:
            raise ModelTransportError("The configured model is unavailable. Check Settings > Model.") from error
        self._record_response_diagnostics(response)
        return str(response.choices[0].message.content or "")

    def stream_generate(
        self, messages, character_prompt, *, seed: int | None = None,
    ) -> Iterator[str]:
        stream = None
        self.last_response_diagnostics = {}
        try:
            if seed is None:
                seed = self.new_request_seed()
            request = self._request(messages, character_prompt, seed=seed, stream=True)
            stream = self.client.chat.completions.create(
                **request,
            )
            with self._stream_lock:
                self._active_stream = stream
            for event in stream:
                self._record_response_diagnostics(event)
                choices = getattr(event, "choices", ())
                if not choices:
                    continue
                text = getattr(getattr(choices[0], "delta", None), "content", None)
                if text:
                    yield str(text)
        except Exception as error:
            raise ModelTransportError("The configured model is unavailable. Check Settings > Model.") from error
        finally:
            # Closing the provider iterator is the provider-neutral cancellation
            # seam used when a newer product turn replaces this stream.  Both
            # Gemini-compatible and managed-local endpoints share this adapter.
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            with self._stream_lock:
                if self._active_stream is stream:
                    self._active_stream = None

    def cancel_active_generation(self) -> None:
        """Abort the current compatible-provider response, if one exists."""
        with self._stream_lock:
            stream = self._active_stream
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def discover_models(self) -> tuple[str, ...]:
        models = self.client.models.list()
        return tuple(sorted(str(item.id) for item in models.data if getattr(item, "id", None)))
