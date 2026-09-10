using TMPro;
using UnityEngine;
using UnityEngine.TextCore.LowLevel;

namespace AIFren.UnityPoc.UI
{
    // One reversible subtitle-only treatment. Never persists or resets user preferences.
    internal static class SubtitleStyle
    {
        internal const float FontSize = 34f, MinimumFontSize = 28f;
        internal const float OutlineWidth = .18f;
        internal const float WordFadeSeconds = .18f, PageDwellSeconds = .18f;
        internal const float PageFadeSeconds = .07f, FinalDwellSeconds = .45f, FinalFadeSeconds = .25f;
        internal const float SideInset = .05f, BottomInset = .045f, RegionHeight = .145f;
        internal const int MaximumPageWords = 24;
        internal static readonly Color Face = new Color(.99f, .975f, .95f, 1f);
        internal static readonly Color Edge = new Color(.10f, .075f, .13f, 1f);

        internal static TMP_FontAsset CreateFont(TMP_FontAsset fallback)
        {
            Font source = Resources.Load<Font>("Subtitles/Nunito-SemiBold");
            if (source == null) return null;
            TMP_FontAsset asset = TMP_FontAsset.CreateFontAsset(source, 90, 18,
                GlyphRenderMode.SDFAA, 1024, 1024, AtlasPopulationMode.Dynamic, true);
            asset.name = "AIFren Subtitle Nunito SemiBold SDF";
            if (fallback != null) asset.fallbackFontAssetTable = new System.Collections.Generic.List<TMP_FontAsset> { fallback };
            return asset;
        }

        internal static void Apply(TMP_Text text, Material material, Color? face = null)
        {
            text.color = face ?? Face; text.fontStyle = FontStyles.Normal;
            text.lineSpacing = 1f; text.paragraphSpacing = 0f;
            if (material == null) return;
            material.SetColor(ShaderUtilities.ID_FaceColor, Color.white);
            material.EnableKeyword(ShaderUtilities.Keyword_Outline);
            material.SetColor(ShaderUtilities.ID_OutlineColor, Edge);
            material.SetFloat(ShaderUtilities.ID_OutlineWidth, OutlineWidth);
            // TMP centers its outline on the original SDF contour. Expand by
            // the same amount so the edge grows outward, retaining the white
            // interior of thin glyphs at the normal portrait reading size.
            material.SetFloat(ShaderUtilities.ID_FaceDilate, OutlineWidth);
            material.SetFloat(ShaderUtilities.ID_OutlineSoftness, .035f);
            material.EnableKeyword(ShaderUtilities.Keyword_Underlay);
            material.SetColor(ShaderUtilities.ID_UnderlayColor, new Color(.03f, .02f, .045f, .38f));
            material.SetFloat(ShaderUtilities.ID_UnderlayOffsetX, .12f);
            material.SetFloat(ShaderUtilities.ID_UnderlayOffsetY, -.22f);
            material.SetFloat(ShaderUtilities.ID_UnderlayDilate, 0f);
            material.SetFloat(ShaderUtilities.ID_UnderlaySoftness, .18f);
            text.UpdateMeshPadding();
        }
    }
}
