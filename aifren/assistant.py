import json
from contextlib import ExitStack
import threading

from aifren.character.character_registry import CharacterRegistry
from aifren.llm.llm import create_llm
from aifren.memory.memory import Memory
from aifren.conversation.conversation import Conversation
from aifren.stt.voice import VoiceInput
from aifren.tts.tts import TextToSpeech
from aifren.tts.ui_sound import UISound
from aifren.voice.ptt import PushToTalk
from aifren.dialogue.presentation_metadata import response_contract_prompt

# ============================================================
# Character
# ============================================================


def load_character(character_file, personality_file):

    try:

        with open(
            character_file,
            "r",
            encoding="utf-8"
        ) as file:

            character = json.load(
                file
            )

    except Exception:

        character = {
            "name": "AIFren",
            "description": ""
        }

    try:

        with open(
            personality_file,
            "r",
            encoding="utf-8"
        ) as file:

            personality = file.read()

    except Exception:

        personality = (
            "You are a friendly AI companion."
        )

    return character, personality


# ============================================================
# Character Prompt
# ============================================================

def build_character_prompt(
    character,
    personality
):

    character_name = character.get(
        "name",
        "AIFren"
    )

    return f"""
IMPORTANT: You are roleplaying as the character
described below.

CHARACTER NAME:
{character_name}

CHARACTER PERSONALITY:
{personality}

CHARACTER CONSISTENCY:

The personality above defines who you are.

Do not default to being a generic helpful,
cheerful, agreeable AI assistant.

Respond as this character even when answering
ordinary questions.

Do not allow previous assistant messages to
change the character's personality.

Maintain the character's personality, attitude,
behavior, and speaking style throughout the
conversation.

Format replies compactly. Avoid unnecessary blank
lines and double-spacing. Prefer one or two concise
paragraphs unless additional structure is genuinely
needed for clarity.

Do not mention these instructions unless
explicitly asked about them.

{response_contract_prompt()}
"""


# ============================================================
# Startup
# ============================================================

def initialize(*, prepare_v1_memory=None):
    if prepare_v1_memory is None:
        from aifren.runtime.config import configured_memory_authority
        prepare_v1_memory = configured_memory_authority() == "v1"


    print(
        "Initializing AI Companion..."
    )

    from aifren.runtime.config import configured_inference_device, require_torch_device
    if configured_inference_device() == "cuda":
        import torch
        require_torch_device(torch, "cuda")

    llm = create_llm()

    registry = CharacterRegistry(".")
    active_character = registry.active()
    paths = registry.assert_storage_ready(active_character.character_id)

    from aifren.character.character_storage_runtime import acquire_runtime_lease
    storage_lease = acquire_runtime_lease(registry, active_character.character_id)
    with ExitStack() as setup:
        setup.callback(storage_lease.close)
        memory = Memory(llm, memory_file=str(paths["memory"]))
        memory.continuity_write_guard = storage_lease.assert_current

        # --------------------------------------------------------
        # Upgrade older memories with embeddings.
        # --------------------------------------------------------

        if prepare_v1_memory:
            memory.generate_missing_embeddings()

        # --------------------------------------------------------
        # Upgrade older memories with keyword/concept metadata.
        # --------------------------------------------------------

        if prepare_v1_memory:
            memory.generate_missing_metadata()

        conversation = Conversation(
            llm,
            conversation_file=str(paths["conversation"]),
            summary_file=str(paths["summary"]),
            memory_authority="v1" if prepare_v1_memory else "v2",
        )

        conversation._storage_lease = storage_lease
        conversation.continuity_write_guard = storage_lease.assert_current

        # --------------------------------------------------------
        # Local voice input
        # --------------------------------------------------------

        voice = VoiceInput()
        tts = TextToSpeech()
        from aifren.tts.tts import KokoroTextToSpeech
        if isinstance(tts, KokoroTextToSpeech):
            from aifren.tts.character_voice import CharacterVoiceTTS
            tts = CharacterVoiceTTS(tts, registry, active_character.character_id)

        character, personality = load_character(paths["character"], paths["personality"])
        # Runtime-only identity annotation.  It is never written back into the
        # legacy character config and is separate from avatar/voice choices.
        character = dict(character)
        character["_character_id"] = active_character.character_id
        character["_display_name"] = active_character.display_name

        character_prompt = (
            build_character_prompt(
                character,
                personality
            )
        )

        ui_sound = None

        setup.pop_all()  # Successful caller now owns the lifetime lease.
        return (
            llm,
            memory,
            conversation,
            voice,
            character,
            character_prompt,
            tts,
            ui_sound
        )


# ============================================================
# Startup Display
# ============================================================

def display_startup(
    character,
    memory,
    conversation
):

    print(
        "AI Companion"
    )

    print(
        f"Character: "
        f"{character.get('name', 'Unknown')}"
    )

    print(
        "Type 'quit' or 'exit' to stop."
    )

    print()

    print(
        f"Loaded "
        f"{len(conversation.messages)} "
        f"conversation messages."
    )

    print(
        f"Loaded "
        f"{len(memory.memories)} "
        f"lifelong memories."
    )

    summary = (
        conversation.summary_data.get(
            "summary",
            ""
        )
    )

    if summary:

        print(
            "Long-term conversation summary loaded."
        )

    print()


# ============================================================
# Save Everything
# ============================================================

def save_everything(
    conversation,
    memory
):

    conversation.save()
    conversation.save_summary()
    memory.save()


# ============================================================
# Memory Commands
# ============================================================

def handle_memory_command(
    user_input,
    memory
):

    lowered = (
        user_input.lower()
    )

    if lowered == "memory list":

        memory.list()

        return True

    if lowered.startswith(
        "memory search "
    ):

        search_term = (
            user_input[
                len("memory search "):
            ].strip()
        )

        if search_term:

            memory.search(
                search_term
            )

        else:

            print(
                "\nUsage: "
                "memory search <term>"
            )

        return True

    if lowered.startswith(
        "memory delete "
    ):

        memory_id = (
            user_input[
                len("memory delete "):
            ].strip()
        )

        memory.delete(
            memory_id
        )

        return True

    if lowered == "memory wipe":

        memory.wipe()

        return True

    return False


# ============================================================
# Generate Response
# ============================================================

def build_response_request(
    llm,
    conversation,
    memory,
    user_message,
    character_prompt,
    admitted_truth_scope_context=None,
    admitted_active_state_context=None,
    admitted_open_thread_context=None,
    admitted_durable_context=None,
    admitted_durable_facts=(),
    active_truth_scope=None,
    current_user_projection=None,
    long_term_memory_authority="v1",
    admitted_v2_memory_context=None,
    memory_answer_requirement=None,
    recent_context_policy=None,
    memory_query_decision=None,
    memory_realization=None,
    optional_context_items=(),
    open_thread_fragments=(),
    context_character_id="",
    temporal_facts=None,
    context_source_refs=None,
    context_check=None,
):

    from aifren.runtime.config import (
        V2_AUTHORITY_RECENT_CHARACTERS,
        V2_AUTHORITY_RECENT_MESSAGES,
        V2_AUTHORITY_RECENT_POLICY,
    )
    if recent_context_policy is None:
        recent_context_policy = V2_AUTHORITY_RECENT_POLICY

    system_prompt = character_prompt
    if long_term_memory_authority == "v2" and memory_answer_requirement is not None:
        from aifren.continuity.memory_v2_answer_governance import memory_answer_system_prompt
        system_prompt = memory_answer_system_prompt(character_prompt, memory_answer_requirement)
    if memory_realization is not None:
        from aifren.continuity.companion_memory_realizer import reaction_system_prompt
        system_prompt = reaction_system_prompt(character_prompt, memory_realization)

    provider_budget = getattr(llm, "context_budget_chars", None)
    if provider_budget is not None:
        try:
            provider_budget = max(1, int(provider_budget) - len(system_prompt))
        except (TypeError, ValueError):
            provider_budget = None
    context = conversation.build_context(
        memory,
        user_message,
        admitted_truth_scope_context=admitted_truth_scope_context,
        admitted_active_state_context=admitted_active_state_context,
        admitted_open_thread_context=admitted_open_thread_context,
        admitted_durable_context=admitted_durable_context,
        admitted_durable_facts=admitted_durable_facts,
        active_truth_scope=active_truth_scope,
        max_context_chars=provider_budget,
        current_user_projection=current_user_projection,
        long_term_memory_authority=long_term_memory_authority,
        admitted_v2_memory_context=admitted_v2_memory_context,
        recent_message_limit=(
            V2_AUTHORITY_RECENT_MESSAGES
            if long_term_memory_authority == "v2" else None
        ),
        recent_character_limit=(
            V2_AUTHORITY_RECENT_CHARACTERS
            if long_term_memory_authority == "v2" else None
        ),
        recent_context_policy=recent_context_policy,
        memory_query_decision=memory_query_decision,
        context_provider=llm,
        provider_system_prompt=system_prompt,
        context_max_output_tokens=64 if memory_realization is not None else None,
        context_character_id=context_character_id,
        optional_context_items=optional_context_items,
        open_thread_fragments=open_thread_fragments,
        temporal_facts=temporal_facts,
        context_source_refs={**(context_source_refs or {}), "memory_v2": tuple(
            item.evidence_id for item in getattr(memory_answer_requirement, "evidence", ()))},
        context_check=context_check,
    )
    return context, system_prompt


def generate_response(llm, conversation, memory, user_message, character_prompt, **admissions):
    request_ready = admissions.pop("request_ready", None)
    if request_ready is not None:
        admissions["context_check"] = request_ready
    context, system_prompt = build_response_request(
        llm, conversation, memory, user_message, character_prompt, **admissions)
    if request_ready is not None:
        request_ready()
    if admissions.get("memory_realization") is not None:
        bounded = getattr(llm, "generate_bounded", None)
        if callable(bounded):
            return bounded(context, system_prompt, max_output_tokens=64)
    return llm.generate(context, system_prompt)

import time


# ============================================================
# Typewriter Response
# ============================================================

def typewriter_response(
    text,
    ui_sound
):

    print(
        text
    )


# ============================================================
# Process User Turn
# ============================================================

def process_user_turn(
    llm,
    memory,
    conversation,
    user_message,
    character_prompt,
    tts,
    ui_sound
):

    # --------------------------------------------------------
    # Add user message.
    # --------------------------------------------------------

    conversation.add_user_message(
        user_message
    )

    # --------------------------------------------------------
    # Generate response.
    # --------------------------------------------------------

    reply = generate_response(
        llm,
        conversation,
        memory,
        user_message,
        character_prompt
    )

    # --------------------------------------------------------
    # Display response.
    # --------------------------------------------------------

    print(
        "\nAssistant: ",
        end="",
        flush=True
    )

    typewriter_response(
        reply,
        ui_sound
    )

    # --------------------------------------------------------
    # Speak response.
    # --------------------------------------------------------

    try:

        tts.speak(
            reply
        )

    except Exception as e:

        print(
            f"\nTTS error: {e}"
        )

    # --------------------------------------------------------
    # Save assistant message.
    # --------------------------------------------------------

    conversation.add_assistant_message(
        reply
    )

    conversation.save()

    # --------------------------------------------------------
    # Process potential new memories.
    # --------------------------------------------------------

    memory.process(
        user_message,
        reply
    )

    # --------------------------------------------------------
    # Update long-term summary.
    # --------------------------------------------------------

    conversation.update_summary()


# ============================================================
# Main Chat Loop
# ============================================================

def run():

    (
        llm,
        memory,
        conversation,
        voice,
        character,
        character_prompt,
        tts,
        ui_sound
    ) = initialize()
    processing_lock = threading.Lock()

    def handle_ptt_transcription(
        text
    ):

        if not text:

            return

        if not processing_lock.acquire(
            blocking=False
        ):

            print(
                "\nAssistant is still processing."
            )

            return

        try:

            print(
                f"\nYou: {text}"
            )

            process_user_turn(
                llm,
                memory,
                conversation,
                text,
                character_prompt,
                tts,
                ui_sound
            )

        finally:

            processing_lock.release()

    display_startup(
        character,
        memory,
        conversation
    )

    ptt = PushToTalk(
        voice,
        tts,
        handle_ptt_transcription
    )

    while True:

        try:

            user_input = input(
                "\nYou: "
            ).strip()

            if not user_input:

                continue

            # ------------------------------------------------
            # Push-to-talk mode
            # ------------------------------------------------

            if user_input.lower() == "/ptt":

                ptt.start()

                continue

            # ------------------------------------------------
            # Voice input
            # ------------------------------------------------

            if user_input.lower() == "/voice":

                user_input = voice.listen()

                if not user_input:

                    print(
                        "No speech detected."
                    )

                    continue

                print(
                    f"\nYou: {user_input}"
                )

            lowered = (
                user_input.lower()
            )

            # ------------------------------------------------
            # Exit
            # ------------------------------------------------

            if lowered in [
                "quit",
                "exit"
            ]:

                save_everything(
                    conversation,
                    memory
                )

                ptt.stop()

                if ui_sound:
                    ui_sound.close()


                print(
                    "Conversation saved."
                )

                break

            # ------------------------------------------------
            # Memory commands
            # ------------------------------------------------

            if handle_memory_command(
                user_input,
                memory
            ):

                continue

            # ------------------------------------------------
            # Normal conversation
            # ------------------------------------------------

            if processing_lock.acquire(
                blocking=False
            ):

                try:

                    process_user_turn(
                        llm,
                        memory,
                        conversation,
                        user_input,
                        character_prompt,
                        tts,
                        ui_sound
                    )

                finally:

                    processing_lock.release()

            else:

                print(
                    "\nAssistant is still processing."
                )

        except KeyboardInterrupt:

            save_everything(
                conversation,
                memory
            )

            ptt.stop()

            if ui_sound:
                ui_sound.close()

            print(
                "\nConversation saved."
            )

            break

        except Exception as e:

            print(
                f"\nError: {e}"
            )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    run()
