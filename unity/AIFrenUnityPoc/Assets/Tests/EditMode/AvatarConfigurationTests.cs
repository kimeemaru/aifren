using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarConfigurationTests
    {
        [Test]
        public void DefaultConfigurationUsesReusableLocalCharacterPath()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();

            Assert.AreEqual("LocalCharacter/model", configuration.avatarResourcePath);
            Assert.IsTrue(configuration.IsValid(out string error), error);
        }

        [Test]
        public void PortraitAndLandscapeHaveIndependentAuthoredPresentationTransforms()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();
            AvatarPresentationTransform portrait = configuration.PresentationTransform(true);
            AvatarPresentationTransform landscape = configuration.PresentationTransform(false);

            Assert.AreNotSame(portrait, landscape);
            Assert.AreEqual(1f, portrait.scale);
            Assert.AreEqual(0f, portrait.y);
            Assert.AreEqual(1f, landscape.scale);
            Assert.AreEqual(0f, landscape.y);
            Assert.IsTrue(portrait.IsValid());
            Assert.IsTrue(landscape.IsValid());
        }

        [Test]
        public void InvalidPresentationTransformIsRejectedBeforeLoading()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                portraitPresentation = new AvatarPresentationTransform { scale = .99f }
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("presentation transforms", error);
        }

        [Test]
        public void FullAvatarRenderTargetProvidesDensityForTheScaledPresentation()
        {
            Vector2 presentationPixels = new Vector2(900f, 1100f);
            Vector2Int target = AvatarRenderQuality.RequiredRenderTextureSize(presentationPixels, 1.8f, 1.35f);

            Assert.GreaterOrEqual(target.x, Mathf.CeilToInt(presentationPixels.x * 1.8f * 1.35f));
            Assert.GreaterOrEqual(target.y, Mathf.CeilToInt(presentationPixels.y * 1.8f * 1.35f));
        }

        [Test]
        public void RenderTargetQualityCapPreservesAspectRatio()
        {
            Vector2Int capped = AvatarRenderQuality.ClampToMaximumDimension(new Vector2Int(6144, 3456));

            Assert.AreEqual(AvatarRenderQuality.MaximumRenderTextureDimension, capped.x);
            Assert.AreEqual(1728, capped.y);
        }

        [Test]
        public void EmptyAvatarPathIsRejectedBeforeLoading()
        {
            AvatarConfiguration configuration = new AvatarConfiguration { avatarResourcePath = string.Empty };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("Resources path", error);
        }

        [Test]
        public void InsufficientFullBodyCameraPaddingIsRejected()
        {
            AvatarConfiguration configuration = new AvatarConfiguration { fullBodyCameraPadding = .99f };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("padding", error);
        }

        [Test]
        public void BoundsAwareDistanceFitsTheLimitingCameraAxis()
        {
            Bounds bounds = new Bounds(Vector3.zero, new Vector3(2f, 4f, 1f));
            float landscape = AvatarFraming.RequiredCameraDistance(bounds, 25f, 16f / 9f, 1.1f);
            float portrait = AvatarFraming.RequiredCameraDistance(bounds, 25f, 9f / 16f, 1.06f);

            Assert.Greater(landscape, 0f);
            Assert.Greater(portrait, 0f);
            Assert.AreNotEqual(landscape, portrait);
        }

        [Test]
        public void CameraSpaceFitIsTighterThanTheFormerPaddedBoundingSphere()
        {
            Bounds bounds = new Bounds(Vector3.zero, new Vector3(1f, 4f, .6f));
            float fov = 25f;
            float aspect = 9f / 16f;
            float padding = 1.08f;
            float tightFit = AvatarFraming.RequiredCameraDistance(bounds, Vector3.forward, fov, aspect, padding);
            float formerSphereFit = bounds.extents.magnitude * padding /
                Mathf.Sin(Mathf.Atan(Mathf.Tan(fov * Mathf.Deg2Rad * .5f) * aspect));

            Assert.Greater(tightFit, 0f);
            Assert.Less(tightFit, formerSphereFit);
        }
    }
}
