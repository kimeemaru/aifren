using System;
using System.Collections.Generic;

namespace AIFren.UnityPoc.UI
{
    internal static class SubtitlePagination
    {
        private readonly struct PageWord
        {
            internal PageWord(string text, bool emphasized) { Text = text; Emphasized = emphasized; }
            internal string Text { get; }
            internal bool Emphasized { get; }
        }

        internal static List<string> Split(string text, int maximumWords = 28, Func<string, bool> pageFits = null)
        {
            List<string> pages = new List<string>();
            if (string.IsNullOrWhiteSpace(text)) return pages;
            maximumWords = Math.Max(1, maximumWords);
            List<PageWord> words = Tokenize(text);
            for (int start = 0; start < words.Count;)
            {
                int count = Math.Min(maximumWords, words.Count - start);
                while (count > 1 && pageFits != null && !pageFits(FormatPage(words, start, count))) count--;
                // Prefer a natural sentence/clause break near the target over
                // a mechanically even word count.
                int preferred = -1;
                int earliestBreak = start + Math.Max(1, count / 2) - 1;
                for (int index = start + count - 1; index >= earliestBreak; index--)
                {
                    if (EndsNaturalBreak(words[index].Text)) { preferred = index; break; }
                }
                if (preferred >= start) count = preferred - start + 1;
                // Avoid a tiny orphan by borrowing it into the preceding page.
                int remaining = words.Count - (start + count);
                if (remaining > 0 && remaining < 7)
                {
                    int expanded = words.Count - start;
                    string expandedPage = FormatPage(words, start, expanded);
                    // Preserve the established orphan absorption for plain
                    // prose. Styled spans retain the requested cap so a long
                    // emphasis can be closed/reopened safely across pages.
                    bool crossesStyledWords = false;
                    for (int index = start; index < words.Count; index++)
                        if (words[index].Emphasized) { crossesStyledWords = true; break; }
                    if ((!crossesStyledWords || expanded <= maximumWords) &&
                        (pageFits == null || pageFits(expandedPage)))
                        count = expanded;
                }
                pages.Add(FormatPage(words, start, count));
                // count may be shortened at punctuation or expanded to avoid an
                // orphan. Advance by the actual page ownership, never the
                // requested maximum, or pages can skip/overlap words.
                start += count;
            }
            return pages;
        }

        private static List<PageWord> Tokenize(string text)
        {
            List<PageWord> words = new List<PageWord>();
            foreach (DialogueSpan span in DialoguePresentationParser.Parse(text))
            {
                if (span.Kind == DialogueSpanKind.Emote) continue;
                string[] spanWords = span.Text.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
                for (int index = 0; index < spanWords.Length; index++)
                {
                    string word = spanWords[index];
                    // Markup boundaries can leave trailing punctuation in its
                    // own plain-text span (for example **that**.). It is still
                    // part of the preceding spoken word and must not acquire a
                    // separate timing/ownership slot.
                    if (index == 0 && words.Count > 0 && !char.IsWhiteSpace(span.Text[0]) &&
                        IsPunctuationOnly(word))
                    {
                        PageWord previous = words[words.Count - 1];
                        words[words.Count - 1] = new PageWord(previous.Text + word, previous.Emphasized);
                    }
                    else words.Add(new PageWord(word, span.Kind == DialogueSpanKind.Emphasis));
                }
            }
            return words;
        }

        private static bool IsPunctuationOnly(string value)
        {
            if (string.IsNullOrEmpty(value)) return false;
            foreach (char character in value) if (char.IsLetterOrDigit(character)) return false;
            return true;
        }

        private static string FormatPage(List<PageWord> words, int start, int count)
        {
            System.Text.StringBuilder page = new System.Text.StringBuilder();
            bool emphasisOpen = false;
            for (int index = start; index < start + count; index++)
            {
                PageWord word = words[index];
                if (!word.Emphasized && emphasisOpen) { page.Append("**"); emphasisOpen = false; }
                if (page.Length > 0) page.Append(' ');
                if (word.Emphasized && !emphasisOpen) { page.Append("**"); emphasisOpen = true; }
                page.Append(word.Text);
            }
            if (emphasisOpen) page.Append("**");
            return page.ToString();
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
