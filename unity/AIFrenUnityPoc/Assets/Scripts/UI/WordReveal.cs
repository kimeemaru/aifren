using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Presentation-only word reveal. It never affects protocol, persistence,
    /// TTS, or the complete conversation text kept by the controller.
    /// </summary>
    public sealed class WordReveal
    {
        private static readonly Regex TokenPattern = new Regex(@"\S+\s*", RegexOptions.Compiled);
        private readonly List<string> tokens = new List<string>();
        private float accumulator;
        private float latestTokenAlpha = 1f;
        private int revealedTokenCount;

        public string FullText { get; private set; } = string.Empty;
        public float WordsPerSecond { get; set; } = 7f;
        public int WordCount => tokens.Count;
        public int RevealedTokenCount => revealedTokenCount;
        public bool IsComplete => revealedTokenCount >= tokens.Count;
        public string VisibleText => BuildVisibleText();
        /// <summary>Presentation alpha for the most recently revealed token.</summary>
        public float LatestTokenAlpha => latestTokenAlpha;
        public bool LatestTokenIsFading => revealedTokenCount > 0 && latestTokenAlpha < .999f;

        public void Begin(string text, bool revealImmediately)
        {
            FullText = text ?? string.Empty;
            tokens.Clear();
            accumulator = 0f;
            latestTokenAlpha = 1f;
            revealedTokenCount = 0;

            foreach (Match match in TokenPattern.Matches(FullText))
            {
                tokens.Add(match.Value);
            }

            if (revealImmediately)
            {
                RevealAll();
            }
        }

        public bool Advance(float deltaTime)
        {
            AdvanceLatestTokenFade(deltaTime);

            if (IsComplete || tokens.Count == 0)
            {
                return false;
            }

            accumulator += Math.Max(0f, deltaTime) * Math.Max(0.1f, WordsPerSecond);
            int wordsToReveal = (int)accumulator;

            if (wordsToReveal <= 0)
            {
                return false;
            }

            accumulator -= wordsToReveal;
            revealedTokenCount = Math.Min(tokens.Count, revealedTokenCount + wordsToReveal);
            latestTokenAlpha = 0f;
            return true;
        }

        /// <summary>
        /// Advances only the presentation fade of the latest token. Timestamp
        /// driven subtitle schedules use this so they never reveal an extra
        /// word through the generic words-per-second path.
        /// </summary>
        public void AdvanceLatestTokenFade(float deltaTime)
        {
            if (revealedTokenCount <= 0 || latestTokenAlpha >= 1f) return;
            latestTokenAlpha = Math.Min(1f, latestTokenAlpha + Math.Max(0f, deltaTime) / .12f);
        }

        /// <summary>Seeds a page with one visible token without relying on frame timing.</summary>
        public bool RevealNext()
        {
            if (IsComplete || tokens.Count == 0) return false;
            revealedTokenCount++;
            accumulator = 0f;
            latestTokenAlpha = 0f;
            return true;
        }

        public void RevealAll()
        {
            revealedTokenCount = tokens.Count;
            accumulator = 0f;
            latestTokenAlpha = 1f;
        }

        public void RevealTo(int tokenCount)
        {
            int clamped = Math.Max(0, Math.Min(tokens.Count, tokenCount));
            if (clamped <= revealedTokenCount) return;
            revealedTokenCount = clamped;
            accumulator = 0f;
            // The newest timestamp-due token gets the same short visual ramp
            // as a normally revealed token. Earlier newly-due tokens are
            // already fully visible, so a page catch-up never hides content.
            latestTokenAlpha = 0f;
        }

        public static float WordsPerSecondForDuration(int wordCount, float durationSeconds, float fallback)
        {
            if (wordCount <= 0 || durationSeconds <= 0f)
            {
                return Math.Max(0.1f, fallback);
            }

            return Math.Max(0.1f, wordCount / durationSeconds);
        }

        private string BuildVisibleText()
        {
            if (revealedTokenCount <= 0)
            {
                return string.Empty;
            }

            return string.Concat(tokens.GetRange(0, revealedTokenCount));
        }
    }
}
