using System.Collections.Generic;
using System.Linq;
using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class HiddenSubtitlePresenterTests
    {
        private sealed class Sink : IHiddenSubtitleRenderTarget
        {
            internal bool Renderable; internal float Alpha; internal readonly List<string> Prepared = new List<string>();
            public void Prepare(string page, int shownWords, float newestWordAlpha) { Prepared.Add(page + "|" + shownWords); }
            public void SetRenderable(bool value) { Renderable = value; }
            public void SetAlpha(float value) { Alpha = value; }
            public void Clear() { }
        }

        [Test]
        public void InitialPageCommitsRenderableAtZeroAlpha()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "One two" }, new[] { 0f, .2f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .2f }, 0f);
            presenter.Tick(0f, true, true);
            Assert.IsTrue(sink.Renderable); Assert.AreEqual(0f, sink.Alpha); Assert.AreEqual("One two|0", sink.Prepared[0]);
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
            Assert.IsTrue(sink.Prepared.Exists(item => item == "One two three four|2"));
        }

        [Test]
        public void CompletedNonFinalPageFadesOutImmediatelyAfterFinalWordRamp()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "First page", "Second page" }, new[] { 0f, .01f, 1f, 1.1f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .01f, 1f, 1.1f }, 0f);

            presenter.Tick(0f, true, true);
            presenter.Tick(.13f, true, true);
            presenter.Tick(.26f, true, true);
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
        public void SuppressionDoesNotConsumeAnUnseenIncomingPage()
        {
            Sink sink = new Sink(); HiddenSubtitlePresenter presenter = new HiddenSubtitlePresenter(sink);
            presenter.Begin(Session(new[] { "First page", "Once upon a time" }, new[] { 0f, .01f, .02f, .03f, .04f, .05f }));
            presenter.OnPlaybackStarted(1, 7, new List<float> { 0f, .01f, .02f, .03f, .04f, .05f }, 0f);
            presenter.Tick(0f, true, true);
            presenter.Tick(.13f, true, true);
            presenter.Tick(.26f, true, true);
            Assert.AreEqual(HiddenSubtitleState.PageFadeOut, presenter.State);

            presenter.SetSuppressed(true, .26f);
            presenter.Tick(.80f, true, true);
            Assert.IsFalse(sink.Prepared.Exists(item => item.StartsWith("Once upon a time|")));

            presenter.SetSuppressed(false, .80f);
            presenter.Tick(.80f, true, true);
            presenter.Tick(.90f, true, true);
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
