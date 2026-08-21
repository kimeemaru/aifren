using System;
using System.Collections.Generic;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>Reusable semantic requests, never model-specific clip names.</summary>
    public enum AvatarGestureIntent
    {
        None,
        Nod,
        HeadShake,
        Wave,
        Shrug,
        HeadTilt,
        Thinking
    }

    public static class AvatarGestureMapper
    {
        public static AvatarGestureIntent FirstSupported(IList<string> emotes)
        {
            return TryFirstSupported(emotes, out AvatarGestureIntent intent, out _) ? intent : AvatarGestureIntent.None;
        }

        public static bool TryFirstSupported(IList<string> emotes, out AvatarGestureIntent intent, out string matchedEmote)
        {
            intent = AvatarGestureIntent.None;
            matchedEmote = null;
            if (emotes == null) return false;
            foreach (string emote in emotes)
            {
                intent = Map(emote);
                if (intent == AvatarGestureIntent.None) continue;
                matchedEmote = emote;
                return true;
            }
            return false;
        }

        public static AvatarGestureIntent Map(string emote)
        {
            string value = (emote ?? string.Empty).Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(value)) return AvatarGestureIntent.None;
            string[] words = value.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
            bool mentionsHead = value.Contains("head");
            foreach (string word in words)
            {
                string verb = NormalizeVerb(word);
                if (IsOneOf(verb, "nod", "nods", "nodded", "nodding")) return AvatarGestureIntent.Nod;
                if (IsOneOf(verb, "shake", "shakes", "shook", "shaking") && mentionsHead) return AvatarGestureIntent.HeadShake;
                if (IsOneOf(verb, "wave", "waves", "waved", "waving")) return AvatarGestureIntent.Wave;
                if (IsOneOf(verb, "shrug", "shrugs", "shrugged", "shrugging")) return AvatarGestureIntent.Shrug;
                if (IsOneOf(verb, "tilt", "tilts", "tilted", "tilting") && mentionsHead) return AvatarGestureIntent.HeadTilt;
                if (IsOneOf(verb, "think", "thinks", "thought", "thinking", "ponder", "ponders", "pondered", "pondering",
                    "consider", "considers", "considered", "considering")) return AvatarGestureIntent.Thinking;
            }
            if (value.Contains("thoughtful")) return AvatarGestureIntent.Thinking;
            return AvatarGestureIntent.None;
        }

        private static string NormalizeVerb(string value) => (value ?? string.Empty).Trim('"', '\'', '.', ',', '!', '?', ';', ':');

        private static bool IsOneOf(string value, params string[] choices)
        {
            foreach (string choice in choices) if (string.Equals(value, choice, StringComparison.Ordinal)) return true;
            return false;
        }
    }
}
