import time
from aifren.character.character_storage_runtime import continuity_write_scope
from aifren.conversation.persistence import ConversationPersistenceError, load_json, save_json
from aifren.runtime.config import RECENT_CONTEXT_MAX_CHARS, RECENT_CONTEXT_MAX_MESSAGES
from aifren.conversation.context_hygiene import ContextHygiene
from aifren.conversation.temporal_context import (
    build_temporal_context_block,
    clock_local_datetime,
    derive_temporal_context_facts,
)
from aifren.conversation.truth_scope import (
    active_scope_from_provenance,
    filter_scope_compatible_history,
)


# ============================================================
# Configuration
# ============================================================

CONVERSATION_FILE = "conversation.json"
SUMMARY_FILE = "conversation_summary.json"

# Compatibility-only policies. The normal V2 governor returns before all of
# these paths, including summary access, fixed recent selection and V1 retrieval.
V1_RECENT_MESSAGES = RECENT_CONTEXT_MAX_MESSAGES
V1_SUMMARY_INTERVAL = 20

V1_MAX_MEMORIES = 5

# Approximate character budgets.
#
# These are intentionally conservative. They are not exact
# token counts, but they prevent context from growing without
# limit.
V1_MAX_SUMMARY_CHARS = 6000
V1_MAX_MEMORY_CHARS = 5000
V1_MAX_RECENT_CHARS = RECENT_CONTEXT_MAX_CHARS

# Number of recent messages that should always be preserved
# before older messages are considered for removal.
V1_MIN_RECENT_MESSAGES = 6


# ============================================================
# Context Manager
# ============================================================

class ContextManager:

    def __init__(self, *, max_recent_chars=None):

        self.max_summary_chars = (
            V1_MAX_SUMMARY_CHARS
        )

        self.max_memory_chars = (
            V1_MAX_MEMORY_CHARS
        )

        self.max_recent_chars = V1_MAX_RECENT_CHARS if max_recent_chars is None else int(max_recent_chars)
        if self.max_recent_chars < 1:
            raise ValueError("recent context character budget must be positive")

    # ========================================================
    # Text Limiting
    # ========================================================

    def limit_text(
        self,
        text,
        maximum
    ):

        if not text:

            return ""

        text = str(
            text
        )

        if len(text) <= maximum:

            return text

        return (
            text[:maximum]
            + "\n[Context truncated]"
        )

    # ========================================================
    # Summary
    # ========================================================

    def build_summary_context(
        self,
        summary
    ):

        if not summary:

            return None

        return {
            "role": "user",
            "content": f"""
LONG-TERM CONVERSATION BACKGROUND:

The following is a compressed summary of
previous conversations.

Use this as background context for continuity.

The summary is NOT necessarily authoritative
when it conflicts with a relevant lifelong
memory.

Do not invent information that is not contained
in the summary.

LONG-TERM SUMMARY:

{summary}

END LONG-TERM SUMMARY.
"""
        }

    # ========================================================
    # Memories
    # ========================================================

    def build_memory_context(
        self,
        memories
    ):

        if not memories:

            return None

        memory_text = ""

        for memory in memories:

            category = memory.get(
                "category",
                "unknown"
            )

            content = memory.get(
                "content",
                ""
            )

            memory_text += (
                f"- [{category}] "
                f"{content}\n"
            )

        return {
            "role": "user",
            "content": f"""
AUTHORITATIVE LIFELONG MEMORIES ABOUT THE USER:

The following information comes from the
user's lifelong memory database.

These memories represent information that has
been intentionally retained because it is
expected to remain useful across conversations.

When answering factual questions about the user,
use these memories as the primary source of truth.

If recent conversation text appears to conflict
with a relevant lifelong memory, do not casually
replace the lifelong memory with the conversation.

A recent conversation may contain:

- temporary statements
- hypothetical statements
- jokes
- misunderstandings
- roleplay
- assistant mistakes
- outdated information

Relevant lifelong memories should therefore be
treated as authoritative unless the user clearly
corrects or changes the information.

Do not invent additional personal information.

Do not assume that an unrelated memory applies
to the current question.

RELEVANT LIFELONG MEMORIES:

{memory_text}

END AUTHORITATIVE LIFELONG MEMORIES.
"""
        }

    # ========================================================
    # Recent Conversation
    # ========================================================

    def build_recent_context(
        self,
        messages
    ):

        if not messages:

            return []

        # Keep a contiguous newest suffix.  Whole messages are preserved, the
        # oldest eligible message is dropped first, and the latest message is
        # retained even when it alone exceeds the approximate character cap.
        # This is intentionally provider-neutral; a future profile can replace
        # the character budget with a tokenizer without changing semantics.
        selected = []
        used_chars = 0
        for message in reversed(messages):
            content = str(message.get("content", "")) if isinstance(message, dict) else ""
            if selected and used_chars + len(content) > self.max_recent_chars:
                break
            selected.append(message)
            used_chars += len(content)
        selected.reverse()
        return selected

    # ========================================================
    # Current Personality Authority
    # ========================================================

    def build_personality_authority_context(
        self
    ):

        return {
            "role": "user",
            "content": (
                "[Current character personality authority]\n\n"

                "The character definition supplied by the "
                "application is the CURRENT and AUTHORITATIVE "
                "personality of the character.\n\n"

                "Previous conversation is historical context. "
                "It may contain behavior, attitudes, opinions, "
                "or characterization from an older version of "
                "the character.\n\n"

                "Use previous conversation to preserve useful "
                "continuity, including facts, relationships, "
                "events, preferences, and ongoing topics.\n\n"

                "Do NOT copy an outdated personality from "
                "previous assistant messages.\n\n"

                "If historical assistant behavior conflicts "
                "with the current character definition, "
                "ALWAYS follow the current character definition.\n\n"

                "A change to the character personality applies "
                "immediately. Do not gradually continue the "
                "old personality simply because it appeared "
                "in earlier messages.\n\n"

                "Do not allow historical dialogue to redefine, "
                "override, weaken, or replace the current "
                "character personality.\n\n"

                "[End current character personality authority]"
            )
        }

    # ========================================================
    # Full Context
    # ========================================================

    def build_context(
        self,
        summary,
        memories,
        recent_messages,
        admitted_truth_scope_context=None,
        admitted_active_state_context=None,
        admitted_open_thread_context=None,
        admitted_durable_context=None,
        admitted_episode_context=None,
        admitted_memory_v2_context=None,
        admitted_recent_conversation_boundary=None,
        temporal_context=None,
        max_context_chars=None,
    ):

        context = []

        # Current-turn time and truth scope establish the read boundary for
        # scoped current context. Neither mutates state or memory.
        if temporal_context:
            context.append({"role": "user", "content": str(temporal_context)})
        if admitted_truth_scope_context:
            context.append({"role": "user", "content": str(admitted_truth_scope_context)})

        # Active State and Open Threads are already scope-filtered, selected,
        # and rendered as typed background data outside this builder.
        if admitted_active_state_context:
            context.append({
                "role": "user",
                "content": str(admitted_active_state_context),
            })
        if admitted_open_thread_context:
            context.append({
                "role": "user",
                "content": str(admitted_open_thread_context),
            })

        # ----------------------------------------------------
        # Priority 1: Relevant V1 / legacy memories
        # ----------------------------------------------------

        memory_context = self.build_memory_context(memories)
        if memory_context:
            context.append(memory_context)

        # Durable context has already passed lookup, relevance, and prompt
        # admission outside this context builder. It remains after active state
        # but before the current/recent user messages.
        if admitted_durable_context:
            context.append({
                "role": "user",
                "content": str(admitted_durable_context),
            })

        # The Development-only V2 authority owner renders a bounded typed
        # long-term block. It stays distinct from current governed facts.
        if admitted_memory_v2_context:
            context.append({
                "role": "user",
                "content": str(admitted_memory_v2_context),
            })

        # The rolling summary describes only older continuity.  It follows
        # authoritative, admitted state/facts and remains before verbatim raw
        # conversation, whose latest user message has the practical final say.
        summary_context = self.build_summary_context(summary)
        if summary_context:
            context.append(summary_context)

        # A validated episode cache is derived, non-authoritative background.
        # It replaces the legacy rolling summary only when it covers a complete
        # canonical prefix; current memories and verbatim dialogue remain the
        # higher-priority sources of truth.
        if admitted_episode_context:
            context.append({
                "role": "user",
                "content": str(admitted_episode_context),
            })

        if admitted_recent_conversation_boundary:
            context.append({
                "role": "user",
                "content": str(admitted_recent_conversation_boundary),
            })

        # ----------------------------------------------------
        # Priority 2: Recent conversation
        # ----------------------------------------------------

        recent_context = (
            self.build_recent_context(
                recent_messages
            )
        )

        # A provider-capacity profile may impose a bounded total context. Keep
        # the authoritative/admitted blocks and latest user turn first; trim
        # only the oldest raw conversation messages. This is generic context
        # assembly, not a local-model memory fork.
        if max_context_chars is not None:
            try:
                budget = max(1, int(max_context_chars))
            except (TypeError, ValueError):
                budget = 0
            fixed_chars = self.calculate_characters(context)
            available_recent_chars = max(0, budget - fixed_chars)
            if available_recent_chars:
                recent_context = ContextManager(max_recent_chars=available_recent_chars).build_recent_context(recent_context)
            elif recent_context:
                recent_context = [recent_context[-1]]

        context.extend(recent_context)

        return context

    # ========================================================
    # Context Statistics
    # ========================================================

    def calculate_characters(
        self,
        context
    ):

        total = 0

        for item in context:

            total += len(
                item.get(
                    "content",
                    ""
                )
            )

        return total

    def print_statistics(
        self,
        context
    ):

        total_chars = (
            self.calculate_characters(
                context
            )
        )

        print(
            "[Context]"
        )

        print(
            f"  Items: {len(context)}"
        )

        print(
            f"  Characters: {total_chars}"
        )


# ============================================================
# Conversation
# ============================================================

class Conversation:

    def __init__(
        self,
        llm,
        conversation_file=None,
        summary_file=None,
        episode_compaction_cache=None,
        episode_compaction_rollover=None,
        clock=None,
        memory_authority="v1",
    ):

        self.llm = llm
        self.conversation_file = conversation_file or CONVERSATION_FILE
        self.summary_file = summary_file or SUMMARY_FILE

        self._load_error = None
        self._memory_authority = None
        self.messages, self._archive_exists = load_json(
            self.conversation_file, [], record_kind="conversation",
        )
        self._persisted_message_count = len(self.messages)
        self.summary_data = {"summary": "", "summarized_messages": 0}
        self.set_memory_authority(memory_authority)

        self.context_manager = (
            ContextManager()
        )
        self.context_hygiene = ContextHygiene()
        self.episode_compaction_cache = episode_compaction_cache
        self.episode_compaction_rollover = episode_compaction_rollover
        self._clock = clock
        self._last_context_hygiene_metrics = {}

    # ========================================================
    # Saving
    # ========================================================

    def prepare_maintenance(self):
        """Verify the persisted archive without replacing an unchanged file.

        A review is not a canonical write. Replacing identical bytes changes
        the observer's file identity and can invalidate the reviewed inventory
        on its next idle pass. Only a never-saved empty archive is materialized.
        External changes block this owner until explicit reload, including its
        later close/save hook; maintenance must not overwrite them.
        """
        with continuity_write_scope(self):
            self._ensure_loaded()
            try:
                messages, exists = load_json(
                    self.conversation_file, [], record_kind="conversation",
                    allow_missing=not self._archive_exists,
                )
                if (exists and self._archive_exists and messages == self.messages
                        and self._persisted_message_count == len(messages)):
                    return False
                if (not exists and not self._archive_exists and not self.messages
                        and self._persisted_message_count == 0):
                    self.save()
                    return True
                raise ConversationPersistenceError(record_kind="conversation", stage="load")
            except ConversationPersistenceError as error:
                self._load_error = error
                raise

    def save(self):
        """Save canonical messages only, reconciling failed pending mutations."""
        with continuity_write_scope(self):
            self._ensure_loaded()
            try:
                save_json(self.conversation_file, self.messages, record_kind="conversation")
            except ConversationPersistenceError as error:
                if error.committed:
                    self._archive_exists = True
                    self._persisted_message_count = len(self.messages)
                else:
                    # Read only on failure; do not keep a second lifetime archive
                    # in RAM. Preserve the list referenced by V2 context owners.
                    try:
                        self._reload_messages()
                    except ConversationPersistenceError:
                        # _reload_messages blocks further use until explicit reload
                        # succeeds, without replacing either disk or memory data.
                        raise self._load_error from error
                raise
            self._archive_exists = True
            self._persisted_message_count = len(self.messages)

    def save_summary(self):
        """The sole summary write boundary; V2 compatibility data is read-only."""
        if self._memory_authority != "v1":
            return
        with continuity_write_scope(self):
            self._ensure_loaded()
            try:
                save_json(self.summary_file, self.summary_data, record_kind="summary")
            except ConversationPersistenceError as error:
                if not error.committed:
                    try:
                        self.summary_data, _ = load_json(
                            self.summary_file, {"summary": "", "summarized_messages": 0},
                            record_kind="summary",
                        )
                    except ConversationPersistenceError as load_error:
                        self._load_error = load_error
                        raise load_error from error
                raise

    def set_memory_authority(self, memory_authority):
        if memory_authority not in {"v1", "v2"}:
            raise ValueError("memory authority must be v1 or v2")
        if memory_authority == "v1" and self._memory_authority != "v1":
            self.summary_data, _ = load_json(
                self.summary_file, {"summary": "", "summarized_messages": 0},
                record_kind="summary",
            )
        self._memory_authority = memory_authority

    def _ensure_loaded(self):
        if self._load_error is not None:
            raise self._load_error

    def is_message_persisted(self, index, message):
        return (0 <= index < self._persisted_message_count
                and index < len(self.messages) and self.messages[index] == message)

    def _reload_messages(self):
        try:
            messages, exists = load_json(
                self.conversation_file, [], record_kind="conversation",
                allow_missing=not self._archive_exists,
            )
        except ConversationPersistenceError as error:
            self._load_error = error
            raise
        self.messages[:] = messages
        self._archive_exists = exists
        self._persisted_message_count = len(messages)

    def start_episode_compaction_rollover(self):
        rollover = getattr(self, "episode_compaction_rollover", None)
        if rollover is None:
            return False
        try:
            return bool(rollover.start_pending(self._semantic_context_messages()))
        except Exception:
            return False

    def close_episode_compaction_rollover(self):
        rollover = getattr(self, "episode_compaction_rollover", None)
        close = getattr(rollover, "close", None)
        if callable(close):
            close()

    # ========================================================
    # Messages
    # ========================================================

    def add_user_message(
        self,
        content,
        *,
        truth_scope=None,
        origin=None,
    ):
        self._ensure_loaded()
        record = {
                "role": "user",
                "content": content,
                "timestamp": (
                    clock_local_datetime(getattr(self, "_clock", None)).isoformat()
                )
            }
        if truth_scope is not None:
            record["truth_scope"] = dict(truth_scope)
        if origin is not None:
            record["origin"] = dict(origin)
        self.messages.append(record)

    def add_assistant_message(
        self,
        content,
        *,
        truth_scope=None,
    ):
        self._ensure_loaded()
        record = {
                "role": "assistant",
                "content": content,
                "timestamp": (
                    clock_local_datetime(getattr(self, "_clock", None)).isoformat()
                )
            }
        if truth_scope is not None:
            record["truth_scope"] = dict(truth_scope)
        self.messages.append(record)

    def get_recent_messages(self):

        count_bounded = self.messages[-V1_RECENT_MESSAGES:]
        return self.context_manager.build_recent_context(count_bounded)

    def _recent_context_start_index(self):
        """Return the first raw message kept verbatim for this generation."""
        return len(self.messages) - len(self.get_recent_messages())

    # ========================================================
    # Memory Retrieval
    # ========================================================

    def get_relevant_memories(
        self,
        memory,
        user_message
    ):

        try:
            started = time.perf_counter()
            memories = memory.get_relevant_memories(
                user_message,
                max_memories=V1_MAX_MEMORIES
            )
            # Shadow-mode only: capture IDs/categories, never text or a new
            # ranking.  This is intentionally inert unless the service enables
            # the private flag for one diagnostic turn.
            if getattr(self, "_capture_v1_retrieval_diagnostics", False):
                self._last_v1_retrieval_latency_ms = (
                    time.perf_counter() - started
                ) * 1000.0
                self._last_v1_retrieval_diagnostics = tuple(
                    {
                        "id": str(item.get("id")),
                        "category": str(item.get("category", "unknown")),
                        "rank": rank,
                    }
                    for rank, item in enumerate(memories, 1)
                    if isinstance(item, dict) and item.get("id") is not None
                )
            return memories

        except Exception as e:

            if getattr(self, "_capture_v1_retrieval_diagnostics", False):
                self._last_v1_retrieval_latency_ms = None

            print(
                f"Warning: Could not retrieve "
                f"relevant memories: {e}"
            )

            return []

    # ========================================================
    # Context Construction
    # ========================================================

    def build_context(
        self,
        memory,
        user_message,
        admitted_truth_scope_context=None,
        admitted_active_state_context=None,
        admitted_open_thread_context=None,
        admitted_durable_context=None,
        admitted_durable_facts=(),
        active_truth_scope=None,
        max_context_chars=None,
        current_user_projection=None,
        long_term_memory_authority="v1",
        admitted_v2_memory_context=None,
        recent_message_limit=None,
        recent_character_limit=None,
        recent_context_policy=None,
        memory_query_decision=None,
        context_provider=None,
        provider_system_prompt="",
        context_max_output_tokens=None,
        context_character_id="",
        optional_context_items=(),
        open_thread_fragments=(),
        temporal_facts=None,
        context_source_refs=None,
        context_check=None,
    ):
        from aifren.context.context_governor import governor_enabled
        if governor_enabled(long_term_memory_authority):
            from aifren.conversation.governed_context import build_governed_context
            return build_governed_context(
                self, user_message, provider=context_provider,
                system_prompt=provider_system_prompt, max_output_tokens=context_max_output_tokens,
                character_id=context_character_id, optional_items=optional_context_items,
                thread_fragments=open_thread_fragments, temporal_facts=temporal_facts,
                admitted_truth_scope_context=admitted_truth_scope_context,
                admitted_active_state_context=admitted_active_state_context,
                admitted_open_thread_context=admitted_open_thread_context,
                admitted_durable_context=admitted_durable_context,
                admitted_v2_memory_context=admitted_v2_memory_context,
                active_truth_scope=active_truth_scope,
                current_user_projection=current_user_projection,
                memory_query_decision=memory_query_decision,
                recent_context_policy=recent_context_policy,
                context_source_refs=context_source_refs,
                context_check=context_check,
            )
        self._last_context_plan = None

        semantic_query = (
            str(current_user_projection)
            if current_user_projection is not None else user_message
        )
        if recent_context_policy is None:
            from aifren.runtime.config import V2_AUTHORITY_RECENT_POLICY
            recent_context_policy = V2_AUTHORITY_RECENT_POLICY

        # ----------------------------------------------------
        # Retrieve memories
        # ----------------------------------------------------

        if long_term_memory_authority not in {"v1", "v2"}:
            raise ValueError("long-term memory authority must be v1 or v2")
        relevant_memories = (
            self.get_relevant_memories(memory, semantic_query)
            if long_term_memory_authority == "v1" else []
        )
        if admitted_durable_facts:
            try:
                from aifren.memory_v2_store.durable_prompt import filter_v1_duplicates
                relevant_memories = filter_v1_duplicates(
                    relevant_memories, tuple(admitted_durable_facts),
                )
            except Exception:
                # V1 remains available if the narrow dedup projection fails.
                pass
        if getattr(self, "_capture_v1_retrieval_diagnostics", False):
            # This is the exact post-admission V1 set that can reach the
            # provider context, not the earlier raw lookup result.
            self._last_v1_prompt_diagnostics = tuple(
                {
                    "id": str(item.get("id")),
                    "category": str(item.get("category", "unknown")),
                    "rank": rank,
                }
                for rank, item in enumerate(relevant_memories, 1)
                if isinstance(item, dict) and item.get("id") is not None
            )

        # ----------------------------------------------------
        # Display retrieved memories
        # ----------------------------------------------------

        if relevant_memories and long_term_memory_authority == "v1":

            print(
                "\n[Relevant memories]"
            )

            for item in relevant_memories:

                print(
                    f"- [{item.get('category', 'unknown')}] "
                    f"{item.get('content', '')}"
                )

            print(
                "[End relevant memories]\n"
            )

        elif long_term_memory_authority == "v1":

            print(
                "\n[No relevant memories found]\n"
            )

        # ----------------------------------------------------
        # Get summary
        # ----------------------------------------------------

        summary = self.summary_data.get("summary", "") if long_term_memory_authority == "v1" else ""
        active_scope = active_scope_from_provenance(active_truth_scope)
        # The legacy rolling summary has no source-level scope provenance.
        # Once the backend supplies an authoritative scope, scoped raw history
        # and validated derived episodes replace it for this transient prompt.
        if active_scope is not None:
            summary = ""

        # ----------------------------------------------------
        # Get recent conversation
        # ----------------------------------------------------

        episode_selection = None
        cache = getattr(self, "episode_compaction_cache", None)
        rollover = getattr(self, "episode_compaction_rollover", None)
        if cache is not None and long_term_memory_authority == "v1":
            try:
                selector = rollover if rollover is not None else cache
                episode_selection = selector.select_for_context(
                    self._semantic_context_messages(),
                    maximum_raw_messages=V1_RECENT_MESSAGES,
                    maximum_raw_characters=self.context_manager.max_recent_chars,
                    active_truth_scope=active_truth_scope,
                )
            except Exception:
                # Derived V2 state is fail-open. Corruption, version drift, or
                # a temporarily busy store restores the established raw path.
                episode_selection = None

        if episode_selection is not None:
            # The validated compacted prefix supersedes the old lossy rolling
            # summary in this transient prompt only. Neither source file is
            # changed, and the newer canonical suffix remains verbatim.
            summary = ""
            raw_recent_messages = list(
                self._semantic_context_messages()[episode_selection.raw_start_index:]
            )
        else:
            raw_recent_messages = list(self._semantic_context_messages()[-V1_RECENT_MESSAGES:])
        if current_user_projection is not None:
            for index in range(len(raw_recent_messages) - 1, -1, -1):
                if raw_recent_messages[index].get("role") == "user":
                    replacement = dict(raw_recent_messages[index])
                    replacement["content"] = semantic_query
                    if isinstance(active_truth_scope, dict):
                        replacement["truth_scope"] = dict(active_truth_scope)
                    raw_recent_messages[index] = replacement
                    break
        raw_recent_messages = filter_scope_compatible_history(
            raw_recent_messages,
            active_scope,
        )
        recent_conversation_boundary = None
        if long_term_memory_authority == "v2":
            from aifren.continuity.memory_v2_source_containment import (
                RECENT_CONVERSATION_BOUNDARY,
                select_recent_messages,
            )
            raw_recent_messages = list(select_recent_messages(
                raw_recent_messages,
                recent_context_policy,
                maximum_messages=(recent_message_limit or 12),
                memory_query_decision=memory_query_decision,
            ))
            recent_conversation_boundary = RECENT_CONVERSATION_BOUNDARY
        if recent_message_limit is not None:
            try:
                bounded_messages = max(1, int(recent_message_limit))
            except (TypeError, ValueError):
                bounded_messages = 1
            raw_recent_messages = raw_recent_messages[-bounded_messages:]
        if recent_character_limit is not None and raw_recent_messages:
            try:
                bounded_characters = max(1, int(recent_character_limit))
            except (TypeError, ValueError):
                bounded_characters = 1
            raw_recent_messages = ContextManager(
                max_recent_chars=bounded_characters,
            ).build_recent_context(raw_recent_messages)
        raw_recent_messages = self.context_manager.build_recent_context(raw_recent_messages)
        hygiene = getattr(self, "context_hygiene", None)
        if hygiene is None:
            hygiene = ContextHygiene()
            self.context_hygiene = hygiene
        hygiene_result = hygiene.filter(raw_recent_messages)
        recent_messages = list(hygiene_result.messages)
        temporal_facts = self.temporal_context_facts(semantic_query, active_truth_scope=active_truth_scope)
        temporal_context = build_temporal_context_block(temporal_facts)

        # ----------------------------------------------------
        # Build managed context
        # ----------------------------------------------------

        context = (
            self.context_manager.build_context(
                summary,
                relevant_memories,
                recent_messages,
                admitted_truth_scope_context=admitted_truth_scope_context,
                admitted_active_state_context=admitted_active_state_context,
                admitted_open_thread_context=admitted_open_thread_context,
                admitted_durable_context=admitted_durable_context,
                admitted_memory_v2_context=admitted_v2_memory_context,
                admitted_recent_conversation_boundary=recent_conversation_boundary,
                admitted_episode_context=(
                    episode_selection.context_block if episode_selection is not None else None
                ),
                temporal_context=temporal_context,
                max_context_chars=max_context_chars,
            )
        )

        admitted_identity = {id(message) for message in recent_messages}
        admitted_recent_count = sum(id(message) in admitted_identity for message in context)
        stats = hygiene_result.stats
        final_context_characters = self.context_manager.calculate_characters(context)
        rollover_metrics = getattr(rollover, "metrics", None)
        self._last_context_hygiene_metrics = {
            "context_hygiene_candidates": stats.candidate_count,
            "context_hygiene_assistant_only_suppressed": stats.assistant_only_suppressed_count,
            "context_hygiene_suppressed": stats.suppressed_count,
            "context_hygiene_user_echo_count": stats.user_echo_count,
            "context_hygiene_self_redundancy_count": stats.self_redundancy_count,
            "context_hygiene_repetitive_run_count": stats.repetitive_run_count,
            "exchange_candidates": stats.exchange_candidate_count,
            "exchange_pairs_suppressed": stats.exchange_pairs_suppressed_count,
            "raw_recent_message_count": stats.raw_recent_message_count,
            "admitted_recent_message_count": admitted_recent_count,
            "recent_context_characters": sum(
                len(str(message.get("content", ""))) for message in recent_messages
            ),
            "recent_context_approximate_tokens": (
                sum(len(str(message.get("content", ""))) for message in recent_messages) + 3
            ) // 4,
            "memory_context_characters": len(str(admitted_v2_memory_context or "")),
            "context_hygiene_removed_characters": stats.removed_characters,
            "context_hygiene_approximate_tokens_removed": (stats.removed_characters + 3) // 4,
            "final_context_characters": final_context_characters,
            "long_term_memory_authority_v2": int(long_term_memory_authority == "v2"),
            "approximate_final_context_tokens": (final_context_characters + 3) // 4,
            "compaction_version": (
                episode_selection.compaction_version if episode_selection is not None else 0
            ),
            "episode_count": (
                episode_selection.total_episode_count if episode_selection is not None else 0
            ),
            "episode_context_count": (
                episode_selection.selected_episode_count if episode_selection is not None else 0
            ),
            "episode_source_record_count": (
                episode_selection.source_record_count if episode_selection is not None else 0
            ),
            "compacted_context_characters": (
                episode_selection.compacted_context_characters if episode_selection is not None else 0
            ),
            "compacted_context_approximate_tokens": (
                (episode_selection.compacted_context_characters + 3) // 4
                if episode_selection is not None else 0
            ),
            "consolidated_episode_count": (
                episode_selection.consolidated_episode_count if episode_selection is not None else 0
            ),
            "consolidated_source_record_count": (
                episode_selection.consolidated_source_record_count
                if episode_selection is not None else 0
            ),
            "lower_level_episodes_replaced": (
                episode_selection.lower_level_episodes_replaced
                if episode_selection is not None else 0
            ),
            "episode_retrieval_version": (
                episode_selection.retrieval_version if episode_selection is not None else 0
            ),
            "episode_retrieval_candidate_count": (
                episode_selection.retrieval_candidate_count if episode_selection is not None else 0
            ),
            "retrieved_episode_count": (
                episode_selection.retrieved_episode_count if episode_selection is not None else 0
            ),
            "retrieved_episode_source_record_count": (
                episode_selection.retrieved_source_record_count
                if episode_selection is not None else 0
            ),
            "retrieved_episode_context_characters": (
                episode_selection.retrieved_context_characters
                if episode_selection is not None else 0
            ),
            "episode_retrieval_query_term_count": (
                episode_selection.retrieval_query_term_count
                if episode_selection is not None else 0
            ),
            "episode_retrieval_signal_code": (
                {
                    "none": 0,
                    "entity": 1,
                    "temporal_entity": 2,
                    "temporal_activity": 3,
                }.get(episode_selection.retrieval_signal, 0)
                if episode_selection is not None else 0
            ),
            "retrieved_episode_source_start_index": (
                episode_selection.retrieved_source_start_index
                if episode_selection is not None else -1
            ),
            "retrieved_episode_source_end_index_exclusive": (
                episode_selection.retrieved_source_end_index_exclusive
                if episode_selection is not None else -1
            ),
            "temporal_retrieval_version": (
                episode_selection.temporal_retrieval_version
                if episode_selection is not None else 0
            ),
            "temporal_query_present": (
                episode_selection.temporal_query_present
                if episode_selection is not None else False
            ),
            "temporal_retrieval_candidate_count": (
                episode_selection.temporal_candidate_count
                if episode_selection is not None else 0
            ),
            "temporal_window_source_record_count": (
                episode_selection.temporal_window_source_record_count
                if episode_selection is not None else 0
            ),
            "temporal_activity_match_count": (
                episode_selection.temporal_activity_match_count
                if episode_selection is not None else 0
            ),
            "temporal_raw_match_count": (
                episode_selection.temporal_raw_match_count
                if episode_selection is not None else 0
            ),
            "episode_retrieval_result_state_code": (
                {
                    "no_match": 0,
                    "recent_context_sufficient": 1,
                    "ambiguous": 2,
                    "too_broad": 3,
                    "single_result": 4,
                    "multi_result": 5,
                    "related_context": 6,
                }.get(episode_selection.retrieval_result_state, 0)
                if episode_selection is not None else 0
            ),
            "temporal_source_span_count": (
                episode_selection.temporal_source_span_count
                if episode_selection is not None else 0
            ),
            "temporal_source_record_count": (
                episode_selection.temporal_source_record_count
                if episode_selection is not None else 0
            ),
            "temporal_distinct_result_count": (
                episode_selection.temporal_distinct_result_count
                if episode_selection is not None else 0
            ),
            "temporal_related_result_count": (
                episode_selection.temporal_related_result_count
                if episode_selection is not None else 0
            ),
            "temporal_source_episode_count": (
                episode_selection.temporal_source_episode_count
                if episode_selection is not None else 0
            ),
            "temporal_result_truncated": (
                episode_selection.temporal_result_truncated
                if episode_selection is not None else False
            ),
            "episode_cache_suffix_message_count": (
                int(getattr(rollover_metrics, "suffix_message_count", 0))
                if rollover_metrics is not None else 0
            ),
            "episode_cache_rollover_trigger_messages": (
                int(getattr(rollover_metrics, "trigger_message_count", 0))
                if rollover_metrics is not None else 0
            ),
            "episode_cache_rollover_hard_limit_messages": (
                int(getattr(rollover_metrics, "hard_limit_message_count", 0))
                if rollover_metrics is not None else 0
            ),
            "episode_cache_rollover_grace_limit_messages": (
                int(getattr(rollover_metrics, "grace_limit_message_count", 0))
                if rollover_metrics is not None else 0
            ),
            "episode_cache_rollover_state": (
                int(getattr(rollover_metrics, "state_code", 0))
                if rollover_metrics is not None else 0
            ),
            "episode_cache_selector_available": (
                bool(getattr(rollover_metrics, "selector_available", False))
                if rollover_metrics is not None else episode_selection is not None
            ),
            "episode_cache_temporary_grace_active": (
                bool(getattr(rollover_metrics, "temporary_grace_active", False))
                if rollover_metrics is not None else False
            ),
            "episode_cache_temporary_fallback": (
                bool(getattr(rollover_metrics, "temporary_fallback", False))
                if rollover_metrics is not None else False
            ),
        }

        return context

    @staticmethod
    def _semantic_context_message(message):
        """Project canonically retained but inaccessible input without revealing it."""
        if not isinstance(message, dict):
            return message
        admission = message.get("semantic_admission")
        if not isinstance(admission, dict) or admission.get("understood") is not False:
            return message
        projected = dict(message)
        channel = str(admission.get("channel") or "unavailable channel")
        projected["content"] = (
            f"[A user message was delivered through unavailable {channel}. "
            "Its semantic content was not available to the companion.]"
        )
        return projected

    def temporal_context_facts(self, user_message, *, active_truth_scope=None):
        return derive_temporal_context_facts(
            self.messages, user_message, clock=getattr(self, "_clock", None),
            reply_is_human_owned=getattr(self, "_temporal_reply_is_human_owned", None),
            active_truth_scope=active_truth_scope,
        )

    def _semantic_context_messages(self):
        return [self._semantic_context_message(message) for message in self.messages]

    # ========================================================
    # Summary
    # ========================================================

    def update_summary(self):
        if self._memory_authority != "v1":
            return
        self._ensure_loaded()

        summarized_count = (
            self.summary_data.get(
                "summarized_messages",
                0
            )
        )

        # Protect against corrupted or outdated
        # summary indexes.
        if summarized_count < 0:

            summarized_count = 0

        if summarized_count > len(
            self.messages
        ):

            summarized_count = 0

        # Keep the newest messages outside
        # the summary.
        # Future summary updates stop exactly at the fresh raw-context suffix,
        # including character-budget trimming.  This avoids newly summarizing
        # text that is still rendered verbatim.  Older summary files cannot be
        # safely de-duplicated without regenerating their derived content.
        summary_end = self._recent_context_start_index()

        if summary_end <= summarized_count:

            return

        new_messages = self._semantic_context_messages()[
            summarized_count:
            summary_end
        ]

        if len(new_messages) < V1_SUMMARY_INTERVAL:

            return

        conversation_text = ""

        for message in new_messages:

            role = message.get(
                "role",
                "unknown"
            )

            content = message.get(
                "content",
                ""
            )

            conversation_text += (
                f"{role.upper()}: "
                f"{content}\n"
            )

        previous_summary = (
            self.summary_data.get(
                "summary",
                ""
            )
        )

        previous_summary = (
            self.context_manager.limit_text(
                previous_summary,
                V1_MAX_SUMMARY_CHARS
            )
        )

        prompt = f"""
You maintain the long-term conversation
summary for an AI companion.

Your job is to preserve useful information
from older conversation so the companion can
continue behaving consistently over time.

Previous summary:

{previous_summary}

New conversation:

{conversation_text}

Create an updated compact summary.

Preserve:

- important facts about the user
- stable preferences
- interests
- important events
- ongoing projects
- decisions
- unresolved topics
- important changes to previous information

Prioritize information that is likely to
remain useful in future conversations.

Do not include trivial small talk.

Do not invent information.

Do not assume information that was not stated.

If newer information contradicts older
information, prefer the newer information.

Write plain text only.
"""

        try:

            response = self.llm.generate(
                [],
                prompt
            )

        except Exception as e:

            print(
                f"\nWarning: Summary generation "
                f"failed: {e}"
            )

            return

        if response is None:

            return

        response = str(
            response
        ).strip()

        if not response:

            return

        self.summary_data = {
            "summary": response,
            "summarized_messages": (
                summary_end
            )
        }

        self.save_summary()

        print(
            "\nConversation summary updated."
        )

    # ========================================================
    # Conversation Turn
    # ========================================================

    def respond(
        self,
        memory,
        character_prompt,
        user_message
    ):

        user_message = str(
            user_message
        ).strip()

        if not user_message:

            return ""

        # ----------------------------------------------------
        # Add current user message
        # ----------------------------------------------------

        self.add_user_message(
            user_message
        )

        # ----------------------------------------------------
        # Build context
        # ----------------------------------------------------

        context = self.build_context(
            memory,
            user_message
        )

        # ----------------------------------------------------
        # Generate response
        # ----------------------------------------------------

        try:

            reply = self.llm.generate(
                context,
                character_prompt
            )

        except Exception:

            # Remove the user message we just
            # added if generation failed.
            if self.messages:

                last_message = (
                    self.messages[-1]
                )

                if (
                    last_message.get("role")
                    == "user"
                    and
                    last_message.get("content")
                    == user_message
                ):

                    self.messages.pop()

            raise

        if reply is None:

            reply = ""

        reply = str(
            reply
        ).strip()

        # ----------------------------------------------------
        # Store assistant response
        # ----------------------------------------------------

        self.add_assistant_message(
            reply
        )

        # ----------------------------------------------------
        # Save conversation
        # ----------------------------------------------------

        self.save()

        # Derived compaction is scheduled during context construction and may
        # start only after the canonical pair has reached its save point.
        # This returns immediately; provider work and atomic publication stay
        # off the live turn path.
        self.start_episode_compaction_rollover()

        # ----------------------------------------------------
        # Process lifelong memory
        # ----------------------------------------------------

        try:

            memory.process(
                user_message,
                reply
            )

        except Exception as e:

            print(
                f"\nWarning: Memory processing "
                f"failed: {e}"
            )

        # ----------------------------------------------------
        # Update summary if necessary
        # ----------------------------------------------------

        self.update_summary()

        # ----------------------------------------------------
        # Final save
        # ----------------------------------------------------

        self.save()

        return reply

    # ========================================================
    # Conversation Management
    # ========================================================

    def clear_conversation(
        self,
        keep_summary=True
    ):

        self._ensure_loaded()
        self.messages.clear()
        self.save()

        if not keep_summary:

            self.summary_data = {
                "summary": "",
                "summarized_messages": 0
            }

            self.save_summary()

    def reload(self):
        try:
            messages, exists = load_json(
                self.conversation_file, [], record_kind="conversation",
                allow_missing=not self._archive_exists,
            )
            summary = self.summary_data
            if self._memory_authority == "v1":
                summary, _ = load_json(
                    self.summary_file, {"summary": "", "summarized_messages": 0},
                    record_kind="summary",
                )
        except ConversationPersistenceError as error:
            self._load_error = error
            raise
        self.messages[:] = messages
        self._archive_exists = exists
        self._persisted_message_count = len(messages)
        self.summary_data = summary
        self._load_error = None

    # ========================================================
    # Status
    # ========================================================

    def status(self):

        summary = (
            self.summary_data.get(
                "summary",
                ""
            )
        )

        summarized_count = (
            self.summary_data.get(
                "summarized_messages",
                0
            )
        )

        print(
            "\nConversation status:"
        )

        print(
            f"  Total messages: "
            f"{len(self.messages)}"
        )

        print(
            f"  Recent message limit: "
            f"{V1_RECENT_MESSAGES}"
        )

        print(
            f"  Memory limit: "
            f"{V1_MAX_MEMORIES}"
        )

        print(
            f"  Summary interval: "
            f"{V1_SUMMARY_INTERVAL}"
        )

        print(
            f"  Summarized messages: "
            f"{summarized_count}"
        )

        print(
            f"  Summary available: "
            f"{bool(summary.strip())}"
        )

        print(
            f"  Summary characters: "
            f"{len(summary)}"
        )

        print()
