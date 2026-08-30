using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarAnimationControllerTests
    {
        [Test]
        public void SevenKeyDeveloperSequenceStillMatchesExactlyOnce()
        {
            string buffer = string.Empty;
            int matches = 0;
            for (int index = 0; index < 7; index++)
                AIFrenPocController.AdvanceHiddenSequence(ref buffer, '7', '7', 7, () => matches++);

            Assert.AreEqual(1, matches);
            AIFrenPocController.AdvanceHiddenSequence(ref buffer, 'x', '7', 7, () => matches++);
            Assert.AreEqual(string.Empty, buffer);
            Assert.AreEqual(1, matches);
        }

        [Test]
        public void FlightRecorderSixKeySequenceMatchesExactlyOnce()
        {
            string buffer = string.Empty;
            int matches = 0;
            for (int index = 0; index < 7; index++)
                AIFrenPocController.AdvanceHiddenSequence(ref buffer, '6', '6', 7, () => matches++);

            Assert.AreEqual(1, matches);
            Assert.AreEqual(string.Empty, buffer);
        }

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

        [Test]
        public void GestureEnvelopeEasesFromAndBackToTheCapturedBasePose()
        {
            float early = AvatarAnimationMath.GestureEnvelope(.08f);
            float peak = AvatarAnimationMath.GestureEnvelope(.5f);
            float late = AvatarAnimationMath.GestureEnvelope(.9f);

            Assert.AreEqual(0f, AvatarAnimationMath.GestureEnvelope(0f), .0001f);
            Assert.AreEqual(0f, AvatarAnimationMath.GestureEnvelope(1f), .0001f);
            Assert.Greater(early, 0f);
            Assert.Greater(peak, early);
            Assert.Greater(late, 0f);
            Assert.Less(late, peak);
        }

        [Test]
        public void CooldownSuppressesOnlyRepeatedGestures()
        {
            Assert.IsTrue(AvatarAnimationMath.IsSameGestureCoolingDown(AvatarGestureIntent.Nod, AvatarGestureIntent.Nod, 1f, 1.5f));
            Assert.IsFalse(AvatarAnimationMath.IsSameGestureCoolingDown(AvatarGestureIntent.Wave, AvatarGestureIntent.Nod, 1f, 1.5f));
            Assert.IsFalse(AvatarAnimationMath.IsSameGestureCoolingDown(AvatarGestureIntent.Nod, AvatarGestureIntent.Nod, 1.5f, 1.5f));
        }

    }
}
