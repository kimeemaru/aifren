from openai import OpenAI

from config import (
    GEMINI_MODEL
)

from local_settings import get_gemini_api_key


class Gemini:

    def __init__(self):

        api_key, _ = get_gemini_api_key()
        if not api_key:
            raise RuntimeError("Gemini API key is not configured. Add one in Settings > Models.")
        self.client = OpenAI(
            api_key=api_key,
            base_url=(
                "https://generativelanguage.googleapis.com/v1beta/openai/"
            )
        )

    def generate(
        self,
        messages,
        character_prompt
    ):

        request_messages = [
            {
                "role": "user",
                "content": character_prompt
            }
        ]

        request_messages.extend(
            messages
        )

        response = (
            self.client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=request_messages
            )
        )

        return (
            response
            .choices[0]
            .message
            .content
        )
