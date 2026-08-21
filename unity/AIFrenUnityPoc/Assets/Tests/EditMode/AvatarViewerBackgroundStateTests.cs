using AIFren.UnityPoc.Avatar;
using System.Collections.Generic;
using NUnit.Framework;
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

        [SetUp]
        public void SetUp()
        {
            hadPortrait = PlayerPrefs.HasKey(PortraitKey);
            hadLandscape = PlayerPrefs.HasKey(LandscapeKey);
            savedPortrait = PlayerPrefs.GetInt(PortraitKey);
            savedLandscape = PlayerPrefs.GetInt(LandscapeKey);
            AvatarViewerBackgroundState.DeletePersistedValues();
        }

        [TearDown]
        public void TearDown()
        {
            AvatarViewerBackgroundState.DeletePersistedValues();
            if (hadPortrait) PlayerPrefs.SetInt(PortraitKey, savedPortrait);
            if (hadLandscape) PlayerPrefs.SetInt(LandscapeKey, savedLandscape);
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
        public void DirectBackgroundCanBeSelectedBeforeTheLoaderCreatesItsCamera()
        {
            GameObject host = new GameObject("Avatar loader initialization test");
            AvatarLoader loader = host.AddComponent<AvatarLoader>();

            Assert.DoesNotThrow(() => loader.SetDirectBackground(AvatarViewerBackground.Bedroom, null));
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
