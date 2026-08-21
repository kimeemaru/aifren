using System;
using System.IO;
using System.Threading.Tasks;
using UniVRM10;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>
    /// Loads the active avatar configured in Resources/CharacterAvatarConfig.
    /// The VRM file is imported by UniVRM in the Unity editor; this component
    /// only instantiates its generated GameObject at runtime.
    /// </summary>
    public sealed class AvatarLoader : MonoBehaviour
    {
        public const string CustomModelPathPreference = "AIFren.AvatarModelPath";
        internal static void ClearCustomModelPathPreference()
        {
            PlayerPrefs.DeleteKey(CustomModelPathPreference);
            PlayerPrefs.Save();
        }
        public event Action<GameObject> AvatarLoaded;
        public event Action<string> AvatarLoadFailed;

        public GameObject ActiveAvatar { get; private set; }
        public string LastError { get; private set; } = string.Empty;
        public string ActiveModelPath { get; private set; } = string.Empty;
        public string LastLoadedModelName { get; private set; } = string.Empty;

        private Camera previewCamera;
        private Light keyLight;
        private Light fillLight;
        private RawImage previewSurface;
        private RenderTexture previewTexture;
        private float presentationRenderScale = 1f;
        private Vector2 previewViewportPixels;
        private bool hasPreviewViewportPixels;
        private bool previewIsPortrait;
        private AvatarConfiguration activeConfiguration;
        private Vector3 idleBasePosition;
        private Quaternion idleBaseRotation;
        private HumanPoseHandler humanPoseHandler;
        private HumanPose relaxedPose;
        private bool hasRelaxedPose;
        private bool loggedFullBodyFrustum;
        private float originalAmbientIntensity;
        private float originalReflectionIntensity;
        private bool savedRenderSettings;
        private AvatarAnimationController animationController;
        private Vector2Int lastLoggedPresentationTextureSize;
        private bool directPresentation = true;
        private AvatarPresentationValues directPresentationValues = new AvatarPresentationValues { scale = 1f };
        private AvatarViewerBackground directBackground = AvatarViewerBackground.LightNeutral;
        private Texture directBedroomTexture;
        private AvatarDirectBackgroundRenderer directBackgroundRenderer;
        private float directBaselineAspect = -1f;

        private void Start()
        {
            string savedPath = PlayerPrefs.GetString(CustomModelPathPreference, string.Empty);
            if (!string.IsNullOrWhiteSpace(savedPath) && File.Exists(savedPath))
                _ = LoadAvatarFromPathAsync(savedPath);
            else
            {
                if (!string.IsNullOrWhiteSpace(savedPath))
                    Debug.LogWarning("Saved avatar model is unavailable; using the bundled model.");
                LoadConfiguredAvatar();
            }
        }

        public bool LoadConfiguredAvatar()
        {
            AvatarConfiguration configuration = AvatarConfiguration.Load();

            if (!configuration.IsValid(out string validationError))
            {
                Fail(validationError);
                return false;
            }

            GameObject avatarPrefab = Resources.Load<GameObject>(configuration.avatarResourcePath);
            if (avatarPrefab == null)
            {
                Fail(
                    "VRM avatar was not found at Resources/" +
                    configuration.avatarResourcePath +
                    ". Put model.vrm in Assets/Resources/LocalCharacter/."
                );
                return false;
            }

            try
            {
                ActivateAvatar(Instantiate(avatarPrefab), configuration, "Bundled model");
                return true;
            }
            catch (Exception exception)
            {
                Fail("Unable to instantiate the VRM avatar: " + exception.Message);
                return false;
            }
        }

        /// <summary>Loads VRM 1.0 or migrates VRM 0.x through UniVRM's unified runtime API.</summary>
        public async Task<bool> LoadAvatarFromPathAsync(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path) ||
                !string.Equals(Path.GetExtension(path), ".vrm", StringComparison.OrdinalIgnoreCase))
            {
                Fail("Choose a readable .vrm file.");
                return false;
            }

            GameObject candidate = null;
            try
            {
                string metadataName = string.Empty;
                Vrm10Instance instance = await Vrm10.LoadPathAsync(path, canLoadVrm0X: true, showMeshes: true,
                    vrmMetaInformationCallback: (_, vrm10, vrm0) => metadataName = MetadataName(vrm10) ?? MetadataName(vrm0));
                if (instance == null) throw new InvalidOperationException("UniVRM returned no avatar instance.");
                candidate = instance.gameObject;
                if (candidate.GetComponentsInChildren<Renderer>(true).Length == 0)
                    throw new InvalidOperationException("The VRM contains no renderable avatar geometry.");
                ActivateAvatar(candidate, AvatarConfiguration.Load(), path);
                LastLoadedModelName = string.IsNullOrWhiteSpace(metadataName) ? Path.GetFileNameWithoutExtension(path) : metadataName.Trim();
                return true;
            }
            catch (Exception exception)
            {
                if (candidate != null && candidate != ActiveAvatar) Destroy(candidate);
                Fail("Could not load VRM: " + exception.Message);
                return false;
            }
        }

        private static string MetadataName(object metadata)
        {
            if (metadata == null) return null;
            foreach (string member in new[] { "name", "title", "Title", "Name" })
            {
                var field = metadata.GetType().GetField(member);
                string value = field != null ? field.GetValue(metadata) as string : null;
                if (!string.IsNullOrWhiteSpace(value)) return value;
                var property = metadata.GetType().GetProperty(member);
                value = property != null ? property.GetValue(metadata, null) as string : null;
                if (!string.IsNullOrWhiteSpace(value)) return value;
            }
            return null;
        }

        private void ActivateAvatar(GameObject avatar, AvatarConfiguration configuration, string source)
        {
            GameObject previous = ActiveAvatar;
            ActiveAvatar = avatar;
            ActiveAvatar.name = "Active VRM Avatar";
            ActiveAvatar.transform.position = configuration.position.ToVector3();
            ActiveAvatar.transform.rotation = Quaternion.Euler(configuration.rotationEuler.ToVector3());
            ActiveAvatar.transform.localScale = Vector3.one * configuration.scale;
            activeConfiguration = configuration;
            idleBasePosition = ActiveAvatar.transform.position;
            ConfigureRelaxedPose(configuration);
            ConfigurePreviewCamera(configuration);
            idleBaseRotation = ActiveAvatar.transform.rotation;
            animationController = gameObject.GetComponent<AvatarAnimationController>() ?? gameObject.AddComponent<AvatarAnimationController>();
            animationController.Configure(ActiveAvatar);
            ActiveModelPath = source;
            LastError = string.Empty;
            loggedFullBodyFrustum = false;
            AvatarLoaded?.Invoke(ActiveAvatar);
            if (previous != null && previous != ActiveAvatar) Destroy(previous);
        }

        private void ConfigurePreviewCamera(AvatarConfiguration configuration)
        {
            if (previewCamera == null)
            {
                GameObject cameraObject = new GameObject("AIFren Avatar Camera");
                previewCamera = cameraObject.AddComponent<Camera>();
                previewCamera.clearFlags = CameraClearFlags.SolidColor;
                previewCamera.backgroundColor = new Color(0f, 0f, 0f, 0f);
                previewCamera.fieldOfView = configuration.fieldOfView;
                previewCamera.allowHDR = false;
                previewCamera.allowMSAA = true;

                GameObject lightObject = new GameObject("AIFren Avatar Light");
                keyLight = lightObject.AddComponent<Light>();
                keyLight.type = LightType.Directional;
                keyLight.color = new Color(1f, 0.96f, 0.92f, 1f);
                keyLight.transform.rotation = Quaternion.Euler(48f, -32f, 0f);

                GameObject fillLightObject = new GameObject("AIFren Avatar Fill Light");
                fillLight = fillLightObject.AddComponent<Light>();
                fillLight.type = LightType.Directional;
                fillLight.color = new Color(0.82f, 0.88f, 1f, 1f);
                fillLight.transform.rotation = Quaternion.Euler(28f, 138f, 0f);
            }

            previewCamera.fieldOfView = configuration.fieldOfView;
            UpdatePreviewFraming();
            keyLight.intensity = configuration.keyLightIntensity;
            fillLight.intensity = configuration.fillLightIntensity;
            ConfigureSoftEnvironmentLighting(configuration);
            if (directPresentation) ApplyDirectPresentationView();
            else ConfigureRenderTexture();
        }

        private void ConfigureSoftEnvironmentLighting(AvatarConfiguration configuration)
        {
            if (!savedRenderSettings)
            {
                originalAmbientIntensity = RenderSettings.ambientIntensity;
                originalReflectionIntensity = RenderSettings.reflectionIntensity;
                savedRenderSettings = true;
            }

            // This isolated presentation scene only contains the avatar preview.
            // Lowering ambient/reflection contribution avoids clipping light VRM
            // clothing while the complementary key/fill lights keep the face visible.
            RenderSettings.ambientIntensity = configuration.ambientIntensity;
            RenderSettings.reflectionIntensity = configuration.reflectionIntensity;
        }

        public void SetPreviewSurface(RawImage surface)
        {
            previewSurface = surface;
            ConfigureRenderTexture();
        }

        /// <summary>
        /// Selects the direct screen camera or the retained RenderTexture path.
        /// The RenderTexture implementation remains available as a rollback.
        /// </summary>
        public void SetDirectPresentation(bool enabled)
        {
            if (directPresentation == enabled) return;
            directPresentation = enabled;
            directBaselineAspect = -1f;
            if (previewCamera == null) return;

            if (directPresentation)
            {
                ReleasePreviewTexture();
                previewCamera.targetTexture = null;
                EnsureDirectBackgroundRenderer();
                ApplyDirectPresentationView();
            }
            else
            {
                directBackgroundRenderer?.SetVisible(false);
                previewCamera.clearFlags = CameraClearFlags.SolidColor;
                previewCamera.usePhysicalProperties = false;
                previewCamera.lensShift = Vector2.zero;
                previewCamera.fieldOfView = activeConfiguration != null ? activeConfiguration.fieldOfView : previewCamera.fieldOfView;
                ConfigureRenderTexture();
            }
        }

        public void SetDirectPresentationValues(AvatarPresentationValues values)
        {
            directPresentationValues = values;
            if (directPresentation) ApplyDirectPresentationView();
        }

        public void SetDirectBackground(AvatarViewerBackground background, Texture bedroomTexture)
        {
            directBackground = background;
            directBedroomTexture = bedroomTexture;
            // Controller UI construction runs before this component's Start
            // method can create the preview camera. Retain the selection now;
            // ConfigurePreviewCamera applies it once the direct camera exists.
            if (!directPresentation || previewCamera == null) return;
            EnsureDirectBackgroundRenderer();
            directBackgroundRenderer.Set(directBackground, directBedroomTexture);
        }

        /// <summary>
        /// The UI controller owns orientation. Never infer it from the fitted
        /// RawImage because presentation transforms change its dimensions.
        /// </summary>
        public void SetPresentationOrientation(bool isPortrait)
        {
            if (previewIsPortrait == isPortrait) return;
            previewIsPortrait = isPortrait;
            if (directPresentation) ApplyDirectPresentationView(); else ConfigureRenderTexture();
        }

        /// <summary>
        /// Sets the stable presentation container used to size the full-body
        /// capture. UI layout never alters the camera's complete-body framing.
        /// </summary>
        public void SetPresentationViewportPixels(Vector2 pixels)
        {
            Vector2 safePixels = new Vector2(Mathf.Max(1f, pixels.x), Mathf.Max(1f, pixels.y));
            if (hasPreviewViewportPixels && Vector2.SqrMagnitude(previewViewportPixels - safePixels) < .25f) return;
            previewViewportPixels = safePixels;
            hasPreviewViewportPixels = true;
            if (directPresentation) directBaselineAspect = -1f; else ConfigureRenderTexture();
        }

        /// <summary>Keeps the avatar target in sync with the active graphics AA setting.</summary>
        public void SetAntiAliasing(int samples)
        {
            QualitySettings.antiAliasing = samples;
            if (previewCamera != null)
            {
                previewCamera.allowMSAA = samples > 0;
            }
            if (!directPresentation) ConfigureRenderTexture();
        }

        /// <summary>
        /// Presentation-only supersampling. This never affects the full-body
        /// camera, its padding, orientation, or its full-avatar composition.
        /// </summary>
        public void SetPresentationRenderScale(float scale)
        {
            float normalized = Mathf.Clamp(scale, 1f, 2f);
            if (Mathf.Abs(presentationRenderScale - normalized) < .001f) return;
            presentationRenderScale = normalized;
            if (!directPresentation) ConfigureRenderTexture();
        }

        private void LateUpdate()
        {
            if (directPresentation)
            {
                float aspect = Mathf.Max(.1f, Screen.width / (float)Mathf.Max(1, Screen.height));
                if (Mathf.Abs(directBaselineAspect - aspect) > .0001f)
                {
                    directBaselineAspect = aspect;
                    previewCamera.fieldOfView = activeConfiguration != null
                        ? activeConfiguration.fieldOfView
                        : previewCamera.fieldOfView;
                    previewCamera.lensShift = Vector2.zero;
                    UpdatePreviewFraming();
                }
                ApplyDirectPresentationView();
            }
            else
            {
                ConfigureRenderTexture();
                UpdatePreviewFraming();
            }

            if (hasRelaxedPose)
            {
                humanPoseHandler.SetHumanPose(ref relaxedPose);
            }

            if (ActiveAvatar == null || activeConfiguration == null || activeConfiguration.idleSwayDegrees <= 0f)
            {
                FacePreviewCamera();
                return;
            }

            float phase = Time.unscaledTime * activeConfiguration.idleSwayCyclesPerSecond * Mathf.PI * 2f;
            ActiveAvatar.transform.position = idleBasePosition + Vector3.up * (Mathf.Sin(phase * 0.5f) * 0.008f);
            FacePreviewCamera(Mathf.Sin(phase) * activeConfiguration.idleSwayDegrees);
        }

        private void FacePreviewCamera(float idleYaw = 0f)
        {
            if (ActiveAvatar == null || previewCamera == null || activeConfiguration == null)
            {
                return;
            }

            Vector3 towardCamera = previewCamera.transform.position - ActiveAvatar.transform.position;
            towardCamera.y = 0f;
            if (towardCamera.sqrMagnitude < 0.0001f)
            {
                return;
            }

            // A VRoid VRM's visual forward is its root +Z direction. Aim that
            // direction directly at the actual preview camera after HumanPose
            // restoration, then apply an optional per-asset correction.
            ActiveAvatar.transform.rotation = Quaternion.LookRotation(towardCamera.normalized, Vector3.up)
                * Quaternion.Euler(0f, activeConfiguration.facingYawOffset + idleYaw, 0f);
        }

        private void ConfigureRelaxedPose(AvatarConfiguration configuration)
        {
            hasRelaxedPose = false;
            humanPoseHandler?.Dispose();
            humanPoseHandler = null;

            Animator animator = ActiveAvatar.GetComponentInChildren<Animator>();
            if (animator == null || animator.avatar == null || !animator.avatar.isHuman)
            {
                Debug.LogWarning("VRM avatar has no humanoid Animator; leaving its imported pose unchanged.");
                return;
            }

            try
            {
                humanPoseHandler = new HumanPoseHandler(animator.avatar, animator.transform);
                humanPoseHandler.GetHumanPose(ref relaxedPose);
                SetMuscle(ref relaxedPose, "Left Shoulder Down-Up", configuration.relaxedArmDown * 0.3f);
                SetMuscle(ref relaxedPose, "Right Shoulder Down-Up", configuration.relaxedArmDown * 0.3f);
                SetMuscle(ref relaxedPose, "Left Arm Down-Up", configuration.relaxedArmDown);
                SetMuscle(ref relaxedPose, "Right Arm Down-Up", configuration.relaxedArmDown);
                SetMuscle(ref relaxedPose, "Left Arm Front-Back", 0.08f);
                SetMuscle(ref relaxedPose, "Right Arm Front-Back", 0.08f);
                humanPoseHandler.SetHumanPose(ref relaxedPose);
                hasRelaxedPose = true;
            }
            catch (Exception exception)
            {
                humanPoseHandler?.Dispose();
                humanPoseHandler = null;
                Debug.LogWarning("Unable to apply the presentation idle pose: " + exception.Message);
            }
        }

        private static void SetMuscle(ref HumanPose pose, string muscleName, float value)
        {
            for (int index = 0; index < HumanTrait.MuscleCount; index++)
            {
                if (HumanTrait.MuscleName[index] == muscleName)
                {
                    pose.muscles[index] = Mathf.Clamp(value, -1f, 1f);
                    return;
                }
            }
        }

        private void ConfigureRenderTexture()
        {
            if (directPresentation || previewCamera == null || previewSurface == null)
            {
                return;
            }

            Rect rect = previewSurface.rectTransform.rect;
            AvatarPresentationTransform presentation = GetPresentationTransform();
            Vector2 capturePixels = hasPreviewViewportPixels
                ? previewViewportPixels
                : new Vector2(rect.width, rect.height);
            Vector2Int requiredSize = AvatarRenderQuality.RequiredRenderTextureSize(
                capturePixels,
                presentation != null ? presentation.scale : 1f,
                (activeConfiguration != null ? activeConfiguration.renderTextureSupersample : 1f) * presentationRenderScale
            );
            requiredSize = AvatarRenderQuality.ClampToMaximumDimension(requiredSize);
            int width = Mathf.Max(64, requiredSize.x);
            int height = Mathf.Max(64, requiredSize.y);

            int msaaSamples = Mathf.Max(1, QualitySettings.antiAliasing);
            if (previewTexture != null && previewTexture.width == width && previewTexture.height == height &&
                previewTexture.antiAliasing == msaaSamples)
            {
                return;
            }

            if (previewTexture != null)
            {
                previewTexture.Release();
                Destroy(previewTexture);
            }

            previewTexture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32)
            {
                name = "AIFren Avatar Presentation",
                // The avatar is composited through this target texture, so its
                // MSAA must be set explicitly rather than relying only on the
                // standalone quality profile.
                antiAliasing = msaaSamples,
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp,
                useMipMap = false,
                autoGenerateMips = false
            };
            previewTexture.Create();
            previewCamera.targetTexture = previewTexture;
            previewSurface.texture = previewTexture;
        }

        private void UpdatePreviewFraming()
        {
            if (previewCamera == null || activeConfiguration == null)
            {
                return;
            }

            Vector3 avatarPosition = activeConfiguration.position.ToVector3();
            Vector3 lookTarget = avatarPosition + Vector3.up * activeConfiguration.lookAtHeight;
            Vector3 configuredCameraPosition = avatarPosition + activeConfiguration.cameraOffset.ToVector3();
            Vector3 cameraDirection = (configuredCameraPosition - lookTarget).normalized;
            float cameraDistance = Vector3.Distance(configuredCameraPosition, lookTarget);

            bool hasAvatarBounds = TryGetAvatarBounds(out Bounds avatarBounds);
            if (hasAvatarBounds)
            {
                // This camera deliberately contains the complete avatar with
                // only its authored margin. Presentation transforms never
                // compensate for camera framing.
                lookTarget = avatarBounds.center;
                float aspect = directPresentation
                    ? Mathf.Max(.1f, Screen.width / (float)Mathf.Max(1, Screen.height))
                    : previewTexture != null
                    ? previewTexture.width / (float)previewTexture.height
                    : 16f / 9f;
                cameraDistance = AvatarFraming.RequiredCameraDistance(
                    avatarBounds,
                    -cameraDirection,
                    previewCamera.fieldOfView,
                    aspect,
                    activeConfiguration.fullBodyCameraPadding
                );
            }

            previewCamera.transform.position = lookTarget + (cameraDirection * cameraDistance);
            previewCamera.transform.LookAt(lookTarget);
            if (hasAvatarBounds) LogPresentationOccupancy(avatarBounds);
            if (hasAvatarBounds) ValidateFullBodyFrustum(avatarBounds);
        }

        private void ApplyDirectPresentationView()
        {
            if (!directPresentation || previewCamera == null || activeConfiguration == null) return;
            AvatarDirectCameraView view = AvatarDirectPresentationCamera.FromPresentation(
                activeConfiguration.fieldOfView, directPresentationValues);
            previewCamera.targetTexture = null;
            previewCamera.clearFlags = CameraClearFlags.Depth;
            previewCamera.usePhysicalProperties = true;
            previewCamera.fieldOfView = view.fieldOfView;
            previewCamera.lensShift = view.lensShift;
            EnsureDirectBackgroundRenderer();
            directBackgroundRenderer.Set(directBackground, directBedroomTexture);
        }

        private void EnsureDirectBackgroundRenderer()
        {
            if (previewCamera != null && directBackgroundRenderer == null)
                directBackgroundRenderer = new AvatarDirectBackgroundRenderer(previewCamera);
        }

        private void ReleasePreviewTexture()
        {
            if (previewTexture == null) return;
            previewTexture.Release();
            Destroy(previewTexture);
            previewTexture = null;
        }

        private void LogPresentationOccupancy(Bounds bounds)
        {
            if (previewTexture == null || previewCamera == null) return;
            Vector2Int textureSize = new Vector2Int(previewTexture.width, previewTexture.height);
            if (textureSize == lastLoggedPresentationTextureSize) return;

            float minimumX = 1f, minimumY = 1f, maximumX = 0f, maximumY = 0f;
            for (int x = -1; x <= 1; x += 2)
            for (int y = -1; y <= 1; y += 2)
            for (int z = -1; z <= 1; z += 2)
            {
                Vector3 point = bounds.center + Vector3.Scale(bounds.extents, new Vector3(x, y, z));
                Vector3 viewport = previewCamera.WorldToViewportPoint(point);
                minimumX = Mathf.Min(minimumX, viewport.x);
                minimumY = Mathf.Min(minimumY, viewport.y);
                maximumX = Mathf.Max(maximumX, viewport.x);
                maximumY = Mathf.Max(maximumY, viewport.y);
            }

            lastLoggedPresentationTextureSize = textureSize;
            Debug.Log(string.Format(
                "[AIFren Avatar] RT {0}x{1}; complete-bounds occupancy {2:P1} wide x {3:P1} high; renderer bounds {4:F2} x {5:F2} x {6:F2}.",
                textureSize.x, textureSize.y, maximumX - minimumX, maximumY - minimumY,
                bounds.size.x, bounds.size.y, bounds.size.z));
        }

        private AvatarPresentationTransform GetPresentationTransform()
        {
            if (activeConfiguration == null) return null;
            return activeConfiguration.PresentationTransform(previewIsPortrait);
        }

        [System.Diagnostics.Conditional("DEVELOPMENT_BUILD")]
        private void ValidateFullBodyFrustum(Bounds bounds)
        {
            if (loggedFullBodyFrustum || previewCamera == null) return;
            bool contained = true;
            for (int x = -1; x <= 1; x += 2)
            for (int y = -1; y <= 1; y += 2)
            for (int z = -1; z <= 1; z += 2)
            {
                Vector3 point = bounds.center + Vector3.Scale(bounds.extents, new Vector3(x, y, z));
                Vector3 viewport = previewCamera.WorldToViewportPoint(point);
                contained &= viewport.z > 0f && viewport.x >= 0f && viewport.x <= 1f && viewport.y >= 0f && viewport.y <= 1f;
            }
            loggedFullBodyFrustum = true;
            if (!contained) Debug.LogWarning("AIFren avatar full-body bounds are outside the preview frustum.");
            else Debug.Log("AIFren avatar full-body bounds safely fit inside the preview RenderTexture.");
        }

        private bool TryGetAvatarBounds(out Bounds bounds)
        {
            bounds = new Bounds();
            if (ActiveAvatar == null)
            {
                return false;
            }

            Renderer[] renderers = ActiveAvatar.GetComponentsInChildren<Renderer>();
            bool foundRenderer = false;
            foreach (Renderer renderer in renderers)
            {
                if (!renderer.enabled || !renderer.gameObject.activeInHierarchy)
                {
                    continue;
                }

                if (!foundRenderer)
                {
                    bounds = renderer.bounds;
                    foundRenderer = true;
                }
                else
                {
                    bounds.Encapsulate(renderer.bounds);
                }
            }

            return foundRenderer;
        }

        private void DestroyActiveAvatar()
        {
            hasRelaxedPose = false;
            animationController?.ClearAvatar();
            humanPoseHandler?.Dispose();
            humanPoseHandler = null;

            if (ActiveAvatar != null)
            {
                Destroy(ActiveAvatar);
                ActiveAvatar = null;
            }
        }

        private void OnDestroy()
        {
            humanPoseHandler?.Dispose();

            if (savedRenderSettings)
            {
                RenderSettings.ambientIntensity = originalAmbientIntensity;
                RenderSettings.reflectionIntensity = originalReflectionIntensity;
            }

            if (previewTexture != null)
            {
                ReleasePreviewTexture();
            }
            directBackgroundRenderer?.Dispose();
        }

        private void Fail(string error)
        {
            LastError = error;
            Debug.LogWarning(error);
            AvatarLoadFailed?.Invoke(error);
        }
    }
}
