using System;

namespace AIFren.UnityPoc.Protocol
{
    /// <summary>Immutable live binding identity, not character data or memory.</summary>
    public sealed class CharacterSessionOwner
    {
        public string CharacterId { get; }
        public string Session { get; }
        public long Generation { get; }
        public CharacterSessionOwner(string characterId, string session, long generation)
        { CharacterId = characterId; Session = session; Generation = generation; }
        public bool IsValid => !string.IsNullOrWhiteSpace(CharacterId)
            && !string.IsNullOrWhiteSpace(Session) && Generation >= 0;
        public bool Matches(CharacterSessionOwner other) => other != null
            && Generation == other.Generation && CharacterId == other.CharacterId && Session == other.Session;
    }

    /// <summary>Same-connection session fence, downstream of backend bind authority.</summary>
    internal sealed class CharacterSessionFence
    {
        internal CharacterSessionOwner Current { get; private set; }
        private long latestGeneration = -1;
        internal bool Switching { get; private set; }
        internal string RejectionReason { get; private set; } = "none";
        private bool Reject(string reason) { RejectionReason = reason; return false; }

        internal void Reset() { Current = null; latestGeneration = -1; Switching = false; }
        internal void InvalidateCurrent() { Current = null; }
        internal bool IsCurrent(CharacterSessionOwner owner) => !Switching && Current != null && Current.Matches(owner);

        internal bool Accept(ServerMessage message)
        {
            RejectionReason = "none";
            if (message == null) return Reject("missing_message");
            var incoming = new CharacterSessionOwner(message.character_id, message.character_session, message.character_generation);
            if (message.type == "event" && message.@event?.type == "character_switching")
            {
                if (string.IsNullOrWhiteSpace(incoming.CharacterId) || !string.IsNullOrEmpty(incoming.Session)
                    || incoming.Generation <= latestGeneration) return Reject("switch_generation");
                latestGeneration = incoming.Generation; Current = null; Switching = true; return true;
            }
            if (message.type == "snapshot")
            {
                SnapshotData data = message.data;
                if (!incoming.IsValid || data == null || data.character?.character_id != incoming.CharacterId
                    || data.character_id != incoming.CharacterId || data.character_session != incoming.Session
                    || data.character_generation != incoming.Generation || incoming.Generation < latestGeneration)
                    return Reject("snapshot_identity");
                if (!Switching && Current != null && incoming.Generation == latestGeneration && !Current.Matches(incoming))
                    return Reject("snapshot_binding");
                // A failed attempted switch can settle the new generation back
                // to the original ID. Only the authoritative snapshot binds it.
                latestGeneration = incoming.Generation; Current = incoming; Switching = false; return true;
            }
            if (incoming.IsValid)
            {
                if (Switching || Current == null) return Reject("no_binding");
                if (Current.CharacterId != incoming.CharacterId) return Reject("character");
                if (Current.Generation != incoming.Generation) return Reject("generation");
                if (Current.Session != incoming.Session) return Reject("session");
                return true;
            }
            // Global settings/readiness do not own character content. Character
            // events and scoped command failures require a captured bind stamp.
            return !IsScopedMessage(message) || Reject("missing_owner");
        }

        internal static bool IsScopedCommand(string command)
        {
            switch (command)
            {
                case "submit_text": case "continuity_control": case "memory_view_query":
                case "memory_view_detail": case "memory_view_mutate":
                case "ptt_press": case "ptt_release": case "stop_tts": case "character_voice": return true;
                default: return false;
            }
        }

        private static bool IsScopedMessage(ServerMessage message)
        {
            if (message.type == "character_voice") return true;
            if (message.type == "command_error")
            {
                string code = message.error?.code ?? "";
                // Selection/creation failures own no character content. A
                // failed transition settles only through its fresh snapshot.
                switch (code)
                {
                    case "invalid_character_id": case "invalid_character_name": case "invalid_character_personality":
                    case "character_create_failed": case "character_select_failed": case "unknown_character":
                    case "character_switch_busy": case "character_switch_failed":
                    case "character_operation_failed": case "invalid_character_operation": case "character_folder_failed": return false;
                }
                return code.Contains("character") || code.Contains("continuity") || code.Contains("memory_view")
                    || code.Contains("submit") || code.Contains("ptt") || code.Contains("speech");
            }
            if (message.type != "event") return false;
            switch (message.@event?.type)
            {
                case "turn_started": case "turn_cancelled": case "assistant_delta": case "assistant_response":
                case "conversation_message": case "truth_scope_changed": case "continuity_changed":
                case "continuity_control_result": case "memory_view_page": case "memory_view_detail":
                case "memory_view_mutation_result": case "automatic_expression": case "tts_state":
                case "voice_state": case "voice_transcription": return true;
                default: return false;
            }
        }
    }
}
