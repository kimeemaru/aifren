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
        internal bool AudioUnavailable;
        internal float StartedAt;
        internal float PlaybackStartedAt;
        internal float AudioUnavailableAt;
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
        void SetWordOpacities(float[] values, int count);
        void Clear();
    }

    /// <summary>Single owner for hidden subtitle renderability, alpha, page text, and word presentation.</summary>
    internal sealed class HiddenSubtitlePresenter
    {
        internal const float InitialFadeSeconds = SubtitleStyle.WordFadeSeconds;
        internal const float PageFadeOutSeconds = SubtitleStyle.PageFadeSeconds;
        internal const float FinalHoldSeconds = SubtitleStyle.FinalDwellSeconds;
        internal const float FinalFadeSeconds = SubtitleStyle.FinalFadeSeconds;
        private readonly IHiddenSubtitleRenderTarget target;
        private SubtitleSession session;
        private HiddenSubtitleState state = HiddenSubtitleState.Inactive;
        private int pageIndex, startedWords, shownWords;
        private float stateAt, nextWordAt, suppressedAt;
        private bool suppressed, instant;
        private float wordsPerSecond = 7f;
        private float[] starts = Array.Empty<float>(), opacities = Array.Empty<float>();
        internal HiddenSubtitleState State => state;
        internal bool IsActive => state != HiddenSubtitleState.Inactive;
        internal bool IsSuppressed => suppressed;
        internal int PresentedOnPage => shownWords;
        internal bool HasFadingWord => startedWords > 0 && opacities[startedWords - 1] < 1f;
        internal event Action<int> WordPresented;
        internal event Action<int> PageActivated;
        internal HiddenSubtitlePresenter(IHiddenSubtitleRenderTarget target) { this.target = target; }
        internal void ConfigureReveal(float rate, bool instantText)
        { wordsPerSecond = Mathf.Max(.1f, rate); instant = instantText; }
        internal void Preload(string page) { if (!string.IsNullOrWhiteSpace(page)) target.Preload(page); }

        internal void Begin(SubtitleSession value)
        {
            if (value == null || value.Pages == null || value.Pages.Count == 0) { Cancel(); return; }
            if (!SubtitleTimingPlan.TryValidatePageDefinitions(value.Pages, value.Ranges,
                value.WordTimes != null ? value.WordTimes.Count : 0,
                DialoguePresentationParser.SpokenText, out string error))
            { Debug.LogError("[AIFren Subtitle] refusing invalid page ownership: " + error); Cancel(); return; }
            int largest = 0;
            foreach (var range in value.Ranges) largest = Mathf.Max(largest, range.LastWordIndex - range.FirstWordIndex + 1);
            if (starts.Length < largest) { starts = new float[largest]; opacities = new float[largest]; }
            target.SetAlpha(0f); target.SetRenderable(false);
            session = value; startedWords = shownWords = 0;
            state = HiddenSubtitleState.WaitingForPlaybackOrFallback; stateAt = value.StartedAt;
        }
        internal void OnPlaybackStarted(int generation, int playbackId, List<float> schedule, float now)
        {
            if (session == null || session.Generation != generation || !IsActive) return;
            if (schedule == null || schedule.Count != session.WordTimes.Count) return;
            // A timing refinement for the same playback must not restart its clock or word fades.
            if (session.PlaybackStarted && session.PlaybackId != playbackId) return;
            session.WordTimes = schedule;
            if (!session.PlaybackStarted) session.PlaybackStartedAt = now;
            session.PlaybackStarted = true; session.PlaybackId = playbackId;
        }
        internal void OnAudioUnavailable(int generation, float now)
        {
            if (session == null || session.Generation != generation || !IsActive || session.PlaybackStarted) return;
            if (!session.AudioUnavailable) session.AudioUnavailableAt = now;
            session.AudioUnavailable = true;
        }
        internal void OnPlaybackStopped(int playbackId, float now, bool interrupted = false)
        {
            if (session == null || !IsActive || (playbackId > 0 && session.PlaybackId > 0 && playbackId != session.PlaybackId)) return;
            if (interrupted) { Cancel(); return; }
            // Natural audio completion does not discard words still pending at the user's reading speed.
            session.PlaybackStopped = true;
        }
        internal void SetSuppressed(bool value, float now)
        {
            if (suppressed == value) return;
            suppressed = value;
            if (value) { suppressedAt = now; target.SetRenderable(false); return; }
            if (!IsActive || session == null) return;
            if (state == HiddenSubtitleState.WaitingForPlaybackOrFallback) return;
            float pause = Mathf.Max(0f, now - suppressedAt);
            for (int i = 0; i < startedWords; i++) starts[i] += pause;
            nextWordAt += pause; stateAt += pause;
            // Preserve visible fade progress. Only the audio schedule continues during peek.
            target.SetRenderable(true); Apply(now);
        }
        internal void Cancel()
        {
            session = null; state = HiddenSubtitleState.Inactive; suppressed = false;
            target.SetAlpha(0f); target.SetRenderable(false); target.Clear();
        }
        private void RetireNaturally()
        {
            session = null; state = HiddenSubtitleState.Inactive;
            target.SetAlpha(0f); target.SetRenderable(false);
        }
        internal void Tick(float now, bool uiHidden, bool enabled)
        {
            if (!enabled) { Cancel(); return; }
            if (session == null || !uiHidden || suppressed) return;
            if (state == HiddenSubtitleState.WaitingForPlaybackOrFallback)
            {
                if (!session.PlaybackStarted && !session.AudioUnavailable) return;
                BeginPage(now, 0);
            }
            if (state == HiddenSubtitleState.PageFadeOut && now - stateAt >= PageFadeOutSeconds)
                BeginPage(now, pageIndex + 1);
            if (state == HiddenSubtitleState.FinalFadeOut && now - stateAt >= FinalFadeSeconds)
            { RetireNaturally(); return; }

            if (state == HiddenSubtitleState.InitialFadeIn || state == HiddenSubtitleState.ShowingPage)
            {
                int due = DueOnPage(now);
                if (startedWords < due && (startedWords == 0 || instant || now >= nextWordAt))
                {
                    do { starts[startedWords++] = now; } while (instant && startedWords < due);
                    nextWordAt = now + 1f / wordsPerSecond;
                }
            }
            Apply(now);
            if (state == HiddenSubtitleState.InitialFadeIn && startedWords > 0 && opacities[0] >= 1f)
                state = HiddenSubtitleState.ShowingPage;
            if (state == HiddenSubtitleState.InitialFadeIn || state == HiddenSubtitleState.ShowingPage)
            {
                int count = PageCount;
                if (shownWords == count && Elapsed(now) >= session.WordTimes[session.Ranges[pageIndex].LastWordIndex])
                {
                    float opaqueAt = starts[count - 1] + (instant ? 0f : InitialFadeSeconds);
                    if (pageIndex + 1 < session.Pages.Count)
                    {
                        // Borrow dwell from the existing audio gap; do not shift later timestamps.
                        float nextDueAt = ClockOrigin + session.WordTimes[session.Ranges[pageIndex + 1].FirstWordIndex];
                        float exitAt = Mathf.Max(opaqueAt + SubtitleStyle.PageDwellSeconds, nextDueAt - PageFadeOutSeconds);
                        if (now >= exitAt) { state = HiddenSubtitleState.PageFadeOut; stateAt = now; }
                    }
                    else if (session.PlaybackStopped || session.AudioUnavailable)
                    { state = HiddenSubtitleState.FinalHold; stateAt = opaqueAt; }
                }
            }
            if (state == HiddenSubtitleState.FinalHold && now >= stateAt + FinalHoldSeconds)
            { state = HiddenSubtitleState.FinalFadeOut; stateAt = now; }
        }
        private int PageCount => session.Ranges[pageIndex].LastWordIndex - session.Ranges[pageIndex].FirstWordIndex + 1;
        private float ClockOrigin => session.PlaybackStarted ? session.PlaybackStartedAt : session.AudioUnavailableAt;
        private float Elapsed(float now) => Mathf.Max(0f, now - ClockOrigin);
        private int DueOnPage(float now)
        {
            if (instant) return PageCount;
            int global = session.Ranges[pageIndex].FirstWordIndex;
            while (global <= session.Ranges[pageIndex].LastWordIndex && session.WordTimes[global] <= Elapsed(now)) global++;
            return global - session.Ranges[pageIndex].FirstWordIndex;
        }
        private void BeginPage(float now, int index)
        {
            pageIndex = index; startedWords = shownWords = 0; nextWordAt = now;
            Array.Clear(opacities, 0, opacities.Length);
            target.SetRenderable(false); target.SetAlpha(0f);
            target.ShowPage(session.Pages[index], 0); target.SetWordOpacities(opacities, PageCount);
            state = HiddenSubtitleState.InitialFadeIn; stateAt = now;
            target.SetRenderable(true); PageActivated?.Invoke(index);
        }
        private void Apply(float now)
        {
            float alpha = 1f;
            if (state == HiddenSubtitleState.PageFadeOut) alpha = 1f - Mathf.Clamp01((now - stateAt) / PageFadeOutSeconds);
            else if (state == HiddenSubtitleState.FinalFadeOut) alpha = 1f - Mathf.Clamp01((now - stateAt) / FinalFadeSeconds);
            for (int i = 0; i < startedWords; i++)
                opacities[i] = instant ? 1f : Mathf.SmoothStep(0f, 1f, Mathf.Clamp01((now - starts[i]) / InitialFadeSeconds));
            target.SetAlpha(alpha); target.ShowPage(session.Pages[pageIndex], startedWords);
            target.SetWordOpacities(opacities, PageCount);
            // A due/start timestamp at zero opacity is not a displayed word.
            while (shownWords < startedWords && opacities[shownWords] >= 1f / 255f && alpha > 0f)
                { int word = session.Ranges[pageIndex].FirstWordIndex + shownWords++; WordPresented?.Invoke(word); }
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
        private Vector2 layoutSize;
        private float[] wordOpacities;
        private int opacityCount;
        private int[] characterWords = Array.Empty<int>();
        private byte[][] baseAlphas = Array.Empty<byte[]>();
        internal int MeshPreparationCount { get; private set; }
        internal int LayoutPreparationCount { get; private set; }

        internal TmpHiddenSubtitleRenderTarget(GameObject root, CanvasGroup group, RectTransform viewport,
            TMP_Text visible, TMP_Text measurement)
        {
            this.root = root; this.group = group; this.viewport = viewport;
            this.visible = visible; this.measurement = measurement;
            if (root != null) root.SetActive(true);
            SetRenderable(false);
            layoutSize = viewport.rect.size;
            WarmPresentationMesh();
            if (visible != null) visible.OnPreRenderText += MeshRebuilt;
        }

        internal void Dispose() { if (visible != null) visible.OnPreRenderText -= MeshRebuilt; }

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
                measurement.fontSize = SubtitleStyle.FontSize;
                measurement.GetPreferredValues(formatted, width, 0f);
            }
            if (visible != null)
            {
                visible.fontSize = SubtitleStyle.FontSize;
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
            activePage = null; shownWords = int.MinValue; wordOpacities = null; opacityCount = 0;
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
            Vector2 size = viewport.rect.size;
            bool resized = layoutSize != size;
            if (resized) { preparedPages.Clear(); preparedPageOrder.Clear(); layoutSize = size; }
            if (resized || !string.Equals(activePage, sourcePage, StringComparison.Ordinal))
            {
                PreparedPage prepared = GetOrPrepare(sourcePage);
                visible.fontSize = prepared.FontSize;
                visible.maxVisibleWords = int.MaxValue;
                visible.text = prepared.FormattedText; activePage = sourcePage;
                visible.ForceMeshUpdate(true, true);
                // TMP builds inactive geometry but invokes OnPreRenderText only
                // for an active renderer. Prepare its alpha map before enabling.
                if (!visible.isActiveAndEnabled) MeshRebuilt(visible.textInfo);
            }
            shownWords = Mathf.Max(0, visibleWords);
        }
        public void SetWordOpacities(float[] values, int count)
        {
            wordOpacities = values; opacityCount = count;
            if (ApplyVertexAlphas()) visible.UpdateVertexData(TMP_VertexDataUpdateFlags.Colors32);
        }
        private void MeshRebuilt(TMP_TextInfo info)
        {
            MeshPreparationCount++;
            if (characterWords.Length < info.characterCount) characterWords = new int[info.characterCount];
            int word = -1; bool boundary = true;
            for (int c = 0; c < info.characterCount; c++)
            {
                bool space = char.IsWhiteSpace(info.characterInfo[c].character);
                if (!space && boundary) word++;
                characterWords[c] = space ? -1 : word; boundary = space;
            }
            if (baseAlphas.Length != info.materialCount) baseAlphas = new byte[info.materialCount][];
            for (int m = 0; m < info.materialCount; m++)
            {
                var colors = info.meshInfo[m].colors32;
                if (baseAlphas[m] == null || baseAlphas[m].Length < colors.Length) baseAlphas[m] = new byte[colors.Length];
                for (int v = 0; v < colors.Length; v++) baseAlphas[m][v] = colors[v].a;
            }
            ApplyVertexAlphas(); // TMP uploads this complete mesh after the callback.
        }
        private bool ApplyVertexAlphas()
        {
            if (visible == null || baseAlphas.Length == 0) return false;
            var info = visible.textInfo; bool changed = false;
            for (int c = 0; c < info.characterCount; c++)
            {
                var ch = info.characterInfo[c];
                if (!ch.isVisible) continue;
                int word = characterWords[c];
                float alpha = wordOpacities != null && word >= 0 && word < opacityCount ? wordOpacities[word] : 0f;
                var colors = info.meshInfo[ch.materialReferenceIndex].colors32;
                for (int v = ch.vertexIndex; v < ch.vertexIndex + 4; v++)
                {
                    byte next = (byte)Mathf.RoundToInt(baseAlphas[ch.materialReferenceIndex][v] * Mathf.Clamp01(alpha));
                    changed |= colors[v].a != next; colors[v].a = next;
                }
            }
            return changed;
        }
        internal float RenderedWordOpacity(int word)
        {
            if (visible == null) return 0f;
            var info = visible.textInfo;
            for (int c = 0; c < info.characterCount; c++)
                if (characterWords[c] == word && info.characterInfo[c].isVisible)
                {
                    var ch = info.characterInfo[c];
                    return info.meshInfo[ch.materialReferenceIndex].colors32[ch.vertexIndex].a / 255f;
                }
            return 0f;
        }
        private PreparedPage GetOrPrepare(string sourcePage)
        {
            if (preparedPages.TryGetValue(sourcePage, out PreparedPage cached)) return cached;
            LayoutPreparationCount++;
            string full = DialoguePresentationParser.FormatSubtitleText(sourcePage);
            float width = Mathf.Max(1f, viewport.rect.width - 36f);
            float height = Mathf.Max(1f, viewport.rect.height - 20f);
            float size = SubtitleStyle.FontSize;
            TMP_Text sizingText = measurement != null ? measurement : visible;
            for (; size >= SubtitleStyle.MinimumFontSize; size -= 1f)
            {
                sizingText.fontSize = size;
                if (sizingText.GetPreferredValues(full, width, 0f).y <= height) break;
            }
            var prepared = new PreparedPage { FormattedText = full, FontSize = Mathf.Max(SubtitleStyle.MinimumFontSize, size) };
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
