using System;
using System.Collections.Generic;

namespace AIFren.UnityPoc.UI
{
    internal static class SubtitlePagination
    {
        internal static List<string> Split(string text, int maximumWords = 28)
        {
            List<string> pages = new List<string>();
            if (string.IsNullOrWhiteSpace(text)) return pages;
            string[] words = text.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
            for (int start = 0; start < words.Length;)
            {
                int count = Math.Min(maximumWords, words.Length - start);
                // Prefer a natural sentence/clause break near the target over
                // a mechanically even word count.
                int preferred = -1;
                for (int index = start + count - 1; index >= start + maximumWords / 2; index--)
                {
                    if (EndsNaturalBreak(words[index])) { preferred = index; break; }
                }
                if (preferred >= start) count = preferred - start + 1;
                // Avoid a tiny orphan by borrowing it into the preceding page.
                if (words.Length - (start + count) > 0 && words.Length - (start + count) < 7) count = words.Length - start;
                pages.Add(string.Join(" ", words, start, count));
                // count may be shortened at punctuation or expanded to avoid an
                // orphan. Advance by the actual page ownership, never the
                // requested maximum, or pages can skip/overlap words.
                start += count;
            }
            return pages;
        }

        private static bool EndsNaturalBreak(string word)
        {
            if (string.IsNullOrEmpty(word)) return false;
            if (word.Length > 2 && word[0] == '*' && word[word.Length - 1] == '*')
                word = word.Substring(1, word.Length - 2);
            char last = word[word.Length - 1];
            return last == '.' || last == '!' || last == '?' || last == ';' || last == ':' || last == '。' || last == '！' || last == '？';
        }

        internal static float PageDuration(string page, float totalSpeechSeconds, int pageCount)
        {
            if (totalSpeechSeconds > 0f && pageCount > 0) return Math.Max(1.4f, totalSpeechSeconds / pageCount);
            int words = string.IsNullOrWhiteSpace(page) ? 0 : page.Split((char[])null, StringSplitOptions.RemoveEmptyEntries).Length;
            return Math.Max(1.8f, words / 3.2f + .7f);
        }
    }
}
