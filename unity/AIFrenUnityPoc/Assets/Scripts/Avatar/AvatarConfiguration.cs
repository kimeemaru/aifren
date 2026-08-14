using System;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    [Serializable]
    public sealed class AvatarVector3
    {
        public float x;
        public float y;
        public float z;

        public Vector3 ToVector3()
        {
            return new Vector3(x, y, z);
        }
    }

    [Serializable]
    public sealed class AvatarVector2
    {
        public float x;
        public float y;

        public Vector2 ToVector2()
        {
            return new Vector2(x, y);
        }
    }

    [Serializable]
    public sealed class AvatarCrop
    {
        // Normalized RenderTexture coordinates. These crop only the UI image;
        // they never change the camera frustum or the rendered VRM body.
        public float x;
        public float y;
        public float width = 1f;
        public float height = 1f;

        public Rect ToRect()
        {
            return new Rect(x, y, width, height);
        }

        public bool IsValid()
        {
            return width > 0f && height > 0f && x >= 0f && y >= 0f &&
                x + width <= 1f && y + height <= 1f;
        }
    }

    [Serializable]
    public sealed class AvatarConfiguration
    {
        public const string DefaultResourcePath = "LocalCharacter/model";
        public string avatarResourcePath = DefaultResourcePath;
        public AvatarVector3 position = new AvatarVector3 { x = 0f, y = 0f, z = 0f };
        public AvatarVector3 rotationEuler = new AvatarVector3 { x = 0f, y = 0f, z = 0f };
        public float scale = 1.25f;
        public AvatarVector3 cameraOffset = new AvatarVector3 { x = 0f, y = 1.45f, z = -3.2f };
        public float lookAtHeight = 1.4f;
        public float fieldOfView = 25f;
        // Camera framing always contains the complete dynamic renderer bounds.
        // This extra margin covers arm gestures, sway, hair physics and similar
        // animation excursions. Presentation crops belong to the UI below.
        public float fullBodyCameraPadding = 1.30f;
        // Each displayed crop is rendered above its display resolution so UV
        // cropping does not turn a full-body safety render into a soft closeup.
        public float renderTextureSupersample = 1.15f;
        // The full render stays intact; portrait and landscape crop its RawImage
        // independently to create the close companion compositions. Crop x/y
        // stay geometrically centered; visual per-avatar correction is stored
        // separately so user pan remains a pure, resettable delta.
        public AvatarCrop landscapeUiCrop = new AvatarCrop { x = .20f, y = .2375f, width = .6f, height = .525f };
        public AvatarCrop portraitUiCrop = new AvatarCrop { x = .30f, y = .22f, width = .40f, height = .56f };
        public AvatarVector2 landscapeAlignmentOffset = new AvatarVector2 { x = 0f, y = .1625f };
        public AvatarVector2 portraitAlignmentOffset = new AvatarVector2 { x = 0f, y = .14f };
        // The zero-pan authored composition may have a deliberate default zoom.
        // Keep it alongside its crop and alignment instead of scattering 1f
        // defaults through startup, persistence, and Reset paths.
        public float landscapeDefaultZoom = 1f;
        public float portraitDefaultZoom = 1f;
        public float idleSwayDegrees = 1.2f;
        public float idleSwayCyclesPerSecond = 0.08f;
        public float relaxedArmDown = -0.55f;
        // Some imported characters use a different local-forward convention.
        // This offset is applied after the avatar has been aimed at its preview
        // camera, rather than relying on a fixed world-space rotation.
        public float facingYawOffset = 0f;
        public float keyLightIntensity = 0.65f;
        public float fillLightIntensity = 0.20f;
        public float ambientIntensity = 0.50f;
        public float reflectionIntensity = 0.35f;

        public static AvatarConfiguration Load()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();
            TextAsset configFile = Resources.Load<TextAsset>("CharacterAvatarConfig");

            if (configFile != null && !string.IsNullOrWhiteSpace(configFile.text))
            {
                JsonUtility.FromJsonOverwrite(configFile.text, configuration);
            }

            return configuration;
        }

        public Vector2 PresentationAlignment(bool portrait)
        {
            AvatarVector2 alignment = portrait ? portraitAlignmentOffset : landscapeAlignmentOffset;
            return alignment != null ? alignment.ToVector2() : Vector2.zero;
        }

        public float PresentationDefaultZoom(bool portrait)
        {
            return portrait ? portraitDefaultZoom : landscapeDefaultZoom;
        }

        public bool IsValid(out string error)
        {
            if (string.IsNullOrWhiteSpace(avatarResourcePath))
            {
                error = "Avatar configuration requires a Resources path.";
                return false;
            }

            if (scale <= 0f)
            {
                error = "Avatar configuration scale must be greater than zero.";
                return false;
            }

            if (fieldOfView <= 1f || fieldOfView >= 179f)
            {
                error = "Avatar camera field of view must be between 1 and 179.";
                return false;
            }

            if (fullBodyCameraPadding < 1f)
            {
                error = "Avatar full-body camera padding must be at least one.";
                return false;
            }

            if (renderTextureSupersample < 1f || renderTextureSupersample > 2f)
            {
                error = "Avatar render texture supersampling must be between one and two.";
                return false;
            }

            if (landscapeUiCrop == null || portraitUiCrop == null ||
                !landscapeUiCrop.IsValid() || !portraitUiCrop.IsValid())
            {
                error = "Avatar UI crop rectangles must stay within the rendered texture.";
                return false;
            }

            if (landscapeDefaultZoom < AvatarUiFraming.MinimumZoom(landscapeUiCrop) ||
                landscapeDefaultZoom > AvatarUiFraming.MaximumZoom ||
                portraitDefaultZoom < AvatarUiFraming.MinimumZoom(portraitUiCrop) ||
                portraitDefaultZoom > AvatarUiFraming.MaximumZoom)
            {
                error = "Avatar default zoom must stay within the supported framing range.";
                return false;
            }

            if (keyLightIntensity < 0f || fillLightIntensity < 0f || ambientIntensity < 0f || reflectionIntensity < 0f)
            {
                error = "Avatar lighting values cannot be negative.";
                return false;
            }

            error = null;
            return true;
        }
    }

    /// <summary>Bounds-aware camera math for the presentation avatar.</summary>
    public static class AvatarFraming
    {
        public static float RequiredCameraDistance(Bounds bounds, float verticalFieldOfView, float aspect, float padding)
        {
            float halfVerticalRadians = Mathf.Deg2Rad * verticalFieldOfView * 0.5f;
            float halfHorizontalRadians = Mathf.Atan(Mathf.Tan(halfVerticalRadians) * Mathf.Max(0.1f, aspect));
            float limitingHalfAngle = Mathf.Min(halfVerticalRadians, halfHorizontalRadians);
            float radius = Mathf.Max(0.01f, bounds.extents.magnitude * Mathf.Max(1f, padding));
            return radius / Mathf.Sin(limitingHalfAngle);
        }
    }

    /// <summary>Derived render-target sizing for aspect-correct UI cropping.</summary>
    public static class AvatarRenderQuality
    {
        public const int MaximumRenderTextureDimension = 3072;

        public static Vector2Int RequiredRenderTextureSize(Vector2 displayedCropPixels, AvatarCrop crop, float supersample)
        {
            float safeWidth = Mathf.Max(1f, displayedCropPixels.x);
            float safeHeight = Mathf.Max(1f, displayedCropPixels.y);
            float safeSupersample = Mathf.Max(1f, supersample);
            return new Vector2Int(
                Mathf.CeilToInt(safeWidth / Mathf.Max(.01f, crop.width) * safeSupersample),
                Mathf.CeilToInt(safeHeight / Mathf.Max(.01f, crop.height) * safeSupersample)
            );
        }

        /// <summary>Keeps presentation quality bounded on normal desktop GPUs.</summary>
        public static Vector2Int ClampToMaximumDimension(Vector2Int requested, int maximumDimension = MaximumRenderTextureDimension)
        {
            int safeMaximum = Mathf.Max(64, maximumDimension);
            int largestDimension = Mathf.Max(requested.x, requested.y);
            if (largestDimension <= safeMaximum) return requested;

            float scale = safeMaximum / (float)largestDimension;
            return new Vector2Int(
                Mathf.Max(64, Mathf.FloorToInt(requested.x * scale)),
                Mathf.Max(64, Mathf.FloorToInt(requested.y * scale))
            );
        }

        public static bool CropMatchesDisplayAspect(Vector2 displayedCropPixels, AvatarCrop crop, float tolerance = .015f)
        {
            if (displayedCropPixels.x <= 0f || displayedCropPixels.y <= 0f || crop == null || !crop.IsValid()) return false;
            float displayAspect = displayedCropPixels.x / displayedCropPixels.y;
            float cropAspect = crop.width / crop.height;
            return Mathf.Abs(displayAspect - cropAspect) <= tolerance;
        }
    }

    /// <summary>
    /// Resolves presentation-only UI sampling from the full padded avatar RT.
    /// The renderer/camera stays full-body; this class only returns a valid UV
    /// rectangle and the matching display aspect for a chosen composition.
    /// </summary>
    public static class AvatarUiFraming
    {
        public const float MaximumZoom = 3.5f;

        public static float MinimumZoom(AvatarCrop authoredCrop)
        {
            if (authoredCrop == null || !authoredCrop.IsValid()) return 1f;
            // At this zoom both authored dimensions expand to the complete RT.
            return Mathf.Clamp(Mathf.Min(authoredCrop.width, authoredCrop.height), .01f, 1f);
        }

        public static Rect Resolve(AvatarCrop authoredCrop, float zoom, float horizontalPan, float verticalPan)
        {
            Vector2 legacyAlignment = authoredCrop == null
                ? Vector2.zero
                : new Vector2(
                    authoredCrop.x + authoredCrop.width * .5f - .5f,
                    authoredCrop.y + authoredCrop.height * .5f - .5f
                );
            return Resolve(authoredCrop, legacyAlignment, zoom, horizontalPan, verticalPan);
        }

        public static Rect Resolve(AvatarCrop authoredCrop, Vector2 alignmentOffset, float zoom, float horizontalPan, float verticalPan)
        {
            if (authoredCrop == null || !authoredCrop.IsValid()) return new Rect(0f, 0f, 1f, 1f);

            float safeZoom = Mathf.Clamp(zoom, MinimumZoom(authoredCrop), MaximumZoom);
            float width = Mathf.Min(1f, authoredCrop.width / safeZoom);
            float height = Mathf.Min(1f, authoredCrop.height / safeZoom);
            // Canonical composition: literal RT center + hidden avatar alignment
            // + user pan. The alignment is never mutated by Reset or persistence.
            float defaultX = Mathf.Clamp(.5f + alignmentOffset.x, width * .5f, 1f - width * .5f);
            float defaultY = Mathf.Clamp(.5f + alignmentOffset.y, height * .5f, 1f - height * .5f);
            float centerX = ApplyPan(defaultX, width, horizontalPan);
            float centerY = ApplyPan(defaultY, height, verticalPan);
            return new Rect(centerX - width * .5f, centerY - height * .5f, width, height);
        }

        public static float DisplayAspect(Rect crop, int renderTextureWidth, int renderTextureHeight)
        {
            return Mathf.Max(.01f, crop.width * Mathf.Max(1, renderTextureWidth) /
                (crop.height * Mathf.Max(1, renderTextureHeight)));
        }

        /// <summary>
        /// True only when a crop at this zoom has remaining legal movement on
        /// the requested axis. At full-RT zoom there is intentionally no pan;
        /// callers must leave the stored user delta alone rather than letting
        /// an invisible drag accumulate and jump at the next zoom level.
        /// </summary>
        public static bool HasPanRange(AvatarCrop authoredCrop, float zoom, bool horizontal)
        {
            if (authoredCrop == null || !authoredCrop.IsValid()) return false;
            float safeZoom = Mathf.Clamp(zoom, MinimumZoom(authoredCrop), MaximumZoom);
            float dimension = horizontal
                ? Mathf.Min(1f, authoredCrop.width / safeZoom)
                : Mathf.Min(1f, authoredCrop.height / safeZoom);
            return 1f - dimension > .0001f;
        }

        private static float ApplyPan(float defaultCenter, float dimension, float pan)
        {
            float minimum = dimension * .5f;
            float maximum = 1f - minimum;
            if (maximum - minimum <= .0001f)
            {
                return .5f;
            }
            float clampedPan = Mathf.Clamp(pan, -1f, 1f);
            return clampedPan < 0f
                ? Mathf.Lerp(defaultCenter, minimum, -clampedPan)
                : Mathf.Lerp(defaultCenter, maximum, clampedPan);
        }
    }
}
