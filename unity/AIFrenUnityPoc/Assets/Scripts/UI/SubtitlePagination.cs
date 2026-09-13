using System;
using System.Collections.Generic;

namespace AIFren.UnityPoc.UI
{
    /// <summary>A page of already-owned spoken words and escaped presentation markup.</summary>
    internal sealed class SubtitlePage
    {
        internal readonly string SourceText, SpokenText, FormattedText;
        internal SubtitlePage(string source, string spoken, string formatted)
        { SourceText = source; SpokenText = spoken; FormattedText = formatted; }
        internal static SubtitlePage FromMarkup(string text) => new SubtitlePage(text ?? string.Empty,
            DialoguePresentationParser.SpokenText(text), DialoguePresentationParser.FormatSubtitleText(text));
    }

    internal static class SubtitlePagination
    {
        private readonly struct PageWord
        {
            internal PageWord(string text, string formattedText, string richText, bool emphasized)
            { Text = text; FormattedText = formattedText; RichText = richText; Emphasized = emphasized; }
            internal string Text { get; }
            internal string FormattedText { get; }
            internal string RichText { get; }
            internal bool Emphasized { get; }
        }

        internal static List<string> Split(string text, int maximumWords = 28, Func<string, bool> pageFits = null)
        {
            // Compatibility for existing callers with complete markup. Normal
            // publication carries the typed pages all the way to the renderer.
            var owned = SplitOwned(DialoguePresentationParser.ParseDocument(text), maximumWords,
                pageFits == null ? null : new Func<SubtitlePage, bool>(page => pageFits(page.SourceText)));
            return owned.ConvertAll(page => page.SourceText);
        }

        internal static List<SubtitlePage> SplitOwned(DialogueDocument document, int maximumWords = 28,
            Func<SubtitlePage, bool> pageFits = null)
        {
            var pages = new List<SubtitlePage>();
            if (document == null || string.IsNullOrWhiteSpace(document.SpokenText)) return pages;
            maximumWords = Math.Max(1, maximumWords);
            List<PageWord> words = Tokenize(document.Spans);
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
                    SubtitlePage expandedPage = FormatPage(words, start, expanded);
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

        private static List<PageWord> Tokenize(IReadOnlyList<DialogueSpan> spans)
        {
            List<PageWord> words = new List<PageWord>();
            var spokenWord = new System.Text.StringBuilder();
            var formattedWord = new System.Text.StringBuilder();
            var richWord = new System.Text.StringBuilder();
            bool emphasisOpen = false, hasEmphasis = false;
            foreach (DialogueSpan span in spans)
            {
                if (span.Kind == DialogueSpanKind.Emote) continue;
                for (int cursor = 0; cursor < span.Text.Length;)
                {
                    if (char.IsWhiteSpace(span.Text[cursor]))
                    {
                        FlushWord(words, spokenWord, formattedWord, richWord, emphasisOpen, hasEmphasis);
                        emphasisOpen = hasEmphasis = false;
                        cursor++;
                        continue;
                    }
                    int start = cursor;
                    while (cursor < span.Text.Length && !char.IsWhiteSpace(span.Text[cursor])) cursor++;
                    string fragment = span.Text.Substring(start, cursor - start);
                    bool emphasized = span.Kind == DialogueSpanKind.Emphasis;
                    if (emphasized != emphasisOpen)
                    {
                        formattedWord.Append("**");
                        richWord.Append(emphasized ? "<i>" : "</i>");
                    }
                    emphasisOpen = emphasized;
                    hasEmphasis |= emphasized;
                    spokenWord.Append(fragment);
                    formattedWord.Append(fragment);
                    richWord.Append(Escape(fragment));
                }
            }
            FlushWord(words, spokenWord, formattedWord, richWord, emphasisOpen, hasEmphasis);
            return words;
        }

        private static void FlushWord(List<PageWord> words, System.Text.StringBuilder spoken,
            System.Text.StringBuilder formatted, System.Text.StringBuilder rich, bool emphasisOpen, bool hasEmphasis)
        {
            if (spoken.Length == 0) return;
            if (emphasisOpen) { formatted.Append("**"); rich.Append("</i>"); }
            // Only spoken whitespace ends a timing word. Markup can split an
            // attached suffix/prefix (for example **doing**—growing) without
            // introducing a new spoken token or changing which letters are styled.
            words.Add(new PageWord(spoken.ToString(), formatted.ToString(), rich.ToString(), hasEmphasis));
            spoken.Clear(); formatted.Clear(); rich.Clear();
        }

        private static SubtitlePage FormatPage(List<PageWord> words, int start, int count)
        {
            System.Text.StringBuilder page = new System.Text.StringBuilder();
            System.Text.StringBuilder spoken = new System.Text.StringBuilder();
            System.Text.StringBuilder rich = new System.Text.StringBuilder();
            for (int index = start; index < start + count; index++)
            {
                if (page.Length > 0) { page.Append(' '); spoken.Append(' '); rich.Append(' '); }
                page.Append(words[index].FormattedText);
                spoken.Append(words[index].Text);
                rich.Append(words[index].RichText);
            }
            return new SubtitlePage(page.ToString(), spoken.ToString(), rich.ToString());
        }

        private static string Escape(string text) => text.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");

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
