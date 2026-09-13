"""Read-only management shell when no character can safely bind.

This is not a memory fallback. It opens no conversation, database, microphone or
provider and refuses dialogue. It keeps recovery/create controls reachable.
"""
from types import SimpleNamespace
import uuid

class CharacterUnavailableService:
    storage_unavailable = True
    def __init__(self, character=None, reason="Create or select a character in Settings > Character."):
        self.character_id = character.character_id if character else "no-character"
        self.character = {"name": character.display_name if character else "Create a character",
                          "_character_id": self.character_id}
        self.reason = reason
        self._session = str(uuid.uuid4())
        self.conversation = SimpleNamespace(messages=[])
        self.tts = SimpleNamespace(device="unavailable")
        self.voice = SimpleNamespace()
        self.llm = SimpleNamespace(is_available=False, model="unavailable")
        self._listeners = []
    def character_binding(self): return {"character_id": self.character_id, "character_session": self._session}
    def require_character_binding(self, character_id, character_session):
        if (character_id,character_session) != (self.character_id,self._session):
            raise RuntimeError("This character selection is retired.")
    def subscribe(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None
    def _error(self):
        from aifren.assistant_service import AssistantEvent
        event=AssistantEvent("error", {**self.character_binding(), "code":"character_storage_unavailable",
                                      "message":self.reason,"recoverable":True})
        for callback in tuple(self._listeners): callback(event)
    def process_text_turn(self, text, **kwargs):
        from aifren.assistant_service import TurnResult
        self._error(); return TurnResult(user_message=text,error=self.reason)
    def memory_authority_status(self):
        from aifren.runtime.config import configured_memory_authority
        return {"mode":configured_memory_authority(),"v1_prompt_enabled":False,"v2_ready":False,"storage_unavailable":True}
    def memory_view_page(self, **kwargs): return {"rows":[],"state":"unavailable","error":self.reason}
    def memory_view_detail(self, **kwargs): return {"state":"unavailable","error":self.reason}
    def apply_memory_view_mutation(self, **kwargs): raise RuntimeError(self.reason)
    def apply_continuity_control(self, **kwargs): raise RuntimeError(self.reason)
    def can_reconfigure_model(self): return True
    def replace_llm(self, llm): self.llm=llm
    def prepare_character_switch(self): self._session=str(uuid.uuid4())
    def wait_for_character_switch_idle(self, timeout=30): return True
    def character_switch_busy(self): return False
    def save(self): pass
    def close(self): pass
    def stop_speaking(self, **kwargs): pass
    def push_to_talk_press(self): self._error(); return False
    def push_to_talk_release(self): pass
    def set_push_to_talk_binding(self, binding): pass
    def set_ptt_auto_submit_transcriptions(self, mode): pass
    def set_tts_volume(self, volume): pass
