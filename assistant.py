import json
import threading

from character_registry import CharacterRegistry
from llm.llm import create_llm
from memory.memory import Memory
from conversation.conversation import Conversation
from stt.voice import VoiceInput
from tts.tts import TextToSpeech
from voice.ptt import PushToTalk
from presentation_metadata import response_contract_prompt

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

def initialize():

    print(
        "Initializing AI Companion..."
    )

    llm = create_llm()

    registry = CharacterRegistry(".")
    active_character = registry.active()
    paths = registry.runtime_paths(active_character.character_id)

    memory = Memory(llm, memory_file=str(paths["memory"]))

    # --------------------------------------------------------
    # Upgrade older memories with embeddings.
    # --------------------------------------------------------

    memory.generate_missing_embeddings()

    # --------------------------------------------------------
    # Upgrade older memories with keyword/concept metadata.
    # --------------------------------------------------------

    memory.generate_missing_metadata()

    conversation = Conversation(
        llm,
        conversation_file=str(paths["conversation"]),
        summary_file=str(paths["summary"]),
    )

    # --------------------------------------------------------
    # Local voice input
    # --------------------------------------------------------

    voice = VoiceInput()
    tts = TextToSpeech()

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
    
    return (
        llm,
        memory,
        conversation,
        voice,
        character,
        character_prompt,
        tts
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

def generate_response(
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
):

    provider_budget = getattr(llm, "context_budget_chars", None)
    if provider_budget is not None:
        try:
            provider_budget = max(1, int(provider_budget) - len(character_prompt))
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
    )

    return llm.generate(
        context,
        character_prompt
    )

import time


# ============================================================
# Typewriter Response
# ============================================================

def typewriter_response(
    text
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
    tts
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
        reply
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
        tts
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
                tts
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
                        tts
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
