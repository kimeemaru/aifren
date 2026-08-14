from abc import ABC, abstractmethod


class LLM(ABC):

    @abstractmethod
    def generate(
        self,
        messages,
        system_prompt
    ):
        """
        Generate a response from the model.

        messages:
            Conversation/context messages.

        system_prompt:
            Character/personality/system instructions.

        Returns:
            String containing the assistant response.
        """

        raise NotImplementedError