using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    public enum AvatarPresentationFramingField
    {
        Zoom,
        HorizontalPan,
        VerticalPan
    }

    public struct AvatarPresentationFramingValues
    {
        public float zoom;
        public float panX;
        public float panY;
    }

    /// <summary>
    /// Owns the user-adjustable presentation framing values and their local
    /// persistence. Authored crop, alignment, and default zoom remain in
    /// AvatarConfiguration and are never reconstructed from a RawImage.
    /// </summary>
    public sealed class AvatarPresentationFramingState
    {
        private const string PortraitZoomKey = "AIFren.AvatarScalePortrait";
        private const string PortraitHorizontalKey = "AIFren.AvatarHorizontalPortrait";
        private const string PortraitVerticalKey = "AIFren.AvatarVerticalPortrait";
        private const string LandscapeZoomKey = "AIFren.AvatarScaleLandscape";
        private const string LandscapeHorizontalKey = "AIFren.AvatarHorizontalLandscape";
        private const string LandscapeVerticalKey = "AIFren.AvatarVerticalLandscape";

        private readonly AvatarConfiguration configuration;
        private AvatarPresentationFramingValues portrait;
        private AvatarPresentationFramingValues landscape;

        private AvatarPresentationFramingState(AvatarConfiguration configuration)
        {
            this.configuration = configuration ?? new AvatarConfiguration();
            portrait = LoadValues(true);
            landscape = LoadValues(false);
        }

        public static AvatarPresentationFramingState Load(AvatarConfiguration configuration)
        {
            return new AvatarPresentationFramingState(configuration);
        }

        public AvatarPresentationFramingValues GetValues(bool isPortrait)
        {
            return isPortrait ? portrait : landscape;
        }

        public float GetValue(bool isPortrait, AvatarPresentationFramingField field)
        {
            AvatarPresentationFramingValues values = GetValues(isPortrait);
            return field == AvatarPresentationFramingField.Zoom
                ? values.zoom
                : field == AvatarPresentationFramingField.HorizontalPan
                    ? values.panX
                    : values.panY;
        }

        /// <summary>
        /// Updates one value and persists it. Retained for simple callers; an
        /// interactive framing session should use SetValues(..., false) and
        /// Commit so Cancel has no durable side effect.
        /// </summary>
        public void SetValue(bool isPortrait, AvatarPresentationFramingField field, float value)
        {
            SetValue(isPortrait, field, value, true);
        }

        public void SetValue(bool isPortrait, AvatarPresentationFramingField field, float value, bool persist)
        {
            AvatarPresentationFramingValues values = GetValues(isPortrait);
            if (field == AvatarPresentationFramingField.Zoom)
            {
                values.zoom = ClampZoom(isPortrait, value);
            }
            else if (field == AvatarPresentationFramingField.HorizontalPan)
            {
                values.panX = Mathf.Clamp(value, -1f, 1f);
            }
            else
            {
                values.panY = Mathf.Clamp(value, -1f, 1f);
            }
            SetValues(isPortrait, values, persist);
        }

        public void Reset(bool isPortrait)
        {
            Reset(isPortrait, true);
        }

        public void Reset(bool isPortrait, bool persist)
        {
            SetValues(isPortrait, AuthoredDefaults(isPortrait), persist);
        }

        /// <summary>Applies a complete framing tuple, optionally durably.</summary>
        public void SetValues(bool isPortrait, AvatarPresentationFramingValues values, bool persist)
        {
            values.zoom = ClampZoom(isPortrait, values.zoom);
            values.panX = Mathf.Clamp(values.panX, -1f, 1f);
            values.panY = Mathf.Clamp(values.panY, -1f, 1f);
            StoreValues(isPortrait, values);
            if (persist) SaveValues(isPortrait);
        }

        /// <summary>Persists the current already-applied tuple for one orientation.</summary>
        public void Commit(bool isPortrait)
        {
            SaveValues(isPortrait);
        }

        public void ResetAll()
        {
            Reset(true);
            Reset(false);
        }

        public Rect Resolve(bool isPortrait)
        {
            AvatarPresentationFramingValues values = GetValues(isPortrait);
            AvatarCrop crop = isPortrait ? configuration.portraitUiCrop : configuration.landscapeUiCrop;
            return AvatarUiFraming.Resolve(
                crop,
                configuration.PresentationAlignment(isPortrait),
                values.zoom,
                values.panX,
                values.panY
            );
        }

        public static void DeleteAllPersisted()
        {
            PlayerPrefs.DeleteKey(PortraitZoomKey);
            PlayerPrefs.DeleteKey(PortraitHorizontalKey);
            PlayerPrefs.DeleteKey(PortraitVerticalKey);
            PlayerPrefs.DeleteKey(LandscapeZoomKey);
            PlayerPrefs.DeleteKey(LandscapeHorizontalKey);
            PlayerPrefs.DeleteKey(LandscapeVerticalKey);
        }

        private AvatarPresentationFramingValues LoadValues(bool isPortrait)
        {
            AvatarPresentationFramingValues defaults = AuthoredDefaults(isPortrait);
            return new AvatarPresentationFramingValues
            {
                zoom = ClampZoom(isPortrait, PlayerPrefs.GetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.Zoom), defaults.zoom)),
                panX = Mathf.Clamp(PlayerPrefs.GetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.HorizontalPan), 0f), -1f, 1f),
                panY = Mathf.Clamp(PlayerPrefs.GetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.VerticalPan), 0f), -1f, 1f)
            };
        }

        private AvatarPresentationFramingValues AuthoredDefaults(bool isPortrait)
        {
            return new AvatarPresentationFramingValues
            {
                zoom = ClampZoom(isPortrait, configuration.PresentationDefaultZoom(isPortrait)),
                panX = 0f,
                panY = 0f
            };
        }

        private float ClampZoom(bool isPortrait, float zoom)
        {
            AvatarCrop crop = isPortrait ? configuration.portraitUiCrop : configuration.landscapeUiCrop;
            return Mathf.Clamp(zoom, AvatarUiFraming.MinimumZoom(crop), AvatarUiFraming.MaximumZoom);
        }

        private void StoreValues(bool isPortrait, AvatarPresentationFramingValues values)
        {
            if (isPortrait) portrait = values;
            else landscape = values;
        }

        private void SaveValues(bool isPortrait)
        {
            AvatarPresentationFramingValues values = GetValues(isPortrait);
            PlayerPrefs.SetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.Zoom), values.zoom);
            PlayerPrefs.SetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.HorizontalPan), values.panX);
            PlayerPrefs.SetFloat(KeyFor(isPortrait, AvatarPresentationFramingField.VerticalPan), values.panY);
            PlayerPrefs.Save();
        }

        private static string KeyFor(bool isPortrait, AvatarPresentationFramingField field)
        {
            if (isPortrait)
            {
                return field == AvatarPresentationFramingField.Zoom
                    ? PortraitZoomKey
                    : field == AvatarPresentationFramingField.HorizontalPan ? PortraitHorizontalKey : PortraitVerticalKey;
            }

            return field == AvatarPresentationFramingField.Zoom
                ? LandscapeZoomKey
                : field == AvatarPresentationFramingField.HorizontalPan ? LandscapeHorizontalKey : LandscapeVerticalKey;
        }
    }
}
