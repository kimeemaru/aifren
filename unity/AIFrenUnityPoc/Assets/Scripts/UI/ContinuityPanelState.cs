using System;
using System.Collections.Generic;
using AIFren.UnityPoc.Protocol;

namespace AIFren.UnityPoc.UI
{
    public static class ContinuityPanelState
    {
        public const int MaximumThreads = 6;

        public static string ScopeText(TruthScopeSnapshot scope)
        {
            if (scope != null && string.Equals(scope.kind, "scenario", StringComparison.Ordinal))
            {
                string label = Compact(scope.label, 96);
                return string.IsNullOrEmpty(label) ? "Roleplay" : "RP: " + label;
            }
            return "Real world";
        }

        public static string ActivityText(ContinuityActivity activity)
        {
            string value = activity != null ? Compact(activity.value, 144) : string.Empty;
            return string.IsNullOrEmpty(value) ? "None" : value;
        }

        public static string SceneText(ContinuitySceneSubject[] subjects,
            ContinuitySceneRelation[] relations = null, ContinuityCapabilityEffect[] effects = null,
            ContinuityProfileBaseline[] baseline = null)
        {
            List<string> rows = new List<string>();
            List<string> section = new List<string>();
            for (int index = 0; subjects != null && index < subjects.Length; index++)
            {
                ContinuitySceneSubject subject = subjects[index];
                if (subject == null) continue;
                string kind = Compact(subject.kind, 48);
                if (string.IsNullOrEmpty(kind)) kind = "object";
                string scope = Compact(subject.scope, 64);
                string summary = Compact(subject.summary, 180);
                string confirmed = Compact(subject.confirmed, 40);
                string heading = kind + (string.IsNullOrEmpty(scope) ? string.Empty : " [" + scope + "]");
                string details = string.IsNullOrEmpty(summary) ? string.Empty : " — " + summary;
                string recency = string.IsNullOrEmpty(confirmed) ? string.Empty : "\nConfirmed " + confirmed;
                section.Add("• " + heading + details + recency);
            }
            if (section.Count > 0)
            {
                rows.Add("<b>SCENE SUBJECTS</b>");
                rows.AddRange(section);
            }
            section.Clear();
            for (int index = 0; relations != null && index < relations.Length; index++)
            {
                ContinuitySceneRelation relation = relations[index];
                if (relation == null) continue;
                string target = Compact(relation.target, 24);
                string facet = Compact(relation.facet, 24);
                string predicate = Compact(relation.predicate, 32).Replace('_', ' ');
                string cause = Compact(relation.cause, 64);
                string scope = Compact(relation.scope, 64);
                section.Add("• " + target + " " + facet + ": " + predicate + " — " + cause
                    + (string.IsNullOrEmpty(scope) ? string.Empty : " [" + scope + "]"));
            }
            if (section.Count > 0)
            {
                rows.Add("<b>RELATIONS</b>");
                rows.AddRange(section);
            }
            section.Clear();
            string capabilityTarget = null;
            for (int index = 0; effects != null && index < effects.Length; index++)
            {
                ContinuityCapabilityEffect effect = effects[index];
                if (effect == null) continue;
                string target = Compact(effect.target, 24);
                if (!string.Equals(target, capabilityTarget, StringComparison.Ordinal))
                {
                    capabilityTarget = target;
                    section.Add("<b>" + ActorLabel(target) + "</b>");
                }
                string row = "• " + TitleLabel(Compact(effect.capability, 24)) + ": "
                    + Compact(effect.state, 32).Replace('_', ' ');
                string cause = Compact(effect.cause, 96);
                if (!string.IsNullOrEmpty(cause))
                {
                    string[] causes = cause.Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
                    for (int causeIndex = 0; causeIndex < causes.Length; causeIndex++)
                        row += "\n  - " + Compact(causes[causeIndex], 64);
                }
                section.Add(row);
            }
            if (section.Count > 0)
            {
                rows.Add("<b>DERIVED CAPABILITIES</b>");
                rows.AddRange(section);
            }
            section.Clear();
            for (int index = 0; baseline != null && index < baseline.Length; index++)
            {
                ContinuityProfileBaseline item = baseline[index];
                if (item == null) continue;
                section.Add("• Profile default: " + Compact(item.item, 64)
                    + " [" + Compact(item.region, 24).Replace('_', ' ') + "]");
            }
            if (section.Count > 0)
            {
                rows.Add("<b>PROFILE BASELINE</b>");
                rows.AddRange(section);
            }
            return rows.Count == 0 ? "None" : string.Join("\n", rows);
        }

        private static string ActorLabel(string value)
        {
            if (string.Equals(value, "companion", StringComparison.OrdinalIgnoreCase)) return "Companion";
            if (string.Equals(value, "user", StringComparison.OrdinalIgnoreCase)) return "User";
            return TitleLabel(value);
        }

        private static string TitleLabel(string value)
        {
            if (string.IsNullOrEmpty(value)) return "Capability";
            string normalized = value.Replace('_', ' ');
            return char.ToUpperInvariant(normalized[0]) + normalized.Substring(1);
        }

        public static string ProactiveEligibilityText(string outcome, int nextSeconds = -1, int ignoredStreak = 0)
        {
            string compact = Compact(outcome, 80).Replace('_', ' ');
            if (string.IsNullOrEmpty(compact) || compact == "unavailable") return "Unavailable";
            if (compact.StartsWith("eligible ", StringComparison.Ordinal))
                return "Eligible: " + compact.Substring("eligible ".Length);
            string status = compact.StartsWith("backed off", StringComparison.Ordinal)
                ? "Backed off: prior check-in unanswered" : "Ineligible: " + compact;
            if (nextSeconds >= 0) status += "\nNext opportunity: ~" + nextSeconds + " sec";
            if (ignoredStreak > 0) status += "\nUnanswered check-ins: " + ignoredStreak;
            return status;
        }

        public static string ThreadPrefix(string kind)
        {
            switch (kind)
            {
                case "waiting": return "Waiting";
                case "plan_or_intention": return "Plan";
                case "decision_or_question": return "Question";
                case "ongoing_shared_thread": return "Shared";
                default: return "Open";
            }
        }

        public static ContinuityThread[] BoundedThreads(ContinuityThread[] source)
        {
            if (source == null || source.Length == 0) return Array.Empty<ContinuityThread>();
            List<ContinuityThread> result = new List<ContinuityThread>();
            for (int index = 0; index < source.Length && result.Count < MaximumThreads; index++)
            {
                ContinuityThread item = source[index];
                if (item == null || string.IsNullOrWhiteSpace(item.description)) continue;
                result.Add(item);
            }
            return result.ToArray();
        }

        public static string Compact(string value, int maximum)
        {
            string compact = string.Join(" ", (value ?? string.Empty).Split(
                new[] { ' ', '\t', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries));
            if (compact.Length > maximum) compact = compact.Substring(0, maximum).TrimEnd() + "…";
            return compact;
        }
    }
}
