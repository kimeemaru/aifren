using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEngine;

namespace AIFren.UnityPoc
{
    /// <summary>One preference owner: host PlayerPrefs, isolated QA, or explicit package file.</summary>
    public static class PresentationPreferences
    {
        // Resolve before the first access, including Awake and test setup. No
        // normal key is borrowed, cleared or restored on QA exit/domain reload.
        private static readonly bool qaIsolation = NeedsIsolation();
        private static readonly PreferenceFile package = qaIsolation ? null : OpenPackagePreferences(Environment.GetCommandLineArgs());
        private static readonly Dictionary<string, object> isolated = qaIsolation
            ? new Dictionary<string, object>() : package?.Values;
        public static bool IsIsolated => qaIsolation;

        // A finite file plan is the only automation entry. Decide before any
        // preference read; validation failures remain isolated and fail closed.
        internal static bool HasFinitePlayerRequest(string[] args, bool development) => development &&
            Array.IndexOf(args, "-aifren-qa-plan") >= 0;
        internal static bool FinitePlayerRequest
        {
            get
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                return HasFinitePlayerRequest(Environment.GetCommandLineArgs(), Debug.isDebugBuild);
#else
                return false;
#endif
            }
        }

        private static bool NeedsIsolation()
        {
            if (Application.productName == "AIFren QA" || FinitePlayerRequest) return true;
#if UNITY_EDITOR
            return Environment.GetCommandLineArgs().Any(arg =>
                string.Equals(arg, "-runTests", StringComparison.OrdinalIgnoreCase));
#else
            return false;
#endif
        }

        public static bool HasKey(string key) => isolated != null ? isolated.ContainsKey(key) : PlayerPrefs.HasKey(key);
        public static int GetInt(string key, int fallback = 0) => isolated != null
            ? (isolated.TryGetValue(key, out object value) && value is int number ? number : fallback)
            : PlayerPrefs.GetInt(key, fallback);
        public static float GetFloat(string key, float fallback = 0) => isolated != null
            ? (isolated.TryGetValue(key, out object value) && value is float number ? number : fallback)
            : PlayerPrefs.GetFloat(key, fallback);
        public static string GetString(string key, string fallback = "") => isolated != null
            ? (isolated.TryGetValue(key, out object value) && value is string text ? text : fallback)
            : PlayerPrefs.GetString(key, fallback);
        public static void SetInt(string key, int value) { if (isolated != null) isolated[key] = value; else PlayerPrefs.SetInt(key, value); }
        public static void SetFloat(string key, float value) { if (isolated != null) isolated[key] = value; else PlayerPrefs.SetFloat(key, value); }
        public static void SetString(string key, string value) { if (isolated != null) isolated[key] = value; else PlayerPrefs.SetString(key, value); }
        public static void DeleteKey(string key) { if (isolated != null) isolated.Remove(key); else PlayerPrefs.DeleteKey(key); }
        public static void Save() { if (package != null) package.Save(); else if (isolated == null) PlayerPrefs.Save(); }
        public static void DeleteAll()
        {
            if (!qaIsolation) throw new InvalidOperationException("AIFren cannot clear normal preferences wholesale.");
            isolated.Clear();
        }

        private static PreferenceFile OpenPackagePreferences(string[] args)
        {
            int index = Array.IndexOf(args, "-aifren-preferences-file");
            if (index < 0) return null;
            if (index + 1 >= args.Length || !Path.IsPathRooted(args[index + 1]))
                throw new IOException("The package preference path must be absolute.");
            return new PreferenceFile(args[index + 1]);
        }

        /// <summary>Typed JSON only. No live objects, machine fallback or QA snapshot restoration.</summary>
        public sealed class PreferenceFile
        {
            [Serializable] private sealed class Entry
            {
                public string key, kind, text;
                public int integer;
                public float number;
            }
            [Serializable] private sealed class Document { public int version = 1; public Entry[] entries; }
            public readonly Dictionary<string, object> Values = new Dictionary<string, object>();
            private readonly string path;
            private const int MaximumBytes = 1024 * 1024;

            public PreferenceFile(string filename)
            {
                if (!Path.IsPathRooted(filename)) throw new IOException("Preference path must be absolute.");
                path = Path.GetFullPath(filename);
                CheckPath();
                if (!File.Exists(path)) return;
                if (new FileInfo(path).Length > MaximumBytes) throw new IOException("Preference file is too large.");
                Document document = JsonUtility.FromJson<Document>(File.ReadAllText(path));
                if (document == null || document.version != 1 || document.entries == null || document.entries.Length > 4096)
                    throw new IOException("The package preference file is invalid. No host preferences were loaded.");
                foreach (Entry entry in document.entries)
                {
                    if (entry == null || string.IsNullOrEmpty(entry.key) || entry.key.Length > 256 || Values.ContainsKey(entry.key))
                        throw new IOException("The package preference file contains an invalid key.");
                    object value;
                    switch (entry.kind)
                    {
                        case "int": value = entry.integer; break;
                        case "float":
                            if (float.IsNaN(entry.number) || float.IsInfinity(entry.number)) throw new IOException("Invalid preference number.");
                            value = entry.number; break;
                        case "string": value = entry.text ?? ""; break;
                        default: throw new IOException("Unsupported preference value.");
                    }
                    Values.Add(entry.key, value);
                }
            }

            private void CheckPath()
            {
                for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                    if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("Package preferences cannot follow links.");
            }

            public void Save()
            {
                CheckPath();
                Document document = new Document { entries = Values.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => {
                    if (string.IsNullOrEmpty(pair.Key) || pair.Key.Length > 256) throw new IOException("Invalid preference key.");
                    Entry entry = new Entry { key = pair.Key };
                    if (pair.Value is int integer) { entry.kind = "int"; entry.integer = integer; }
                    else if (pair.Value is float number && !float.IsNaN(number) && !float.IsInfinity(number)) { entry.kind = "float"; entry.number = number; }
                    else if (pair.Value is string text) { entry.kind = "string"; entry.text = text; }
                    else throw new IOException("Unsupported preference value.");
                    return entry;
                }).ToArray() };
                byte[] bytes = System.Text.Encoding.UTF8.GetBytes(JsonUtility.ToJson(document));
                if (document.entries.Length > 4096 || bytes.Length > MaximumBytes) throw new IOException("Preference file is too large.");
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
                try
                {
                    using (FileStream stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                    CheckPath();
                    if (File.Exists(path)) File.Replace(temporary, path, null);
                    else File.Move(temporary, path);
                }
                finally { if (File.Exists(temporary)) File.Delete(temporary); }
            }
        }
    }
}
