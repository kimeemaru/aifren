using AIFren.UnityPoc.Protocol;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>
    /// Resolves frontend-neutral response presentation semantics for the active
    /// avatar. Concrete VRM expression keys and animation assets remain Unity
    /// presentation details and never cross the backend boundary.
    /// </summary>
    public sealed class AvatarPresentationResolver : MonoBehaviour
    {
        private const float DefaultIntensity = .70f;

        private AvatarExpressionController expressions;
        private AvatarAnimationController animation;
        private string handsMode = "free";
        private string locomotionMode = "walking";
        private string postureMode = string.Empty;
        private string speechMode = "normal";
        private string visionMode = "available";
        private string awarenessMode = "normal";

        public string HandsMode => handsMode;
        public string LocomotionMode => locomotionMode;
        public string PostureMode => postureMode;
        public string SpeechMode => speechMode;
        public bool AllowsLipSync => SpeechAllowsLipSync(speechMode) && awarenessMode != "asleep";

        public void Configure()
        {
            expressions = GetComponent<AvatarExpressionController>();
            animation = GetComponent<AvatarAnimationController>();
        }

        public void Apply(PresentationMetadata presentation)
        {
            if (presentation == null) return;
            handsMode = KnownMode(presentation.hands_mode, new[] { "free", "partially_occupied", "occupied" }, handsMode);
            locomotionMode = KnownMode(presentation.locomotion_mode,
                new[] { "walking", "rolling", "skating", "cycling", "driving", "riding", "assisted", "swimming", "other" },
                locomotionMode);
            postureMode = KnownOptionalMode(
                presentation.posture_mode, new[] { "standing", "sitting", "lying" }, postureMode);
            speechMode = KnownMode(presentation.speech_mode,
                new[] { "normal", "constrained", "mumble", "nonverbal", "unavailable" }, speechMode);
            visionMode = KnownMode(presentation.vision_mode, new[] { "available", "obstructed", "unavailable" }, visionMode);
            awarenessMode = KnownMode(presentation.awareness_mode, new[] { "normal", "reduced", "asleep" }, awarenessMode);
            float intensity = presentation.has_intensity ? Mathf.Clamp01(presentation.intensity) : DefaultIntensity;
            bool appliedEmotion = ApplyEmotion(presentation.emotion, intensity);
            bool requestedGesture = TryResolveGesture(presentation.gesture, out AvatarGestureIntent gesture);
            if (requestedGesture && GestureAllowed(gesture, handsMode, awarenessMode)) animation?.PlayGesture(gesture);
            bool explicitSleeping = string.Equals(presentation.pose, "sleeping", System.StringComparison.OrdinalIgnoreCase);
            bool awake = string.Equals(presentation.pose, "awake", System.StringComparison.OrdinalIgnoreCase);
            if (explicitSleeping) awarenessMode = "asleep";
            else if (awake) awarenessMode = "normal";
            bool sleeping = explicitSleeping || awarenessMode == "asleep";
            if (sleeping || awake) animation?.SetSleepingPresentation(sleeping);
            animation?.SetMobilityPresentation(locomotionMode);
            animation?.SetPosturePresentation(postureMode);
            animation?.PlayStateReaction(presentation.reaction, sleeping);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            string emotion = string.IsNullOrWhiteSpace(presentation.emotion) ? "unchanged" : presentation.emotion;
            string gestureName = requestedGesture ? gesture.ToString() : "none";
            Debug.Log("[Presentation] " + emotion + " " + intensity.ToString("0.00") + " / " + gestureName +
                (appliedEmotion ? "." : " (no matching concrete expression)."));
#endif
        }

        private bool ApplyEmotion(string semanticEmotion, float intensity)
        {
            if (string.IsNullOrWhiteSpace(semanticEmotion)) return false;
            if (string.Equals(semanticEmotion, "neutral", System.StringComparison.OrdinalIgnoreCase))
            {
                expressions?.ClearExpression();
                return expressions != null;
            }
            if (!TryResolveEmotion(semanticEmotion, out ExpressionPreset[] candidates)) return false;
            if (expressions == null) return false;
            foreach (ExpressionPreset candidate in candidates)
            {
                if (expressions.TrySetPresetExpression(candidate, intensity)) return true;
            }
            return false;
        }

        public static bool TryResolveEmotion(string semanticEmotion, out ExpressionPreset[] candidates)
        {
            candidates = null;
            if (string.IsNullOrWhiteSpace(semanticEmotion)) return false;
            switch (semanticEmotion.Trim().ToLowerInvariant())
            {
                case "happy": candidates = new[] { ExpressionPreset.happy, ExpressionPreset.relaxed }; return true;
                case "amused": candidates = new[] { ExpressionPreset.relaxed, ExpressionPreset.happy }; return true;
                case "relaxed": candidates = new[] { ExpressionPreset.relaxed }; return true;
                case "sad": candidates = new[] { ExpressionPreset.sad }; return true;
                case "angry": candidates = new[] { ExpressionPreset.angry }; return true;
                case "surprised": candidates = new[] { ExpressionPreset.surprised }; return true;
                default: return false;
            }
        }

        public static bool TryResolveGesture(string semanticGesture, out AvatarGestureIntent intent)
        {
            intent = AvatarGestureIntent.None;
            if (string.IsNullOrWhiteSpace(semanticGesture)) return false;
            switch (semanticGesture.Trim().ToLowerInvariant())
            {
                // Use only stable procedural capabilities exposed by the
                // current semantic resolver.
                case "greeting":
                case "agreement":
                case "encouragement": intent = AvatarGestureIntent.Nod; return true;
                case "disagreement": intent = AvatarGestureIntent.HeadShake; return true;
                case "thinking": intent = AvatarGestureIntent.Thinking; return true;
                case "surprise": intent = AvatarGestureIntent.Shrug; return true;
                default: return false;
            }
        }

        public static bool GestureAllowed(AvatarGestureIntent intent, string hands, string awareness)
        {
            if (string.Equals(awareness, "asleep", System.StringComparison.OrdinalIgnoreCase)) return false;
            if (!string.Equals(hands, "occupied", System.StringComparison.OrdinalIgnoreCase)) return true;
            return intent != AvatarGestureIntent.Wave
                && intent != AvatarGestureIntent.Thinking
                && intent != AvatarGestureIntent.Shrug;
        }

        public static bool SpeechAllowsLipSync(string mode)
        {
            return !string.Equals(mode, "nonverbal", System.StringComparison.OrdinalIgnoreCase)
                && !string.Equals(mode, "unavailable", System.StringComparison.OrdinalIgnoreCase);
        }

        private static string KnownMode(string candidate, string[] allowed, string fallback)
        {
            if (string.IsNullOrWhiteSpace(candidate)) return fallback;
            string normalized = candidate.Trim().ToLowerInvariant();
            foreach (string value in allowed) if (normalized == value) return normalized;
            return fallback;
        }

        private static string KnownOptionalMode(string candidate, string[] allowed, string fallback)
        {
            if (candidate == null) return fallback;
            if (string.IsNullOrWhiteSpace(candidate)) return string.Empty;
            return KnownMode(candidate, allowed, fallback);
        }
    }
}
