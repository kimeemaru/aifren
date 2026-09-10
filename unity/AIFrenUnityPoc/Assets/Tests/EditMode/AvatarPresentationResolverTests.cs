using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarPresentationResolverTests
    {
        [Test]
        public void StateWakeReactionCannotBypassAuthoritativeSleep()
        {
            GameObject root = new GameObject("bounded presentation fixture");
            try
            {
                var animation = root.AddComponent<AvatarAnimationController>();
                typeof(AvatarAnimationController).GetField("head", System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.NonPublic).SetValue(animation, root.transform);
                var resolver = root.AddComponent<AvatarPresentationResolver>(); resolver.Configure();
                resolver.Apply(new AIFren.UnityPoc.Protocol.PresentationMetadata {
                    awareness_mode = "asleep", pose = "sleeping", reaction = "wake" });
                var gesture = typeof(AvatarAnimationController).GetField("activeGesture", System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.NonPublic).GetValue(animation);
                Assert.That(gesture, Is.EqualTo(AvatarGestureIntent.None));
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void ReplacementAndRetirementNeverResurrectFinalPresentation()
        {
            var owner = new AIFren.UnityPoc.UI.ResponsePresentationTurn();
            Assert.That(owner.TryPublish(1), Is.False);
            owner.Begin(1); Assert.That(owner.TryPublish(1), Is.True);
            Assert.That(owner.TryPublish(1), Is.False);
            owner.Begin(2); Assert.That(owner.Retire(1), Is.False);
            Assert.That(owner.TryPublish(1), Is.False);
            Assert.That(owner.TryPublish(2), Is.True);
            owner.Reset(); Assert.That(owner.TryPublish(2), Is.False);
        }

        [Test]
        public void MetadataBodyOwnsOneReplyAndFallbackObeysHands()
        {
            var root = new GameObject("one reply fixture");
            try
            {
                var animation = root.AddComponent<AvatarAnimationController>();
                typeof(AvatarAnimationController).GetField("head", System.Reflection.BindingFlags.Instance |
                    System.Reflection.BindingFlags.NonPublic).SetValue(animation, root.transform);
                var resolver = root.AddComponent<AvatarPresentationResolver>(); resolver.Configure();
                resolver.ApplyReply(new AIFren.UnityPoc.Protocol.PresentationMetadata {
                    hands_mode = "occupied" }, AvatarGestureIntent.Thinking);
                Assert.That(animation.ActiveGesture, Is.EqualTo(AvatarGestureIntent.None));
                resolver.ApplyReply(new AIFren.UnityPoc.Protocol.PresentationMetadata {
                    gesture = "agreement", reaction = "startle" }, AvatarGestureIntent.HeadShake);
                Assert.That(animation.ActiveGesture, Is.EqualTo(AvatarGestureIntent.Nod));
                animation.RetireResponseMotion();
                Assert.That(animation.ActiveGesture, Is.EqualTo(AvatarGestureIntent.None));
                Assert.That(resolver.TryGesture(AvatarGestureIntent.Nod), Is.False, "interruption retains cooldown");
            }
            finally { Object.DestroyImmediate(root); }
        }

        private sealed class ExpressionWeights
        {
            public readonly System.Collections.Generic.Dictionary<object, float> Values =
                new System.Collections.Generic.Dictionary<object, float>();
            public void SetWeight(object key, float value) => Values[key] = value;
        }

        [Test]
        public void RetiredBodyAndSpeechPreservePersistentFaceButClearTheirOwnChannels()
        {
            var root = new GameObject("independent expression channels");
            try
            {
                var body = root.AddComponent<AvatarAnimationController>();
                var face = root.AddComponent<AvatarExpressionController>();
                var weights = new ExpressionWeights();
                var happy = new ExpressionKey(ExpressionPreset.happy);
                var mouth = new ExpressionKey(ExpressionPreset.aa);
                void Set(object owner, string name, object value) => owner.GetType().GetField(name,
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).SetValue(owner, value);
                Set(body, "runtimeExpression", weights);
                Set(body, "setWeightMethod", typeof(ExpressionWeights).GetMethod("SetWeight"));
                Set(body, "expressionPresentation", face);
                Set(body, "hasHappy", true); Set(body, "happyKey", happy); Set(body, "mouthKey", mouth);
                Set(face, "active", new AvatarExpressionCapability(happy, null));
                Set(face, "activeWeight", .7f);
                weights.SetWeight(happy, .7f); weights.SetWeight(mouth, .4f);
                body.PlayStateReaction("settle", false);
                body.RetireResponseMotion(); body.StopSpeech();
                Assert.That(weights.Values[happy], Is.EqualTo(.7f));
                Assert.That(weights.Values[mouth], Is.Zero);
                Assert.That(face.ActiveIntensity, Is.EqualTo(.7f));
                Set(face, "active", null); Set(face, "activeWeight", 0f);
                body.PlayStateReaction("settle", false); weights.SetWeight(happy, .1f);
                body.RetireResponseMotion();
                Assert.That(weights.Values[happy], Is.Zero, "Unowned transient reaction must not remain stuck.");
            }
            finally { Object.DestroyImmediate(root); }
        }

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

        [Test]
        public void ExplicitAvailableSnapshotRestoresGazeAndClearsPosture()
        {
            GameObject root = new GameObject("presentation-restoration-test");
            try
            {
                AvatarGazeController gaze = root.AddComponent<AvatarGazeController>();
                AvatarPresentationResolver resolver = root.AddComponent<AvatarPresentationResolver>();
                resolver.Configure();
                resolver.Apply(new AIFren.UnityPoc.Protocol.PresentationMetadata {
                    gaze_mode = "suppressed", vision_mode = "unavailable", posture_mode = "lying"
                });
                Assert.IsTrue(gaze.PresentationSuppressed);
                Assert.AreEqual("lying", resolver.PostureMode);

                resolver.Apply(new AIFren.UnityPoc.Protocol.PresentationMetadata {
                    vision_mode = "available", awareness_mode = "normal", posture_mode = ""
                });
                Assert.IsFalse(gaze.PresentationSuppressed);
                Assert.AreEqual(string.Empty, resolver.PostureMode);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }
    }
}
