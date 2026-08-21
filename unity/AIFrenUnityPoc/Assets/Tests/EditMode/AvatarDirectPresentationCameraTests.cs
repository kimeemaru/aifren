using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarDirectPresentationCameraTests
    {
        [Test]
        public void NeutralPresentationKeepsTheBaselineCamera()
        {
            AvatarDirectCameraView view = AvatarDirectPresentationCamera.FromPresentation(25f,
                new AvatarPresentationValues { x = 0f, y = 0f, scale = 1f });

            Assert.AreEqual(25f, view.fieldOfView, .0001f);
            Assert.AreEqual(Vector2.zero, view.lensShift);
        }

        [Test]
        public void ZoomUsesTheSpecifiedTangentRelationship()
        {
            AvatarDirectCameraView view = AvatarDirectPresentationCamera.FromPresentation(30f,
                new AvatarPresentationValues { scale = 2f });
            float expected = Mathf.Rad2Deg * 2f * Mathf.Atan(Mathf.Tan(15f * Mathf.Deg2Rad) / 2f);

            Assert.AreEqual(expected, view.fieldOfView, .0001f);
        }

        [Test]
        public void PositivePresentationTranslationMovesTheCameraLensOppositeTheAvatar()
        {
            AvatarDirectCameraView view = AvatarDirectPresentationCamera.FromPresentation(25f,
                new AvatarPresentationValues { x = .25f, y = -.4f, scale = 1.5f });

            Assert.AreEqual(new Vector2(-.25f, .4f), view.lensShift);
        }

        [Test]
        public void NegativeLensShiftMovesTheAvatarRightForRawImageParity()
        {
            GameObject cameraObject = new GameObject("Direct camera mapping test");
            Camera camera = cameraObject.AddComponent<Camera>();
            camera.fieldOfView = 60f;
            camera.usePhysicalProperties = true;
            camera.lensShift = new Vector2(-.25f, 0f);
            Assert.AreEqual(.75f, camera.WorldToViewportPoint(new Vector3(0f, 0f, 10f)).x, .0001f);
            Object.DestroyImmediate(cameraObject);
        }
    }
}
