using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

namespace AIFren.UnityPoc.UI
{
    internal enum DialogueSpanKind { PlainText, Emphasis, Emote }

    internal readonly struct DialogueSpan
    {
        internal DialogueSpan(DialogueSpanKind kind, string text) { Kind = kind; Text = text ?? string.Empty; }
        internal DialogueSpanKind Kind { get; }
        internal string Text { get; }
    }

    /// <summary>Presentation-only parsing. Canonical conversation text is never changed.</summary>
    internal static class DialoguePresentationParser
    {
        private const string EmoteColor = "#74B8FF";
        private static readonly HashSet<string> ActionVerbs = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "smile", "smiles", "smiled", "smiling", "nod", "nods", "nodded", "nodding",
            "shake", "shakes", "shook", "shaking", "wave", "waves", "waved", "waving",
            "shrug", "shrugs", "shrugged", "shrugging", "tilt", "tilts", "tilted", "tilting",
            "cross", "crosses", "crossed", "crossing", "look", "looks", "looked", "looking",
            "sigh", "sighs", "sighed", "sighing", "think", "thinks", "thought", "thinking",
            "ponder", "ponders", "pondered", "pondering", "laugh", "laughs", "laughed", "laughing",
            "grin", "grins", "grinned", "grinning", "frown", "frowns", "frowned", "frowning",
            "turn", "turns", "turned", "turning", "blink", "blinks", "blinked", "blinking",
            "pause", "pauses", "paused", "pausing", "blush", "blushes", "blushed", "blushing",
            "chuckle", "chuckles", "chuckled", "chuckling", "giggle", "giggles", "giggled", "giggling",
            "gasp", "gasps", "gasped", "gasping", "stare", "stares", "stared", "staring",
            "glance", "glances", "glanced", "glancing", "raise", "raises", "raised", "raising",
            "lower", "lowers", "lowered", "lowering", "rub", "rubs", "rubbed", "rubbing",
            "bite", "bites", "bit", "biting", "lean", "leans", "leaned", "leaning",
            "shift", "shifts", "shifted", "shifting", "tap", "taps", "tapped", "tapping",
            "take", "takes", "took", "taking", "breathe", "breathes", "breathed", "breathing",
            "walk", "walks", "walked", "walking"
        };

        internal static IReadOnlyList<DialogueSpan> Parse(string raw)
        {
            List<DialogueSpan> spans = new List<DialogueSpan>();
            if (string.IsNullOrEmpty(raw)) return spans;
            int cursor = 0;
            while (cursor < raw.Length)
            {
                int start = FindMarkerStart(raw, cursor, out int markerLength);
                if (start < 0) { Add(spans, DialogueSpanKind.PlainText, raw.Substring(cursor)); break; }
                int end = FindMarkerEnd(raw, start + markerLength, markerLength);
                if (end < 0) { Add(spans, DialogueSpanKind.PlainText, raw.Substring(cursor)); break; }

                Add(spans, DialogueSpanKind.PlainText, raw.Substring(cursor, start - cursor));
                string text = raw.Substring(start + markerLength, end - start - markerLength).Trim();
                if (text.Length == 0 || !ContainsLetter(text)) Add(spans, DialogueSpanKind.PlainText, raw.Substring(start, end - start + markerLength));
                else
                {
                    bool isSingleMarkerEmote = markerLength == 1 &&
                        (IsActionEmote(text) || CountNormalizedWords(text) >= 4);
                    Add(spans, isSingleMarkerEmote ? DialogueSpanKind.Emote : DialogueSpanKind.Emphasis, text);
                }
                cursor = end + markerLength;
            }
            return spans;
        }

        internal static string FormatVisible(string raw, bool revealing = false)
        {
            if (string.IsNullOrEmpty(raw)) return string.Empty;
            StringBuilder output = new StringBuilder();
            string complete = raw;
            string partialAction = null;
            if (revealing && TryGetTrailingOpenAction(raw, out int start, out string partial))
            {
                complete = raw.Substring(0, start);
                partialAction = partial;
            }
            AppendVisible(output, Parse(complete));
            if (partialAction != null)
                output.Append("<color=").Append(EmoteColor).Append(">*").Append(Escape(partialAction)).Append("</color>");
            string formatted = NormalizeWhitespace(output.ToString());
            return Regex.Replace(formatted, @"</color>\s*\n\s*\n+\s*", "</color>\n");
        }

        internal static string SpokenText(string raw) => BuildSpoken(Parse(raw), false);

        /// <summary>Keeps only emphasis markers so subtitle styling retains one word per spoken token.</summary>
        internal static string SubtitleSourceText(string raw) => BuildSpoken(Parse(raw), true);

        internal static string FormatSubtitleText(string text)
        {
            StringBuilder output = new StringBuilder();
            foreach (DialogueSpan span in Parse(text))
            {
                if (span.Kind == DialogueSpanKind.PlainText) output.Append(Escape(span.Text));
                else if (span.Kind == DialogueSpanKind.Emphasis) output.Append("<i>").Append(Escape(span.Text)).Append("</i>");
                // Emotes are intentionally omitted from hidden spoken subtitles.
            }
            return NormalizeWhitespace(output.ToString());
        }

        internal static List<string> EmoteTexts(string raw)
        {
            List<string> actions = new List<string>();
            foreach (DialogueSpan span in Parse(raw)) if (span.Kind == DialogueSpanKind.Emote) actions.Add(span.Text);
            return actions;
        }

        internal static bool IsActionEmote(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return false;
            string[] words = text.Trim().Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
            if (words.Length == 0) return false;
            string first = words[0].Trim('"', '\'', '.', ',', '!', '?', ';', ':').ToLowerInvariant();
            if (ActionVerbs.Contains(first)) return true;
            // Keep existing third-person stage directions such as
            // "*AIFren waves*" action-like without classifying ordinary
            // inline emphasis such as "*very close*" as an emote.
            if (words.Length < 2) return false;
            string second = words[1].Trim('"', '\'', '.', ',', '!', '?', ';', ':').ToLowerInvariant();
            return ActionVerbs.Contains(second);
        }

        private static string BuildSpoken(IReadOnlyList<DialogueSpan> spans, bool preserveEmphasisMarkers)
        {
            StringBuilder output = new StringBuilder();
            foreach (DialogueSpan span in spans)
            {
                if (span.Kind == DialogueSpanKind.PlainText) output.Append(span.Text);
                else if (span.Kind == DialogueSpanKind.Emphasis)
                {
                    if (preserveEmphasisMarkers) output.Append('*').Append(span.Text).Append('*');
                    else output.Append(span.Text);
                }
                // Emotes are never spoken.
            }
            return NormalizeWhitespace(output.ToString());
        }

        private static void AppendVisible(StringBuilder output, IReadOnlyList<DialogueSpan> spans)
        {
            foreach (DialogueSpan span in spans)
            {
                if (span.Kind == DialogueSpanKind.PlainText) output.Append(Escape(span.Text));
                else if (span.Kind == DialogueSpanKind.Emphasis) output.Append("<i>").Append(Escape(span.Text)).Append("</i>");
                else output.Append("<color=").Append(EmoteColor).Append(">*").Append(Escape(span.Text)).Append("*</color>");
            }
        }

        private static bool TryGetTrailingOpenAction(string value, out int start, out string partial)
        {
            start = -1;
            partial = null;
            int cursor = 0;
            while (cursor < value.Length)
            {
                int candidate = FindMarkerStart(value, cursor, out int markerLength);
                if (candidate < 0) return false;
                int end = FindMarkerEnd(value, candidate + markerLength, markerLength);
                if (end < 0) { start = candidate; break; }
                cursor = end + markerLength;
            }
            if (start < 0) return false;
            if (start + 1 < value.Length && value[start + 1] == '*') return false;
            string openContent = value.Substring(start + 1);
            if (!ContainsLetter(openContent) || !IsActionEmote(openContent)) return false;
            partial = openContent;
            return true;
        }

        private static void Add(List<DialogueSpan> spans, DialogueSpanKind kind, string text)
        {
            if (!string.IsNullOrEmpty(text)) spans.Add(new DialogueSpan(kind, text));
        }

        private static string NormalizeWhitespace(string value)
        {
            value = value.Replace("\r\n", "\n").Replace('\r', '\n');
            value = Regex.Replace(value, "[\\t ]+", " ");
            value = Regex.Replace(value, " *\n *", "\n");
            value = Regex.Replace(value, "\n{3,}", "\n\n");
            return value.Trim();
        }

        private static string Escape(string value) => value.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
        private static int FindMarkerStart(string value, int offset, out int markerLength)
        {
            markerLength = 0;
            for (int index = offset; index < value.Length; index++)
            {
                if (value[index] != '*' || IsEscaped(value, index)) continue;
                bool doubleMarker = index + 1 < value.Length && value[index + 1] == '*' &&
                    (index == 0 || value[index - 1] != '*') && (index + 2 >= value.Length || value[index + 2] != '*');
                if (doubleMarker)
                {
                    if (index + 2 >= value.Length || char.IsWhiteSpace(value[index + 2])) continue;
                    markerLength = 2;
                    return index;
                }
                if (IsDoubleStar(value, index) || index + 1 >= value.Length || char.IsWhiteSpace(value[index + 1])) continue;
                markerLength = 1;
                return index;
            }
            return -1;
        }

        private static int FindMarkerEnd(string value, int offset, int markerLength)
        {
            for (int index = offset; index < value.Length; index++)
            {
                if (value[index] != '*' || IsEscaped(value, index)) continue;
                if (markerLength == 2 && index + 1 < value.Length && value[index + 1] == '*' &&
                    (index == 0 || value[index - 1] != '*') && (index + 2 >= value.Length || value[index + 2] != '*')) return index;
                if (markerLength == 1 && !IsDoubleStar(value, index)) return index;
            }
            return -1;
        }

        private static bool IsEscaped(string value, int index) => index > 0 && value[index - 1] == '\\';
        private static bool IsDoubleStar(string value, int index) =>
            (index > 0 && value[index - 1] == '*') || (index + 1 < value.Length && value[index + 1] == '*');
        private static int CountNormalizedWords(string value) => value.Trim().Split((char[])null, StringSplitOptions.RemoveEmptyEntries).Length;
        private static bool ContainsLetter(string value) { foreach (char character in value) if (char.IsLetter(character)) return true; return false; }
    }
}
