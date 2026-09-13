using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using System.Collections.Generic;
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
        private AvatarGazeController gaze;
        private string handsMode = "free";
        private string locomotionMode = "walking";
        private string postureMode = string.Empty;
        private string speechMode = "normal";
        private string visionMode = "available";
        private string awarenessMode = "normal";
        private AutomaticExpressionLease automaticResponseLease;
        private bool automaticAdmissionOpen;

        public string HandsMode => handsMode;
        public string LocomotionMode => locomotionMode;
        public string PostureMode => postureMode;
        public string SpeechMode => speechMode;
        public bool AllowsLipSync => SpeechAllowsLipSync(speechMode) && awarenessMode != "asleep";
        public string LastFaceOrigin { get; private set; } = "no_change";
        public string LastFaceRequest { get; private set; } = "none";
        public bool LastFaceApplied { get; private set; }
        public string LastFaceFallbackReason { get; private set; } = "no_action";

        public void Configure()
        {
            expressions = GetComponent<AvatarExpressionController>();
            animation = GetComponent<AvatarAnimationController>();
            gaze = GetComponent<AvatarGazeController>();
        }

        public void Apply(PresentationMetadata presentation)
        {
            ApplyReply(presentation, AvatarGestureIntent.None);
            // Authoritative snapshots also use this resolver (including a
            // character's neutral reset). They are not model-selected metadata.
            if (LastFaceOrigin == "model_metadata") LastFaceOrigin = "state_update";
        }

        public void ApplyReply(PresentationMetadata presentation, AvatarGestureIntent fallback)
            => ApplyReply(presentation, fallback, null);

        internal void ApplyDialogueReply(PresentationMetadata presentation, DialogueDocument dialogue, string selfName,
            bool reserveAutomaticFace = false)
        {
            var emotes = new List<string>();
            foreach (DialogueSpan span in dialogue.Spans)
                if (span.Kind == DialogueSpanKind.Emote) emotes.Add(span.Text);
            AvatarGestureMapper.TryFirstSupported(emotes, out AvatarGestureIntent body, out _);
            string face = FacialEmoteProjection.Select(dialogue, selfName, out string reason);
            if (reserveAutomaticFace) { face = null; reason = "automatic_pending"; }
            LastFaceFallbackReason = reason;
            ApplyReply(presentation, body, face);
        }

        internal AutomaticExpressionLease BeginAutomaticExpressionLease()
        {
            RetireAutomaticExpression(automaticResponseLease);
            automaticAdmissionOpen = true;
            return automaticResponseLease = new AutomaticExpressionLease();
        }

        internal bool RetireAutomaticExpression(AutomaticExpressionLease lease)
        {
            if (lease == null) return false;
            if (ReferenceEquals(automaticResponseLease, lease))
            { automaticResponseLease = null; automaticAdmissionOpen = false; }
            bool retired = expressions != null && expressions.RetireAutomaticExpression(lease);
            if (retired && LastFaceOrigin == "automatic")
            {
                LastFaceOrigin = "automatic_retired";
                LastFaceRequest = "none"; LastFaceApplied = false;
            }
            return retired;
        }

        internal bool FinishAutomaticExpression(AutomaticExpressionLease lease)
        {
            if (!automaticAdmissionOpen || lease == null || !ReferenceEquals(automaticResponseLease, lease)) return false;
            // Keep ownership through the dwell so a replacement or restriction
            // can retire the layer immediately, while closing late admission.
            automaticAdmissionOpen = false;
            return expressions != null && expressions.FinishAutomaticExpression(lease, Time.unscaledTime);
        }

        internal bool ApplyAutomaticExpression(PresentationMetadata presentation, AutomaticExpressionLease lease)
        {
            // A late optional result owns only the semantic face. Never reapply
            // capabilities, pose, gaze, speech, gestures or scene reactions.
            if (!automaticAdmissionOpen || lease == null || !ReferenceEquals(automaticResponseLease, lease)
                || presentation == null || presentation.origin != "automatic"
                || awarenessMode == "asleep" || expressions == null || expressions.ManualOverride)
                return false;
            if (!TryResolveEmotion(presentation.emotion, out _)
                && !string.Equals(presentation.emotion, "neutral", System.StringComparison.OrdinalIgnoreCase))
                return false;
            if (presentation.has_intensity && (float.IsNaN(presentation.intensity)
                || float.IsInfinity(presentation.intensity) || presentation.intensity < 0f || presentation.intensity > 1f))
                return false;
            LastFaceOrigin = "automatic";
            LastFaceRequest = presentation.emotion.ToLowerInvariant();
            float intensity = presentation.has_intensity ? presentation.intensity : DefaultIntensity;
            if (string.Equals(presentation.emotion, "neutral", System.StringComparison.OrdinalIgnoreCase))
                LastFaceApplied = expressions.TrySetAutomaticNeutral(lease, Time.unscaledTime);
            else
            {
                LastFaceApplied = false;
                TryResolveEmotion(presentation.emotion, out ExpressionPreset[] candidates);
                foreach (ExpressionPreset candidate in candidates)
                    if (expressions.TrySetAutomaticPresetExpression(candidate, intensity, lease, Time.unscaledTime))
                    { LastFaceApplied = true; break; }
            }
            return LastFaceApplied;
        }

        private void ApplyReply(PresentationMetadata presentation, AvatarGestureIntent fallback, string facialFallback)
        {
            presentation = presentation ?? new PresentationMetadata();
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
            float intensity = presentation.has_intensity && !float.IsNaN(presentation.intensity)
                && !float.IsInfinity(presentation.intensity) ? Mathf.Clamp01(presentation.intensity) : DefaultIntensity;
            // An explicit request owns this channel even when the avatar cannot
            // supply its preset. Unrelated presentation fields do not mask emotes.
            bool explicitEmotion = !string.IsNullOrWhiteSpace(presentation.emotion);
            string face = explicitEmotion ? presentation.emotion : facialFallback;
            if (explicitEmotion || facialFallback != null || awarenessMode == "asleep"
                || string.Equals(presentation.pose, "sleeping", System.StringComparison.OrdinalIgnoreCase))
                RetireAutomaticExpression(automaticResponseLease);
            LastFaceOrigin = explicitEmotion ? "model_metadata" : facialFallback != null ? "explicit_emote" : "no_change";
            if (explicitEmotion && presentation.origin == "act") LastFaceOrigin = "act";
            LastFaceRequest = string.Equals(face, "neutral", System.StringComparison.OrdinalIgnoreCase) ? "neutral" :
                TryResolveEmotion(face, out _) ? face.ToLowerInvariant() : "none";
            bool appliedEmotion = ApplyEmotion(face, explicitEmotion ? intensity : DefaultIntensity);
            LastFaceApplied = appliedEmotion;
            bool requestedGesture = TryResolveGesture(presentation.gesture, out AvatarGestureIntent gesture);
            bool explicitSleeping = string.Equals(presentation.pose, "sleeping", System.StringComparison.OrdinalIgnoreCase);
            bool awake = string.Equals(presentation.pose, "awake", System.StringComparison.OrdinalIgnoreCase);
            if (explicitSleeping) awarenessMode = "asleep";
            else if (awake && string.IsNullOrWhiteSpace(presentation.awareness_mode)) awarenessMode = "normal";
            bool sleeping = explicitSleeping || awarenessMode == "asleep";
            if (sleeping || awake) animation?.SetSleepingPresentation(sleeping);
            if (visionMode != "available" || sleeping
                || string.Equals(presentation.gaze_mode, "suppressed", System.StringComparison.OrdinalIgnoreCase))
                gaze?.SetPresentationSuppressed(true);
            else if (string.Equals(presentation.gaze_mode, "normal", System.StringComparison.OrdinalIgnoreCase)
                || string.Equals(presentation.vision_mode, "available", System.StringComparison.OrdinalIgnoreCase)
                || string.Equals(presentation.awareness_mode, "normal", System.StringComparison.OrdinalIgnoreCase))
                gaze?.SetPresentationSuppressed(false);
            animation?.SetMobilityPresentation(locomotionMode);
            animation?.SetPosturePresentation(postureMode);
            // Capabilities and pose are active before a single optional body
            // request. A rejected metadata request cannot route around its guard
            // via an emote or state-reaction fallback.
            if (requestedGesture) TryGesture(gesture);
            else if (!string.IsNullOrEmpty(presentation.reaction))
            {
                switch (presentation.reaction)
                {
                    case "shift": case "stir":
                        // Existing bounded sleeping stir, never an awake gesture.
                        if (sleeping) animation?.PlayStateReaction("stir", true);
                        else TryGesture(AvatarGestureIntent.HeadTilt);
                        break;
                    case "startle": TryGesture(AvatarGestureIntent.Shrug); break;
                    case "wake": TryGesture(AvatarGestureIntent.Nod); break;
                    case "settle": animation?.PlayStateReaction("settle", sleeping); break;
                }
            }
            else if (fallback != AvatarGestureIntent.None) TryGesture(fallback);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            string emotion = LastFaceRequest;
            string gestureName = requestedGesture ? gesture.ToString() : "none";
            Debug.Log("[Presentation] origin=" + LastFaceOrigin + " " + emotion + " " + intensity.ToString("0.00") + " / " + gestureName +
                (appliedEmotion ? "." : " (no matching concrete expression)."));
#endif
        }

        public bool TryGesture(AvatarGestureIntent intent) =>
            GestureAllowed(intent, handsMode, awarenessMode) && animation != null && animation.PlayGesture(intent);

        private bool ApplyEmotion(string semanticEmotion, float intensity)
        {
            if (string.IsNullOrWhiteSpace(semanticEmotion)) return false;
            if (string.Equals(semanticEmotion, "neutral", System.StringComparison.OrdinalIgnoreCase))
            {
                return expressions != null && expressions.ClearSemanticExpression();
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
                // Use only stable procedural capabilities. Wave is deliberately
                // excluded: its portable authored source remains development QA.
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
