using System;
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
        public event Action<GameObject> AvatarLoaded;
        public event Action<string> AvatarLoadFailed;

        public GameObject ActiveAvatar { get; private set; }
        public string LastError { get; private set; } = string.Empty;

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

        private void Start()
        {
            LoadConfiguredAvatar();
        }

        public bool LoadConfiguredAvatar()
        {
            DestroyActiveAvatar();
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
                ActiveAvatar = Instantiate(avatarPrefab);
                ActiveAvatar.name = "Active VRM Avatar";
                ActiveAvatar.transform.position = configuration.position.ToVector3();
                ActiveAvatar.transform.rotation = Quaternion.Euler(configuration.rotationEuler.ToVector3());
                ActiveAvatar.transform.localScale = Vector3.one * configuration.scale;
                activeConfiguration = configuration;
                idleBasePosition = ActiveAvatar.transform.position;
                ConfigureRelaxedPose(configuration);
                ConfigurePreviewCamera(configuration);
                idleBaseRotation = ActiveAvatar.transform.rotation;
                animationController = gameObject.GetComponent<AvatarAnimationController>() ??
                    gameObject.AddComponent<AvatarAnimationController>();
                animationController.Configure(ActiveAvatar);
                LastError = string.Empty;
                AvatarLoaded?.Invoke(ActiveAvatar);
                return true;
            }
            catch (Exception exception)
            {
                DestroyActiveAvatar();
                Fail("Unable to instantiate the VRM avatar: " + exception.Message);
                return false;
            }
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
            ConfigureRenderTexture();
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
        /// The UI controller owns orientation. Never infer it from the fitted
        /// RawImage because user crop/zoom can change that image's dimensions.
        /// </summary>
        public void SetPresentationOrientation(bool isPortrait)
        {
            if (previewIsPortrait == isPortrait) return;
            previewIsPortrait = isPortrait;
            ConfigureRenderTexture();
        }

        /// <summary>
        /// Sets the stable presentation viewport used to size the full-body
        /// capture. User UV crop changes do not alter this capture basis.
        /// </summary>
        public void SetPresentationViewportPixels(Vector2 pixels)
        {
            Vector2 safePixels = new Vector2(Mathf.Max(1f, pixels.x), Mathf.Max(1f, pixels.y));
            if (hasPreviewViewportPixels && Vector2.SqrMagnitude(previewViewportPixels - safePixels) < .25f) return;
            previewViewportPixels = safePixels;
            hasPreviewViewportPixels = true;
            ConfigureRenderTexture();
        }

        /// <summary>Keeps the avatar target in sync with the active graphics AA setting.</summary>
        public void SetAntiAliasing(int samples)
        {
            QualitySettings.antiAliasing = samples;
            if (previewCamera != null)
            {
                previewCamera.allowMSAA = samples > 0;
            }
            ConfigureRenderTexture();
        }

        /// <summary>
        /// Presentation-only supersampling. This never affects the full-body
        /// camera, its padding, orientation, or the UI framing crop.
        /// </summary>
        public void SetPresentationRenderScale(float scale)
        {
            float normalized = Mathf.Clamp(scale, 1f, 2f);
            if (Mathf.Abs(presentationRenderScale - normalized) < .001f) return;
            presentationRenderScale = normalized;
            ConfigureRenderTexture();
        }

        private void LateUpdate()
        {
            ConfigureRenderTexture();
            UpdatePreviewFraming();

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
            if (previewCamera == null || previewSurface == null)
            {
                return;
            }

            Rect rect = previewSurface.rectTransform.rect;
            AvatarCrop crop = GetPresentationCrop();
            Vector2 capturePixels = hasPreviewViewportPixels
                ? previewViewportPixels
                : new Vector2(rect.width, rect.height);
            Vector2Int requiredSize = AvatarRenderQuality.RequiredRenderTextureSize(
                capturePixels,
                crop,
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
                // This camera deliberately contains the *entire* avatar.  The
                // visible close-up is produced later by RawImage uv cropping,
                // so gestures and animated body parts never pop into view.
                lookTarget = avatarBounds.center;
                float aspect = previewTexture != null
                    ? previewTexture.width / (float)previewTexture.height
                    : 16f / 9f;
                cameraDistance = AvatarFraming.RequiredCameraDistance(
                    avatarBounds,
                    previewCamera.fieldOfView,
                    aspect,
                    activeConfiguration.fullBodyCameraPadding
                );
            }

            previewCamera.transform.position = lookTarget + (cameraDirection * cameraDistance);
            previewCamera.transform.LookAt(lookTarget);
            if (hasAvatarBounds) ValidateFullBodyFrustum(avatarBounds);
        }

        private AvatarCrop GetPresentationCrop()
        {
            if (activeConfiguration == null) return new AvatarCrop();
            return previewIsPortrait ? activeConfiguration.portraitUiCrop : activeConfiguration.landscapeUiCrop;
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
                previewTexture.Release();
                Destroy(previewTexture);
            }
        }

        private void Fail(string error)
        {
            LastError = error;
            Debug.LogWarning(error);
            AvatarLoadFailed?.Invoke(error);
        }
    }
}
