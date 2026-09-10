using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using AIFren.UnityPoc.Avatar;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarViewerBackgroundStateTests
    {
        private const string PortraitKey = "AIFren.AvatarViewerBackground.Portrait";
        private const string LandscapeKey = "AIFren.AvatarViewerBackground.Landscape";
        private bool hadPortrait;
        private bool hadLandscape;
        private int savedPortrait;
        private int savedLandscape;
        private readonly Dictionary<string, string> customPaths = new Dictionary<string, string>();

        [SetUp]
        public void SetUp()
        {
            hadPortrait = PlayerPrefs.HasKey(PortraitKey);
            hadLandscape = PlayerPrefs.HasKey(LandscapeKey);
            savedPortrait = PlayerPrefs.GetInt(PortraitKey);
            savedLandscape = PlayerPrefs.GetInt(LandscapeKey);
            customPaths.Clear();
            foreach (string orientation in new[] { "Portrait", "Landscape" })
            {
                string key = "AIFren.AvatarViewerBackground.CustomPath." + orientation;
                if (PlayerPrefs.HasKey(key)) customPaths[key] = PlayerPrefs.GetString(key);
            }
            AvatarViewerBackgroundState.DeletePersistedValues();
        }

        [TearDown]
        public void TearDown()
        {
            AvatarViewerBackgroundState.DeletePersistedValues();
            if (hadPortrait) PlayerPrefs.SetInt(PortraitKey, savedPortrait);
            if (hadLandscape) PlayerPrefs.SetInt(LandscapeKey, savedLandscape);
            foreach (var item in customPaths) PlayerPrefs.SetString(item.Key, item.Value);
            PlayerPrefs.Save();
        }

        [Test]
        public void DefaultsMatchThePortraitAndLandscapeViewerPolicy()
        {
            AvatarViewerBackgroundState state = AvatarViewerBackgroundState.Load();
            Assert.AreEqual(AvatarViewerBackground.LightNeutral, state.Get(true));
            Assert.AreEqual(AvatarViewerBackground.Bedroom, state.Get(false));
        }

        [Test]
        public void PersistsEachOrientationIndependently()
        {
            AvatarViewerBackgroundState state = AvatarViewerBackgroundState.Load();
            state.Set(true, AvatarViewerBackground.NeutralGrey, true);
            state.Set(false, AvatarViewerBackground.LightNeutral, true);

            AvatarViewerBackgroundState reloaded = AvatarViewerBackgroundState.Load();
            Assert.AreEqual(AvatarViewerBackground.NeutralGrey, reloaded.Get(true));
            Assert.AreEqual(AvatarViewerBackground.LightNeutral, reloaded.Get(false));
        }

        [Test]
        public void PersistsCustomImagePathsIndependently()
        {
            AvatarViewerBackgroundState state = AvatarViewerBackgroundState.Load();
            state.SetCustomPath(true, "/tmp/portrait.png", true);
            state.Set(true, AvatarViewerBackground.CustomImage, true);
            state.SetCustomPath(false, "/tmp/landscape.jpg", true);
            state.Set(false, AvatarViewerBackground.CustomImage, true);

            AvatarViewerBackgroundState reloaded = AvatarViewerBackgroundState.Load();
            Assert.AreEqual(AvatarViewerBackground.CustomImage, reloaded.Get(true));
            Assert.AreEqual("/tmp/portrait.png", reloaded.GetCustomPath(true));
            Assert.AreEqual(AvatarViewerBackground.CustomImage, reloaded.Get(false));
            Assert.AreEqual("/tmp/landscape.jpg", reloaded.GetCustomPath(false));
        }

        [Test]
        public void DeletingActivePortraitCustomBackgroundFallsBackToLightNeutral()
        {
            AvatarViewerBackgroundState state = AvatarViewerBackgroundState.Load();
            state.SetCustomPath(true, "/managed/portrait.png", false);
            state.Set(true, AvatarViewerBackground.CustomImage, false);
            state.RepairDeletedCustomPaths(new HashSet<string> { "/managed/portrait.png" }, false);

            Assert.AreEqual(AvatarViewerBackground.LightNeutral, state.Get(true));
            Assert.AreEqual(string.Empty, state.GetCustomPath(true));
        }

        [Test]
        public void DeletingActiveLandscapeCustomBackgroundFallsBackToBedroom()
        {
            AvatarViewerBackgroundState state = AvatarViewerBackgroundState.Load();
            state.SetCustomPath(false, "/managed/landscape.jpg", false);
            state.Set(false, AvatarViewerBackground.CustomImage, false);
            state.RepairDeletedCustomPaths(new HashSet<string> { "/managed/landscape.jpg" }, false);

            Assert.AreEqual(AvatarViewerBackground.Bedroom, state.Get(false));
            Assert.AreEqual(string.Empty, state.GetCustomPath(false));
        }

        [Test]
        public void LoaderCreatesAnOpaqueClearSurfaceBeforeAvatarLoading()
        {
            GameObject host = new GameObject("Avatar loader initialization test");
            AvatarLoader loader = host.AddComponent<AvatarLoader>();
            MethodInfo awake = typeof(AvatarLoader).GetMethod("Awake", BindingFlags.Instance | BindingFlags.NonPublic);
            awake.Invoke(loader, null);
            FieldInfo cameraField = typeof(AvatarLoader).GetField("previewCamera", BindingFlags.Instance | BindingFlags.NonPublic);
            FieldInfo rendererField = typeof(AvatarLoader).GetField("directBackgroundRenderer", BindingFlags.Instance | BindingFlags.NonPublic);
            Camera camera = (Camera)cameraField.GetValue(loader);
            AvatarDirectBackgroundRenderer renderer = (AvatarDirectBackgroundRenderer)rendererField.GetValue(loader);

            Assert.NotNull(camera);
            Assert.NotNull(renderer);
            Assert.AreEqual(CameraClearFlags.Depth, camera.clearFlags);
            Assert.AreEqual(CameraClearFlags.SolidColor, renderer.BackgroundCamera.clearFlags);
            Assert.IsTrue(renderer.BackgroundCamera.enabled);
            Object.DestroyImmediate(host);
        }

        [Test]
        public void RuntimeVrmShadersAreRetainedForStandaloneImports()
        {
            UnityEngine.Object[] settingsAssets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset");
            Assert.IsNotEmpty(settingsAssets);
            SerializedProperty shaders = new SerializedObject(settingsAssets[0]).FindProperty("m_AlwaysIncludedShaders");
            foreach (string shaderName in new[] { "VRM10/MToon10", "UniGLTF/UniUnlit" })
            {
                Shader runtimeShader = Shader.Find(shaderName);
                Assert.NotNull(runtimeShader);
                bool retained = false;
                for (int index = 0; index < shaders.arraySize; index++)
                    retained |= shaders.GetArrayElementAtIndex(index).objectReferenceValue == runtimeShader;
                Assert.IsTrue(retained, shaderName + " must survive standalone shader stripping.");
            }
        }

        [Test]
        public void LateUpdateSafelyWaitsForTheDirectPresentationCamera()
        {
            GameObject host = new GameObject("Avatar loader late update initialization test");
            AvatarLoader loader = host.AddComponent<AvatarLoader>();
            MethodInfo lateUpdate = typeof(AvatarLoader).GetMethod("LateUpdate", BindingFlags.Instance | BindingFlags.NonPublic);

            Assert.DoesNotThrow(() => lateUpdate.Invoke(loader, null));
            Object.DestroyImmediate(host);
        }

        [Test]
        public void QuadFrontFacePointsTowardTheDirectBackgroundCamera()
        {
            GameObject quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            float normalZ = quad.GetComponent<MeshFilter>().sharedMesh.normals[0].z;
            Assert.Less(normalZ, 0f);
            Object.DestroyImmediate(quad);
        }

        [Test]
        public void BedroomTextureIsAssignedAndRenderedByTheBackgroundOnlyCamera()
        {
            GameObject avatarCameraObject = new GameObject("Avatar camera background test");
            Camera avatarCamera = avatarCameraObject.AddComponent<Camera>();
            AvatarDirectBackgroundRenderer renderer = new AvatarDirectBackgroundRenderer(avatarCamera);
            Texture2D bedroom = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            bedroom.SetPixels(new[] { Color.red, Color.red, Color.red, Color.red });
            bedroom.Apply();
            RenderTexture target = new RenderTexture(32, 32, 16);

            renderer.Set(AvatarViewerBackground.Bedroom, bedroom);
            renderer.BackgroundCamera.targetTexture = target;
            renderer.BackgroundCamera.Render();
            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = target;
            Texture2D result = new Texture2D(1, 1, TextureFormat.RGBA32, false);
            result.ReadPixels(new Rect(16f, 16f, 1f, 1f), 0, 0);
            result.Apply();

            Assert.IsTrue(renderer.IsBedroomImageActive);
            Assert.AreSame(bedroom, renderer.AssignedImageTexture);
            Assert.Greater(result.GetPixel(0, 0).r, .8f);

            RenderTexture.active = previous;
            renderer.BackgroundCamera.targetTexture = null;
            renderer.Dispose();
            Object.DestroyImmediate(target);
            Object.DestroyImmediate(result);
            Object.DestroyImmediate(bedroom);
            Object.DestroyImmediate(avatarCameraObject);
        }
    }
}
