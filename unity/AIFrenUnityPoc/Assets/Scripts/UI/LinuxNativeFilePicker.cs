using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

namespace AIFren.UnityPoc.UI
{
    internal static class LinuxNativeFilePicker
    {
        private const string RecentDirectoryKey = "AIFren.NativePickerDirectory";
        // Zenity has no supported option for selecting a GTK filter by index.
        // Supplying only this filter keeps the picker focused on supported
        // avatar containers; metadata validation still happens after selection.
        internal static readonly string[] AvatarModelFilters =
        {
            "Compatible VRM and .glb Avatars | *.vrm *.glb",
        };
        internal readonly struct Result
        {
            internal readonly string path;
            internal readonly string error;
            internal readonly long requestedAt;
            internal readonly long processStartedAt;
            internal readonly long completedAt;

            internal Result(string path, string error, long requestedAt = 0, long processStartedAt = 0, long completedAt = 0)
            {
                this.path = path ?? string.Empty;
                this.error = error ?? string.Empty;
                this.requestedAt = requestedAt;
                this.processStartedAt = processStartedAt;
                this.completedAt = completedAt;
            }
        }

        // This method is called by a Unity UI callback before the worker starts.
        // PlayerPrefs is intentionally read here, never from Task.Run.
        internal static Task<Result> PickAsync(string title, params string[] filters)
        {
            string initialDirectory = RecentDirectory();
            string[] capturedFilters = filters == null ? Array.Empty<string>() : (string[])filters.Clone();
            long requestedAt = Stopwatch.GetTimestamp();
            return Task.Run(() => PickInBackground(title ?? string.Empty, capturedFilters, initialDirectory, requestedAt));
        }

        // This method must be called from the Unity main thread after a selection.
        internal static void Remember(string path)
        {
            string directory = Path.GetDirectoryName(path);
            if (Directory.Exists(directory)) PlayerPrefs.SetString(RecentDirectoryKey, directory);
            PlayerPrefs.Save();
        }

        private static Result PickInBackground(string title, string[] filters, string initialDirectory, long requestedAt)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo
                {
                    FileName = "zenity",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                info.ArgumentList.Add("--file-selection");
                info.ArgumentList.Add("--title=" + title);
                foreach (string filter in filters)
                    if (!string.IsNullOrWhiteSpace(filter)) info.ArgumentList.Add("--file-filter=" + filter);
                info.ArgumentList.Add("--filename=" + initialDirectory + Path.DirectorySeparatorChar);

                using (Process process = Process.Start(info))
                {
                    if (process == null) throw new InvalidOperationException("Could not start zenity.");
                    long processStartedAt = Stopwatch.GetTimestamp();
                    NativeProcessOutput.Result capture = NativeProcessOutput.Collect(process, 300000, 4096);
                    if (capture.Truncated) return new Result(string.Empty, "Native file picker result was too long.");
                    Result result = InterpretProcessResult(process.ExitCode, capture.Output.Trim(), string.Empty);
                    return new Result(result.path, result.error, requestedAt, processStartedAt, Stopwatch.GetTimestamp());
                }
            }
            catch (Exception)
            {
                // Do not call UnityEngine APIs here. The main-thread caller logs once.
                return new Result(string.Empty, "Native file picker could not complete. Retry or choose another file.",
                    requestedAt, 0, Stopwatch.GetTimestamp());
            }
        }

        internal static double ElapsedMilliseconds(long start, long end)
        {
            if (start <= 0 || end < start) return -1d;
            return (end - start) * 1000d / Stopwatch.Frequency;
        }

        // Pure .NET result handling; retained separately for focused EditMode tests.
        internal static Result InterpretProcessResult(int exitCode, string selected, string standardError)
        {
            if (exitCode == 0)
            {
                if (File.Exists(selected)) return new Result(selected, string.Empty);
                return new Result(string.Empty, "Native file picker returned no readable file.");
            }

            // Zenity uses exit code 1 for cancellation. This is a normal no-op.
            if (exitCode == 1) return new Result(string.Empty, string.Empty);
            return new Result(string.Empty, "Native file picker failed (exit " + exitCode + "). Please retry.");
        }

        private static string RecentDirectory()
        {
            string saved = PlayerPrefs.GetString(RecentDirectoryKey, string.Empty);
            return Directory.Exists(saved) ? saved : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        }
    }
}
