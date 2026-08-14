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
        public BackendStatus status;
        public VoiceSnapshot voice;
        public TtsSnapshot tts;
        public ModelsSnapshot models;
    }

    [Serializable]
    public sealed class ConversationMessage
    {
        public string role;
        public string content;
        public string timestamp;
    }

    [Serializable]
    public sealed class CharacterIdentity
    {
        public string name;
        public string description;
        public string avatar;
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
    public sealed class TtsSnapshot
    {
        public float volume;
        public string provider;
        public string configured_provider;
        public string voice;
        public string device;
    }

    [Serializable]
    public sealed class ModelsSnapshot { public GeminiModelSnapshot gemini; }

    [Serializable]
    public sealed class GeminiModelSnapshot { public bool configured; public string source; public string model; }

    [Serializable]
    public sealed class BackendEvent
    {
        public string type;
        public BackendEventData data;
    }

    [Serializable]
    public sealed class BackendEventData
    {
        public string role;
        public string content;
        public string state;
        public string message;
        public string user_message;
        public string source;
        public string action;
        public float volume;
        public float duration_seconds;
        public float[] lip_sync_envelope;
        public bool global_listener;
        public string[] lines;
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
