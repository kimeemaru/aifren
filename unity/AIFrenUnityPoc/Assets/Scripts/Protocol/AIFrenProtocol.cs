using System;
using UnityEngine;

namespace AIFren.UnityPoc.Protocol
{
    [Serializable]
    public sealed class ClientCommand
    {
        public string command;
        public string text;
        public string mode;
        public string binding;
        public float volume;
        public string api_key;
        public string local_api_key;
        public string provider;
        public string online_model;
        public string online_base_url;
        public string local_endpoint;
        public string local_model;
        public bool local_auto_start;
        public bool has_local_auto_start;
        public bool early_speech;
        public bool proactive_behavior;
        public int proactive_interval_seconds;
        public string character_id;
        public string display_name;
        public string personality;
        public string scenario;
        public string capture_id;
        public string reason;
        public string command_id;
        public string action;
        public string action_token;
        public string expected_revision;
        public int unity_pid;
    }

    [Serializable]
    public sealed class ServerMessage
    {
        public string type;
        public SnapshotData data;
        public BackendEvent @event;
        public CommandError error;
    }

    [Serializable]
    public sealed class SnapshotData
    {
        public int transport_version;
        public ConversationMessage[] conversation;
        public CharacterIdentity character;
        public CharacterSummary[] characters;
        public BackendStatus status;
        public VoiceSnapshot voice;
        public TtsSnapshot tts;
        public CompanionSettingsSnapshot companion;
        public ModelsSnapshot models;
        public TruthScopeSnapshot truth_scope;
        public ContinuitySnapshot continuity;
    }

    [Serializable]
    public sealed class ConversationMessage
    {
        public string message_id;
        public string role;
        public string content;
        public string timestamp;
    }

    [Serializable]
    public sealed class CharacterIdentity
    {
        public string character_id;
        public string name;
        public string description;
        public string avatar;
    }

    [Serializable]
    public sealed class CharacterSummary
    {
        public string character_id;
        public string display_name;
        public bool is_active;
    }

    [Serializable]
    public sealed class BackendStatus
    {
        public string state;
        public string message;
    }

    [Serializable]
    public sealed class VoiceSnapshot { public string state; public bool global_listener; }

    [Serializable]
    public sealed class CompanionSettingsSnapshot
    {
        public bool proactive_behavior;
        public int proactive_interval_seconds;
        public string proactive_eligibility;
        public int proactive_ignored_streak;
        public int proactive_next_opportunity_seconds;
        public PresentationMetadata state_presentation;
    }

    [Serializable]
    public sealed class TruthScopeSnapshot
    {
        public string kind;
        public string label;
    }

    [Serializable]
    public sealed class ContinuitySnapshot
    {
        public TruthScopeSnapshot scope;
        public ContinuityActivity activity;
        public ContinuityActivity companion_activity;
        public ContinuitySceneSubject[] scene_subjects;
        public ContinuitySceneRelation[] scene_relations;
        public ContinuityCapabilityEffect[] capability_effects;
        public ContinuityProfileBaseline[] profile_baseline;
        public ContinuityThread[] open_threads;
        public string revision;
    }

    [Serializable]
    public sealed class ContinuityActivity
    {
        public string value;
        public bool can_clear;
    }

    [Serializable]
    public sealed class ContinuityThread
    {
        public string kind;
        public string description;
        public string action_token;
    }

    [Serializable]
    public sealed class ContinuitySceneSubject
    {
        public string kind;
        public string label;
        public string lifecycle;
        public string scope;
        public string summary;
        public string[] conditions;
        public string confirmed;
        public bool can_remove;
        public string remove_token;
    }

    [Serializable]
    public sealed class ContinuitySceneRelation
    {
        public string target;
        public string facet;
        public string side;
        public string predicate;
        public string cause;
        public string effect;
        public int quantity;
        public string scope;
        public bool can_clear;
        public string clear_token;
    }

    [Serializable]
    public sealed class ContinuityCapabilityEffect
    {
        public string target;
        public string domain;
        public string capability;
        public string state;
        public int severity;
        public int source_count;
        public string cause;
    }

    [Serializable]
    public sealed class ContinuityProfileBaseline
    {
        public string relation;
        public string item;
        public string region;
        public string provenance;
    }

    [Serializable]
    public sealed class TtsSnapshot
    {
        public float volume;
        public string provider;
        public string configured_provider;
        public string voice;
        public string device;
        public string fallback_reason;
        public bool early_speech;
        public bool early_speech_configured;
        public bool early_speech_overridden;
        public bool early_speech_supported;
    }

    [Serializable]
    public sealed class ModelsSnapshot { public ModelSettingsSnapshot current; public LocalModelRuntimeSnapshot local_runtime; }

    [Serializable]
    public sealed class ModelSettingsSnapshot
    {
        public string mode;
        public string provider;
        public string model;
        public string endpoint;
        public bool configured;
        public string availability;
        public string credential_source;
        public string selected_model;
        public bool local_auto_start;
    }

    [Serializable]
    public sealed class LocalModelOption { public string identifier; public string display_name; public string source; }

    [Serializable]
    public sealed class LocalModelRuntimeSnapshot
    {
        public string state;
        public string ownership;
        public string active_model;
        public string selected_model;
        public string compute;
        public string error;
        public int effective_context_tokens;
        public LocalModelOption[] installed_models;
    }

    [Serializable]
    public sealed class BackendEvent
    {
        public string type;
        public BackendEventData data;
    }

    [Serializable]
    public sealed class BackendEventData
    {
        public string message_id;
        public string timestamp;
        public string role;
        public string content;
        public string subtitle_content;
        public string state;
        public string message;
        public string user_message;
        public string source;
        public string action;
        public float volume;
        public float duration_seconds;
        public float[] lip_sync_envelope;
        public float[] word_start_seconds;
        public int playback_id;
        public int chunk_index;
        public int turn_id;
        public string generation_origin;
        public bool proactive;
        public string capture_id;
        public string reason;
        public string scope_kind;
        public string scope_label;
        public string command_id;
        public string outcome;
        public bool accepted;
        public bool duplicate;
        public ContinuitySnapshot continuity;
        public float minimum_available_ram_mb;
        public float swap_in_pages_total;
        public float swap_out_pages_total;
        public float peak_gpu_utilization_percent;
        public float peak_vram_mb;
        public float peak_backend_rss_mb;
        public float peak_unity_rss_mb;
        public float peak_llama_rss_mb;
        public bool streamed;
        public bool interrupted;
        public bool global_listener;
        public string[] lines;
        public LocalModelOption[] models;
        public LocalModelRuntimeSnapshot local_runtime;
        // Optional semantic presentation selected by the backend alongside a
        // response. Older backends simply omit these fields.
        public bool has_presentation;
        public PresentationMetadata presentation;
    }

    [Serializable]
    public sealed class PresentationMetadata
    {
        public string emotion;
        public float intensity;
        public bool has_intensity;
        public string gesture;
        public string pose;
        public string gaze_mode;
        public string reaction;
        public string speech_mode;
        public string vision_mode;
        public string hands_mode;
        public string locomotion_mode;
        public string posture_mode;
        public string awareness_mode;
    }

    [Serializable]
    public sealed class CommandError
    {
        public string code;
        public string message;
    }

    public static class AIFrenProtocol
    {
        public static string SerializeCommand(ClientCommand command)
        {
            return JsonUtility.ToJson(command);
        }

        public static ServerMessage ParseServerMessage(string json)
        {
            return JsonUtility.FromJson<ServerMessage>(json);
        }
    }
}
