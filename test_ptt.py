import time

from stt.voice import VoiceInput
from tts.tts import TextToSpeech
from voice.ptt import PushToTalk


print(
    "Creating voice systems..."
)

voice = VoiceInput()

tts = TextToSpeech()

ptt = PushToTalk(
    voice,
    tts
)

ptt.start()

print()
print(
    "PTT test ready."
)

print(
    "Hold F8 and speak."
)

print(
    "Release F8 when finished."
)

print(
    "The next step will connect the transcription "
    "to the configured TTS provider."
)

try:

    while True:

        time.sleep(0.1)

except KeyboardInterrupt:

    print(
        "\nStopping..."
    )

    ptt.stop()
    tts.stop()
