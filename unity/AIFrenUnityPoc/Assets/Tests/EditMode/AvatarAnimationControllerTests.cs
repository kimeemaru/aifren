using AIFren.UnityPoc.Avatar;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarAnimationControllerTests
    {
        [Test]
        public void EnvelopeSamplingUsesPlaybackTimeAndReturnsNeutralOutsideSpeech()
        {
            float[] envelope = { 0f, .5f, 1f };

            Assert.AreEqual(0f, AvatarAnimationMath.SampleEnvelope(envelope, -.1f, 2f));
            Assert.AreEqual(.5f, AvatarAnimationMath.SampleEnvelope(envelope, 1f, 2f), .001f);
            Assert.AreEqual(1f, AvatarAnimationMath.SampleEnvelope(envelope, 3f, 2f), .001f);
        }

        [Test]
        public void MouthSmoothingAttacksAndReleasesWithoutOvershoot()
        {
            float opened = AvatarAnimationMath.SmoothMouth(0f, 1f, .05f);
            float closed = AvatarAnimationMath.SmoothMouth(opened, 0f, .05f);

            Assert.Greater(opened, 0f);
            Assert.LessOrEqual(opened, 1f);
            Assert.Less(closed, opened);
            Assert.GreaterOrEqual(closed, 0f);
        }
    }
}
