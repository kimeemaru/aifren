using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
#if UNITY_EDITOR || DEVELOPMENT_BUILD
    [Serializable] internal sealed class DevelopmentFrameSample
    {
        public int frame;
        public float realtime;
        public float frameMilliseconds;
        public float cpuFrameMilliseconds;
        public float gpuFrameMilliseconds;
        public int gcCollection0;
        public int gcCollection1;
        public int gcCollection2;
    }

    [Serializable] internal sealed class DevelopmentFrameMarker
    {
        public int frame;
        public float realtime;
        public string name;
    }

    [Serializable] internal sealed class DevelopmentFrameProfileReport
    {
        public string configuration;
        public int width;
        public int height;
        public List<DevelopmentFrameSample> frames = new List<DevelopmentFrameSample>();
        public List<DevelopmentFrameMarker> markers = new List<DevelopmentFrameMarker>();
    }

    /// <summary>Development-player frame distribution recorder; never part of release UX.</summary>
    internal sealed class DevelopmentFrameProfiler : MonoBehaviour
    {
        private readonly DevelopmentFrameProfileReport report = new DevelopmentFrameProfileReport();
        private readonly FrameTiming[] timings = new FrameTiming[1];
        private bool recording;
        private string outputPath;
        private bool exitOnFinish;

        internal void Begin(string configuration)
        {
            if (recording) return;
            outputPath = ArgumentValue("-aifren-profile-output") ?? System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-player-profile.json");
            exitOnFinish = Array.Exists(Environment.GetCommandLineArgs(), value => value == "-aifren-profile-exit");
            report.configuration = configuration;
            report.width = Screen.width;
            report.height = Screen.height;
            recording = true;
            Mark("profile_begin");
            Debug.Log("[AIFren Player Profile] recording to " + outputPath);
        }

        internal void Mark(string name)
        {
            if (!recording) return;
            report.markers.Add(new DevelopmentFrameMarker
            {
                frame = Time.frameCount,
                realtime = Time.realtimeSinceStartup,
                name = name ?? string.Empty,
            });
        }

        internal void Finish()
        {
            if (!recording) return;
            Mark("profile_end");
            recording = false;
            report.width = Screen.width;
            report.height = Screen.height;
            try
            {
                File.WriteAllText(outputPath, JsonUtility.ToJson(report, true));
                Debug.Log("[AIFren Player Profile] wrote " + report.frames.Count + " frames and " +
                    report.markers.Count + " markers to " + outputPath);
            }
            catch (Exception exception)
            {
                Debug.LogError("[AIFren Player Profile] write failed: " + exception.GetType().Name);
            }
            if (exitOnFinish) Application.Quit(0);
        }

        private void Update()
        {
            if (!recording) return;
            FrameTimingManager.CaptureFrameTimings();
            uint count = FrameTimingManager.GetLatestTimings(1, timings);
            FrameTiming timing = count > 0 ? timings[0] : default;
            report.frames.Add(new DevelopmentFrameSample
            {
                frame = Time.frameCount,
                realtime = Time.realtimeSinceStartup,
                frameMilliseconds = Time.unscaledDeltaTime * 1000f,
                cpuFrameMilliseconds = count > 0 ? (float)timing.cpuFrameTime : 0f,
                gpuFrameMilliseconds = count > 0 ? (float)timing.gpuFrameTime : 0f,
                gcCollection0 = GC.CollectionCount(0),
                gcCollection1 = GC.CollectionCount(1),
                gcCollection2 = GC.CollectionCount(2),
            });
        }

        private static string ArgumentValue(string name)
        {
            string[] arguments = Environment.GetCommandLineArgs();
            for (int index = 0; index + 1 < arguments.Length; index++)
                if (arguments[index] == name) return arguments[index + 1];
            return null;
        }
    }
#endif
}
