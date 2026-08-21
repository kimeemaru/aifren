using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

namespace AIFren.UnityPoc.UI
{
    internal struct SubtitlePageWordRange
    {
        internal SubtitlePageWordRange(int firstWordIndex, int lastWordIndex)
        {
            FirstWordIndex = firstWordIndex;
            LastWordIndex = lastWordIndex;
        }

        internal int FirstWordIndex { get; }
        internal int LastWordIndex { get; }
    }

    /// <summary>
    /// Immutable fallback schedule for spoken subtitle words. It intentionally
    /// uses one normalized plan per response rather than recalculating from
    /// remaining time as playback progresses.
    /// </summary>
    internal static class SubtitleTimingPlan
    {
        private static readonly Regex WordPattern = new Regex(@"\S+", RegexOptions.Compiled);

        internal static int WordCount(string text) => WordPattern.Matches(text ?? string.Empty).Count;

        internal static List<string> TokenizeWords(string text)
        {
            MatchCollection matches = WordPattern.Matches(text ?? string.Empty);
            List<string> words = new List<string>(matches.Count);
            foreach (Match match in matches) words.Add(match.Value);
            return words;
        }

        internal static List<SubtitlePageWordRange> BuildPageWordRanges(IList<string> pages)
        {
            List<SubtitlePageWordRange> ranges = new List<SubtitlePageWordRange>();
            int nextWord = 0;
            foreach (string page in pages ?? Array.Empty<string>())
            {
                int count = WordCount(page);
                if (count == 0) continue;
                ranges.Add(new SubtitlePageWordRange(nextWord, nextWord + count - 1));
                nextWord += count;
            }
            return ranges;
        }

        internal static bool CoversAllWordsExactlyOnce(IList<SubtitlePageWordRange> ranges, int wordCount)
        {
            int expectedFirst = 0;
            foreach (SubtitlePageWordRange range in ranges ?? Array.Empty<SubtitlePageWordRange>())
            {
                if (range.FirstWordIndex != expectedFirst || range.LastWordIndex < range.FirstWordIndex) return false;
                expectedFirst = range.LastWordIndex + 1;
            }
            return expectedFirst == wordCount;
        }

        internal static bool TryValidatePageDefinitions(
            IList<string> pages, IList<SubtitlePageWordRange> ranges, int totalWordCount, out string error)
        {
            if (pages == null || ranges == null || pages.Count != ranges.Count)
            {
                error = "page/range count mismatch";
                return false;
            }

            int expectedFirst = 0;
            for (int index = 0; index < pages.Count; index++)
            {
                SubtitlePageWordRange range = ranges[index];
                int pageWords = WordCount(pages[index]);
                if (range.FirstWordIndex != expectedFirst || range.LastWordIndex < range.FirstWordIndex ||
                    pageWords != range.LastWordIndex - range.FirstWordIndex + 1)
                {
                    error = "invalid ownership at page " + index + " (range " + range.FirstWordIndex + "-" +
                        range.LastWordIndex + ", words=" + pageWords + ")";
                    return false;
                }
                expectedFirst = range.LastWordIndex + 1;
            }

            if (expectedFirst != totalWordCount)
            {
                error = "page ownership ends at " + (expectedFirst - 1) + " but total words=" + totalWordCount;
                return false;
            }

            error = null;
            return true;
        }

        internal static bool TryValidatePagesMatchCanonicalText(
            string canonicalText, IList<string> pages, IList<SubtitlePageWordRange> ranges, out string error)
        {
            return TryValidatePagesMatchCanonicalText(canonicalText, pages, ranges, null, out error);
        }

        internal static bool TryValidatePagesMatchCanonicalText(
            string canonicalText, IList<string> pages, IList<SubtitlePageWordRange> ranges,
            Func<string, string> pageToSpokenText, out string error)
        {
            List<string> allWords = TokenizeWords(canonicalText);
            if (!TryValidatePageDefinitions(pages, ranges, allWords.Count, out error)) return false;

            for (int pageIndex = 0; pageIndex < pages.Count; pageIndex++)
            {
                string pageText = pageToSpokenText != null ? pageToSpokenText(pages[pageIndex]) : pages[pageIndex];
                List<string> pageWords = TokenizeWords(pageText);
                SubtitlePageWordRange range = ranges[pageIndex];
                for (int localIndex = 0; localIndex < pageWords.Count; localIndex++)
                {
                    int globalIndex = range.FirstWordIndex + localIndex;
                    if (!string.Equals(pageWords[localIndex], allWords[globalIndex], StringComparison.Ordinal))
                    {
                        error = "page " + pageIndex + " local word " + localIndex + " ('" + pageWords[localIndex] +
                            "') does not match canonical global word " + globalIndex + " ('" + allWords[globalIndex] + "')";
                        return false;
                    }
                }
            }

            error = null;
            return true;
        }

        internal static bool IsPageFinalWordDue(SubtitlePageWordRange range, IList<float> wordStarts, float playbackElapsed)
        {
            return wordStarts != null && range.LastWordIndex >= 0 && range.LastWordIndex < wordStarts.Count &&
                playbackElapsed >= wordStarts[range.LastWordIndex];
        }

        /// <summary>
        /// Applies a fixed visible-caption lead once after an immutable word
        /// schedule is built. It preserves every interval and ordering.
        /// </summary>
        internal static void ApplyLead(List<float> wordStarts, float leadSeconds)
        {
            if (wordStarts == null || leadSeconds <= 0f || float.IsNaN(leadSeconds) || float.IsInfinity(leadSeconds)) return;
            for (int index = 0; index < wordStarts.Count; index++)
                wordStarts[index] = Math.Max(0f, wordStarts[index] - leadSeconds);
        }

        internal static List<float> Build(string text, float durationSeconds, float fallbackWordsPerSecond)
        {
            MatchCollection words = WordPattern.Matches(text ?? string.Empty);
            List<float> starts = new List<float>(words.Count);
            if (words.Count == 0) return starts;

            float totalWeight = 0f;
            float[] weights = new float[words.Count];
            for (int index = 0; index < words.Count; index++)
            {
                string word = words[index].Value;
                float weight = 1f + Math.Min(.35f, Math.Max(0, word.Length - 8) * .05f);
                char ending = word[word.Length - 1];
                if (ending == ',' || ending == ';' || ending == ':' || ending == '、') weight += .28f;
                else if (ending == '.' || ending == '!' || ending == '?' || ending == '…' ||
                         ending == '。' || ending == '！' || ending == '？') weight += .62f;
                weights[index] = weight;
                totalWeight += weight;
            }

            float duration = durationSeconds > 0f
                ? durationSeconds
                : Math.Max(1.2f, totalWeight / Math.Max(.1f, fallbackWordsPerSecond));
            float elapsedWeight = 0f;
            for (int index = 0; index < words.Count; index++)
            {
                starts.Add(duration * elapsedWeight / totalWeight);
                elapsedWeight += weights[index];
            }
            return starts;
        }
    }
}
