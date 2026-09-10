using System;
using System.Text.RegularExpressions;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// A deliberately small projection of leading, typed, current self-action
    /// spans. Not sentiment analysis; unknown syntax never selects an expression.
    /// Call only for an accepted final response under its presentation owner.
    /// </summary>
    internal static class FacialEmoteProjection
    {
        private const RegexOptions Options = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
        private static readonly TimeSpan MatchLimit = TimeSpan.FromMilliseconds(20);
        // This vocabulary can only veto an unsupported compound, never select a face.
        private static readonly Regex FacialWords = Pattern(@"\b(smile[sd]?|smiling|beams?|beaming|grins?|grinning|frowns?|frowning|face|facial|expression|brows?|neutral)\b");
        private static readonly Regex Smile = Pattern(@"^(?:smiles|beams)(?: (?:warmly|softly|gently|brightly))?(?: at you)?$");
        // Present self pose + an affirmative simultaneous smile, as in the
        // recorded reply. A bounded location noun is data, never a scene action.
        private static readonly Regex OfferedSmile = Pattern(@"^(?:(?:shifts|settles)(?: (?:slightly|gently))?(?: (?:on|into) (?:the|a) [a-z]{1,20})?, offering|offers) a (?:warm|gentle|soft|bright) smile$");
        // An explicit present smile on the named subject's face; "brightens"
        // alone never selects an emotion. No arbitrary introductory clauses.
        private static readonly Regex PresentSmile = Pattern(@"^brightens, a (?:warm|gentle|soft|bright) smile spreading across (?:my|her|his|their) face$");
        private static readonly Regex Neutral = Pattern(@"^returns to (?:a |an? explicitly )?neutral (?:face|expression)$");
        private static readonly Regex FaceDescription = Pattern(@"^a (?:soft |warm |happy,? |encouraging )*look (?:of (?:encouraging )?pride on (?:my|her|his|their) face|settling over (?:my|her|his|their) features)$");
        private static readonly Regex Body = Pattern(@"^(?:nods?|waves?)(?: (?:gently|softly|once))?$");
        private static readonly Regex MetalinguisticTail = Pattern(@"^(?:[\""'“”‘’`]|(?:is|was|means|would|could|should|might|will|if|when|as an example)\b)");

        private static Regex Pattern(string value) => new Regex(value, Options, MatchLimit);

        internal static string Select(DialogueDocument document, string selfName, out string reason)
        {
            reason = "no_action";
            if (document == null || document.Spans.Count > 32) { reason = "bound"; return null; }
            string selected = null;
            bool spoken = false;
            try
            {
                foreach (DialogueSpan span in document.Spans)
                {
                    if (span.Kind != DialogueSpanKind.Emote)
                    {
                        // Unsupported marked facial wording cannot contradict an
                        // admitted action. It still remains spoken emphasis; it
                        // can veto this projection, never supply a facial request.
                        if (selected != null && span.Kind == DialogueSpanKind.Emphasis && FacialWords.IsMatch(span.Text))
                        { reason = "unsupported_compound"; return null; }
                        if (selected != null && !spoken && MetalinguisticTail.IsMatch(span.Text.TrimStart()))
                        { reason = "quoted_or_framed"; return null; }
                        if (!string.IsNullOrWhiteSpace(span.Text)) spoken = true;
                        continue;
                    }
                    if (span.Text.Length > 256) { reason = "bound"; return null; }
                    string action = span.Text.Trim().TrimEnd('.').Trim();
                    if (!FacialWords.IsMatch(action)) continue;
                    if (spoken) { reason = "nonleading_action"; return null; }
                    action = StripSelf(action, selfName);
                    string emotion = SelectAction(action);
                    if (emotion == null) { reason = "unsupported_action"; return null; }
                    if (selected != null && selected != emotion) { reason = "conflict"; return null; }
                    selected = emotion;
                }
            }
            catch (RegexMatchTimeoutException) { reason = "bound"; return null; }
            reason = selected != null ? "eligible" : "no_action";
            return selected;
        }

        private static string StripSelf(string action, string selfName)
        {
            // A display name is data, never a regex/alias inferred from personality.
            if (!string.IsNullOrWhiteSpace(selfName) && selfName.Length <= 64 &&
                action.StartsWith(selfName.Trim() + " ", StringComparison.OrdinalIgnoreCase))
                return action.Substring(selfName.Trim().Length + 1);
            if (action.StartsWith("I am smiling", StringComparison.OrdinalIgnoreCase))
                return "smiles" + action.Substring(12);
            foreach (var pair in new[] { ("I smile", "smiles"), ("I beam", "beams"), ("I return", "returns"), ("I offer", "offers"), ("I shift", "shifts"), ("I settle", "settles") })
                if (action.StartsWith(pair.Item1 + " ", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(action, pair.Item1, StringComparison.OrdinalIgnoreCase))
                    return pair.Item2 + action.Substring(pair.Item1.Length);
            return action; // Pronouns/another actor remain unmatched by the anchored grammar.
        }

        private static string SelectAction(string action)
        {
            if (OfferedSmile.IsMatch(action) || PresentSmile.IsMatch(action)) return "happy";
            // A body clause is allowed only in this closed affirmative form.
            int and = action.IndexOf(" and ", StringComparison.OrdinalIgnoreCase);
            if (and >= 0)
            {
                string left = action.Substring(0, and), right = action.Substring(and + 5);
                if (Body.IsMatch(right)) action = left;
                else if (Body.IsMatch(left)) action = right;
                else return null;
            }
            int comma = action.IndexOf(',');
            if (comma >= 0)
            {
                if (!FaceDescription.IsMatch(action.Substring(comma + 1).Trim())) return null;
                action = action.Substring(0, comma).TrimEnd();
            }
            if (Smile.IsMatch(action)) return "happy";
            if (Neutral.IsMatch(action)) return "neutral";
            return null;
        }
    }
}
