using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class HiddenSubtitlePresenterTests
    {
        [Test]
        public void TmpTargetUsesOneVisibleTextAndASeparateMeasurementText()
        {
            GameObject root = CreateTargetObjects(out TMP_Text visible, out TMP_Text measurement);
            try
            {
                var target = new TmpHiddenSubtitleRenderTarget(
                    root, root.GetComponent<CanvasGroup>(), root.GetComponent<RectTransform>(), visible, measurement);

                Assert.IsTrue(root.activeSelf);
                Assert.IsFalse(visible.enabled);
                Assert.AreEqual(2, root.GetComponentsInChildren<TMP_Text>(true).Length,
                    "the target must not clone presentation or backing TMP objects");
                target.SetRenderable(true);
                Assert.IsTrue(visible.enabled);
                Assert.IsFalse(measurement.gameObject.activeSelf);
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void PreloadMeasuresWithoutMutatingTheVisibleText()
        {
            GameObject root = CreateTargetObjects(out TMP_Text visible, out TMP_Text measurement);
            try
            {
                Color intended = new Color(.98f, .62f, .78f, 1f);
                visible.color = intended;
                var target = new TmpHiddenSubtitleRenderTarget(
                    root, root.GetComponent<CanvasGroup>(), root.GetComponent<RectTransform>(), visible, measurement);

                target.Preload("One **two** three");
                Assert.AreEqual(1, target.LayoutPreparationCount);
                Assert.IsTrue(string.IsNullOrEmpty(visible.text));
                target.ShowPage("One **two** three", 1);

                Assert.AreEqual(1, target.LayoutPreparationCount);
                Assert.AreEqual(DialoguePresentationParser.FormatSubtitleText("One **two** three"), visible.text);
                Assert.AreEqual(int.MaxValue, visible.maxVisibleWords, "complete geometry is fixed; the target owns vertex alpha");
                Assert.AreEqual(0f, target.RenderedWordOpacity(1));
                Assert.AreEqual(intended, visible.color, "page activation must not replace the intended subtitle color");
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void RepeatedPageChangesKeepOneStableRendererAndUsePreparedLayout()
        {
            GameObject root = CreateTargetObjects(out TMP_Text visible, out TMP_Text measurement);
            try
            {
                visible.color = new Color(.98f, .62f, .78f, 1f);
                var target = new TmpHiddenSubtitleRenderTarget(
                    root, root.GetComponent<CanvasGroup>(), root.GetComponent<RectTransform>(), visible, measurement);
                Stopwatch preload = Stopwatch.StartNew();
                target.Preload("First prepared page");
                target.Preload("Second **prepared** page");
                preload.Stop();
                target.SetRenderable(true);
                Stopwatch activation = Stopwatch.StartNew();
                target.ShowPage("First prepared page", 2);
                target.ShowPage("Second **prepared** page", 0);
                activation.Stop();

                Assert.AreEqual(2, target.LayoutPreparationCount);
                Assert.AreEqual(2, root.GetComponentsInChildren<TMP_Text>(true).Length);
                Assert.AreEqual(0f, target.RenderedWordOpacity(0),
                    "a new page must establish zero visible opacity before it can render");
                Assert.AreEqual(new Color(.98f, .62f, .78f, 1f), visible.color);
                TestContext.Progress.WriteLine("subtitle preload={0:F3}ms two-page activation={1:F3}ms",
                    preload.Elapsed.TotalMilliseconds, activation.Elapsed.TotalMilliseconds);
                UnityEngine.Debug.Log("[AIFren Test Timing] subtitle preload=" +
                    preload.Elapsed.TotalMilliseconds.ToString("F3") + "ms two-page activation=" +
                    activation.Elapsed.TotalMilliseconds.ToString("F3") + "ms");
            }
            finally { Object.DestroyImmediate(root); }
        }

        private static GameObject CreateTargetObjects(out TMP_Text visible, out TMP_Text measurement)
        {
            GameObject root = new GameObject("subtitle-root", typeof(RectTransform), typeof(Canvas), typeof(CanvasGroup));
            root.GetComponent<RectTransform>().sizeDelta = new Vector2(900f, 220f);
            GameObject visibleObject = new GameObject("visible", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            GameObject measurementObject = new GameObject("measurement", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            visibleObject.transform.SetParent(root.transform, false);
            measurementObject.transform.SetParent(root.transform, false);
            visibleObject.GetComponent<RectTransform>().sizeDelta = new Vector2(900f, 220f);
            measurementObject.GetComponent<RectTransform>().sizeDelta = new Vector2(900f, 220f);
            visible = visibleObject.GetComponent<TMP_Text>();
            measurement = measurementObject.GetComponent<TMP_Text>();
            visible.font = TMP_Settings.defaultFontAsset;
            measurement.font = TMP_Settings.defaultFontAsset;
            measurementObject.SetActive(false);
            return root;
        }

        private sealed class Sink : IHiddenSubtitleRenderTarget
        {
            internal bool Renderable; internal float Alpha; internal int ClearCount;
            internal readonly List<string> Prepared = new List<string>();
            public void Preload(string page) { Prepared.Add("preload:" + page); }
            public void ShowPage(string page, int shownWords) { Prepared.Add(page + "|" + shownWords); }
            public void SetRenderable(bool value) { Renderable = value; }
            public void SetAlpha(float value) { Alpha = value; }
            public void Clear() { ClearCount++; }
            internal float[] Opacities;
            public void SetWordOpacities(float[] values, int count) { Opacities = values; }
        }

        [Test]
        public void NaturalRetirementPreservesAQueuedChunkPreload()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Preload("Future chunk");
            presenter.Begin(Session(new[] { "Current chunk" }, new[] { 0f, .1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .1f }, 0f);
            presenter.Tick(0f, true, true);
            presenter.OnPlaybackStopped(7, .1f);
            presenter.Tick(.1f, true, true);
            presenter.Tick(.23f, true, true);
            for (float now = .71f; now < 1.7f; now += .02f) presenter.Tick(now, true, true);

            Assert.IsFalse(presenter.IsActive);
            Assert.AreEqual(0, sink.ClearCount);
            CollectionAssert.Contains(sink.Prepared, "preload:Future chunk");
        }

        [Test]
        public void AcceptedReplacementSessionPreservesItsQueuedPreload()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Preload("Prepared replacement");
            presenter.Begin(Session(new[] { "Prepared replacement" }, new[] { 0f, .1f }));

            Assert.AreEqual(0, sink.ClearCount);
            Assert.IsTrue(presenter.IsActive);
            CollectionAssert.Contains(sink.Prepared, "preload:Prepared replacement");
        }

        [Test]
        public void InitialPageCommitsRenderableAtZeroAlpha()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f }, 0f);
            presenter.Tick(0f, true, true);
            Assert.IsTrue(sink.Renderable); Assert.AreEqual(0f, sink.Opacities[0]); Assert.AreEqual("One two|0", sink.Prepared[0]);
        }

        [Test]
        public void KnownPendingSynthesisNeverRevealsOnAWallClockTimeout()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));

            presenter.Tick(7f, true, true);

            Assert.AreEqual(HiddenSubtitleState.WaitingForPlaybackOrFallback, presenter.State);
            Assert.IsFalse(sink.Renderable);
            Assert.IsFalse(sink.Prepared.Exists(item => item.StartsWith("One two|")));
        }

        [Test]
        public void DelayedCpuPlaybackStartsHiddenPresentationFromAudioClock()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));
            presenter.Tick(6f, true, true);

            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f }, 6.5f);
            presenter.Tick(6.5f, true, true);

            Assert.AreEqual(HiddenSubtitleState.InitialFadeIn, presenter.State);
            Assert.IsTrue(sink.Renderable);
            Assert.AreEqual("One two|0", sink.Prepared[0]);
        }

        [Test]
        public void ExplicitNoAudioReleasesReadablePresentationWithFreshTiming()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));
            presenter.Tick(5f, true, true);

            presenter.OnAudioUnavailable(1, 5f);
            presenter.Tick(5f, true, true);

            Assert.AreEqual(HiddenSubtitleState.InitialFadeIn, presenter.State);
            Assert.AreEqual("One two|0", sink.Prepared[0]);
            presenter.Tick(5.21f, true, true);
            Assert.IsTrue(sink.Prepared.Contains("One two|2"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExplicitNoAudioCompletesEveryWordAndRetiresWithoutPlaybackStop(bool multiplePages)
        {
            GameObject root = CreateTargetObjects(out TMP_Text visible, out TMP_Text measurement);
            try
            {
                var target = new TmpHiddenSubtitleRenderTarget(root, root.GetComponent<CanvasGroup>(),
                    root.GetComponent<RectTransform>(), visible, measurement);
                var presenter = new HiddenSubtitlePresenter(target);
                var presented = new List<int>();
                presenter.WordPresented += presented.Add;
                presenter.Begin(Session(multiplePages ? new[] { "One two", "Three four" } : new[] { "One two three four" },
                    new[] { 0f, .2f, .4f, .6f }));
                presenter.Tick(10f, true, true);
                Assert.IsFalse(visible.enabled, "pending synthesis must not start a fallback timer");
                presenter.OnAudioUnavailable(1, 10f);
                // There is no playback ID and no later playback-stopped event.
                // A temporary peek must not consume words or retire this session.
                presenter.SetSuppressed(true, 10f);
                presenter.Tick(11f, true, true);
                Assert.IsEmpty(presented);
                presenter.SetSuppressed(false, 11f);
                for (float now = 11f; now < 14f; now += .02f) presenter.Tick(now, true, true);

                CollectionAssert.AreEqual(new[] { 0, 1, 2, 3 }, presented);
                Assert.IsFalse(presenter.IsActive, "text-only completion must retire without inventing an audio event");
                Assert.IsFalse(visible.enabled);
                Assert.AreEqual(0f, root.GetComponent<CanvasGroup>().alpha);
            }
            finally { Object.DestroyImmediate(root); }
        }

        [Test]
        public void DueIncomingWordsArePresentedRatherThanSilentlyConsumed()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "First page", "Once upon a time" }, new[] { 0f, .01f, .02f, .03f, .04f, .05f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .01f, .02f, .03f, .04f, .05f }, 0f);
            for (float now = 0f; now < .8f; now += .06f) presenter.Tick(now, true, true);
            bool sawOpeningPending = false;
            foreach (string item in sink.Prepared) if (item == "Once upon a time|1") sawOpeningPending = true;
            Assert.IsTrue(sawOpeningPending);
        }

        [Test]
        public void TemporarySuppressionKeepsTheSessionAndRestoresTheCurrentPage()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two three four" }, new[] { 0f, .1f, .2f, .3f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .1f, .2f, .3f }, 0f);
            presenter.Tick(.06f, true, true);
            presenter.SetSuppressed(true, .06f);

            presenter.Tick(.45f, true, true);
            Assert.IsTrue(presenter.IsActive);
            Assert.IsFalse(sink.Renderable);

            presenter.SetSuppressed(false, .45f);
            presenter.Tick(.45f, true, true);
            Assert.IsTrue(sink.Renderable);
            Assert.IsTrue(presenter.IsActive);
            presenter.Tick(.62f, true, true);
            Assert.IsTrue(sink.Prepared.Exists(item => item == "One two three four|2"));
        }

        [Test]
        public void RepeatedUiSlideSuppressionDoesNotCancelOrResetTheSession()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            List<int> presented = new List<int>();
            presenter.WordPresented += presented.Add;
            presenter.Begin(Session(new[] { "One two three four" }, new[] { 0f, .2f, .4f, .6f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f, .4f, .6f }, 0f);
            presenter.Tick(.21f, true, true);

            presenter.SetSuppressed(true, .22f);
            presenter.Tick(.35f, false, true);
            presenter.SetSuppressed(false, .36f);
            presenter.Tick(.36f, true, true);
            presenter.SetSuppressed(true, .37f);
            presenter.Tick(.55f, false, true);
            presenter.SetSuppressed(false, .56f);
            presenter.Tick(.56f, true, true);

            Assert.IsTrue(presenter.IsActive);
            Assert.IsTrue(sink.Renderable);
            CollectionAssert.AreEqual(new[] { 0 }, presented, "peek must not consume due-but-unrendered words");
            for (float now = .57f; now < 1.3f; now += .02f) presenter.Tick(now, true, true);
            CollectionAssert.AreEqual(new[] { 0, 1, 2, 3 }, presented);
        }

        [Test]
        public void PeekReleaseWhileInactiveDoesNotSuppressTheNextResponse()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "Response one" }, new[] { 0f, .1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .1f }, 0f);
            presenter.Tick(.11f, true, true);
            presenter.OnPlaybackStopped(7, .11f);
            presenter.Tick(.11f, true, true);
            presenter.Tick(.24f, true, true);
            for (float now = .71f; now < 1.7f; now += .02f) presenter.Tick(now, true, true);
            Assert.IsFalse(presenter.IsActive);

            presenter.SetSuppressed(true, .71f);
            Assert.IsTrue(presenter.IsSuppressed);
            presenter.SetSuppressed(false, .90f);
            Assert.IsFalse(presenter.IsSuppressed);

            presenter.Begin(new SubtitleSession(
                new List<string> { "Response two appears" },
                SubtitleTimingPlan.BuildPageWordRanges(new List<string> { "Response two appears" }),
                new List<float> { 0f, .1f, .2f }, 2, 1f));
            presenter.OnPlaybackStarted(2, 8, new List<float> { 0f, .1f, .2f }, 1f);
            presenter.Tick(1f, true, true);

            Assert.IsTrue(presenter.IsActive);
            Assert.IsFalse(presenter.IsSuppressed);
            Assert.IsTrue(sink.Renderable);
        }

        [Test]
        public void CommittedShowCancellationClearsSuppressionAndCannotResumeTheSession()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .1f }, 0f);
            presenter.Tick(0f, true, true);
            presenter.SetSuppressed(true, .01f);

            presenter.Cancel();
            presenter.SetSuppressed(false, .20f);
            presenter.Tick(.20f, true, true);

            Assert.IsFalse(presenter.IsActive);
            Assert.IsFalse(presenter.IsSuppressed);
            Assert.IsFalse(sink.Renderable);
            Assert.AreEqual(1, sink.ClearCount);
        }

        [Test]
        public void RepeatedPeeksAcrossSeveralResponsesAlwaysReleaseSuppression()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            for (int generation = 1; generation <= 3; generation++)
            {
                float started = generation * 2f;
                var pages = new List<string> { "Response " + generation + " remains visible" };
                var times = new List<float> { 0f, .1f, .2f, .3f };
                presenter.Begin(new SubtitleSession(
                    pages, SubtitleTimingPlan.BuildPageWordRanges(pages), times, generation, started));
                presenter.OnPlaybackStarted(generation, 10 + generation, times, started);
                presenter.Tick(started + .1f, true, true);
                presenter.SetSuppressed(true, started + .11f);
                presenter.Tick(started + .25f, false, true);
                presenter.SetSuppressed(false, started + .26f);
                presenter.Tick(started + .26f, true, true);

                Assert.IsFalse(presenter.IsSuppressed, "peek " + generation + " left stale suppression");
                Assert.IsTrue(sink.Renderable, "response " + generation + " did not resume");
                presenter.Cancel();
            }
        }

        [Test]
        public void PlaybackStopWhileUiIsShownRemainsFinalizableAfterHide()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f }, 0f);
            presenter.Tick(.21f, true, true);
            presenter.SetSuppressed(true, .22f);
            presenter.OnPlaybackStopped(7, .30f);
            presenter.Tick(.30f, false, true);

            presenter.SetSuppressed(false, .31f);
            presenter.Tick(.31f, true, true);

            Assert.IsTrue(sink.Renderable);
            Assert.IsTrue(presenter.IsActive, "natural stop preserves pending readable words");
            for (float now = .32f; now < 1.7f; now += .02f) presenter.Tick(now, true, true);
            Assert.IsFalse(presenter.IsActive);
        }

        [Test]
        public void CompletedNonFinalPageDwellsBeforeItsScheduledSwap()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "First page", "Second page" }, new[] { 0f, .01f, 1f, 1.1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .01f, 1f, 1.1f }, 0f);

            presenter.Tick(0f, true, true);
            for (float now = .01f; now < .7f; now += .01f) presenter.Tick(now, true, true);
            Assert.AreEqual(HiddenSubtitleState.ShowingPage, presenter.State);
            Assert.AreEqual(1f, sink.Opacities[1]);
            presenter.Tick(.94f, true, true);
            Assert.AreEqual(HiddenSubtitleState.PageFadeOut, presenter.State);
            Assert.AreEqual(1f, sink.Alpha);
        }

        [Test]
        public void CancellationWhileSuppressedCannotBeResurrectedByRestore()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .1f }, 0f);
            presenter.Tick(0f, true, true);
            presenter.SetSuppressed(true, 0f);
            presenter.Cancel();
            presenter.SetSuppressed(false, .2f);

            Assert.IsFalse(presenter.IsActive);
            Assert.IsFalse(sink.Renderable);
        }

        [Test]
        public void InterruptedPlaybackStopsAdvancingWordsImmediately()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            List<int> presented = new List<int>();
            presenter.WordPresented += presented.Add;
            presenter.Begin(Session(new[] { "One two three four" }, new[] { 0f, .2f, .4f, .6f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f, .4f, .6f }, 0f);
            presenter.Tick(.21f, true, true);
            presenter.Tick(.24f, true, true);
            presenter.OnPlaybackStopped(7, .24f, interrupted: true);
            int countAtInterruption = presented.Count;

            presenter.Tick(.8f, true, true);
            presenter.Tick(1.2f, true, true);

            Assert.AreEqual(countAtInterruption, presented.Count);
        }

        [Test]
        public void SuppressionDoesNotConsumeAnUnseenIncomingPage()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "First page", "Once upon a time" }, new[] { 0f, .01f, .02f, .03f, .04f, .05f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .01f, .02f, .03f, .04f, .05f }, 0f);
            presenter.Tick(0f, true, true);
            for (float now = .01f; now < .54f; now += .01f) presenter.Tick(now, true, true);
            Assert.AreEqual(HiddenSubtitleState.PageFadeOut, presenter.State);

            presenter.SetSuppressed(true, .54f);
            presenter.Tick(1.5f, true, true);
            Assert.IsFalse(sink.Prepared.Exists(item => item.StartsWith("Once upon a time|")));

            presenter.SetSuppressed(false, 1.5f);
            presenter.Tick(1.5f, true, true);
            presenter.Tick(1.6f, true, true);
            Assert.IsTrue(sink.Prepared.Exists(item => item == "Once upon a time|1"));
        }

        [Test]
        public void PresentationUsesEveryGlobalWordExactlyOnceAcrossThreePages()
        {
            string[] pages = { "w0 w1 w2 w3", "w4 w5 w6 w7", "w8 w9" };
            List<float> times = Enumerable.Range(0, 10).Select(index => index * .03f).ToList();
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            List<int> presentedGlobalWordIds = new List<int>();
            presenter.WordPresented += globalWordId => presentedGlobalWordIds.Add(globalWordId);
            presenter.Begin(new SubtitleSession(new List<string>(pages), SubtitleTimingPlan.BuildPageWordRanges(pages), times, 1, 0f));
            presenter.OnPlaybackStarted(1, 7, new List<float>(times), 0f);

            for (int frame = 0; frame <= 80; frame++) presenter.Tick(frame * .05f, true, true);

            CollectionAssert.AreEqual(Enumerable.Range(0, 10).ToArray(), presentedGlobalWordIds);
        }

        private static SubtitleSession Session(string[] pages, float[] times)
        {
            List<string> list = new List<string>(pages);
            return new SubtitleSession(list, SubtitleTimingPlan.BuildPageWordRanges(list), new List<float>(times), 1, 0f);
        }
    }
}
