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
        public void LandscapeDefaultUsesTheCenteredAuthoredBaseline()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();
            Vector2 alignment = configuration.PresentationAlignment(false);
            Rect startup = AvatarUiFraming.Resolve(configuration.landscapeUiCrop, alignment, 1f, 0f, 0f);
            Rect reset = AvatarUiFraming.Resolve(configuration.landscapeUiCrop, alignment, 1f, 0f, 0f);

            Assert.AreEqual(0f, alignment.x, .0001f);
            Assert.AreEqual(.1625f, alignment.y, .0001f);
            Assert.AreEqual(.5f, startup.center.x, .0001f);
            Assert.AreEqual(.6625f, startup.center.y, .0001f);
            Assert.AreEqual(startup.center.x, reset.center.x, .0001f);
            Assert.AreEqual(startup.center.y, reset.center.y, .0001f);
        }

        [Test]
        public void UserPanIsASeparateDeltaFromTheAuthoredAlignment()
        {
            AvatarCrop crop = new AvatarCrop { x = .20f, y = .2375f, width = .60f, height = .525f };
            Vector2 alignment = new Vector2(0f, .1625f);

            Rect baseline = AvatarUiFraming.Resolve(crop, alignment, 1f, 0f, 0f);
            Rect panned = AvatarUiFraming.Resolve(crop, alignment, 1f, -.5f, .25f);
            Rect afterReset = AvatarUiFraming.Resolve(crop, alignment, 1f, 0f, 0f);

            Assert.AreNotEqual(baseline.center.x, panned.center.x);
            Assert.AreNotEqual(baseline.center.y, panned.center.y);
            Assert.AreEqual(baseline.center.x, afterReset.center.x, .0001f);
            Assert.AreEqual(baseline.center.y, afterReset.center.y, .0001f);
        }

        [Test]
        public void ResetRestoresTheCompleteLandscapeAuthoredFramingTuple()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();
            AvatarCrop crop = configuration.landscapeUiCrop;
            Vector2 alignment = configuration.PresentationAlignment(false);
            float defaultZoom = configuration.PresentationDefaultZoom(false);

            Rect startup = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);
            Rect zoomed = AvatarUiFraming.Resolve(crop, alignment, 2.2f, 0f, 0f);
            Rect pannedAndZoomed = AvatarUiFraming.Resolve(crop, alignment, 2.2f, -.65f, .45f);
            Rect afterZoomReset = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);
            Rect afterPanAndZoomReset = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);
            Rect resetUi = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);

            Assert.AreNotEqual(startup, zoomed);
            Assert.AreNotEqual(startup, pannedAndZoomed);
            Assert.AreEqual(startup, afterZoomReset);
            Assert.AreEqual(startup, afterPanAndZoomReset);
            Assert.AreEqual(startup, resetUi);
        }

        [Test]
        public void ChangedZoomAndPanDoNotAlterThePortraitResetTuple()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();
            AvatarCrop crop = configuration.portraitUiCrop;
            Vector2 alignment = configuration.PresentationAlignment(true);
            float defaultZoom = configuration.PresentationDefaultZoom(true);

            Rect baseline = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);
            Rect changed = AvatarUiFraming.Resolve(crop, alignment, 2.4f, -.6f, .45f);
            Rect afterReset = AvatarUiFraming.Resolve(crop, alignment, defaultZoom, 0f, 0f);

            Assert.AreNotEqual(baseline, changed);
            Assert.AreEqual(baseline, afterReset);
        }

        [Test]
        public void AllocatedRenderTextureProducesTheStableCropAspectUsedByReset()
        {
            AvatarCrop crop = new AvatarCrop { x = .20f, y = .2375f, width = .60f, height = .525f };
            Vector2 displayedViewport = new Vector2(1200f, 720f);
            Vector2Int target = AvatarRenderQuality.RequiredRenderTextureSize(displayedViewport, crop, 1f);
            Rect resolved = AvatarUiFraming.Resolve(crop, new Vector2(.08f, .1625f), 1f, 0f, 0f);

            float finalAspect = AvatarUiFraming.DisplayAspect(resolved, target.x, target.y);

            Assert.AreEqual(displayedViewport.x / displayedViewport.y, finalAspect, .002f);
        }

        [Test]
        public void EmptyAvatarPathIsRejectedBeforeLoading()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                avatarResourcePath = string.Empty
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("Resources path", error);
        }

        [Test]
        public void InvalidScaleIsRejectedBeforeLoading()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                scale = 0f
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("greater than zero", error);
        }

        [Test]
        public void DefaultPresentationLightingAndFacingConfigurationIsValid()
        {
            AvatarConfiguration configuration = new AvatarConfiguration();

            Assert.AreEqual(0f, configuration.facingYawOffset);
            Assert.Greater(configuration.keyLightIntensity, 0f);
            Assert.GreaterOrEqual(configuration.fillLightIntensity, 0f);
            Assert.GreaterOrEqual(configuration.ambientIntensity, 0f);
            Assert.IsTrue(configuration.IsValid(out string error), error);
        }

        [Test]
        public void NegativeLightingValueIsRejected()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                ambientIntensity = -0.01f
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("lighting", error);
        }

        [Test]
        public void InsufficientFullBodyCameraPaddingIsRejected()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                fullBodyCameraPadding = .99f
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("padding", error);
        }

        [Test]
        public void UiCropCannotEscapeTheFullBodyRenderTexture()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                portraitUiCrop = new AvatarCrop { x = .8f, y = .3f, width = .3f, height = .5f }
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("crop", error);
        }

        [Test]
        public void BoundsAwareDistanceFitsTheLimitingCameraAxis()
        {
            Bounds bounds = new Bounds(Vector3.zero, new Vector3(2f, 4f, 1f));
            float landscape = AvatarFraming.RequiredCameraDistance(bounds, 25f, 16f / 9f, 1.1f);
            float portrait = AvatarFraming.RequiredCameraDistance(bounds, 25f, 9f / 16f, 1.06f);

            Assert.Greater(landscape, 0f);
            Assert.Greater(portrait, landscape);
        }

        [Test]
        public void CroppedDisplayGetsNativeDensityRenderTarget()
        {
            AvatarCrop portrait = new AvatarCrop { x = .28f, y = .36f, width = .44f, height = .55f };
            Vector2Int target = AvatarRenderQuality.RequiredRenderTextureSize(
                new Vector2(850f, 1060f), portrait, 1.15f);

            Assert.GreaterOrEqual(target.x * portrait.width, 850f);
            Assert.GreaterOrEqual(target.y * portrait.height, 1060f);
        }

        [Test]
        public void RenderTargetQualityCapPreservesAspectRatio()
        {
            Vector2Int capped = AvatarRenderQuality.ClampToMaximumDimension(new Vector2Int(6144, 3456));

            Assert.AreEqual(AvatarRenderQuality.MaximumRenderTextureDimension, capped.x);
            Assert.AreEqual(1728, capped.y);
        }

        [Test]
        public void UiCropMustMatchItsFittedDisplayAspect()
        {
            AvatarCrop portrait = new AvatarCrop { x = .28f, y = .36f, width = .44f, height = .55f };
            Assert.IsTrue(AvatarRenderQuality.CropMatchesDisplayAspect(new Vector2(800f, 1000f), portrait));
            Assert.IsFalse(AvatarRenderQuality.CropMatchesDisplayAspect(new Vector2(1600f, 800f), portrait));
        }

        [Test]
        public void UiFramingZoomOutCanSampleTheCompleteRenderTexture()
        {
            AvatarCrop portrait = new AvatarCrop { x = .30f, y = .36f, width = .40f, height = .56f };
            Rect result = AvatarUiFraming.Resolve(portrait, AvatarUiFraming.MinimumZoom(portrait), 0f, 0f);

            Assert.AreEqual(0f, result.x, .0001f);
            Assert.AreEqual(0f, result.y, .0001f);
            Assert.AreEqual(1f, result.width, .0001f);
            Assert.AreEqual(1f, result.height, .0001f);
        }

        [Test]
        public void MinimumZoomHasNoPanRangeAndCannotProduceInvalidCrop()
        {
            AvatarCrop portrait = new AvatarCrop { x = .30f, y = .22f, width = .40f, height = .56f };
            float minimum = AvatarUiFraming.MinimumZoom(portrait);
            Rect baseline = AvatarUiFraming.Resolve(portrait, new Vector2(.15f, -.2f), minimum, 0f, 0f);
            Rect dragged = AvatarUiFraming.Resolve(portrait, new Vector2(.15f, -.2f), minimum, 1f, -1f);

            Assert.IsFalse(AvatarUiFraming.HasPanRange(portrait, minimum, true));
            Assert.IsFalse(AvatarUiFraming.HasPanRange(portrait, minimum, false));
            Assert.AreEqual(baseline, dragged);
            Assert.AreEqual(new Rect(0f, 0f, 1f, 1f), dragged);
        }

        [Test]
        public void UiFramingPanMovesWithinValidTextureBoundsAtCloseZoom()
        {
            AvatarCrop landscape = new AvatarCrop { x = .20f, y = .40f, width = .60f, height = .525f };
            Rect left = AvatarUiFraming.Resolve(landscape, 2f, -1f, -1f);
            Rect right = AvatarUiFraming.Resolve(landscape, 2f, 1f, 1f);

            Assert.Less(left.x, right.x);
            Assert.Less(left.y, right.y);
            Assert.GreaterOrEqual(left.x, 0f);
            Assert.GreaterOrEqual(left.y, 0f);
            Assert.LessOrEqual(right.xMax, 1f);
            Assert.LessOrEqual(right.yMax, 1f);
        }
    }
}
