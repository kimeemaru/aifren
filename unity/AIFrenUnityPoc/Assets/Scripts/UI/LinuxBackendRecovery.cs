using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    internal static class LinuxBackendRecovery
    {
        internal readonly struct Result
        {
            internal Result(bool succeeded, string detail)
            {
                Succeeded = succeeded;
                Detail = detail;
            }

            internal bool Succeeded { get; }
            internal string Detail { get; }
        }

        internal static Task<Result> EnsureAsync()
        {
            return Task.Run(Ensure);
        }

        private static Result Ensure()
        {
            string repositoryRoot = Environment.GetEnvironmentVariable("AIFREN_REPOSITORY_ROOT");
            string ownershipFile = Environment.GetEnvironmentVariable("AIFREN_BACKEND_OWNERSHIP_FILE");
            if (string.IsNullOrWhiteSpace(repositoryRoot) || string.IsNullOrWhiteSpace(ownershipFile))
            {
                return new Result(false,
                    "Backend recovery is available when launched through AIFren Dev.");
            }

            string runtime = Path.Combine(repositoryRoot, ".venv-aifren", "bin", "python");
            string lifecycle = Path.Combine(repositoryRoot, "scripts", "ensure_aifren_backend_linux.py");
            if (!File.Exists(runtime) || !File.Exists(lifecycle))
            {
                return new Result(false, "AIFren Dev runtime or backend lifecycle helper is unavailable.");
            }

            try
            {
                ProcessStartInfo start = new ProcessStartInfo
                {
                    FileName = runtime,
                    Arguments = Quote(lifecycle) + " --ensure --repository-root " + Quote(repositoryRoot) +
                        " --python " + Quote(runtime) + " --ownership-file " + Quote(ownershipFile),
                    WorkingDirectory = repositoryRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                using (Process process = Process.Start(start))
                {
                    string output = process.StandardOutput.ReadToEnd();
                    string error = process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(35000))
                    {
                        process.Kill();
                        return new Result(false, "Backend lifecycle helper timed out.");
                    }
                    string detail = LastNonEmptyLine(process.ExitCode == 0 ? output : error);
                    if (string.IsNullOrWhiteSpace(detail)) detail = process.ExitCode == 0
                        ? "Repository backend is ready."
                        : "Backend lifecycle helper failed.";
                    return new Result(process.ExitCode == 0, detail);
                }
            }
            catch (Exception exception)
            {
                return new Result(false, "Could not start backend lifecycle helper: " + exception.Message);
            }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }

        private static string LastNonEmptyLine(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return string.Empty;
            string[] lines = value.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            return lines.Length == 0 ? string.Empty : lines[lines.Length - 1].Trim();
        }
    }
}
