using System;
using System.Collections.Generic;
using TMPro;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    internal enum HiddenSubtitleState { Inactive, WaitingForPlaybackOrFallback, InitialFadeIn, ShowingPage, PageFadeOut, PrepareNextPage, PageFadeIn, FinalHold, FinalFadeOut }

    internal sealed class SubtitleSession
    {
        internal readonly List<string> Pages;
        internal readonly List<SubtitlePageWordRange> Ranges;
        internal List<float> WordTimes;
        internal int Generation;
        internal int PlaybackId;
        internal bool PlaybackStarted;
        internal bool PlaybackStopped;
        internal float StartedAt;
        internal float PlaybackStartedAt;
        internal float StopElapsed;

        internal SubtitleSession(List<string> pages, List<SubtitlePageWordRange> ranges, List<float> wordTimes, int generation, float startedAt)
        {
            Pages = pages; Ranges = ranges; WordTimes = wordTimes; Generation = generation; StartedAt = startedAt;
        }
    }

    internal interface IHiddenSubtitleRenderTarget
    {
        void Preload(string page);
        void ShowPage(string page, int shownWords);
        void SetRenderable(bool renderable);
        void SetAlpha(float alpha);
        void Clear();
    }

    /// <summary>Single owner for hidden subtitle renderability, alpha, page text, and word presentation.</summary>
    internal sealed class HiddenSubtitlePresenter
    {
        internal const float InitialFadeSeconds = .32f;
        internal const float PageFadeOutSeconds = .09f;
        internal const float PageFadeInSeconds = .12f;
        internal const float CatchupSpacingSeconds = .05f;
        internal const float FinalHoldSeconds = .12f;
        internal const float FinalFadeSeconds = .45f;

        private readonly IHiddenSubtitleRenderTarget target;
        private SubtitleSession session;
        private HiddenSubtitleState state = HiddenSubtitleState.Inactive;
        private int pageIndex;
        private int shownWords;
        private float stateAt;
        private float nextCatchupAt;
        private bool suppressed;

        internal HiddenSubtitleState State => state;
        internal bool IsActive => state != HiddenSubtitleState.Inactive;
        internal bool IsSuppressed => suppressed;
        internal event Action<int> WordPresented;
        internal event Action<int> PageActivated;

        internal HiddenSubtitlePresenter(IHiddenSubtitleRenderTarget target) { this.target = target; }

        internal void Preload(string page)
        {
            if (!string.IsNullOrWhiteSpace(page)) target.Preload(page);
        }

        internal void Begin(SubtitleSession value)
        {
            if (value == null || value.Pages == null || value.Pages.Count == 0) { Cancel(); return; }
            if (!SubtitleTimingPlan.TryValidatePageDefinitions(value.Pages, value.Ranges,
                value.WordTimes != null ? value.WordTimes.Count : 0,
                DialoguePresentationParser.SpokenText, out string validationError))
            {
                Debug.LogError("[AIFren Subtitle] refusing invalid page ownership: " + validationError);
                Cancel();
                return;
            }

            // Preserve pre-synthesis page measurements. Reset session
            // visibility without clearing the cache that chunk_queued built.
            target.SetAlpha(0f);
            target.SetRenderable(false);
            session = value;
            state = HiddenSubtitleState.WaitingForPlaybackOrFallback;
            stateAt = value.StartedAt;
        }
        internal void OnPlaybackStarted(int generation, int playbackId, List<float> schedule, float now)
        {
            if (session == null || session.Generation != generation || state == HiddenSubtitleState.Inactive) return;
            session.WordTimes = schedule; session.PlaybackStarted = true; session.PlaybackId = playbackId; session.PlaybackStartedAt = now;
        }
        internal void OnPlaybackStopped(int playbackId, float now)
        {
            if (session == null || state == HiddenSubtitleState.Inactive || (playbackId > 0 && session.PlaybackId > 0 && playbackId != session.PlaybackId)) return;
            session.PlaybackStopped = true; session.StopElapsed = Elapsed(now);
        }
        internal void SetSuppressed(bool value, float now)
        {
            if (suppressed == value) return;
            suppressed = value;
            if (value)
            {
                // A temporary UI peek changes only renderability.  The immutable
                // session, page ownership, and shown-word state continue intact.
                target.SetRenderable(false);
                return;
            }

            if (state == HiddenSubtitleState.Inactive || session == null) return;

            // Rebuild while non-renderable.  This restores only words that were
            // genuinely presented before suppression; timestamp-due words remain
            // pending for the normal bounded catch-up path in Tick.
            target.SetRenderable(false);
            target.SetAlpha(0f);
            Apply(now);
            target.SetRenderable(true);
        }
        internal void Cancel()
        {
            session = null;
            state = HiddenSubtitleState.Inactive;
            suppressed = false;
            target.SetAlpha(0f);
            target.SetRenderable(false);
            target.Clear();
        }
        private void RetireNaturally()
        {
            // A later queued audio chunk may already be measured. Natural
            // completion hides this session without erasing that work;
            // explicit cancellation still clears the cache.
            session = null; state = HiddenSubtitleState.Inactive;
            target.SetAlpha(0f); target.SetRenderable(false);
        }

        internal void Tick(float now, bool uiHidden, bool enabled)
        {
            if (session == null || !enabled) { if (!enabled) Cancel(); return; }
            if (!uiHidden || suppressed) return;
            if (state == HiddenSubtitleState.WaitingForPlaybackOrFallback)
            {
                if (session.PlaybackStarted || now - stateAt >= .9f) BeginPage(now, 0, true, HiddenSubtitleState.InitialFadeIn);
                else return;
            }

            int due = DueOnPage(now);
            if (state == HiddenSubtitleState.InitialFadeIn || state == HiddenSubtitleState.PageFadeIn || state == HiddenSubtitleState.ShowingPage)
            {
                PresentDueWords(now, due);
                if (session.PlaybackStopped) { BeginFinal(now); }
                else if (PageComplete(due, now) && pageIndex + 1 < session.Pages.Count) { state = HiddenSubtitleState.PageFadeOut; stateAt = now; }
            }
            else if (state == HiddenSubtitleState.PageFadeOut && now - stateAt >= PageFadeOutSeconds)
                BeginPage(now, pageIndex + 1, false, HiddenSubtitleState.PageFadeIn);
            else if (state == HiddenSubtitleState.FinalHold && now - stateAt >= FinalHoldSeconds) { state = HiddenSubtitleState.FinalFadeOut; stateAt = now; }
            else if (state == HiddenSubtitleState.FinalFadeOut && now - stateAt >= FinalFadeSeconds) { RetireNaturally(); return; }

            Apply(now);
        }

        private void BeginPage(float now, int index, bool initial, HiddenSubtitleState nextState)
        {
            if (index > 0)
            {
                SubtitlePageWordRange previous = session.Ranges[index - 1];
                SubtitlePageWordRange incoming = session.Ranges[index];
                Debug.Assert(incoming.FirstWordIndex == previous.LastWordIndex + 1,
                    "[AIFren Subtitle] non-contiguous page ownership at page " + index + ".");
            }
            pageIndex = index; shownWords = 0; nextCatchupAt = now;
            // Text and maxVisibleWords are committed while the sole visible
            // TMP is disabled and the CanvasGroup is transparent.  There is
            // no independently stateful staging renderer to promote.
            target.SetRenderable(false); target.SetAlpha(0f); target.ShowPage(session.Pages[index], 0);
            state = nextState; stateAt = now;
            target.SetAlpha(0f); target.SetRenderable(true);
            PageActivated?.Invoke(index);
            if (initial && DueOnPage(now) > 0) ShowOne(now);
        }

        private void PresentDueWords(float now, int due)
        {
            if (shownWords >= due) return;
            bool backlog = due - shownWords > 1;
            if (shownWords == 0 || !backlog || now >= nextCatchupAt) ShowOne(now);
        }
        private void ShowOne(float now)
        {
            SubtitlePageWordRange range = session.Ranges[pageIndex];
            int globalWordIndex = range.FirstWordIndex + shownWords;
            if (globalWordIndex < range.FirstWordIndex || globalWordIndex > range.LastWordIndex)
            {
                Debug.LogError("[AIFren Subtitle] attempted to present global word " + globalWordIndex +
                    " outside page " + pageIndex + " ownership " + range.FirstWordIndex + "-" + range.LastWordIndex + ".");
                return;
            }
            shownWords++; nextCatchupAt = now + CatchupSpacingSeconds;
            target.ShowPage(session.Pages[pageIndex], shownWords);
            WordPresented?.Invoke(globalWordIndex);
        }
        private bool PageComplete(int due, float now)
        {
            SubtitlePageWordRange range = session.Ranges[pageIndex];
            return shownWords >= range.LastWordIndex - range.FirstWordIndex + 1 &&
                due >= shownWords && Elapsed(now) >= session.WordTimes[range.LastWordIndex];
        }
        private void BeginFinal(float now) { state = HiddenSubtitleState.FinalHold; stateAt = now; }
        private int DueOnPage(float now)
        {
            float elapsed = session.PlaybackStopped ? session.StopElapsed : Elapsed(now);
            int global = 0; while (global < session.WordTimes.Count && session.WordTimes[global] <= elapsed) global++;
            SubtitlePageWordRange range = session.Ranges[pageIndex];
            return Mathf.Clamp(global - range.FirstWordIndex, 0, range.LastWordIndex - range.FirstWordIndex + 1);
        }
        private float Elapsed(float now) => Mathf.Max(0f, now - (session.PlaybackStarted ? session.PlaybackStartedAt : session.StartedAt));
        private void Apply(float now)
        {
            float alpha = 1f;
            if (state == HiddenSubtitleState.InitialFadeIn) { alpha = Mathf.Clamp01((now - stateAt) / InitialFadeSeconds); if (alpha >= 1f) state = HiddenSubtitleState.ShowingPage; }
            else if (state == HiddenSubtitleState.PageFadeOut) alpha = 1f - Mathf.Clamp01((now - stateAt) / PageFadeOutSeconds);
            else if (state == HiddenSubtitleState.PageFadeIn) { alpha = Mathf.Clamp01((now - stateAt) / PageFadeInSeconds); if (alpha >= 1f) state = HiddenSubtitleState.ShowingPage; }
            else if (state == HiddenSubtitleState.FinalFadeOut) alpha = 1f - Mathf.Clamp01((now - stateAt) / FinalFadeSeconds);
            target.SetAlpha(alpha); target.ShowPage(session.Pages[pageIndex], shownWords);
        }
    }

    internal sealed class TmpHiddenSubtitleRenderTarget : IHiddenSubtitleRenderTarget
    {
        private sealed class PreparedPage
        {
            internal string FormattedText;
            internal float FontSize;
        }

        private const int PreparedPageLimit = 32;
        private readonly GameObject root;
        private readonly CanvasGroup group;
        private readonly RectTransform viewport;
        private readonly TMP_Text visible;
        private readonly TMP_Text measurement;
        private readonly Dictionary<string, PreparedPage> preparedPages = new Dictionary<string, PreparedPage>();
        private readonly Queue<string> preparedPageOrder = new Queue<string>();
        private string activePage;
        private int shownWords = int.MinValue;
        internal int LayoutPreparationCount { get; private set; }

        internal TmpHiddenSubtitleRenderTarget(GameObject root, CanvasGroup group, RectTransform viewport,
            TMP_Text visible, TMP_Text measurement)
        {
            this.root = root; this.group = group; this.viewport = viewport;
            this.visible = visible; this.measurement = measurement;
            if (root != null) root.SetActive(true);
            SetRenderable(false);
            WarmPresentationMesh();
        }

        private void WarmPresentationMesh()
        {
            // TMP performs sizeable one-time parser, glyph, material, and mesh
            // allocations the first time a non-empty subtitle is measured and
            // rendered. Pay that cold cost while the presenter is created and
            // fully transparent, rather than on the first user response/audio
            // boundary. This is deterministic initialization, not a delay.
            const string sample = "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789.,!?;:'- **readable emphasis**";
            string formatted = DialoguePresentationParser.FormatSubtitleText(sample);
            float width = viewport != null ? Mathf.Max(1f, viewport.rect.width - 36f) : 640f;
            if (measurement != null)
            {
                measurement.fontSize = 35f;
                measurement.GetPreferredValues(formatted, width, 0f);
            }
            if (visible != null)
            {
                visible.fontSize = 35f;
                visible.maxVisibleWords = 0;
                visible.text = formatted;
                visible.ForceMeshUpdate(true, true);
                visible.text = string.Empty;
                visible.maxVisibleWords = 0;
                visible.ForceMeshUpdate(true, true);
            }
            activePage = null;
            shownWords = int.MinValue;
        }

        public void SetRenderable(bool renderable)
        {
            if (root != null && !root.activeSelf) root.SetActive(true);
            if (visible != null) visible.enabled = renderable;
        }
        public void SetAlpha(float alpha) { if (group != null) group.alpha = Mathf.Clamp01(alpha); }
        public void Clear()
        {
            activePage = null; shownWords = int.MinValue;
            preparedPages.Clear(); preparedPageOrder.Clear();
            if (visible != null) { visible.text = string.Empty; visible.maxVisibleWords = 0; }
        }
        public void Preload(string page)
        {
            string sourcePage = page ?? string.Empty;
            GetOrPrepare(sourcePage);
        }
        public void ShowPage(string page, int visibleWords)
        {
            if (visible == null) return;
            string sourcePage = page ?? string.Empty;
            if (!string.Equals(activePage, sourcePage, StringComparison.Ordinal))
            {
                PreparedPage prepared = GetOrPrepare(sourcePage);
                visible.maxVisibleWords = 0;
                visible.fontSize = prepared.FontSize;
                visible.text = prepared.FormattedText;
                activePage = sourcePage;
                shownWords = int.MinValue;
            }

            int clamped = Mathf.Max(0, visibleWords);
            if (shownWords == clamped) return;
            visible.maxVisibleWords = clamped;
            shownWords = clamped;
        }
        private PreparedPage GetOrPrepare(string sourcePage)
        {
            if (preparedPages.TryGetValue(sourcePage, out PreparedPage cached)) return cached;
            LayoutPreparationCount++;
            string full = DialoguePresentationParser.FormatSubtitleText(sourcePage);
            float width = Mathf.Max(1f, viewport.rect.width - 36f);
            float height = Mathf.Max(1f, viewport.rect.height - 20f);
            float size = 35f;
            TMP_Text sizingText = measurement != null ? measurement : visible;
            for (; size >= 23f; size -= 1f)
            {
                sizingText.fontSize = size;
                if (sizingText.GetPreferredValues(full, width, 0f).y <= height) break;
            }
            var prepared = new PreparedPage { FormattedText = full, FontSize = Mathf.Max(23f, size) };
            preparedPages[sourcePage] = prepared;
            preparedPageOrder.Enqueue(sourcePage);
            while (preparedPageOrder.Count > PreparedPageLimit)
            {
                string expired = preparedPageOrder.Dequeue();
                if (!string.Equals(expired, activePage, StringComparison.Ordinal)) preparedPages.Remove(expired);
            }
            return prepared;
        }
    }
}
