using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public enum PresentationDisplayMode
    {
        Windowed,
        BorderlessFullscreen,
        Fullscreen
    }

    public enum PresentationLayoutMode
    {
        Auto,
        Landscape,
        Portrait
    }

    [Serializable]
    public sealed class PresentationDisplaySettings
    {
        public int displayIndex;
        public int width;
        public int height;
        public PresentationDisplayMode displayMode = PresentationDisplayMode.BorderlessFullscreen;
        public PresentationLayoutMode layoutMode = PresentationLayoutMode.Auto;
        public float uiScale = 1f;
        public bool vSync = true;
        public int frameLimit = 60;
        public int antiAliasing = 4;

        public PresentationDisplaySettings Clone()
        {
            return (PresentationDisplaySettings)MemberwiseClone();
        }
    }

    /// <summary>Pure display-setting validation/mapping used by the UI and tests.</summary>
    public static class PresentationDisplaySettingsPolicy
    {
        public const float MinimumUiScale = 0.75f;
        public const float MaximumUiScale = 1.50f;

        public static readonly int[] FrameLimits = { 60, 120, -1 };
        public static readonly int[] AntiAliasingOptions = { 0, 2, 4, 8 };

        public static PresentationDisplaySettings Normalize(PresentationDisplaySettings settings)
        {
            PresentationDisplaySettings normalized = settings != null ? settings.Clone() : new PresentationDisplaySettings();
            normalized.displayIndex = Mathf.Max(0, normalized.displayIndex);
            normalized.width = Mathf.Max(640, normalized.width);
            normalized.height = Mathf.Max(480, normalized.height);
            normalized.uiScale = Mathf.Clamp(normalized.uiScale, MinimumUiScale, MaximumUiScale);
            if (!FrameLimits.Contains(normalized.frameLimit))
            {
                normalized.frameLimit = 60;
            }
            if (!AntiAliasingOptions.Contains(normalized.antiAliasing))
            {
                normalized.antiAliasing = 4;
            }
            return normalized;
        }

        public static PresentationDisplaySettings NormalizeForScreen(PresentationDisplaySettings settings, int width, int height)
        {
            PresentationDisplaySettings normalized = Normalize(settings);
            // Maintain enough logical space for the essential controls on the
            // narrowest supported orientation. Higher accessibility scaling
            // needs a dedicated layout, not an unsafe reference multiplier.
            float shortestSide = Mathf.Max(1f, Mathf.Min(width, height));
            float safeMaximum = Mathf.Min(MaximumUiScale, shortestSide / 600f);
            normalized.uiScale = Mathf.Clamp(normalized.uiScale, MinimumUiScale, Mathf.Max(MinimumUiScale, safeMaximum));
            return normalized;
        }

        public static FullScreenMode ToUnityMode(PresentationDisplayMode mode)
        {
            switch (mode)
            {
                case PresentationDisplayMode.Windowed:
                    return FullScreenMode.Windowed;
                case PresentationDisplayMode.Fullscreen:
                    return FullScreenMode.ExclusiveFullScreen;
                default:
                    return FullScreenMode.FullScreenWindow;
            }
        }

        public static PresentationDisplayMode FromUnityMode(FullScreenMode mode)
        {
            return mode == FullScreenMode.Windowed
                ? PresentationDisplayMode.Windowed
                : mode == FullScreenMode.ExclusiveFullScreen
                    ? PresentationDisplayMode.Fullscreen
                    : PresentationDisplayMode.BorderlessFullscreen;
        }

        public static bool IsPortrait(PresentationLayoutMode mode, int width, int height)
        {
            return mode == PresentationLayoutMode.Portrait ||
                (mode == PresentationLayoutMode.Auto && height > width);
        }

        public static List<Vector2Int> DistinctResolutions(IEnumerable<Resolution> resolutions, int currentWidth, int currentHeight)
        {
            List<Vector2Int> result = resolutions
                .Select(resolution => new Vector2Int(resolution.width, resolution.height))
                .Distinct()
                .OrderBy(resolution => resolution.x * resolution.y)
                .ThenBy(resolution => resolution.x)
                .ToList();
            Vector2Int current = new Vector2Int(currentWidth, currentHeight);
            if (!result.Contains(current))
            {
                result.Add(current);
            }
            return result.OrderBy(resolution => resolution.x * resolution.y).ThenBy(resolution => resolution.x).ToList();
        }
    }

    /// <summary>Local-calendar grouping for persisted and live conversation entries.</summary>
    public static class PresentationHistoryTime
    {
        public static bool TryGetLocalTime(string value, out DateTime localTime)
        {
            localTime = default(DateTime);
            if (string.IsNullOrWhiteSpace(value)) return false;

            if (DateTimeOffset.TryParse(value, out DateTimeOffset offset))
            {
                localTime = offset.LocalDateTime;
                return true;
            }
            if (DateTime.TryParse(value, out DateTime timestamp))
            {
                localTime = timestamp.Kind == DateTimeKind.Utc ? timestamp.ToLocalTime() : timestamp;
                return true;
            }
            return false;
        }
    }
}
