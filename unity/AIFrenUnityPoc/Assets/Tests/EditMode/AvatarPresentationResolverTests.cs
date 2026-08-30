using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarPresentationResolverTests
    {
        [Test]
        public void HappyAndAmusedResolveToStandardExpressionCandidates()
        {
            Assert.IsTrue(AvatarPresentationResolver.TryResolveEmotion("happy", out ExpressionPreset[] happy));
            Assert.AreEqual(ExpressionPreset.happy, happy[0]);
            Assert.IsTrue(AvatarPresentationResolver.TryResolveEmotion("amused", out ExpressionPreset[] amused));
            Assert.AreEqual(ExpressionPreset.relaxed, amused[0]);
            Assert.IsTrue(AvatarPresentationResolver.TryResolveEmotion("relaxed", out ExpressionPreset[] relaxed));
            Assert.AreEqual(ExpressionPreset.relaxed, relaxed[0]);
        }

        [Test]
        public void UnknownOrAbsentEmotionDoesNotResolveToConcreteMorph()
        {
            Assert.IsFalse(AvatarPresentationResolver.TryResolveEmotion("confused", out _));
            Assert.IsFalse(AvatarPresentationResolver.TryResolveEmotion(null, out _));
        }

        [TestCase("greeting", AvatarGestureIntent.Nod)]
        [TestCase("agreement", AvatarGestureIntent.Nod)]
        [TestCase("disagreement", AvatarGestureIntent.HeadShake)]
        [TestCase("thinking", AvatarGestureIntent.Thinking)]
        [TestCase("encouragement", AvatarGestureIntent.Nod)]
        [TestCase("surprise", AvatarGestureIntent.Shrug)]
        public void SemanticGestureMapsOnlyToStableExistingCapabilities(string semantic, AvatarGestureIntent expected)
        {
            Assert.IsTrue(AvatarPresentationResolver.TryResolveGesture(semantic, out AvatarGestureIntent actual));
            Assert.AreEqual(expected, actual);
        }

        [Test]
        public void UnknownGestureProducesNoOneShotGesture()
        {
            Assert.IsFalse(AvatarPresentationResolver.TryResolveGesture("bow", out AvatarGestureIntent gesture));
            Assert.AreEqual(AvatarGestureIntent.None, gesture);
        }

        [Test]
        public void OccupiedHandsFilterArmGesturesButKeepHeadOnlyGestures()
        {
            Assert.IsFalse(AvatarPresentationResolver.GestureAllowed(
                AvatarGestureIntent.Wave, "occupied", "normal"));
            Assert.IsFalse(AvatarPresentationResolver.GestureAllowed(
                AvatarGestureIntent.Thinking, "occupied", "normal"));
            Assert.IsFalse(AvatarPresentationResolver.GestureAllowed(
                AvatarGestureIntent.Shrug, "occupied", "normal"));
            Assert.IsTrue(AvatarPresentationResolver.GestureAllowed(
                AvatarGestureIntent.Nod, "occupied", "normal"));
            Assert.IsFalse(AvatarPresentationResolver.GestureAllowed(
                AvatarGestureIntent.Nod, "free", "asleep"));
        }

        [Test]
        public void SpeechModesGateLipSyncWithoutDisablingConstrainedSpeech()
        {
            Assert.IsTrue(AvatarPresentationResolver.SpeechAllowsLipSync("normal"));
            Assert.IsTrue(AvatarPresentationResolver.SpeechAllowsLipSync("constrained"));
            Assert.IsTrue(AvatarPresentationResolver.SpeechAllowsLipSync("mumble"));
            Assert.IsFalse(AvatarPresentationResolver.SpeechAllowsLipSync("nonverbal"));
            Assert.IsFalse(AvatarPresentationResolver.SpeechAllowsLipSync("unavailable"));
        }

    }
}
