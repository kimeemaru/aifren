from stt.voice import VoiceInput


print(
    "Creating VoiceInput..."
)

voice = VoiceInput()

print(
    "VoiceInput created."
)

text = voice.listen()

print()
print(
    "FINAL TRANSCRIPTION:"
)

print(
    text
)