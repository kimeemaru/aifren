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
    public sealed class AvatarPresentationTransform
    {
        public const float MaximumScale = 8f;
        // A scaled full-view image needs half of its scale in either direction
        // to bring either original edge to the viewport center. This preserves
        // the complete direct-view composition range at the maximum zoom.
        public const float MaximumTranslation = MaximumScale * .5f;
        // Normalized to the presentation container.  These are authored
        // layout values, not camera or texture coordinates.
        public float x;
        public float y;
        public float scale = 1f;

        public bool IsValid()
        {
            return scale >= 1f && scale <= MaximumScale &&
                x >= -MaximumTranslation && x <= MaximumTranslation &&
                y >= -MaximumTranslation && y <= MaximumTranslation;
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
        // animation excursions. Presentation composition belongs to the UI below.
        public float fullBodyCameraPadding = 1.08f;
        // The complete padded render is supersampled before this presentation
        // transform enlarges it inside a clipped UI container.
        public float renderTextureSupersample = 1.35f;
        // Portrait and landscape keep independently authored composition. The
        // RawImage always samples the entire RenderTexture (uvRect 0..1).
        public AvatarPresentationTransform landscapePresentation = new AvatarPresentationTransform { x = 0f, y = 0f, scale = 1f };
        public AvatarPresentationTransform portraitPresentation = new AvatarPresentationTransform { x = 0f, y = 0f, scale = 1f };
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

        public AvatarPresentationTransform PresentationTransform(bool portrait)
        {
            return portrait ? portraitPresentation : landscapePresentation;
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

            if (landscapePresentation == null || portraitPresentation == null ||
                !landscapePresentation.IsValid() || !portraitPresentation.IsValid())
            {
                error = "Avatar presentation transforms must use a supported scale and container translation.";
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
            return RequiredCameraDistance(bounds, Vector3.forward, verticalFieldOfView, aspect, padding);
        }

        /// <summary>
        /// Fits every corner of the complete renderer bounds to the actual
        /// RenderTexture aspect. This avoids treating a tall humanoid as a
        /// padded sphere, which wastes a large amount of the capture.
        /// </summary>
        public static float RequiredCameraDistance(Bounds bounds, Vector3 cameraForward, float verticalFieldOfView, float aspect, float padding)
        {
            float halfVerticalRadians = Mathf.Deg2Rad * verticalFieldOfView * 0.5f;
            float verticalTangent = Mathf.Max(.0001f, Mathf.Tan(halfVerticalRadians));
            float horizontalTangent = Mathf.Max(.0001f, verticalTangent * Mathf.Max(.1f, aspect));
            Quaternion cameraRotation = Quaternion.LookRotation(cameraForward.normalized, Vector3.up);
            Vector3 right = cameraRotation * Vector3.right;
            Vector3 up = cameraRotation * Vector3.up;
            float safePadding = Mathf.Max(1f, padding);
            float requiredDistance = .01f;

            for (int x = -1; x <= 1; x += 2)
            for (int y = -1; y <= 1; y += 2)
            for (int z = -1; z <= 1; z += 2)
            {
                Vector3 offset = Vector3.Scale(bounds.extents, new Vector3(x, y, z));
                float depth = Vector3.Dot(offset, cameraForward.normalized);
                requiredDistance = Mathf.Max(requiredDistance,
                    Mathf.Abs(Vector3.Dot(offset, right)) * safePadding / horizontalTangent - depth,
                    Mathf.Abs(Vector3.Dot(offset, up)) * safePadding / verticalTangent - depth);
            }

            return requiredDistance;
        }
    }

    /// <summary>Derived render-target sizing for high-density full-avatar presentation.</summary>
    public static class AvatarRenderQuality
    {
        public const int MaximumRenderTextureDimension = 3072;

        public static Vector2Int RequiredRenderTextureSize(Vector2 presentationPixels, float presentationScale, float supersample)
        {
            float safeWidth = Mathf.Max(1f, presentationPixels.x);
            float safeHeight = Mathf.Max(1f, presentationPixels.y);
            float safeScale = Mathf.Max(1f, presentationScale);
            float safeSupersample = Mathf.Max(1f, supersample);
            return new Vector2Int(
                Mathf.CeilToInt(safeWidth * safeScale * safeSupersample),
                Mathf.CeilToInt(safeHeight * safeScale * safeSupersample)
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

    }
}
