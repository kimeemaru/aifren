using System;

namespace AIFren.UnityPoc.UI
{
    /// <summary>Pure formatting policy for backend-authoritative RP status.</summary>
    public static class TruthScopeIndicatorState
    {
        private const int MaximumLabelCharacters = 32;
        public const float OutlineWidth = 0.24f;

        public static UnityEngine.Color32 OutlineColor()
        {
            return new UnityEngine.Color32(0, 0, 0, 230);
        }

        public static UnityEngine.Vector2 SafeAreaAnchor(UnityEngine.Rect safeArea, UnityEngine.Vector2 screenSize)
        {
            if (screenSize.x <= 0f || screenSize.y <= 0f) return UnityEngine.Vector2.zero;
            return new UnityEngine.Vector2(
                UnityEngine.Mathf.Clamp01(safeArea.xMin / screenSize.x),
                UnityEngine.Mathf.Clamp01(safeArea.yMin / screenSize.y));
        }

        public static UnityEngine.Vector2 SafeMargin()
        {
            return new UnityEngine.Vector2(12f, 2f);
        }

        public static UnityEngine.Vector2 Size(bool portrait)
        {
            return new UnityEngine.Vector2(portrait ? 360f : 420f, 22f);
        }

        public static string DisplayText(string kind, string label)
        {
            if (!string.Equals(kind, "scenario", StringComparison.Ordinal)) return string.Empty;
            string compact = CompactLabel(label);
            return string.IsNullOrEmpty(compact) ? "RP" : "RP · " + compact;
        }

        private static string CompactLabel(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return string.Empty;
            foreach (char character in value)
            {
                if (char.IsControl(character)) return string.Empty;
            }
            string compact = string.Join(" ", value.Split((char[])null, StringSplitOptions.RemoveEmptyEntries));
            if (compact.Length <= MaximumLabelCharacters) return compact;
            return compact.Substring(0, MaximumLabelCharacters - 1).TrimEnd() + "…";
        }
    }
}
