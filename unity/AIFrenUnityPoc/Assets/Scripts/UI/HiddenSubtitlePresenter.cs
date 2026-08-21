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
        void Prepare(string page, int shownWords, float newestWordAlpha);
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
        internal const float WordFadeSeconds = .12f;
        internal const float CatchupSpacingSeconds = .05f;
        internal const float FinalHoldSeconds = .12f;
        internal const float FinalFadeSeconds = .45f;

        private readonly IHiddenSubtitleRenderTarget target;
        private SubtitleSession session;
        private HiddenSubtitleState state = HiddenSubtitleState.Inactive;
        private int pageIndex;
        private int shownWords;
        private float newestWordAt;
        private float stateAt;
        private float nextCatchupAt;
        private bool suppressed;

        internal HiddenSubtitleState State => state;
        internal bool IsActive => state != HiddenSubtitleState.Inactive;
        internal event Action<int> WordPresented;

        internal HiddenSubtitlePresenter(IHiddenSubtitleRenderTarget target) { this.target = target; }

        internal void Begin(SubtitleSession value)
        {
            Cancel();
            if (value == null || value.Pages == null || value.Pages.Count == 0) return;
            if (!SubtitleTimingPlan.TryValidatePageDefinitions(value.Pages, value.Ranges,
                value.WordTimes != null ? value.WordTimes.Count : 0, out string validationError))
            {
                Debug.LogError("[AIFren Subtitle] refusing invalid page ownership: " + validationError);
                return;
            }

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
        internal void Cancel() { session = null; state = HiddenSubtitleState.Inactive; target.SetAlpha(0f); target.SetRenderable(false); target.Clear(); }

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
            else if (state == HiddenSubtitleState.FinalFadeOut && now - stateAt >= FinalFadeSeconds) { Cancel(); return; }

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
            pageIndex = index; shownWords = 0; newestWordAt = float.NegativeInfinity; nextCatchupAt = now;
            // Atomic preparation: disabled + alpha zero before text/mesh state.
            target.SetRenderable(false); target.SetAlpha(0f); target.Prepare(session.Pages[index], 0, 0f);
            state = nextState; stateAt = now;
            target.SetAlpha(0f); target.SetRenderable(true);
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
            shownWords++; newestWordAt = now; nextCatchupAt = now + CatchupSpacingSeconds;
            target.Prepare(session.Pages[pageIndex], shownWords, 0f);
            WordPresented?.Invoke(globalWordIndex);
        }
        private bool PageComplete(int due, float now)
        {
            SubtitlePageWordRange range = session.Ranges[pageIndex];
            return shownWords >= range.LastWordIndex - range.FirstWordIndex + 1 &&
                due >= shownWords && Elapsed(now) >= session.WordTimes[range.LastWordIndex] && now - newestWordAt >= WordFadeSeconds;
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
            float wordAlpha = shownWords > 0 ? Mathf.Clamp01((now - newestWordAt) / WordFadeSeconds) : 0f;
            target.SetAlpha(alpha); target.Prepare(session.Pages[pageIndex], shownWords, wordAlpha);
        }
    }

    internal sealed class TmpHiddenSubtitleRenderTarget : IHiddenSubtitleRenderTarget
    {
        private readonly GameObject root;
        private readonly CanvasGroup group;
        private readonly RectTransform viewport;
        private readonly TMP_Text front;
        private readonly IList<TMP_Text> backings;

        internal TmpHiddenSubtitleRenderTarget(GameObject root, CanvasGroup group, RectTransform viewport, TMP_Text front, IList<TMP_Text> backings)
        { this.root = root; this.group = group; this.viewport = viewport; this.front = front; this.backings = backings; }

        public void SetRenderable(bool renderable) { if (root != null) root.SetActive(renderable); }
        public void SetAlpha(float alpha) { if (group != null) group.alpha = Mathf.Clamp01(alpha); }
        public void Clear() { if (front != null) front.text = string.Empty; foreach (TMP_Text text in backings) if (text != null) text.text = string.Empty; }
        public void Prepare(string page, int shownWords, float newestWordAlpha)
        {
            if (front == null) return;
            string full = DialoguePresentationParser.FormatSubtitleText(page);
            front.text = full;
            float width = Mathf.Max(1f, viewport.rect.width - 36f), height = Mathf.Max(1f, viewport.rect.height - 20f), size = 35f;
            for (; size >= 23f; size -= 1f) { front.fontSize = size; if (front.GetPreferredValues(full, width, 0f).y <= height) break; }
            front.fontSize = Mathf.Max(23f, size);
            Apply(front, shownWords, newestWordAlpha);
            foreach (TMP_Text text in backings)
            {
                if (text == null) continue;
                text.text = full; text.fontSize = front.fontSize; text.fontStyle = front.fontStyle; text.alignment = front.alignment; text.color = Color.black;
                Apply(text, shownWords, newestWordAlpha);
            }
        }
        private static void Apply(TMP_Text text, int shownWords, float newestWordAlpha)
        {
            text.ForceMeshUpdate(); TMP_TextInfo info = text.textInfo; int word = -1; bool inWord = false;
            byte newest = (byte)Mathf.RoundToInt(Mathf.Clamp01(newestWordAlpha) * 255f);
            for (int index = 0; index < info.characterCount; index++)
            {
                TMP_CharacterInfo c = info.characterInfo[index];
                if (char.IsWhiteSpace(c.character)) inWord = false; else if (!inWord) { word++; inWord = true; }
                byte alpha = word < shownWords - 1 ? (byte)255 : word == shownWords - 1 ? newest : (byte)0;
                if (!c.isVisible || c.materialReferenceIndex < 0) continue;
                Color32[] colors = info.meshInfo[c.materialReferenceIndex].colors32;
                for (int vertex = 0; vertex < 4; vertex++) { Color32 color = colors[c.vertexIndex + vertex]; color.a = alpha; colors[c.vertexIndex + vertex] = color; }
            }
            text.UpdateVertexData(TMP_VertexDataUpdateFlags.Colors32);
        }
    }
}
