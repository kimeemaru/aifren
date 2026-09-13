using System;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AutomaticExpressionLifetimeTests
    {
        [Test]
        public void NewAcceptedReplyAndAbstentionRetireThePreviousAutomaticSmile()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Owner.Begin(1);
            Assert.True(f.Owner.PublishFinal(1, f.Resolver, null, "I'm glad it worked.", "Mira", true));
            Assert.True(f.Owner.PublishAutomatic(1, f.Resolver, Automatic("happy")));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(45).Within(.1));

            f.Owner.Begin(2);
            Assert.True(f.Owner.PublishFinal(2, f.Resolver, null, "I'm here with you.", "Mira", true));
            Assert.False(f.Owner.PublishAutomatic(2, f.Resolver, null));
            f.Tick(.4f);

            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero,
                "An uncertain new reply must not inherit a prior automatic smile indefinitely.");
            Assert.That(f.Resolver.LastFaceRequest, Is.Not.EqualTo("neutral"),
                "Retiring a presentation layer is not a semantic neutral request.");
        }

        [TestCase("act")]
        [TestCase(null)]
        public void RetirementRestoresThePriorExplicitBaseThroughTheExistingBlend(string origin)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Reply(1, "Okay.", new PresentationMetadata { origin=origin, emotion="sad", intensity=.3f, has_intensity=true });
            PublishAutomatic(f, 2, "happy");
            f.Owner.Begin(3);
            f.Owner.PublishFinal(3, f.Resolver, null, "I'm listening.", "Mira", true);
            f.Tick(.05f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.InRange(.1f, 44.9f));
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.InRange(.1f, 29.9f));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(30).Within(.1));
            Assert.False(f.Face.HasAutomaticExpression);
            Assert.AreEqual("none", f.Resolver.LastFaceRequest);
        }

        [Test]
        public void ManualChoiceAfterAutomaticAdmissionCannotBeClearedByItsOldCleanup()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            AutomaticExpressionLease old = Lease(f.Owner);
            f.Face.SetExpression(f.Face.ActiveExpression.Id, .25f);
            f.Tick(.4f);
            int revision = f.Face.TargetRevision;
            Assert.False(f.Face.ExpireAutomaticExpression(old, float.MaxValue));
            f.Owner.CancelAutomatic();
            f.Owner.Begin(2);
            f.Owner.PublishFinal(2, f.Resolver, new PresentationMetadata { origin="act", emotion="neutral" }, "Okay.", "Mira");
            f.Tick(.4f);
            Assert.True(f.Face.ManualOverride);
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f).Within(.001));
            Assert.AreEqual(revision, f.Face.TargetRevision);
        }

        [TestCase("sad", 30f)]
        [TestCase("neutral", 0f)]
        public void NewExplicitFaceAndNeutralCannotBeUndoneByOldAutomaticRetirement(string emotion, float sadWeight)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            AutomaticExpressionLease old = Lease(f.Owner);
            f.Reply(2, "Okay.", new PresentationMetadata { origin="act", emotion=emotion, intensity=.3f, has_intensity=true });
            int revision = f.Face.TargetRevision;
            Assert.False(f.Resolver.RetireAutomaticExpression(old));
            Assert.False(f.Face.ExpireAutomaticExpression(old, float.MaxValue));
            Assert.False(f.Owner.FinishSpeech(1));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(sadWeight).Within(.1));
            Assert.AreEqual(revision, f.Face.TargetRevision);
            Assert.AreEqual("act", f.Resolver.LastFaceOrigin);
            Assert.AreEqual(emotion, f.Resolver.LastFaceRequest);
        }

        [Test]
        public void EightSecondExpiryReleasesOnlyTheOptionalLayer()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Reply(1, "Okay.", new PresentationMetadata { emotion="sad", intensity=.3f, has_intensity=true });
            var lease = f.Resolver.BeginAutomaticExpressionLease();
            Assert.True(f.Face.TrySetAutomaticPresetExpression(ExpressionPreset.happy, .45f, lease, 10f));
            f.Tick(.4f);
            Assert.AreEqual(18f, f.Face.AutomaticExpiresAt);
            Assert.False(f.Face.ExpireAutomaticExpression(lease, 17.999f));
            Assert.True(f.Face.ExpireAutomaticExpression(lease, 18f));
            f.Tick(.4f);
            Assert.False(f.Face.HasAutomaticExpression);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(30).Within(.1));
        }

        [Test]
        public void EqualAutomaticRenewalExtendsItsLeaseWithoutRestartingAndOldExpiryIsInert()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            var first = new AutomaticExpressionLease();
            var newer = new AutomaticExpressionLease();
            Assert.True(f.Face.TrySetAutomaticPresetExpression(ExpressionPreset.happy, .45f, first, 10f));
            f.Tick(.05f);
            int revision = f.Face.TargetRevision;
            float weight = f.Renderer.GetBlendShapeWeight(0);
            Assert.True(f.Face.TrySetAutomaticPresetExpression(ExpressionPreset.happy, .45f, newer, 14f));
            Assert.AreEqual(revision, f.Face.TargetRevision);
            Assert.AreEqual(weight, f.Renderer.GetBlendShapeWeight(0));
            Assert.AreEqual(22f, f.Face.AutomaticExpiresAt);
            Assert.False(f.Face.ExpireAutomaticExpression(first, 30f));
            Assert.False(f.Face.RetireAutomaticExpression(first));
            Assert.False(f.Face.ExpireAutomaticExpression(newer, 21.999f));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(45).Within(.1));
            Assert.True(f.Face.ExpireAutomaticExpression(newer, 22f));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
        }

        [Test]
        public void SpeechCompletionUsesAPlainPresentationDwellAndRejectsLateProposals()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            AutomaticExpressionLease lease = Lease(f.Owner);
            Assert.False(f.Owner.FinishSpeech(2));
            Assert.True(f.Owner.FinishSpeech(1));
            float expiry = f.Face.AutomaticExpiresAt;
            Assert.That(expiry, Is.EqualTo(Time.unscaledTime + AvatarExpressionController.AutomaticCompletionDwellSeconds).Within(.001));
            Assert.False(f.Face.ExpireAutomaticExpression(lease, expiry - .01f));
            f.Tick(.05f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(45).Within(.1));
            Assert.False(f.Owner.PublishAutomatic(1, f.Resolver, Automatic("sad")));
            Assert.True(f.Face.ExpireAutomaticExpression(lease, expiry));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.AreNotEqual("neutral", f.Resolver.LastFaceRequest);
        }

        [Test]
        public void OlderResponseCleanupCannotReleaseANewerLeaseOnTheSameAvatar()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            AutomaticExpressionLease old = Lease(f.Owner);
            var newer = new ResponsePresentationTurn();
            newer.Begin(1); // Turn numbers can restart; object identity must still differ.
            newer.PublishFinal(1, f.Resolver, null, "A different owned reply.", "Mira", true);
            Assert.True(newer.PublishAutomatic(1, f.Resolver, Automatic("sad")));
            f.Tick(.4f); int revision = f.Face.TargetRevision;
            f.Owner.Reset();
            Assert.False(f.Face.ExpireAutomaticExpression(old, float.MaxValue));
            Assert.AreEqual(revision, f.Face.TargetRevision);
            Assert.True(f.Face.HasAutomaticExpression);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(45).Within(.1));
        }

        [Test]
        public void DifferentResponseOwnerRetiresThePreviousOverlayDuringCompletionDwell()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Reply(90, "Okay.", new PresentationMetadata { origin="act", emotion="sad", intensity=.3f, has_intensity=true });
            PublishAutomatic(f, 1, "happy");
            AutomaticExpressionLease old = Lease(f.Owner);
            Assert.True(f.Owner.FinishSpeech(1));
            Assert.True(f.Face.HasAutomaticExpression, "Natural completion permits a short presentation dwell.");
            Assert.False(f.Resolver.ApplyAutomaticExpression(Automatic("sad"), old),
                "A completed response cannot accept a late optional proposal during its dwell.");

            var newer = new ResponsePresentationTurn();
            newer.Begin(1); // A replacement owner can reuse a process-local turn number.
            Assert.True(newer.PublishFinal(1, f.Resolver, null, "I'm here with you.", "Mira", true));
            Assert.False(newer.PublishAutomatic(1, f.Resolver, null));
            Assert.False(f.Face.HasAutomaticExpression,
                "The next accepted reply must retire an older owner's completed but still visible overlay.");
            Assert.False(f.Owner.FinishSpeech(1));
            Assert.False(f.Face.ExpireAutomaticExpression(old, float.MaxValue));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(30).Within(.1));
            Assert.AreNotEqual("neutral", f.Resolver.LastFaceRequest);
        }

        [Test]
        public void InterruptedSpeechUsesExactTurnOwnershipAndNoCompletionDwell()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 2, "happy");
            Assert.False(f.Owner.FinishSpeech(1, true));
            Assert.True(f.Face.HasAutomaticExpression);
            Assert.True(f.Owner.FinishSpeech(2, true));
            Assert.False(f.Face.HasAutomaticExpression);
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.False(f.Owner.PublishAutomatic(2, f.Resolver, Automatic("sad")));
        }

        [TestCase("stop")]
        [TestCase("disable")]
        [TestCase("character_reset")]
        public void ExistingNativeRetirementHooksReleaseAutomaticButKeepBaseAndBody(string action)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            AIFrenPocController controller = DeliveryController(f);
            f.Reply(90, "Okay.", new PresentationMetadata { origin="act", emotion="sad", intensity=.3f, has_intensity=true });
            var owner = (ResponsePresentationTurn)Field("presentationTurn").GetValue(controller);
            owner.Begin(1); owner.PublishFinal(1, f.Resolver, null, "*nods* Okay.", "Mira", true);
            Assert.True(owner.PublishAutomatic(1, f.Resolver, Automatic("happy")));
            f.Tick(.4f);
            var body = f.Body.ActiveGesture;
            if (action == "stop") Method("StopSpeech").Invoke(controller, null);
            else if (action == "disable") Method("ReceiveCompanionPreferences").Invoke(controller,
                new object[] { "natural", true, false, "Off", true });
            else owner.Reset(); // Existing reconnect/character/asset hook; state reset stays separately owned.
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(30).Within(.1));
            Assert.AreEqual(body, f.Body.ActiveGesture);
            Assert.False(owner.PublishAutomatic(1, f.Resolver, Automatic("happy")));
        }

        [Test]
        public void AutomaticRetirementNeverWritesProceduralMouthOrBlinkWeights()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            var mouth = new ExpressionKey(ExpressionPreset.aa);
            var blink = new ExpressionKey(ExpressionPreset.blink);
            f.Runtime.SetWeight(mouth, .4f); f.Runtime.SetWeight(blink, .3f);
            f.Owner.CancelAutomatic(); f.Tick(.4f);
            Assert.That(f.Runtime.GetWeight(mouth), Is.EqualTo(.4f));
            Assert.That(f.Runtime.GetWeight(blink), Is.EqualTo(.3f));
            Assert.True(f.Resolver.AllowsLipSync);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
        }

        [Test]
        public void BackendSleepingRestrictionRetiresAutomaticWithoutCreatingNeutral()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            PublishAutomatic(f, 1, "happy");
            f.Resolver.Apply(new PresentationMetadata { awareness_mode="asleep" });
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.False(f.Resolver.AllowsLipSync);
            Assert.AreEqual("none", f.Resolver.LastFaceRequest);
        }

        [TestCase(true, false)]
        [TestCase(true, true)]
        [TestCase(false, false)]
        public void WrongTurnOrPlaybackCompletionDoesNotExpireTheCurrentOverlay(bool committed, bool wrongTurn)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            var controller = DeliveryController(f);
            var owner = (ResponsePresentationTurn)Field("presentationTurn").GetValue(controller);
            owner.Begin(2); owner.PublishFinal(2, f.Resolver, null, "Okay.", "Mira", true);
            Assert.True(owner.PublishAutomatic(2, f.Resolver, Automatic("happy")));
            f.Tick(.4f); float expiry = f.Face.AutomaticExpiresAt;
            Field("streamedSubtitleTurnId").SetValue(controller, 2);
            Field("publishedSubtitleTurnId").SetValue(controller, 2);
            Field("committedSpeechTimeline").SetValue(controller, new CommittedSpeechTimeline(2, 80, "Okay."));
            Field("subtitleGeneration").SetValue(controller, 3);
            Field("subtitlePlaybackGeneration").SetValue(controller, 3);
            Field("subtitlePlaybackId").SetValue(controller, 80);
            var data = new BackendEventData { committed_stream=committed, state="stopped",
                turn_id=wrongTurn ? 1 : 2, playback_id=wrongTurn ? 80 : 70 };
            if (committed) Method("HandleCommittedSpeech").Invoke(controller, new object[] { data });
            else Method("HandleServerMessage").Invoke(controller, new object[] {
                new ServerMessage { type="event", @event=new BackendEvent { type="tts_state", data=data } } });
            Assert.AreEqual(expiry, f.Face.AutomaticExpiresAt);
            Assert.True(f.Face.HasAutomaticExpression);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(45).Within(.1));
        }

        private static void PublishAutomatic(FacialEmoteProjectionTests.Fixture f, int turn, string emotion)
        {
            f.Owner.Begin(turn);
            Assert.True(f.Owner.PublishFinal(turn, f.Resolver, null, "A new reply.", "Mira", true));
            Assert.True(f.Owner.PublishAutomatic(turn, f.Resolver, Automatic(emotion)));
            f.Tick(.4f);
        }

        private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;
        private static FieldInfo Field(string name) => typeof(AIFrenPocController).GetField(name, Private);
        private static MethodInfo Method(string name) => typeof(AIFrenPocController).GetMethod(name, Private);
        private static AutomaticExpressionLease Lease(ResponsePresentationTurn owner) =>
            (AutomaticExpressionLease)typeof(ResponsePresentationTurn).GetField("automaticLease", Private).GetValue(owner);
        private static AIFrenPocController DeliveryController(FacialEmoteProjectionTests.Fixture f)
        {
            f.Root.SetActive(false);
            var loader = f.Root.AddComponent<AvatarLoader>(); loader.enabled = false;
            var controller = f.Root.AddComponent<AIFrenPocController>(); controller.enabled = false;
            Field("avatarLoader").SetValue(controller, loader); Field("avatarAnimation").SetValue(controller, f.Body);
            return controller;
        }

        private static PresentationMetadata Automatic(string emotion) => new PresentationMetadata {
            origin = "automatic", emotion = emotion, intensity = .45f, has_intensity = true };
    }
}
