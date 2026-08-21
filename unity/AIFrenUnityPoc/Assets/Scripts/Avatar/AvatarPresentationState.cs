using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    public struct AvatarPresentationValues
    {
        public float x;
        public float y;
        public float scale;
    }

    /// <summary>Persistent, presentation-only transforms for each layout.</summary>
    public sealed class AvatarPresentationState
    {
        private const string PortraitXKey = "AIFren.AvatarPresentation.Portrait.X";
        private const string PortraitYKey = "AIFren.AvatarPresentation.Portrait.Y";
        private const string PortraitScaleKey = "AIFren.AvatarPresentation.Portrait.Scale";
        private const string LandscapeXKey = "AIFren.AvatarPresentation.Landscape.X";
        private const string LandscapeYKey = "AIFren.AvatarPresentation.Landscape.Y";
        private const string LandscapeScaleKey = "AIFren.AvatarPresentation.Landscape.Scale";

        private readonly AvatarConfiguration configuration;
        private AvatarPresentationValues portrait;
        private AvatarPresentationValues landscape;

        private AvatarPresentationState(AvatarConfiguration configuration)
        {
            this.configuration = configuration ?? new AvatarConfiguration();
            portrait = LoadValues(true);
            landscape = LoadValues(false);
        }

        public static AvatarPresentationState Load(AvatarConfiguration configuration) => new AvatarPresentationState(configuration);

        public static void DeletePersistedValues()
        {
            foreach (bool isPortrait in new[] { true, false })
            {
                PlayerPrefs.DeleteKey(Key(isPortrait, 'x'));
                PlayerPrefs.DeleteKey(Key(isPortrait, 'y'));
                PlayerPrefs.DeleteKey(Key(isPortrait, 's'));
            }
            PlayerPrefs.Save();
        }

        public AvatarPresentationValues GetValues(bool isPortrait) => isPortrait ? portrait : landscape;

        public void SetValues(bool isPortrait, AvatarPresentationValues values, bool persist)
        {
            values = Normalize(values);
            if (isPortrait) portrait = values; else landscape = values;
            if (persist) Commit(isPortrait);
        }

        public void Reset(bool isPortrait, bool persist)
        {
            AvatarPresentationTransform authored = configuration.PresentationTransform(isPortrait);
            SetValues(isPortrait, new AvatarPresentationValues
            {
                x = authored != null ? authored.x : 0f,
                y = authored != null ? authored.y : 0f,
                scale = authored != null ? authored.scale : 1f
            }, persist);
        }

        public void Commit(bool isPortrait)
        {
            AvatarPresentationValues values = GetValues(isPortrait);
            PlayerPrefs.SetFloat(Key(isPortrait, 'x'), values.x);
            PlayerPrefs.SetFloat(Key(isPortrait, 'y'), values.y);
            PlayerPrefs.SetFloat(Key(isPortrait, 's'), values.scale);
            PlayerPrefs.Save();
        }

        private AvatarPresentationValues LoadValues(bool isPortrait)
        {
            AvatarPresentationTransform authored = configuration.PresentationTransform(isPortrait);
            return Normalize(new AvatarPresentationValues
            {
                x = PlayerPrefs.GetFloat(Key(isPortrait, 'x'), authored != null ? authored.x : 0f),
                y = PlayerPrefs.GetFloat(Key(isPortrait, 'y'), authored != null ? authored.y : 0f),
                scale = PlayerPrefs.GetFloat(Key(isPortrait, 's'), authored != null ? authored.scale : 1f)
            });
        }

        private static AvatarPresentationValues Normalize(AvatarPresentationValues values)
        {
            values.x = ClampFinite(values.x, -AvatarPresentationTransform.MaximumTranslation,
                AvatarPresentationTransform.MaximumTranslation, 0f);
            values.y = ClampFinite(values.y, -AvatarPresentationTransform.MaximumTranslation,
                AvatarPresentationTransform.MaximumTranslation, 0f);
            values.scale = ClampFinite(values.scale, 1f, AvatarPresentationTransform.MaximumScale, 1f);
            return values;
        }

        private static float ClampFinite(float value, float minimum, float maximum, float fallback)
        {
            return float.IsNaN(value) || float.IsInfinity(value)
                ? fallback
                : Mathf.Clamp(value, minimum, maximum);
        }

        private static string Key(bool portrait, char value)
        {
            if (portrait) return value == 'x' ? PortraitXKey : value == 'y' ? PortraitYKey : PortraitScaleKey;
            return value == 'x' ? LandscapeXKey : value == 'y' ? LandscapeYKey : LandscapeScaleKey;
        }
    }
}
