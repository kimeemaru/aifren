#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using AIFren.UnityPoc.Protocol;
using UnityEngine;
using UnityEngine.Profiling;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Bounded Development-only incident recorder. It stores structural/numeric
    /// telemetry in memory and performs disk I/O only after an incident/manual dump.
    /// </summary>
    internal sealed class DevelopmentFlightRecorder : MonoBehaviour
    {
        private const int FrameCapacity = 3600;
        private const int EventCapacity = 1024;
        private const int ResourceCapacity = 192;
        private const float ResourceInterval = .2f;
        private const float AutomaticPostSeconds = 10f;
        private const float IncidentCooldownSeconds = 60f;
        private static readonly string DirectoryPrefix = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "companion-flight-recorder-");

        private sealed class FrameRecord
        {
            internal double Timestamp; internal float Realtime; internal int Frame;
            internal float WallMs; internal float UnityMs; internal float RecorderCostUs; internal int TransportEvents;
            internal bool SubtitleActive; internal int SubtitlePage; internal int SubtitleWord;
            internal bool UiHidden; internal bool AvatarActive; internal bool LipSyncActive;
        }

        private sealed class EventRecord
        {
            internal double Timestamp; internal float Realtime; internal int Frame;
            internal string Name; internal int TurnId; internal int PlaybackId; internal int Value;
        }

        private sealed class ResourceRecord
        {
            internal double Timestamp; internal float Realtime; internal long RssBytes;
            internal long ManagedHeapBytes; internal long AllocatedBytes; internal long AllocatedDelta;
            internal int Gc0; internal int Gc1; internal int Gc2;
        }

        private sealed class Capture
        {
            internal string Id; internal string Reason; internal double TriggerTimestamp;
            internal float TriggerRealtime; internal List<FrameRecord> Frames;
            internal List<EventRecord> Events; internal List<ResourceRecord> Resources;
            internal readonly List<FrameRecord> PostFrames = new List<FrameRecord>();
            internal readonly List<EventRecord> PostEvents = new List<EventRecord>();
            internal readonly List<ResourceRecord> PostResources = new List<ResourceRecord>();
        }

        private readonly Queue<FrameRecord> frames = new Queue<FrameRecord>(FrameCapacity);
        private readonly Queue<EventRecord> events = new Queue<EventRecord>(EventCapacity);
        private readonly Queue<ResourceRecord> resources = new Queue<ResourceRecord>(ResourceCapacity);
        private readonly Queue<float> recentFiftyMsFrames = new Queue<float>();
        private AIFrenWebSocketClient client;
        private readonly Dictionary<string, int> viewAliases = new Dictionary<string, int>();
        private readonly Queue<string> viewAliasOrder = new Queue<string>();
        private int nextViewAlias;
        private Capture capture;
        private float nextResourceAt;
        private float automaticArmedAt;
        private float lastIncidentAt = -1000f;
        private int transportEventsThisFrame;
        private bool subtitleActive;
        private int subtitlePage = -1;
        private int subtitleWord = -1;
        private bool uiHidden;
        private bool avatarActive;
        private bool lipSyncActive;
        private long previousAllocatedBytes;
        private Coroutine finalizeCoroutine;

        internal string PendingCaptureId => capture != null ? capture.Id : string.Empty;

        internal void Initialize(AIFrenWebSocketClient websocketClient)
        {
            client = websocketClient;
            client.ViewObserved += MarkView;
            automaticArmedAt = Time.realtimeSinceStartup + 10f;
            Mark("unity_recorder_started");
            int processId;
            using (Process process = Process.GetCurrentProcess()) processId = process.Id;
            _ = client.StartDevelopmentFlightRecorderAsync(processId);
        }

        private void OnDestroy()
        { if (client != null) client.ViewObserved -= MarkView; }

        internal void ObserveTransportEvent() { transportEventsThisFrame++; }

        internal void ArmForUserTurn()
        {
            automaticArmedAt = Time.realtimeSinceStartup;
            recentFiftyMsFrames.Clear();
        }

        internal void SetPresentationState(bool hidden, bool subtitle, bool avatar, bool lipSync)
        {
            uiHidden = hidden; subtitleActive = subtitle; avatarActive = avatar; lipSyncActive = lipSync;
        }

        internal void SetSubtitlePage(int page)
        {
            subtitlePage = page;
            Mark("subtitle_page_activated", value: page);
        }

        internal void SetSubtitleWord(int word) { subtitleWord = word; }

        internal void Mark(string name, int turnId = 0, int playbackId = 0, int value = 0)
        {
            var record = new EventRecord
            {
                Timestamp = UnixNow(), Realtime = Time.realtimeSinceStartup, Frame = Time.frameCount,
                Name = SafeLabel(name), TurnId = turnId, PlaybackId = playbackId, Value = value,
            };
            AddBounded(events, record, EventCapacity);
            while (events.Count > 0 && Time.realtimeSinceStartup - events.Peek().Realtime > 30f) events.Dequeue();
            if (capture != null) capture.PostEvents.Add(record);
        }

        internal void MarkView(string stage, string requestId, int count)
        {
            int alias = 0;
            if (!string.IsNullOrEmpty(requestId))
            {
                if (!viewAliases.TryGetValue(requestId, out alias))
                {
                    if (viewAliases.Count >= 128) viewAliases.Remove(viewAliasOrder.Dequeue());
                    alias = ++nextViewAlias; viewAliases[requestId] = alias; viewAliasOrder.Enqueue(requestId);
                }
            }
            Mark("view_" + stage, playbackId: alias, value: count);
        }

        internal void ManualDump()
        {
            if (capture != null) return;
            BeginCapture("manual_hotkey");
            FinalizeCapture();
        }

        internal void AutomaticTrigger(string reason)
        {
            if (Time.realtimeSinceStartup >= automaticArmedAt && capture == null &&
                Time.realtimeSinceStartup - lastIncidentAt >= IncidentCooldownSeconds)
                BeginAutomaticCapture(reason);
        }

        internal void AcceptBackendSummary(BackendEventData data)
        {
            if (capture == null || data == null || data.capture_id != capture.Id) return;
            WriteBundle(data);
            capture = null;
            lastIncidentAt = Time.realtimeSinceStartup;
        }

        private void LateUpdate()
        {
            long recorderStarted = Stopwatch.GetTimestamp();
            float wallMs = Mathf.Max(0f, Time.unscaledDeltaTime * 1000f);
            var frame = new FrameRecord
            {
                Timestamp = UnixNow(), Realtime = Time.realtimeSinceStartup, Frame = Time.frameCount,
                WallMs = wallMs, UnityMs = Time.deltaTime * 1000f,
                TransportEvents = transportEventsThisFrame, SubtitleActive = subtitleActive,
                SubtitlePage = subtitlePage, SubtitleWord = subtitleWord, UiHidden = uiHidden,
                AvatarActive = avatarActive, LipSyncActive = lipSyncActive,
            };
            transportEventsThisFrame = 0;
            AddBounded(frames, frame, FrameCapacity);
            while (frames.Count > 0 && Time.realtimeSinceStartup - frames.Peek().Realtime > 30f) frames.Dequeue();
            if (capture != null) capture.PostFrames.Add(frame);

            if (Time.realtimeSinceStartup >= nextResourceAt)
            {
                nextResourceAt = Time.realtimeSinceStartup + ResourceInterval;
                CaptureResources();
            }

            if (Time.realtimeSinceStartup < automaticArmedAt)
            {
                // Startup frames are useful in a later manual window, but they
                // must not consume the one-incident cooldown before real use.
                recentFiftyMsFrames.Clear();
            }
            else
            {
                while (recentFiftyMsFrames.Count > 0 && Time.realtimeSinceStartup - recentFiftyMsFrames.Peek() > 2f)
                    recentFiftyMsFrames.Dequeue();
                if (wallMs > 50f) recentFiftyMsFrames.Enqueue(Time.realtimeSinceStartup);
            }

            if (Time.realtimeSinceStartup >= automaticArmedAt && capture == null &&
                Time.realtimeSinceStartup - lastIncidentAt >= IncidentCooldownSeconds)
            {
                if (wallMs > 100f) BeginAutomaticCapture(wallMs > 500f ? "wall_frame_over_500ms" : "wall_frame_over_100ms");
                else if (recentFiftyMsFrames.Count >= 3) BeginAutomaticCapture("three_frames_over_50ms_in_2s");
            }
            frame.RecorderCostUs = (float)((Stopwatch.GetTimestamp() - recorderStarted) * 1000000.0 / Stopwatch.Frequency);
        }

        private void CaptureResources()
        {
            long allocated = Profiler.GetTotalAllocatedMemoryLong();
            long delta = previousAllocatedBytes > 0 ? Math.Max(0, allocated - previousAllocatedBytes) : 0;
            previousAllocatedBytes = allocated;
            long rss = 0;
            try { using (Process process = Process.GetCurrentProcess()) rss = process.WorkingSet64; } catch { }
            var record = new ResourceRecord
            {
                Timestamp = UnixNow(), Realtime = Time.realtimeSinceStartup,
                RssBytes = rss, ManagedHeapBytes = Profiler.GetMonoUsedSizeLong(),
                AllocatedBytes = allocated, AllocatedDelta = delta,
                Gc0 = GC.CollectionCount(0), Gc1 = GC.CollectionCount(1), Gc2 = GC.CollectionCount(2),
            };
            AddBounded(resources, record, ResourceCapacity);
            while (resources.Count > 0 && Time.realtimeSinceStartup - resources.Peek().Realtime > 30f) resources.Dequeue();
            if (capture != null) capture.PostResources.Add(record);
        }

        private void BeginAutomaticCapture(string reason)
        {
            BeginCapture(reason);
            finalizeCoroutine = StartCoroutine(FinalizeAfterPostWindow());
        }

        private void BeginCapture(string reason)
        {
            string id = DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 6);
            capture = new Capture
            {
                Id = id, Reason = SafeLabel(reason), TriggerTimestamp = UnixNow(), TriggerRealtime = Time.realtimeSinceStartup,
                Frames = new List<FrameRecord>(frames), Events = new List<EventRecord>(events),
                Resources = new List<ResourceRecord>(resources),
            };
            Mark("capture_triggered");
            _ = client.TriggerDevelopmentFlightRecorderAsync(id, capture.Reason);
        }

        private IEnumerator FinalizeAfterPostWindow()
        {
            yield return new WaitForSecondsRealtime(AutomaticPostSeconds);
            finalizeCoroutine = null;
            FinalizeCapture();
        }

        private void FinalizeCapture()
        {
            if (capture == null) return;
            Mark("capture_finalize_requested");
            _ = client.DumpDevelopmentFlightRecorderAsync(capture.Id);
            StartCoroutine(BackendSummaryTimeout(capture.Id));
        }

        private IEnumerator BackendSummaryTimeout(string captureId)
        {
            yield return new WaitForSecondsRealtime(3f);
            if (capture != null && capture.Id == captureId)
            {
                WriteBundle(null);
                capture = null;
                lastIncidentAt = Time.realtimeSinceStartup;
            }
        }

        private void OnApplicationQuit()
        {
            if (capture == null) return;
            if (finalizeCoroutine != null) StopCoroutine(finalizeCoroutine);
            WriteBundle(null);
            _ = client.DumpDevelopmentFlightRecorderAsync(capture.Id);
        }

        private void WriteBundle(BackendEventData backend)
        {
            Capture value = capture;
            if (value == null) return;
            string directory = DirectoryPrefix + value.Id;
            Directory.CreateDirectory(directory);
            var allFrames = value.Frames.Concat(value.PostFrames).GroupBy(item => item.Frame).Select(group => group.Last()).OrderBy(item => item.Timestamp).ToList();
            var allEvents = value.Events.Concat(value.PostEvents).OrderBy(item => item.Timestamp).ToList();
            var allResources = value.Resources.Concat(value.PostResources).OrderBy(item => item.Timestamp).ToList();
            var timeline = new List<KeyValuePair<double, string>>(allFrames.Count + allEvents.Count + allResources.Count);
            timeline.AddRange(allFrames.Select(item => new KeyValuePair<double, string>(item.Timestamp, FrameJson(item))));
            timeline.AddRange(allEvents.Select(item => new KeyValuePair<double, string>(item.Timestamp, EventJson(item))));
            timeline.AddRange(allResources.Select(item => new KeyValuePair<double, string>(item.Timestamp, ResourceJson(item))));
            using (var writer = new StreamWriter(Path.Combine(directory, "timeline.jsonl"), false, new UTF8Encoding(false)))
            {
                foreach (KeyValuePair<double, string> item in timeline.OrderBy(item => item.Key)) writer.WriteLine(item.Value);
            }
            File.WriteAllText(Path.Combine(directory, "summary.json"), SummaryJson(value, allFrames, allEvents, backend), new UTF8Encoding(false));
            File.WriteAllText(Path.Combine(directory, "README.txt"),
                "AIFren Development flight-recorder incident bundle.\n" +
                "timeline.jsonl: Unity per-frame, resource, and lifecycle records.\n" +
                "backend_timeline.jsonl/backend_summary.json: backend, model, TTS, playback, system, and GPU telemetry.\n" +
                "No conversation, prompt, assistant, subtitle, personality, memory, credential, or protected path content is recorded.\n" +
                "Manual dump shortcut: type 6666666 while the chat field is not focused.\n", new UTF8Encoding(false));
            UnityEngine.Debug.Log("[AIFren Flight Recorder] capture written to " + directory);
        }

        private static string SummaryJson(Capture value, List<FrameRecord> allFrames, List<EventRecord> allEvents, BackendEventData backend)
        {
            List<float> durations = allFrames.Select(item => item.WallMs).OrderBy(item => item).ToList();
            FrameRecord worst = allFrames.OrderByDescending(item => item.WallMs).FirstOrDefault();
            List<EventRecord> nearest = worst == null ? new List<EventRecord>() : allEvents.OrderBy(item => Math.Abs(item.Timestamp - worst.Timestamp)).Take(8).ToList();
            var builder = new StringBuilder();
            builder.Append("{\n");
            JsonProperty(builder, "capture_reason", value.Reason, true);
            JsonProperty(builder, "capture_timestamp", value.TriggerTimestamp, true);
            JsonProperty(builder, "frame_count", allFrames.Count, true);
            JsonProperty(builder, "median_frame_ms", Percentile(durations, .50f), true);
            JsonProperty(builder, "p95_frame_ms", Percentile(durations, .95f), true);
            JsonProperty(builder, "p99_frame_ms", Percentile(durations, .99f), true);
            JsonProperty(builder, "worst_frame_ms", worst != null ? worst.WallMs : 0f, true);
            List<float> recorderCosts = allFrames.Select(item => item.RecorderCostUs).OrderBy(item => item).ToList();
            JsonProperty(builder, "recorder_median_cost_us", Percentile(recorderCosts, .50f), true);
            JsonProperty(builder, "recorder_p99_cost_us", Percentile(recorderCosts, .99f), true);
            JsonProperty(builder, "recorder_max_cost_us", recorderCosts.Count > 0 ? recorderCosts[recorderCosts.Count - 1] : 0f, true);
            JsonProperty(builder, "frames_over_25ms", allFrames.Count(item => item.WallMs > 25f), true);
            JsonProperty(builder, "frames_over_33ms", allFrames.Count(item => item.WallMs > 33.3f), true);
            JsonProperty(builder, "frames_over_50ms", allFrames.Count(item => item.WallMs > 50f), true);
            JsonProperty(builder, "frames_over_100ms", allFrames.Count(item => item.WallMs > 100f), true);
            JsonProperty(builder, "frames_over_250ms", allFrames.Count(item => item.WallMs > 250f), true);
            JsonProperty(builder, "minimum_available_ram_mb", backend != null ? backend.minimum_available_ram_mb : -1f, true);
            JsonProperty(builder, "swap_in_pages_total", backend != null ? backend.swap_in_pages_total : -1f, true);
            JsonProperty(builder, "swap_out_pages_total", backend != null ? backend.swap_out_pages_total : -1f, true);
            JsonProperty(builder, "peak_gpu_utilization_percent", backend != null ? backend.peak_gpu_utilization_percent : -1f, true);
            JsonProperty(builder, "peak_vram_mb", backend != null ? backend.peak_vram_mb : -1f, true);
            JsonProperty(builder, "qwen_generating_at_worst_frame", StateAt(allEvents, worst, "turn_started", "assistant_final"), true);
            JsonProperty(builder, "kokoro_synthesizing_at_worst_frame", StateAt(allEvents, worst, "tts_submit", "playback_started"), true);
            JsonProperty(builder, "portaudio_playing_at_worst_frame", StateAt(allEvents, worst, "playback_started", "playback_stopped"), true);
            JsonProperty(builder, "whisper_active_at_worst_frame", StateAt(allEvents, worst, "stt_start", "stt_final"), true);
            builder.Append("  \"nearest_lifecycle_markers\": [");
            for (int index = 0; index < nearest.Count; index++)
            {
                if (index > 0) builder.Append(',');
                builder.Append("\n    {\"event\":\"").Append(Escape(nearest[index].Name)).Append("\",\"offset_ms\":")
                    .Append(((nearest[index].Timestamp - worst.Timestamp) * 1000.0).ToString("0.###", CultureInfo.InvariantCulture)).Append('}');
            }
            if (nearest.Count > 0) builder.Append('\n');
            builder.Append("  ]\n}\n");
            return builder.ToString();
        }

        private static bool StateAt(List<EventRecord> events, FrameRecord frame, string start, string stop)
        {
            if (frame == null) return false;
            EventRecord latest = events.Where(item => item.Timestamp <= frame.Timestamp && (item.Name == start || item.Name == stop)).OrderByDescending(item => item.Timestamp).FirstOrDefault();
            return latest != null && latest.Name == start;
        }

        private static float Percentile(List<float> ordered, float percentile)
        {
            if (ordered.Count == 0) return 0f;
            int index = Mathf.Clamp(Mathf.CeilToInt(percentile * ordered.Count) - 1, 0, ordered.Count - 1);
            return ordered[index];
        }

        private static string FrameJson(FrameRecord item) => "{\"record_type\":\"unity_frame\",\"timestamp\":" + Number(item.Timestamp) +
            ",\"realtime\":" + Number(item.Realtime) + ",\"frame\":" + item.Frame + ",\"wall_ms\":" + Number(item.WallMs) +
            ",\"unity_delta_ms\":" + Number(item.UnityMs) + ",\"recorder_cost_us\":" + Number(item.RecorderCostUs) + ",\"transport_events\":" + item.TransportEvents +
            ",\"subtitle_active\":" + Bool(item.SubtitleActive) + ",\"subtitle_page\":" + item.SubtitlePage +
            ",\"subtitle_word\":" + item.SubtitleWord + ",\"ui_hidden\":" + Bool(item.UiHidden) +
            ",\"avatar_active\":" + Bool(item.AvatarActive) + ",\"lipsync_active\":" + Bool(item.LipSyncActive) + "}";

        private static string EventJson(EventRecord item) => "{\"record_type\":\"unity_event\",\"timestamp\":" + Number(item.Timestamp) +
            ",\"realtime\":" + Number(item.Realtime) + ",\"frame\":" + item.Frame + ",\"event\":\"" + Escape(item.Name) +
            "\",\"turn_id\":" + item.TurnId + ",\"playback_id\":" + item.PlaybackId + ",\"value\":" + item.Value + "}";

        private static string ResourceJson(ResourceRecord item) => "{\"record_type\":\"unity_resource\",\"timestamp\":" + Number(item.Timestamp) +
            ",\"realtime\":" + Number(item.Realtime) + ",\"rss_bytes\":" + item.RssBytes + ",\"managed_heap_bytes\":" + item.ManagedHeapBytes +
            ",\"allocated_bytes\":" + item.AllocatedBytes + ",\"allocated_delta\":" + item.AllocatedDelta +
            ",\"gc0\":" + item.Gc0 + ",\"gc1\":" + item.Gc1 + ",\"gc2\":" + item.Gc2 + "}";

        private static void JsonProperty(StringBuilder builder, string name, string value, bool comma) => builder.Append("  \"").Append(name).Append("\": \"").Append(Escape(value)).Append("\"").Append(comma ? ",\n" : "\n");
        private static void JsonProperty(StringBuilder builder, string name, double value, bool comma) => builder.Append("  \"").Append(name).Append("\": ").Append(Number(value)).Append(comma ? ",\n" : "\n");
        private static void JsonProperty(StringBuilder builder, string name, bool value, bool comma) => builder.Append("  \"").Append(name).Append("\": ").Append(Bool(value)).Append(comma ? ",\n" : "\n");
        private static string Number(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);
        private static string Bool(bool value) => value ? "true" : "false";
        private static string Escape(string value) => (value ?? string.Empty).Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "_").Replace("\r", "_");
        private static string SafeLabel(string value) => new string((value ?? string.Empty).ToLowerInvariant().Where(character => char.IsLetterOrDigit(character) || "_.:+-".IndexOf(character) >= 0).Take(80).ToArray());
        private static double UnixNow() => (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;

        private static void AddBounded<T>(Queue<T> queue, T value, int capacity)
        {
            while (queue.Count >= capacity) queue.Dequeue();
            queue.Enqueue(value);
        }
    }
}
#endif
