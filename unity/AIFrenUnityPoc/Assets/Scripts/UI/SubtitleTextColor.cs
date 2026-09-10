using System;
using System.Globalization;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    // Presentation only. Named presets retain the original exact float colors.
    internal static class SubtitleTextColor
    {
        internal const string Preference = "AIFren.HiddenSubtitle.TextColor.v1";
        internal static readonly Color ClassicPink = new Color(.98f, .62f, .78f, 1f);
        internal static string Saved => Normalize(PresentationPreferences.GetString(Preference, "white"));
        internal static Color Current { get { TryParse(Saved, out Color color); return color; } }
        internal static bool TryParse(string value, out Color color)
        {
            color = SubtitleStyle.Face;
            string text = (value ?? "").Trim();
            if (text == "white") return true;
            if (text == "pink") { color = ClassicPink; return true; }
            if (text.StartsWith("#", StringComparison.Ordinal)) text = text.Substring(1);
            if (text.Length != 6 || !uint.TryParse(text, NumberStyles.AllowHexSpecifier,
                CultureInfo.InvariantCulture, out uint rgb)) return false;
            color = new Color(((rgb >> 16) & 255) / 255f, ((rgb >> 8) & 255) / 255f, (rgb & 255) / 255f, 1f);
            return true;
        }
        internal static string Normalize(string value) => TryParse(value, out _) ?
            (value.Trim() == "white" || value.Trim() == "pink" ? value.Trim() : "#" + value.Trim().TrimStart('#').ToUpperInvariant()) : "white";
        internal static bool Save(string value)
        {
            if (!TryParse(value, out _)) return false;
            string normalized = Normalize(value);
            if (normalized == "white") PresentationPreferences.DeleteKey(Preference);
            else PresentationPreferences.SetString(Preference, normalized);
            PresentationPreferences.Save();
            return true;
        }
    }
}
