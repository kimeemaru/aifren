using System.Collections.Generic;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    public enum AvatarViewerBackground
    {
        LightNeutral,
        NeutralGrey,
        Bedroom,
        CustomImage
    }

    /// <summary>Local, layout-specific background selection for the avatar viewer.</summary>
    public sealed class AvatarViewerBackgroundState
    {
        private const string PortraitKey = "AIFren.AvatarViewerBackground.Portrait";
        private const string LandscapeKey = "AIFren.AvatarViewerBackground.Landscape";
        private const string PortraitCustomPathKey = "AIFren.AvatarViewerBackground.CustomPath.Portrait";
        private const string LandscapeCustomPathKey = "AIFren.AvatarViewerBackground.CustomPath.Landscape";

        private AvatarViewerBackground portrait;
        private AvatarViewerBackground landscape;
        private string portraitCustomPath;
        private string landscapeCustomPath;

        private AvatarViewerBackgroundState()
        {
            portrait = Load(true);
            landscape = Load(false);
            portraitCustomPath = PlayerPrefs.GetString(PortraitCustomPathKey, string.Empty);
            landscapeCustomPath = PlayerPrefs.GetString(LandscapeCustomPathKey, string.Empty);
        }

        public static AvatarViewerBackgroundState Load() => new AvatarViewerBackgroundState();

        public static void DeletePersistedValues()
        {
            PlayerPrefs.DeleteKey(PortraitKey);
            PlayerPrefs.DeleteKey(LandscapeKey);
            PlayerPrefs.DeleteKey(PortraitCustomPathKey);
            PlayerPrefs.DeleteKey(LandscapeCustomPathKey);
            PlayerPrefs.Save();
        }

        public AvatarViewerBackground Get(bool isPortrait) => isPortrait ? portrait : landscape;
        public string GetCustomPath(bool isPortrait) => isPortrait ? portraitCustomPath : landscapeCustomPath;

        public void Set(bool isPortrait, AvatarViewerBackground background, bool persist)
        {
            background = Normalize(background, isPortrait);
            if (isPortrait) portrait = background; else landscape = background;
            if (!persist) return;
            PlayerPrefs.SetInt(isPortrait ? PortraitKey : LandscapeKey, (int)background);
            PlayerPrefs.Save();
        }

        public void SetCustomPath(bool isPortrait, string path, bool persist)
        {
            if (isPortrait) portraitCustomPath = path ?? string.Empty; else landscapeCustomPath = path ?? string.Empty;
            if (!persist) return;
            PlayerPrefs.SetString(isPortrait ? PortraitCustomPathKey : LandscapeCustomPathKey, path ?? string.Empty);
            PlayerPrefs.Save();
        }

        /// <summary>Repairs selections whose managed image was deleted.</summary>
        public void RepairDeletedCustomPaths(ISet<string> deletedPaths, bool persist)
        {
            if (deletedPaths == null || deletedPaths.Count == 0) return;
            bool changed = false;
            if (portrait == AvatarViewerBackground.CustomImage && deletedPaths.Contains(portraitCustomPath))
            {
                portrait = AvatarViewerBackground.LightNeutral;
                portraitCustomPath = string.Empty;
                changed = true;
            }
            if (landscape == AvatarViewerBackground.CustomImage && deletedPaths.Contains(landscapeCustomPath))
            {
                landscape = AvatarViewerBackground.Bedroom;
                landscapeCustomPath = string.Empty;
                changed = true;
            }
            if (!persist || !changed) return;
            PlayerPrefs.SetInt(PortraitKey, (int)portrait);
            PlayerPrefs.SetInt(LandscapeKey, (int)landscape);
            PlayerPrefs.SetString(PortraitCustomPathKey, portraitCustomPath);
            PlayerPrefs.SetString(LandscapeCustomPathKey, landscapeCustomPath);
            PlayerPrefs.Save();
        }

        public static string Label(AvatarViewerBackground background)
        {
            return background == AvatarViewerBackground.LightNeutral ? "Light neutral" :
                background == AvatarViewerBackground.NeutralGrey ? "Neutral grey" :
                background == AvatarViewerBackground.Bedroom ? "Bedroom" : "Custom image";
        }

        private static AvatarViewerBackground Load(bool isPortrait)
        {
            AvatarViewerBackground fallback = isPortrait ? AvatarViewerBackground.LightNeutral : AvatarViewerBackground.Bedroom;
            return Normalize((AvatarViewerBackground)PlayerPrefs.GetInt(isPortrait ? PortraitKey : LandscapeKey, (int)fallback), isPortrait);
        }

        private static AvatarViewerBackground Normalize(AvatarViewerBackground background, bool isPortrait)
        {
            return background >= AvatarViewerBackground.LightNeutral && background <= AvatarViewerBackground.CustomImage
                ? background
                : isPortrait ? AvatarViewerBackground.LightNeutral : AvatarViewerBackground.Bedroom;
        }
    }
}
