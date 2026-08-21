using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>Camera-owned background behind the direct-rendered avatar.</summary>
    public sealed class AvatarDirectBackgroundRenderer
    {
        private const int BackgroundLayer = 31;
        private const float CoverOverscanPixels = 2f;
        private readonly Camera avatarCamera;
        private readonly Camera backgroundCamera;
        private readonly GameObject imagePlane;
        private readonly Material imageMaterial;
        private AvatarViewerBackground background;
        private Texture imageTexture;

        public Camera BackgroundCamera => backgroundCamera;
        public bool IsBedroomImageActive => imagePlane != null && imagePlane.activeSelf;
        public Texture AssignedImageTexture => imageMaterial != null ? imageMaterial.mainTexture : null;
        public int ImageLayer => imagePlane != null ? imagePlane.layer : -1;

        public AvatarDirectBackgroundRenderer(Camera camera)
        {
            avatarCamera = camera;
            GameObject backgroundCameraObject = new GameObject("AIFren Avatar Background Camera");
            backgroundCamera = backgroundCameraObject.AddComponent<Camera>();
            backgroundCamera.orthographic = true;
            backgroundCamera.orthographicSize = .5f;
            backgroundCamera.clearFlags = CameraClearFlags.SolidColor;
            backgroundCamera.depth = avatarCamera.depth - 1f;
            backgroundCamera.cullingMask = 1 << BackgroundLayer;
            imagePlane = GameObject.CreatePrimitive(PrimitiveType.Quad);
            imagePlane.name = "AIFren Avatar Bedroom Background";
            imagePlane.layer = BackgroundLayer;
            DestroyObject(imagePlane.GetComponent<Collider>());
            imageMaterial = new Material(Shader.Find("Unlit/Texture"));
            imagePlane.GetComponent<MeshRenderer>().sharedMaterial = imageMaterial;
            imagePlane.SetActive(false);
            avatarCamera.cullingMask &= ~(1 << BackgroundLayer);
        }

        public void Set(AvatarViewerBackground value, Texture image)
        {
            bool changed = background != value || imageTexture != image;
            background = value;
            imageTexture = image;
            backgroundCamera.enabled = true;
            bool imageBackground = background == AvatarViewerBackground.Bedroom || background == AvatarViewerBackground.CustomImage;
            imagePlane.SetActive(imageBackground && imageTexture != null);
            if (imagePlane.activeSelf) imageMaterial.mainTexture = imageTexture;
            if (changed && imageBackground)
            {
                if (imageTexture == null) Debug.LogWarning("AIFren direct image background texture was not found.");
                else Debug.Log(string.Format(
                    "[AIFren Avatar] direct image background assigned {0} ({1}x{2}), layer {3}, background camera depth {4:F0}.",
                    imageTexture.name, imageTexture.width, imageTexture.height, BackgroundLayer, backgroundCamera.depth));
            }
            ApplySolidBackground();
            UpdatePlacement();
        }

        public void SetVisible(bool visible)
        {
            if (backgroundCamera != null) backgroundCamera.enabled = visible;
            if (imagePlane != null) imagePlane.SetActive(visible &&
                (background == AvatarViewerBackground.Bedroom || background == AvatarViewerBackground.CustomImage) && imageTexture != null);
        }

        public void UpdatePlacement()
        {
            if (!imagePlane.activeSelf || backgroundCamera == null) return;
            // Match the direct avatar camera before fitting. This avoids an
            // old camera aspect surviving a display/window transition.
            backgroundCamera.aspect = avatarCamera.aspect;
            float viewportAspect = Mathf.Max(.1f, backgroundCamera.aspect);
            float sourceAspect = imageTexture != null
                ? imageTexture.width / (float)Mathf.Max(1, imageTexture.height)
                : viewportAspect;
            float width = sourceAspect > viewportAspect ? sourceAspect : viewportAspect;
            float height = sourceAspect > viewportAspect ? 1f : viewportAspect / sourceAspect;
            int viewportWidth = Mathf.Max(1, backgroundCamera.pixelWidth > 0 ? backgroundCamera.pixelWidth : Screen.width);
            int viewportHeight = Mathf.Max(1, backgroundCamera.pixelHeight > 0 ? backgroundCamera.pixelHeight : Screen.height);
            // Exact mathematical cover can land on a fractional raster edge.
            // Uniformly overscan by two physical pixels on the shorter axis,
            // preserving aspect ratio while guaranteeing every viewport edge.
            float overscan = 1f + CoverOverscanPixels / Mathf.Min(viewportWidth, viewportHeight);
            imagePlane.transform.position = new Vector3(0f, 0f, 10f);
            // Unity's built-in Quad faces a default camera at -Z. Rotating it
            // 180 degrees culled the bedroom image from the background camera.
            imagePlane.transform.rotation = Quaternion.identity;
            imagePlane.transform.localScale = new Vector3(width * overscan, height * overscan, 1f);
        }

        public void Dispose()
        {
            if (imagePlane != null) DestroyObject(imagePlane);
            if (backgroundCamera != null) DestroyObject(backgroundCamera.gameObject);
            if (imageMaterial != null) DestroyObject(imageMaterial);
        }

        private void ApplySolidBackground()
        {
            if (backgroundCamera == null) return;
            backgroundCamera.backgroundColor = background == AvatarViewerBackground.NeutralGrey
                ? new Color(.34f, .34f, .36f, 1f)
                : new Color(.96f, .96f, .94f, 1f);
        }

        private static void DestroyObject(Object value)
        {
            if (value == null) return;
            if (Application.isPlaying) Object.Destroy(value);
            else Object.DestroyImmediate(value);
        }
    }
}
