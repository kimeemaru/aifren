using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

namespace AIFren.UnityPoc.UI
{
    internal static class LinuxNativeFilePicker
    {
        private const string RecentDirectoryKey = "AIFren.NativePickerDirectory";
        internal static Task<string> PickAsync(string title, string filter)
        {
            return Task.Run(() =>
            {
                try
                {
                    ProcessStartInfo info = new ProcessStartInfo
                    {
                        FileName = "zenity",
                        Arguments = "--file-selection --title=" + Quote(title) + " --file-filter=" + Quote(filter) +
                            " --filename=" + Quote(RecentDirectory() + Path.DirectorySeparatorChar),
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        RedirectStandardOutput = true,
                    };
                    using (Process process = Process.Start(info))
                    {
                        if (!process.WaitForExit(300000)) { process.Kill(); return string.Empty; }
                        string selected = process.StandardOutput.ReadToEnd().Trim();
                        if (process.ExitCode == 0 && File.Exists(selected)) return selected;
                        return string.Empty;
                    }
                }
                catch (Exception) { return string.Empty; }
            });
        }

        private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";
        internal static void Remember(string path)
        {
            string directory = Path.GetDirectoryName(path);
            if (Directory.Exists(directory)) UnityEngine.PlayerPrefs.SetString(RecentDirectoryKey, directory);
            UnityEngine.PlayerPrefs.Save();
        }
        private static string RecentDirectory()
        {
            string saved = UnityEngine.PlayerPrefs.GetString(RecentDirectoryKey, string.Empty);
            return Directory.Exists(saved) ? saved : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        }
    }
}
