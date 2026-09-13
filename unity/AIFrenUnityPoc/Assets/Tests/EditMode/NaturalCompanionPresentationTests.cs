using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class NaturalCompanionPresentationTests
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

        [Test] public void PreferenceControlsPreserveDefaultsDraftsAndUnrelatedSettings()
        {
            Assert.True(PresentationPreferences.IsIsolated);
            var root = new GameObject("synthetic companion controls", typeof(RectTransform), typeof(Canvas), typeof(AIFrenPocController));
            try
            {
                var c = root.GetComponent<AIFrenPocController>(); c.enabled = false;
                Field("theme").SetValue(c, PresentationThemes.Dark); Field("font").SetValue(c, TMP_Settings.defaultFontAsset);
                foreach (string method in new[] { "AddConversationStyleControls", "AddResponsiveSpeechControls", "AddAutomaticExpressionControls" })
                    typeof(AIFrenPocController).GetMethod(method, Private).Invoke(c, new object[] { root.transform, -20f });
                Button choice = FindButton(root, "Conversation Style");
                Toggle speech = root.GetComponentsInChildren<Toggle>().Single(x => x.name == "Responsive Speech");
                Toggle face = root.GetComponentsInChildren<Toggle>().Single(x => x.name == "Automatic Expressions");
                Assert.AreEqual("Roleplay", choice.GetComponentInChildren<TMP_Text>().text);
                Assert.True(speech.isOn); Assert.False(face.isOn);
                choice.onClick.Invoke(); Assert.AreEqual("Natural conversation", choice.GetComponentInChildren<TMP_Text>().text);
                speech.isOn = false; face.isOn = true;
                Receive(c, "roleplay", true, false, "off", false);
                Assert.False(speech.isOn); Assert.True(face.isOn); // unsolicited snapshot leaves all drafts
                FindButton(root, "Cancel Companion Style").onClick.Invoke();
                Assert.AreEqual("Roleplay", choice.GetComponentInChildren<TMP_Text>().text);
                Assert.False(speech.isOn); Assert.True(face.isOn); // one Cancel does not discard another draft
                FindButton(root, "Cancel Companion Speech").onClick.Invoke();
                FindButton(root, "Cancel Companion Expressions").onClick.Invoke();
                Assert.True(speech.isOn); Assert.False(face.isOn);
                Receive(c, "natural", true, true, "ready on CPU", true);
                Assert.AreEqual("Natural conversation", choice.GetComponentInChildren<TMP_Text>().text);
                Assert.True(face.isOn);
                FindButton(root, "Save Companion Style").onClick.Invoke();
                Assert.AreEqual("None", Field("savingCompanionPreference").GetValue(c).ToString(), "Disconnected Save must remain recoverable.");
                Assert.False(PresentationPreferences.HasKey("conversation_style"));
                Assert.False(PresentationPreferences.HasKey("automatic_expressions"));
                Assert.False(PresentationPreferences.HasKey("responsive_speech"));
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        [Test] public void PreferenceProtocolRoundTripsExplicitFalseAndReadiness()
        {
            string wire = AIFrenProtocol.SerializeCommand(new ClientCommand {
                command="set_companion_preferences", conversation_style="natural", responsive_speech=true, automatic_expressions=false });
            StringAssert.Contains("\"conversation_style\":\"natural\"", wire);
            StringAssert.Contains("\"responsive_speech\":true", wire);
            StringAssert.Contains("\"automatic_expressions\":false", wire);
            var message = AIFrenProtocol.ParseServerMessage("{\"type\":\"snapshot\",\"data\":{\"companion\":{\"conversation_style\":\"natural\",\"responsive_speech\":true,\"automatic_expressions\":true,\"automatic_expression_status\":\"ready on CPU\"}}}");
            Assert.True(message.data.companion.responsive_speech);
            Assert.AreEqual("ready on CPU", message.data.companion.automatic_expression_status);
        }

        [Test] public void InapplicableCompatibilitySpeechSettingIsHiddenWithoutChangingItsValue()
        {
            var root = new GameObject("speech setting capability", typeof(AIFrenPocController));
            try
            {
                var c = root.GetComponent<AIFrenPocController>(); c.enabled = false;
                var child = new GameObject("legacy", typeof(RectTransform), typeof(Toggle)); child.transform.SetParent(root.transform);
                var toggle = child.GetComponent<Toggle>(); Field("earlySpeechToggle").SetValue(c, toggle);
                var refresh = typeof(AIFrenPocController).GetMethod("RefreshTtsModelUi", Private);
                refresh.Invoke(c, new object[] { new TtsSnapshot { early_speech_configured=true, early_speech_supported=false } });
                Assert.False(child.activeSelf); Assert.True(toggle.isOn);
                refresh.Invoke(c, new object[] { new TtsSnapshot { early_speech_configured=true, early_speech_supported=true } });
                Assert.True(child.activeSelf); Assert.True(toggle.isOn);
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        [Test] public void InterruptedBeforeFirstPcmCancelsPendingSubtitleSession()
        {
            var root = new GameObject("interrupted committed speech", typeof(AIFrenPocController));
            try
            {
                var c = root.GetComponent<AIFrenPocController>(); c.enabled = false;
                var presenter = new HiddenSubtitlePresenter(new Sink());
                Field("theme").SetValue(c, PresentationThemes.Dark);
                var dot = new GameObject("status", typeof(RectTransform), typeof(Image)); dot.transform.SetParent(root.transform);
                Field("statusDot").SetValue(c, dot.GetComponent<Image>());
                foreach (string field in new[] { "statusLabel", "statusDetailLabel" })
                {
                    var label = new GameObject(field, typeof(RectTransform), typeof(TextMeshProUGUI)); label.transform.SetParent(root.transform);
                    Field(field).SetValue(c, label.GetComponent<TMP_Text>());
                }
                var input = new GameObject("input", typeof(RectTransform), typeof(TMP_InputField)); input.transform.SetParent(root.transform);
                var send = new GameObject("send", typeof(RectTransform), typeof(Button)); send.transform.SetParent(root.transform);
                Field("messageInput").SetValue(c, input.GetComponent<TMP_InputField>());
                Field("sendButton").SetValue(c, send.GetComponent<Button>());
                var pages = new List<string> { "This must not appear after interruption." };
                presenter.Begin(new SubtitleSession(pages, SubtitleTimingPlan.BuildPageWordRanges(pages),
                    Enumerable.Repeat(float.PositiveInfinity, 6).ToList(), 1, 0));
                Field("hiddenSubtitlePresenter").SetValue(c, presenter);
                Field("streamedSubtitleTurnId").SetValue(c, 4);
                Field("publishedSubtitleTurnId").SetValue(c, 4);
                Field("subtitleGeneration").SetValue(c, 1);
                typeof(AIFrenPocController).GetMethod("HandleCommittedSpeech", Private).Invoke(c,
                    new object[] { new BackendEventData { state="stopped", committed_stream=true,
                        interrupted=true, turn_id=4, playback_id=0 } });
                Assert.False(presenter.IsActive);
                Assert.True((bool)Field("committedSpeechRetired").GetValue(c));
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        [Test] public void AutomaticFaceReservesOnlyFaceAndAppliesOnceWithoutBodyRedispatch()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Owner.Begin(1);
            Assert.True(f.Owner.PublishFinal(1, f.Resolver, null, "*smiles and nods* Yes.", "Mira", true));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.AreEqual(AvatarGestureIntent.Nod, f.Body.ActiveGesture);
            Assert.True(f.Owner.PublishAutomatic(1, f.Resolver, new PresentationMetadata {
                origin="automatic", emotion="sad", intensity=.4f, has_intensity=true, gesture="disagreement", pose="sleeping" }));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(40).Within(.1));
            Assert.AreEqual(AvatarGestureIntent.Nod, f.Body.ActiveGesture);
            Assert.True(f.Resolver.AllowsLipSync);
            Assert.False(f.Owner.PublishAutomatic(1, f.Resolver, new PresentationMetadata { origin="automatic", emotion="happy" }));
        }

        [Test] public void ExplicitNeutralManualOverrideAndRetirementBeatAutomaticFace()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Owner.Begin(1);
            f.Owner.PublishFinal(1, f.Resolver, new PresentationMetadata { origin="act", emotion="neutral" }, "Okay.", "Mira", true);
            Assert.False(f.Owner.PublishAutomatic(1, f.Resolver, new PresentationMetadata { origin="automatic", emotion="happy" }));
            f.Reply(2, "Okay.", new PresentationMetadata { emotion="happy" });
            f.Face.SetExpression(f.Face.ActiveExpression.Id, .25f); f.Tick(.4f);
            f.Owner.Begin(3); f.Owner.PublishFinal(3, f.Resolver, null, "Okay.", "Mira", true);
            Assert.False(f.Owner.PublishAutomatic(3, f.Resolver, new PresentationMetadata { origin="automatic", emotion="sad" }));
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f));
            f.Owner.Begin(4); f.Owner.PublishFinal(4, f.Resolver, null, "Okay.", "Mira", true);
            f.Owner.Retire(4); f.Owner.Begin(5);
            Assert.False(f.Owner.PublishAutomatic(4, f.Resolver, new PresentationMetadata { origin="automatic", emotion="sad" }));
            Assert.False(f.Owner.PublishAutomatic(5, f.Resolver, new PresentationMetadata { origin="automatic", emotion="sad" }), "Unpublished output owns no face.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DisableOrStopSpendsOnlyThePendingAutomaticOpportunity(bool stopSpeech)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            AIFrenPocController c = CreateAutomaticDeliveryController(f);
            Receive(c, "natural", true, true, "Ready on CPU", false);
            f.Reply(90, "*nods* Okay.", new PresentationMetadata { emotion="happy", intensity=.6f, has_intensity=true });
            var owner = (ResponsePresentationTurn)Field("presentationTurn").GetValue(c);
            owner.Begin(1); owner.PublishFinal(1, f.Resolver, null, "*nods* I'm here.", "Mira", true);
            AvatarGestureIntent body = f.Body.ActiveGesture;

            if (stopSpeech) typeof(AIFrenPocController).GetMethod("StopSpeech", Private).Invoke(c, null);
            else Receive(c, "natural", true, false, "Off", true);

            Assert.False(owner.PublishAutomatic(1, f.Resolver, AutomaticSad()),
                "A result queued before Stop/disable must not change the face afterward.");
            Assert.False(owner.TryPublish(1), "Cancelling optional presentation does not reopen final publication.");
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(60).Within(.1));
            Assert.That(f.Body.ActiveGesture, Is.EqualTo(body));
            Assert.That((bool)Field("savedAutomaticExpressions").GetValue(c), Is.EqualTo(stopSpeech));

            Receive(c, "natural", true, true, "Ready on CPU", true);
            Assert.False(owner.PublishAutomatic(1, f.Resolver, AutomaticSad()), "Re-enabling cannot resurrect a spent opportunity.");
            owner.Begin(2); owner.PublishFinal(2, f.Resolver, null, "A new reply.", "Mira", true);
            Assert.True(owner.PublishAutomatic(2, f.Resolver, AutomaticSad()));
            owner.Begin(3); owner.PublishFinal(3, f.Resolver,
                new PresentationMetadata { origin="act", emotion="neutral" }, "Okay.", "Mira", false);
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.Zero);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void QueuedAutomaticEventHonorsTheEffectiveSavedSetting(bool enabled)
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            AIFrenPocController c = CreateAutomaticDeliveryController(f);
            f.Reply(90, "Okay.", new PresentationMetadata { emotion="happy", intensity=.6f, has_intensity=true });
            var owner = (ResponsePresentationTurn)Field("presentationTurn").GetValue(c);
            owner.Begin(1); owner.PublishFinal(1, f.Resolver, null, "*nods* Okay.", "Mira", true);
            AvatarGestureIntent body = f.Body.ActiveGesture;
            // Exercise the dispatch gate independently of acknowledgement cancellation.
            Field("savedAutomaticExpressions").SetValue(c, enabled);
            DeliverAutomatic(c, 1, AutomaticSad());
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(enabled ? 1 : 0), Is.EqualTo(enabled ? 40 : 60).Within(.1));
            Assert.That(f.Body.ActiveGesture, Is.EqualTo(body));
            Assert.False(owner.PublishAutomatic(1, f.Resolver, AutomaticSad()), "Delivery or rejection spends the one opportunity.");
        }

        [Test]
        public void CancellingAnUnsavedOffDraftKeepsTheEnabledAutomaticOpportunity()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            AIFrenPocController c = CreateAutomaticDeliveryController(f);
            Receive(c, "natural", true, true, "Ready on CPU", false);
            var owner = (ResponsePresentationTurn)Field("presentationTurn").GetValue(c);
            owner.Begin(1); owner.PublishFinal(1, f.Resolver, null, "Okay.", "Mira", true);
            Field("facePreferenceDirty").SetValue(c, true);
            var toggleObject = new GameObject("unsaved automatic off", typeof(RectTransform), typeof(Toggle));
            toggleObject.transform.SetParent(f.Root.transform);
            var toggle = toggleObject.GetComponent<Toggle>(); toggle.isOn = false;
            Field("automaticExpressionToggle").SetValue(c, toggle);
            Type preference = typeof(AIFrenPocController).GetNestedType("CompanionPreference", BindingFlags.NonPublic);
            typeof(AIFrenPocController).GetMethod("CancelCompanionPreferenceDraft", Private).Invoke(c,
                new[] { Enum.Parse(preference, "Face") });
            Assert.True(toggle.isOn);
            Assert.True((bool)Field("savedAutomaticExpressions").GetValue(c));
            Assert.True(owner.PublishAutomatic(1, f.Resolver, AutomaticSad()));
        }

        [Test] public void UncertainAndInvalidAutomaticProposalCannotFallThroughOrOverrideSleep()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Owner.Begin(1); f.Owner.PublishFinal(1, f.Resolver, null, "*smiles* Okay.", "Mira", true);
            Assert.False(f.Owner.PublishAutomatic(1, f.Resolver, null));
            Assert.False(f.Owner.PublishAutomatic(1, f.Resolver, new PresentationMetadata { origin="automatic", emotion="happy" }));
            f.Tick(.4f); Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            f.Owner.Begin(2); f.Owner.PublishFinal(2, f.Resolver, new PresentationMetadata { pose="sleeping" }, "...", "Mira", true);
            Assert.False(f.Owner.PublishAutomatic(2, f.Resolver, new PresentationMetadata { origin="automatic", emotion="happy" }));
            f.Owner.Begin(3); f.Owner.PublishFinal(3, f.Resolver, new PresentationMetadata { pose="awake" }, "Okay.", "Mira", true);
            Assert.False(f.Owner.PublishAutomatic(3, f.Resolver, new PresentationMetadata { origin="automatic", emotion="happy", has_intensity=true, intensity=float.NaN }));
        }

        [Test] public void CommittedTimingAppendsOneIdentityWithHonestStarvationOffsets()
        {
            var timeline = new CommittedSpeechTimeline(4, 8, "One two three four");
            var first = Chunk(0); Assert.True(timeline.TryAppend(first, 7, out _));
            CollectionAssert.AreEqual(new[] { 0f, .1f, float.PositiveInfinity, float.PositiveInfinity }, timeline.Schedule);
            var second = Chunk(1); second.playback_sample_offset = 30000; // .25s actual inserted silence
            Assert.True(timeline.TryAppend(second, 7, out _));
            CollectionAssert.AreEqual(new[] { 0f, .1f, 1.25f, 1.35f }, timeline.Schedule);
            Assert.True(timeline.Finished); Assert.AreEqual(4, timeline.ReceivedWords);
            Assert.False(timeline.TryAppend(second, 7, out _));
        }

        [Test] public void CommittedTimingRejectsSkippedRepeatedWrongWordsAndOwnership()
        {
            foreach (Action<BackendEventData> mutate in new Action<BackendEventData>[] {
                d=>d.turn_id=5, d=>d.playback_id=9, d=>d.sequence=1, d=>d.word_offset=1,
                d=>d.sample_offset=1, d=>d.sample_rate=0, d=>d.chunk_text="One invented",
                d=>d.complete_text="Different utterance", d=>d.final_chunk=true })
            {
                var timeline = new CommittedSpeechTimeline(4, 8, "One two three four");
                var chunk = Chunk(0); mutate(chunk);
                Assert.False(timeline.TryAppend(chunk, 7, out _)); Assert.AreEqual(0, timeline.ReceivedWords);
            }
            var valid = new CommittedSpeechTimeline(4, 8, "One two three four");
            Assert.True(valid.TryAppend(Chunk(0), 7, out _));
            Assert.False(valid.TryAppend(Chunk(0), 7, out _));
            var overlap = Chunk(1); overlap.playback_sample_offset = 23999;
            Assert.False(valid.TryAppend(overlap, 7, out _));
        }

        [Test] public void MissingAlignmentUsesExistingEstimateAndNeverDropsWords()
        {
            var timeline = new CommittedSpeechTimeline(4, 8, "One two three four");
            var chunk = Chunk(0); chunk.word_start_seconds = new[] { float.NaN, float.PositiveInfinity };
            Assert.True(timeline.TryAppend(chunk, 7, out _));
            Assert.That(timeline.Schedule[0], Is.GreaterThanOrEqualTo(0));
            Assert.That(timeline.Schedule[1], Is.GreaterThanOrEqualTo(timeline.Schedule[0]));
            Assert.True(float.IsPositiveInfinity(timeline.Schedule[2]));
        }

        [Test] public void ResponsiveSubtitleRefinementNeverRestartsOrRevealsAnUnpreparedSuffix()
        {
            var sink = new Sink(); var presenter = new HiddenSubtitlePresenter(sink);
            presenter.ConfigureReveal(7, true);
            var pages = new List<string> { "One two three four" };
            var schedule = new List<float> { 0, .1f, float.PositiveInfinity, float.PositiveInfinity };
            var session = new SubtitleSession(pages, SubtitleTimingPlan.BuildPageWordRanges(pages), schedule, 1, 0);
            presenter.Begin(session); var shown = new List<int>(); presenter.WordPresented += shown.Add;
            presenter.OnPlaybackStarted(1, 8, schedule, 0);
            presenter.Tick(.2f, true, true); presenter.Tick(5, true, true);
            CollectionAssert.AreEqual(new[] { 0, 1 }, shown);
            Assert.True(presenter.IsActive);
            presenter.OnPlaybackStarted(1, 8, new List<float> { 0, .1f, 6, 6.1f }, 5);
            Assert.AreEqual(0, session.PlaybackStartedAt, "Later chunks keep the utterance clock.");
            presenter.Tick(6.2f, true, true);
            presenter.OnPlaybackStopped(8, 6.3f);
            CollectionAssert.AreEqual(new[] { 0, 1, 2, 3 }, shown);
            Assert.True(presenter.IsActive, "Final dwell follows actual completion.");
            presenter.Tick(10, true, true); presenter.Tick(11, true, true);
            Assert.False(presenter.IsActive);
        }

        [Test] public void ResponsiveSubtitleInterruptionDropsOnlyPresentationAndNoLateTimingRevivesIt()
        {
            var sink = new Sink(); var presenter = new HiddenSubtitlePresenter(sink);
            var pages = new List<string> { "One two three four" };
            var times = new List<float> { 0, .1f, float.PositiveInfinity, float.PositiveInfinity };
            presenter.Begin(new SubtitleSession(pages, SubtitleTimingPlan.BuildPageWordRanges(pages), times, 1, 0));
            presenter.OnPlaybackStarted(1, 8, times, 0); presenter.Tick(.2f, true, true);
            presenter.OnPlaybackStopped(8, .3f, true);
            presenter.OnPlaybackStarted(1, 8, new List<float> { 0, .1f, 1, 1.1f }, 1);
            presenter.Tick(2, true, true); Assert.False(presenter.IsActive);
        }

        private static BackendEventData Chunk(int sequence) => new BackendEventData {
            committed_stream=true, state="playback_started", turn_id=4, playback_id=8,
            complete_text="One two three four", sequence=sequence, word_offset=sequence*2, word_count=2,
            chunk_text=sequence==0 ? "One two" : "three four", sample_offset=sequence*24000,
            sample_count=24000, sample_rate=24000, playback_sample_offset=sequence*24000,
            final_chunk=sequence==1, duration_seconds=1, word_start_seconds=new[] { 0f, .1f }, alignment_kind="token" };
        private static FieldInfo Field(string name) => typeof(AIFrenPocController).GetField(name, Private);
        private static PresentationMetadata AutomaticSad() => new PresentationMetadata {
            origin="automatic", emotion="sad", intensity=.4f, has_intensity=true };
        private static AIFrenPocController CreateAutomaticDeliveryController(FacialEmoteProjectionTests.Fixture f)
        {
            // Prevent Awake/Start from loading any visual asset or normal preferences.
            f.Root.SetActive(false);
            var loader = f.Root.AddComponent<AvatarLoader>(); loader.enabled = false;
            var c = f.Root.AddComponent<AIFrenPocController>(); c.enabled = false;
            Field("avatarLoader").SetValue(c, loader);
            Field("avatarAnimation").SetValue(c, f.Body);
            return c;
        }
        private static void DeliverAutomatic(AIFrenPocController c, int turn, PresentationMetadata metadata) =>
            typeof(AIFrenPocController).GetMethod("HandleServerMessage", Private).Invoke(c,
                new object[] { new ServerMessage { type="event", @event=new BackendEvent {
                    type="automatic_expression", data=new BackendEventData { turn_id=turn, presentation=metadata } } } });
        private static Button FindButton(GameObject root, string name) => root.GetComponentsInChildren<Button>().Single(x => x.name==name);
        private static void Receive(AIFrenPocController c, string style, bool speech, bool face, string status, bool ack) =>
            typeof(AIFrenPocController).GetMethod("ReceiveCompanionPreferences", Private).Invoke(c, new object[] { style, speech, face, status, ack });
        private sealed class Sink : IHiddenSubtitleRenderTarget
        {
            public void Preload(SubtitlePage page) { }
            public void ShowPage(SubtitlePage page, int words) { }
            public void SetRenderable(bool value) { }
            public void SetAlpha(float value) { }
            public void SetWordOpacities(float[] values, int count) { }
            public void Clear() { }
        }
    }
}
