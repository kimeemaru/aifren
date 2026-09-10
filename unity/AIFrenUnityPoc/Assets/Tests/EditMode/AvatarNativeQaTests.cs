using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarNativeQaTests
    {
        [Test]
        public void FinitePlanGateIsExplicitAndDevelopmentOnlyBeforePreferences()
        {
            Assert.That(PresentationPreferences.HasFinitePlayerRequest(new[] { "player" }, true), Is.False);
            Assert.That(PresentationPreferences.HasFinitePlayerRequest(new[] { "player", "-aifren-qa-plan" }, false), Is.False);
            Assert.That(PresentationPreferences.HasFinitePlayerRequest(new[] { "player", "-aifren-qa-plan" }, true), Is.True);
            Assert.That(PlayerPrefs.IsIsolated, Is.True);
        }

        [Test]
        public void ExpressionTraceToleratesExplicitComponentOnlyOrRetiredAvatar()
        {
            var root = new GameObject("component-only trace fixture");
            try
            {
                var controller = root.AddComponent<AIFrenPocController>();
                Assert.DoesNotThrow(() => typeof(AIFrenPocController).GetMethod("QaSampleExpression",
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).Invoke(controller, null));
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void NormalVisualProofDoesNotSaveItsTransientDisplaySettings()
        {
            const string key = "AIFren.PresentationDisplaySettings.v1";
            bool existed = PlayerPrefs.HasKey(key); string saved = PlayerPrefs.GetString(key);
            bool active = NativeQaSession.Active;
            var activeProperty = typeof(NativeQaSession).GetProperty("Active",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            try
            {
                PlayerPrefs.SetString(key, "preserve exact formatting");
                activeProperty.SetValue(null, true);
                typeof(AIFrenPocController).GetMethod("SaveDisplaySettings", System.Reflection.BindingFlags.Static |
                    System.Reflection.BindingFlags.NonPublic).Invoke(null, new object[] {
                        new PresentationDisplaySettings { width = 900, height = 1600 } });
                Assert.That(PlayerPrefs.GetString(key), Is.EqualTo("preserve exact formatting"));
            }
            finally
            {
                activeProperty.SetValue(null, active);
                if (existed) PlayerPrefs.SetString(key, saved); else PlayerPrefs.DeleteKey(key);
                PlayerPrefs.Save();
            }
        }

        [Test]
        public void FramingContainsPosedSkinOutsideImportedBoundsWithoutChangingBones()
        {
            var owner = new GameObject("owned framing fixture");
            var avatar = new GameObject("synthetic skinned avatar");
            var bone = new GameObject("synthetic bone"); bone.transform.SetParent(avatar.transform);
            var mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up },
                triangles = new[] { 0, 1, 2 }, bindposes = new[] { Matrix4x4.identity },
                boneWeights = new[] { new BoneWeight { boneIndex0 = 0, weight0 = 1 },
                    new BoneWeight { boneIndex0 = 0, weight0 = 1 }, new BoneWeight { boneIndex0 = 0, weight0 = 1 } } };
            try
            {
                mesh.RecalculateBounds();
                var skin = avatar.AddComponent<SkinnedMeshRenderer>();
                skin.sharedMesh = mesh; skin.bones = new[] { bone.transform }; skin.rootBone = avatar.transform;
                skin.localBounds = new Bounds(Vector3.zero, Vector3.one);
                bone.transform.localPosition = Vector3.up * 3;
                Assert.That(skin.bounds.max.y, Is.LessThan(3));
                var loader = owner.AddComponent<AvatarLoader>(); loader.enabled = false;
                typeof(AvatarLoader).GetProperty("ActiveAvatar").SetValue(loader, avatar);
                object[] values = { new Bounds() };
                bool found = (bool)typeof(AvatarLoader).GetMethod("TryGetAvatarBounds",
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).Invoke(loader, values);
                Assert.That(found, Is.True);
                Assert.That(((Bounds)values[0]).max.y, Is.GreaterThanOrEqualTo(4));
                Assert.That(bone.transform.localPosition, Is.EqualTo(Vector3.up * 3));
            }
            finally { Object.DestroyImmediate(owner); if (avatar != null) Object.DestroyImmediate(avatar); Object.DestroyImmediate(mesh); }
        }

        [TestCase(900f / 1600f)]
        [TestCase(1920f / 1080f)]
        public void DirectCameraProjectionUsesTheVerticalFieldOfViewUsedForBounds(float aspect)
        {
            var root = new GameObject("owned camera fixture");
            try
            {
                var loader = root.AddComponent<AvatarLoader>(); loader.enabled = false;
                typeof(AvatarLoader).GetMethod("Awake", System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.NonPublic).Invoke(loader, null);
                Camera camera = loader.PresentationCamera;
                camera.aspect = aspect;
                loader.SetDirectPresentationValues(new AvatarPresentationValues { scale = 1 });
                float expected = 1f / Mathf.Tan(camera.fieldOfView * Mathf.Deg2Rad * .5f);
                Assert.That(camera.projectionMatrix.m11, Is.EqualTo(expected).Within(.001f),
                    "Physical sensor gate must not magnify/shrink the vertical framing after the bounds fit.");
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void MissingModelCannotPassCompanionPreflight()
        {
            Assert.That(AvatarVisualProbe.HasGeometry(null), Is.False);
            Assert.That(AvatarVisualProbe.Inspect(null, null).ready, Is.False);
            Assert.That(new NativeQaSession.Plan().componentOnly, Is.False);
        }

        [Test]
        public void NonNullLoaderOrPrimitiveIsNotVisibleVrmProof()
        {
            var root = GameObject.CreatePrimitive(PrimitiveType.Cube);
            try
            {
                Assert.That(AvatarVisualProbe.HasGeometry(root), Is.False);
                var camera = root.AddComponent<Camera>();
                Assert.That(AvatarVisualProbe.Inspect(root, camera).ready, Is.False);
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void EmptySkinnedRendererCannotSupplyBuildInputOrVisibleGeometry()
        {
            var root = new GameObject("synthetic empty avatar", typeof(SkinnedMeshRenderer), typeof(Camera));
            try
            {
                Assert.That(AvatarVisualProbe.HasGeometry(root), Is.False);
                Assert.That(AvatarVisualProbe.Inspect(root, root.GetComponent<Camera>()).ready, Is.False);
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void QaPresentationImportRejectsUnknownKeysAndKeepsNormalStoreIsolated()
        {
            Assert.That(PlayerPrefs.IsIsolated, Is.True);
            Assert.Throws<System.InvalidOperationException>(() => NativeQaSession.ApplyPresentation(new[] {
                new NativeQaSession.Preference { name = "unapproved", type = "string", value = "synthetic" } }));
            Assert.That(PlayerPrefs.HasKey("unapproved"), Is.False);
        }

        [Test]
        public void QaPresentationImportRetainsPreciseFramingAndRevealValues()
        {
            const string key = "AIFren.AvatarPresentation.Portrait.Scale";
            bool existed = PlayerPrefs.HasKey(key); float original = PlayerPrefs.GetFloat(key);
            try
            {
                NativeQaSession.ApplyPresentation(new[] { new NativeQaSession.Preference {
                    name = key, type = "float", value = "1.375" } });
                Assert.That(PlayerPrefs.GetFloat(key), Is.EqualTo(1.375f));
            }
            finally { if (existed) PlayerPrefs.SetFloat(key, original); else PlayerPrefs.DeleteKey(key); }
        }
    }
}
