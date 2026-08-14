import json
import os
import threading

from config import CHARACTER_DIR
from llm.llm import create_llm
from memory.memory import Memory
from conversation.conversation import Conversation
from stt.voice import VoiceInput
from tts.tts import TextToSpeech
from ui_sound import UISound
from voice.ptt import PushToTalk

# ============================================================
# Character
# ============================================================


CHARACTER_FILE = os.path.join(
    CHARACTER_DIR,
    "character.json"
)

PERSONALITY_FILE = os.path.join(
    CHARACTER_DIR,
    "personality.md"
)


def load_character():

    try:

        with open(
            CHARACTER_FILE,
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
            PERSONALITY_FILE,
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
"""


# ============================================================
# Startup
# ============================================================

def initialize():

    print(
        "Initializing AI Companion..."
    )

    llm = create_llm()

    memory = Memory(
        llm
    )

    # --------------------------------------------------------
    # Upgrade older memories with embeddings.
    # --------------------------------------------------------

    memory.generate_missing_embeddings()

    # --------------------------------------------------------
    # Upgrade older memories with keyword/concept metadata.
    # --------------------------------------------------------

    memory.generate_missing_metadata()

    conversation = Conversation(
        llm
    )

    # --------------------------------------------------------
    # Local voice input
    # --------------------------------------------------------

    voice = VoiceInput()
    tts = TextToSpeech()

    character, personality = (
        load_character()
    )

    character_prompt = (
        build_character_prompt(
            character,
            personality
        )
    )
    
    ui_sound = None

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
    character_prompt
):

    context = conversation.build_context(
        memory,
        user_message
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
