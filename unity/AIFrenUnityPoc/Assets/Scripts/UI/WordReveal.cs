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
        private int revealedTokenCount;

        public string FullText { get; private set; } = string.Empty;
        public float WordsPerSecond { get; set; } = 7f;
        public int WordCount => tokens.Count;
        public bool IsComplete => revealedTokenCount >= tokens.Count;
        public string VisibleText => BuildVisibleText();

        public void Begin(string text, bool revealImmediately)
        {
            FullText = text ?? string.Empty;
            tokens.Clear();
            accumulator = 0f;
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
            return true;
        }

        public void RevealAll()
        {
            revealedTokenCount = tokens.Count;
            accumulator = 0f;
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
