using System;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Frontend-only presentation defaults. This is deliberately separate from
    /// the Python character data and can be replaced without touching backend
    /// persistence.
    /// </summary>
    [Serializable]
    public sealed class CompanionPresentationConfiguration
    {
        public const string DefaultBackgroundResourcePath = "LocalBackground/background";

        public string backgroundResourcePath = DefaultBackgroundResourcePath;
        public float defaultRevealWordsPerSecond = 7f;
        public Color backgroundTopColor = new Color(0.12f, 0.16f, 0.25f, 1f);
        public Color backgroundBottomColor = new Color(0.32f, 0.20f, 0.36f, 1f);

        public static CompanionPresentationConfiguration Load()
        {
            CompanionPresentationConfiguration configuration = new CompanionPresentationConfiguration();
            TextAsset configFile = Resources.Load<TextAsset>("CompanionPresentationConfig");

            if (configFile != null && !string.IsNullOrWhiteSpace(configFile.text))
            {
                JsonUtility.FromJsonOverwrite(configFile.text, configuration);
            }

            return configuration;
        }

        public bool IsValid(out string error)
        {
            if (defaultRevealWordsPerSecond <= 0f)
            {
                error = "Dialogue reveal speed must be greater than zero.";
                return false;
            }

            error = null;
            return true;
        }
    }
}
