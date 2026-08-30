using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.IO;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarAnimationControllerTests
    {
        [Test]
        public void QaFramesStartHiddenEvenInDevelopmentBuilds()
        {
            if (AvatarQaVisibility.Visible) AvatarQaVisibility.Toggle();
            Assert.IsFalse(AvatarQaVisibility.Visible);
        }

        [Test]
        public void QaFrameVisibilityStillTogglesOnAndBackOff()
        {
            if (AvatarQaVisibility.Visible) AvatarQaVisibility.Toggle();
            try
            {
                AvatarQaVisibility.Toggle();
                Assert.IsTrue(AvatarQaVisibility.Visible);
                AvatarQaVisibility.Toggle();
                Assert.IsFalse(AvatarQaVisibility.Visible);
            }
            finally
            {
                if (AvatarQaVisibility.Visible) AvatarQaVisibility.Toggle();
            }
        }

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

        [Test]
        public void InPlaceVrmaHipsUsesTheVrmaReferenceSoAnAuthoredFirstFrameCrouchIsPreserved()
        {
            Vector3 firstSample = new Vector3(.001f, .320f, -.059f);
            Vector3 sourceReference = new Vector3(0f, .906f, .004f);
            Vector3 laterSample = new Vector3(.024f, .860f, .254f);
            Vector3 targetReference = new Vector3(0f, 1.220f, 0f);
            Vector3 targetBaseline = new Vector3(.010f, 1.145f, -.020f);
            float scale = targetReference.y / sourceReference.y;
            Vector3 targetBaselineOffsetInSourceSpace = (targetBaseline - targetReference) / scale;

            Vector3 rebasedFirst = AvatarVrmaGesturePlayer.CalculateInPlaceSourceHips(
                firstSample, sourceReference, targetBaselineOffsetInSourceSpace);
            Vector3 rebasedLater = AvatarVrmaGesturePlayer.CalculateInPlaceSourceHips(
                laterSample, sourceReference, targetBaselineOffsetInSourceSpace);
            Vector3 targetFirst = targetReference + (rebasedFirst - sourceReference) * scale;
            Vector3 targetLater = targetReference + (rebasedLater - sourceReference) * scale;

            Assert.Less((targetFirst - targetBaseline - (firstSample - sourceReference) * scale).magnitude, .0001f);
            Assert.Less((targetLater - targetBaseline - (laterSample - sourceReference) * scale).magnitude, .0001f);
        }

        [Test]
        public void OneShotVrmaTimeClampsWithoutWrappingToFrameZero()
        {
            Assert.AreEqual(0f, AvatarVrmaGesturePlayer.ClampOneShotTime(-.1f, 2.8f), .0001f);
            Assert.AreEqual(1.4f, AvatarVrmaGesturePlayer.ClampOneShotTime(1.4f, 2.8f), .0001f);
            Assert.AreEqual(2.8f, AvatarVrmaGesturePlayer.ClampOneShotTime(4f, 2.8f), .0001f);
            Assert.AreEqual(0f, AvatarVrmaGesturePlayer.ClampOneShotTime(1f, 0f), .0001f);
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        [Test]
        public void VrmaQaInventoryUsesDirectFilesAndDeterministicFilenameOrdering()
        {
            string directory = Path.Combine(Path.GetTempPath(), "aifren-vrma-qa-" + System.Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                File.WriteAllText(Path.Combine(directory, "zeta.vrma"), "test");
                File.WriteAllText(Path.Combine(directory, "Alpha.VRMA"), "test");
                File.WriteAllText(Path.Combine(directory, "ignore.txt"), "test");
                Directory.CreateDirectory(Path.Combine(directory, "nested"));
                File.WriteAllText(Path.Combine(directory, "nested", "hidden.vrma"), "test");

                var files = AvatarVrmaGesturePlayer.DiscoverQaFiles(directory);

                CollectionAssert.AreEqual(new[] { "Alpha.VRMA", "zeta.vrma" },
                    files.ConvertAll(Path.GetFileName));
            }
            finally
            {
                if (Directory.Exists(directory)) Directory.Delete(directory, true);
            }
        }
#endif
    }
}
