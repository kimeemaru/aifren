using System;
using System.Collections.Generic;
using AIFren.UnityPoc.Protocol;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public sealed class SceneOverlayRow
    {
        public string text;
        public string action;
        public string actionToken;
    }

    /// <summary>
    /// Pure projection from the bounded authoritative continuity snapshot to
    /// a compact companion-facing scene surface. It never receives database
    /// identifiers and never derives capabilities on the frontend.
    /// </summary>
    public static class SceneOverlayState
    {
        public const int MaximumRows = 24;

        public static Vector2 PanelAnchorMin(bool portrait)
        {
            return portrait ? new Vector2(.03f, .82f) : new Vector2(.02f, .82f);
        }

        public static Vector2 PanelAnchorMax(bool portrait)
        {
            return portrait ? new Vector2(.47f, .82f) : new Vector2(.285f, .82f);
        }

        public static float PanelHeight(int rowCount, float screenHeight)
        {
            float content = 54f + Mathf.Max(1, rowCount) * 42f;
            float maximum = Mathf.Max(150f, screenHeight * .34f);
            return Mathf.Min(content, maximum);
        }

        public static bool ShouldShow(bool preferenceEnabled, int rowCount, bool normalUiVisible)
        {
            return preferenceEnabled && normalUiVisible;
        }

        public static SceneOverlayRow[] Rows(ContinuitySnapshot snapshot)
        {
            if (snapshot == null) return Array.Empty<SceneOverlayRow>();
            List<SceneOverlayRow> result = new List<SceneOverlayRow>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            for (int index = 0; snapshot.scene_relations != null
                && index < snapshot.scene_relations.Length && result.Count < MaximumRows; index++)
            {
                ContinuitySceneRelation relation = snapshot.scene_relations[index];
                if (relation == null) continue;
                string text = RelationText(relation, snapshot.capability_effects);
                // Two causes may render the same description and still own
                // different removal commands. Text is not an object identity.
                string identity = text + "|" + (relation.clear_token ?? "");
                if (string.IsNullOrEmpty(text) || !seen.Add(identity)) continue;
                seen.Add(text);
                result.Add(new SceneOverlayRow {
                    text = text,
                    // Compact overlay X is an in-world gesture. The detailed
                    // continuity/admin path retains silent clear_scene_relation.
                    action = relation.can_clear ? "interact_scene_relation" : string.Empty,
                    actionToken = relation.can_clear ? Compact(relation.clear_token, 32) : string.Empty,
                });
            }

            // Interaction-relevant conditions are typed by the backend. The
            // overlay must not scrape the human-oriented Scene Details summary
            // or infer state from arbitrary prose.
            for (int index = 0; snapshot.scene_subjects != null
                && index < snapshot.scene_subjects.Length && result.Count < MaximumRows; index++)
            {
                ContinuitySceneSubject subject = snapshot.scene_subjects[index];
                if (subject == null
                    || !string.Equals(subject.lifecycle, "current", StringComparison.Ordinal)
                    || subject.conditions == null) continue;
                string label = Title(Compact(
                    string.IsNullOrWhiteSpace(subject.label) ? subject.kind : subject.label, 64));
                for (int conditionIndex = 0; conditionIndex < subject.conditions.Length
                    && result.Count < MaximumRows; conditionIndex++)
                {
                    string condition = Compact(subject.conditions[conditionIndex], 64);
                    string text = string.IsNullOrEmpty(label) || string.IsNullOrEmpty(condition)
                        ? string.Empty : label + " — " + condition;
                    if (!string.IsNullOrEmpty(text) && seen.Add(text))
                        result.Add(new SceneOverlayRow { text = text });
                }
            }

            // Dormant incidental subjects are not scene facts or capability
            // causes, but remain administratively removable without opening a
            // memory editor. They are shown after all active facts.
            for (int index = 0; snapshot.scene_subjects != null
                && index < snapshot.scene_subjects.Length && result.Count < MaximumRows; index++)
            {
                ContinuitySceneSubject subject = snapshot.scene_subjects[index];
                if (subject == null || !subject.can_remove
                    || !string.Equals(subject.lifecycle, "dormant", StringComparison.Ordinal)) continue;
                string kind = Compact(subject.kind, 48);
                if (string.IsNullOrEmpty(kind)) continue;
                // Dormant means the item is still referenceable but has no
                // active location relation. Do not invent that it is nearby.
                string text = Title(kind) + " (not active)";
                if (!seen.Add(text)) continue;
                result.Add(new SceneOverlayRow {
                    text = text,
                    action = "retire_scene_subject",
                    actionToken = Compact(subject.remove_token, 32),
                });
            }
            return result.ToArray();
        }

        private static string RelationText(
            ContinuitySceneRelation relation, ContinuityCapabilityEffect[] effects)
        {
            string predicate = Compact(relation.predicate, 32).Replace('_', ' ');
            string cause = Compact(relation.cause, 64);
            string target = ActorLabel(Compact(relation.target, 24));
            string facet = Compact(relation.facet, 24).Replace('_', ' ');
            string side = Compact(relation.side, 12);
            string effect = Compact(relation.effect, 40).Replace(' ', '_');
            if (string.IsNullOrEmpty(cause)) return string.Empty;

            if (predicate == "wearing" || predicate == "worn by")
                return (target == "Companion" ? string.Empty : target + ": ") + cause;
            if (predicate == "holding" || predicate == "carrying")
                return target + " holding " + cause;
            if (effect == "restraint" || predicate == "tethered to" || predicate == "restrained by")
            {
                string region = (string.IsNullOrEmpty(side) ? string.Empty : Title(side) + " ")
                    + (string.IsNullOrEmpty(facet) ? "body" : facet.TrimEnd('s'));
                return target + " " + region + " tethered — " + cause;
            }

            string capability = CapabilityForEffect(effect);
            if (!string.IsNullOrEmpty(capability))
            {
                string state = EffectState(effects, relation.target, capability);
                if (string.IsNullOrEmpty(state)) state = "constrained";
                return target + " " + Title(capability) + " " + state.Replace('_', ' ') + " — " + cause;
            }
            if (effect == "mobility_constraint")
                return target + " movement constrained — " + cause;
            return string.Empty;
        }

        private static string CapabilityForEffect(string effect)
        {
            if (effect.EndsWith("_obstruction", StringComparison.Ordinal))
                return effect.Substring(0, effect.Length - "_obstruction".Length);
            if (effect == "body_unavailable") return "hands";
            return string.Empty;
        }

        private static string EffectState(
            ContinuityCapabilityEffect[] effects, string target, string capability)
        {
            for (int index = 0; effects != null && index < effects.Length; index++)
            {
                ContinuityCapabilityEffect effect = effects[index];
                if (effect != null
                    && string.Equals(effect.target, target, StringComparison.OrdinalIgnoreCase)
                    && string.Equals(effect.capability, capability, StringComparison.OrdinalIgnoreCase))
                    return Compact(effect.state, 32);
            }
            return string.Empty;
        }

        private static string ActorLabel(string value)
        {
            if (string.Equals(value, "companion", StringComparison.OrdinalIgnoreCase)) return "Companion";
            if (string.Equals(value, "user", StringComparison.OrdinalIgnoreCase)) return "You";
            return Title(value);
        }

        private static string Title(string value)
        {
            string compact = Compact(value, 40);
            return string.IsNullOrEmpty(compact)
                ? string.Empty : char.ToUpperInvariant(compact[0]) + compact.Substring(1);
        }

        private static string Compact(string value, int maximum)
        {
            return ContinuityPanelState.Compact(value, maximum);
        }
    }
}
