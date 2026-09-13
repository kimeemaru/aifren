using System;
using System.Collections.Generic;
using AIFren.UnityPoc.Protocol;

namespace AIFren.UnityPoc.UI
{
    // Derived timing for one already-published utterance, never a playback owner.
    // Unknown future words have no due time until their ordered audio unit starts.
    internal sealed class CommittedSpeechTimeline
    {
        private readonly List<string> words;
        private readonly List<float> schedule;
        private int nextSequence, nextWord, sampleRate;
        private long nextSample, priorPlaybackEnd;
        internal readonly int TurnId, PlaybackId;
        internal readonly string CompleteText;
        internal bool Finished { get; private set; }
        internal int ReceivedWords => nextWord;
        internal int TotalWords => words.Count;
        internal List<float> Schedule => new List<float>(schedule);

        internal CommittedSpeechTimeline(int turnId, int playbackId, string completeText)
        {
            TurnId = turnId; PlaybackId = playbackId; CompleteText = completeText ?? "";
            words = SubtitleTimingPlan.TokenizeWords(CompleteText);
            schedule = new List<float>(words.Count);
            foreach (string _ in words) schedule.Add(float.PositiveInfinity);
        }

        internal bool TryAppend(BackendEventData data, float wordsPerSecond, out string failure)
        {
            failure = "invalid_chunk";
            if (data == null || Finished || data.turn_id != TurnId || data.playback_id != PlaybackId
                || data.sequence != nextSequence || data.word_offset != nextWord
                || data.sample_offset != nextSample || data.sample_count <= 0
                || data.sample_rate < 8000 || data.sample_rate > 192000
                || (sampleRate > 0 && sampleRate != data.sample_rate)
                || data.playback_sample_offset < data.sample_offset || data.playback_sample_offset < priorPlaybackEnd
                || !string.Equals(CompleteText, data.complete_text, StringComparison.Ordinal)) return false;
            List<string> chunkWords = SubtitleTimingPlan.TokenizeWords(data.chunk_text);
            if (data.word_count <= 0 || data.word_count != chunkWords.Count
                || data.word_count > words.Count - nextWord) return false;
            for (int i = 0; i < chunkWords.Count; i++)
                if (!string.Equals(words[nextWord + i], chunkWords[i], StringComparison.Ordinal))
                { failure = "word_identity"; return false; }
            if (data.final_chunk != (nextWord + data.word_count == words.Count))
            { failure = "completion_boundary"; return false; }
            double duration = (double)data.sample_count / data.sample_rate;
            double offset = (double)data.playback_sample_offset / data.sample_rate;
            if (duration > 600 || offset > 3600 || data.sample_count > long.MaxValue - nextSample
                || data.sample_count > long.MaxValue - data.playback_sample_offset) return false;
            bool aligned = data.word_start_seconds != null && data.word_start_seconds.Length == chunkWords.Count;
            float previous = 0;
            if (aligned)
                foreach (float time in data.word_start_seconds)
                {
                    if (float.IsNaN(time) || float.IsInfinity(time) || time < previous || time > duration + .25)
                    { aligned = false; break; }
                    previous = time;
                }
            // A missing alignment uses the existing honest estimated timing owner.
            IList<float> starts = aligned ? data.word_start_seconds :
                (IList<float>)SubtitleTimingPlan.Build(data.chunk_text, (float)duration, wordsPerSecond);
            for (int i = 0; i < chunkWords.Count; i++) schedule[nextWord + i] = (float)offset + starts[i];
            nextWord += chunkWords.Count; nextSequence++; sampleRate = data.sample_rate;
            nextSample += data.sample_count; priorPlaybackEnd = data.playback_sample_offset + data.sample_count;
            Finished = data.final_chunk; failure = "";
            return true;
        }
    }
}
