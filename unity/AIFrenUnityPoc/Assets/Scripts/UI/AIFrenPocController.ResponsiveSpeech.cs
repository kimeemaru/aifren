using System;
using System.Collections.Generic;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private CommittedSpeechTimeline committedSpeechTimeline;
        private int publishedSubtitleTurnId;
        private bool committedSpeechRetired;

        private bool HandleCommittedSpeech(BackendEventData data)
        {
            if (!data.committed_stream) return false;
            if (data.turn_id <= 0 || data.turn_id != streamedSubtitleTurnId
                || data.turn_id != publishedSubtitleTurnId || committedSpeechRetired) return true;
            if (data.state == "starting")
            {
                streamedSubtitleMode = true;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("tts_submit", data.turn_id, data.playback_id);
#endif
                ApplyStatus("speaking", data.message);
                return true;
            }
            if (data.state == "chunk_queued") return true;
            if (data.state == "playback_started")
            {
                bool first = committedSpeechTimeline == null;
                var timeline = committedSpeechTimeline ?? new CommittedSpeechTimeline(
                    data.turn_id, data.playback_id, data.complete_text);
                if (!timeline.TryAppend(data, revealWordsPerSecond, out string failure))
                {
                    // Displayed canonical dialogue remains intact. Refuse invalid
                    // timing instead of replaying or accelerating subtitle words.
                    Debug.LogWarning("[AIFren Subtitle] rejected committed timing: " + failure);
                    hiddenSubtitlePresenter?.Cancel();
                    avatarAnimation?.StopSpeech(); committedSpeechRetired = true;
                    return true;
                }
                committedSpeechTimeline = timeline;
                if (first)
                {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    developmentFlightRecorder?.Mark("playback_started", data.turn_id, data.playback_id);
#endif
                    string existing = string.Join(" ", SubtitleTimingPlan.TokenizeWords(
                        string.Join(" ", subtitlePages)));
                    string expected = string.Join(" ", SubtitleTimingPlan.TokenizeWords(data.complete_text));
                    if (!string.Equals(existing, expected, StringComparison.Ordinal))
                        BeginSubtitleResponse(data.complete_text);
                    subtitlePlaybackStartedAt = Time.unscaledTime;
                }
                subtitleWordSchedule.Clear(); subtitleWordSchedule.AddRange(timeline.Schedule);
                SubtitleTimingPlan.ApplyLead(subtitleWordSchedule, HiddenSubtitleLeadSeconds);
                subtitleSpeechDuration = (float)((double)(data.playback_sample_offset + data.sample_count) / data.sample_rate);
                subtitleSpeechActive = true; subtitleAwaitingPlayback = false;
                subtitlePlaybackGeneration = subtitleGeneration; subtitlePlaybackId = data.playback_id;
                streamedSubtitleMode = true;
                pendingSpeechReady = true; pendingSpeechDuration = subtitleSpeechDuration;
                var resolver = avatarLoader != null ? avatarLoader.GetComponent<AvatarPresentationResolver>() : null;
                if (resolver == null || resolver.AllowsLipSync)
                    avatarAnimation?.BeginSpeech(data.duration_seconds, data.lip_sync_envelope, !first);
                else avatarAnimation?.StopSpeech();
                hiddenSubtitlePresenter?.OnPlaybackStarted(subtitleGeneration, data.playback_id,
                    new List<float>(subtitleWordSchedule), subtitlePlaybackStartedAt);
                TryBeginPendingAssistantReveal(); ApplyStatus("speaking", data.message);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFrameProfiler?.Mark("committed_speech_chunk:" + data.sequence + ":words=" + timeline.ReceivedWords);
#endif
                return true;
            }
            if (data.state == "stopped" || data.state == "failed" || data.state == "not_started")
            {
                if (committedSpeechTimeline != null && data.playback_id > 0
                    && data.playback_id != committedSpeechTimeline.PlaybackId) return true;
                presentationTurn.FinishSpeech(data.turn_id, data.interrupted);
                avatarAnimation?.StopSpeech();
                pendingSpeechReady = true; pendingSpeechDuration = 0;
                TryBeginPendingAssistantReveal(); subtitleSpeechActive = false; subtitleAwaitingPlayback = false;
                if (data.interrupted)
                    hiddenSubtitlePresenter?.Cancel();
                else if (committedSpeechTimeline == null)
                    hiddenSubtitlePresenter?.OnAudioUnavailable(subtitleGeneration, Time.unscaledTime);
                else
                    hiddenSubtitlePresenter?.OnPlaybackStopped(data.playback_id, Time.unscaledTime,
                        data.interrupted || data.state != "stopped" || !committedSpeechTimeline.Finished);
                ApplyStatus(data.state == "failed" ? "error" : "ready", data.message);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("playback_stopped", data.turn_id, data.playback_id);
#endif
                committedSpeechRetired = true;
                // Keep the retired identity until the next turn; duplicate/late
                // chunks cannot open a fresh subtitle session after completion.
                return true;
            }
            return true;
        }
    }
}
