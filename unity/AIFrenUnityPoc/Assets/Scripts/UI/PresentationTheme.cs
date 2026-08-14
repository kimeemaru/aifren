using System;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public enum PresentationThemeMode { Light, Dark }

    /// <summary>Central palette and typography values for the companion presentation.</summary>
    [Serializable]
    public sealed class PresentationThemeDefinition
    {
        public PresentationThemeMode mode;
        public Color backgroundTint;
        public Color surface;
        public Color surfaceStrong;
        public Color surfaceMuted;
        public Color control;
        public Color controlHover;
        public Color controlPressed;
        public Color mutedText;
        public Color sectionHeader;
        public Color disabledText;
        public Color disabledControl;
        public Color sliderTrack;
        public Color sliderFill;
        public Color outline;
        public Color accent;
        public Color accentPink;
        public Color text;
        public Color secondaryText;
        public Color userText;
        public Color statusReady;
        public Color statusThinking;
        public Color statusSpeaking;
        public Color statusError;
    }

    public static class PresentationThemes
    {
        public const string PreferenceKey = "AIFren.PresentationTheme";

        public static readonly PresentationThemeDefinition Light = new PresentationThemeDefinition
        {
            mode = PresentationThemeMode.Light,
            backgroundTint = new Color(1f, 0.91f, 0.98f, 0.035f),
            surface = new Color(0.98f, 0.96f, 1f, 0.88f),
            surfaceStrong = new Color(0.91f, 0.84f, 0.97f, 0.96f),
            surfaceMuted = new Color(0.47f, 0.33f, 0.68f, 0.14f),
            control = new Color(0.84f, 0.76f, 0.94f, 0.98f),
            controlHover = new Color(0.82f, 0.73f, 0.93f, 1f),
            controlPressed = new Color(0.70f, 0.57f, 0.87f, 1f),
            mutedText = new Color(0.40f, 0.32f, 0.54f, 1f),
            sectionHeader = new Color(0.38f, 0.17f, 0.68f, 1f),
            disabledText = new Color(0.39f, 0.34f, 0.48f, 0.70f),
            disabledControl = new Color(0.72f, 0.68f, 0.78f, 0.66f),
            sliderTrack = new Color(0.52f, 0.42f, 0.66f, 0.45f),
            sliderFill = new Color(0.55f, 0.28f, 0.88f, 1f),
            outline = new Color(0.48f, 0.30f, 0.74f, 0.48f),
            accent = new Color(0.52f, 0.32f, 0.92f, 1f),
            accentPink = new Color(1f, 0.42f, 0.68f, 1f),
            text = new Color(0.16f, 0.10f, 0.25f, 1f),
            secondaryText = new Color(0.34f, 0.27f, 0.46f, 1f),
            userText = new Color(0.25f, 0.20f, 0.52f, 1f),
            statusReady = new Color(0.18f, 0.58f, 0.36f, 1f),
            statusThinking = new Color(0.72f, 0.38f, 0.80f, 1f),
            statusSpeaking = new Color(0.28f, 0.55f, 0.92f, 1f),
            statusError = new Color(0.80f, 0.24f, 0.36f, 1f),
        };

        public static readonly PresentationThemeDefinition Dark = new PresentationThemeDefinition
        {
            mode = PresentationThemeMode.Dark,
            backgroundTint = new Color(0.05f, 0.025f, 0.13f, 0.44f),
            surface = new Color(0.10f, 0.065f, 0.19f, 0.82f),
            surfaceStrong = new Color(0.075f, 0.045f, 0.14f, 0.93f),
            surfaceMuted = new Color(0.42f, 0.25f, 0.62f, 0.42f),
            control = new Color(0.19f, 0.11f, 0.30f, 0.94f),
            controlHover = new Color(0.29f, 0.17f, 0.43f, 1f),
            controlPressed = new Color(0.40f, 0.23f, 0.59f, 1f),
            mutedText = new Color(0.61f, 0.54f, 0.72f, 1f),
            sectionHeader = new Color(0.84f, 0.58f, 1f, 1f),
            disabledText = new Color(0.54f, 0.48f, 0.61f, 0.72f),
            disabledControl = new Color(0.18f, 0.14f, 0.24f, 0.70f),
            sliderTrack = new Color(0.46f, 0.30f, 0.60f, 0.54f),
            sliderFill = new Color(0.68f, 0.42f, 1f, 1f),
            outline = new Color(0.80f, 0.47f, 0.96f, 0.84f),
            accent = new Color(0.62f, 0.38f, 1f, 1f),
            accentPink = new Color(1f, 0.40f, 0.65f, 1f),
            text = new Color(0.97f, 0.94f, 1f, 1f),
            secondaryText = new Color(0.78f, 0.72f, 0.89f, 1f),
            userText = new Color(0.76f, 0.84f, 1f, 1f),
            statusReady = new Color(0.49f, 0.91f, 0.68f, 1f),
            statusThinking = new Color(0.94f, 0.61f, 0.98f, 1f),
            statusSpeaking = new Color(0.48f, 0.75f, 1f, 1f),
            statusError = new Color(1f, 0.43f, 0.50f, 1f),
        };

        public static PresentationThemeDefinition Load()
        {
            return PlayerPrefs.GetString(PreferenceKey, "dark") == "light" ? Light : Dark;
        }

        public static void Save(PresentationThemeMode mode)
        {
            PlayerPrefs.SetString(PreferenceKey, mode == PresentationThemeMode.Light ? "light" : "dark");
            PlayerPrefs.Save();
        }
    }
}
