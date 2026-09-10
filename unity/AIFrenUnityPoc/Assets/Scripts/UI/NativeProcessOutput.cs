using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;

namespace AIFren.UnityPoc.UI
{
    // Pipe handling for the existing native picker/reconnect owners. This is
    // not a log sink: stderr is discarded, and only the picker's path result
    // may be retained. Both pipes drain from launch, including after the cap.
    internal static class NativeProcessOutput
    {
        internal readonly struct Result
        {
            internal readonly string Output;
            internal readonly bool Truncated;
            internal Result(string output, bool truncated) { Output = output; Truncated = truncated; }
        }

        internal static Result Collect(Process process, int timeoutMilliseconds, int outputLimit)
        {
            if (outputLimit < 0 || outputLimit > 4096) throw new ArgumentOutOfRangeException(nameof(outputLimit));
            using (StreamReader stdout = process.StandardOutput)
            using (StreamReader stderr = process.StandardError)
            {
                Task<Result> output = Drain(stdout, outputLimit);
                Task<Result> error = Drain(stderr, 0);
                if (!process.WaitForExit(timeoutMilliseconds))
                {
                    process.Kill(); // Only the helper this caller started.
                    process.WaitForExit(2000);
                    throw new TimeoutException();
                }
                if (!Task.WaitAll(new Task[] { output, error }, 2000)) throw new TimeoutException();
                return output.Result;
            }
        }

        private static async Task<Result> Drain(TextReader reader, int limit)
        {
            var retained = new StringBuilder(limit);
            var buffer = new char[4096];
            bool truncated = false;
            try
            {
                int count;
                while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) != 0)
                {
                    int keep = Math.Min(count, limit - retained.Length);
                    if (keep > 0) retained.Append(buffer, 0, keep);
                    truncated |= count > keep;
                }
            }
            catch (ObjectDisposedException) { truncated = true; }
            catch (IOException) { truncated = true; }
            return new Result(retained.ToString(), truncated);
        }
    }
}
