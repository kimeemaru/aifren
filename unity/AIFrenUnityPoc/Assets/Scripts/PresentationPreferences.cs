using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace AIFren.UnityPoc
{
    /// <summary>Normal PlayerPrefs ownership, with separate process-local native QA/test storage.</summary>
    public static class PresentationPreferences
    {
        // Resolve before the first access, including Awake and test setup. No
        // normal key is borrowed, cleared or restored on QA exit/domain reload.
        private static readonly Dictionary<string, object> isolated = NeedsIsolation()
            ? new Dictionary<string, object>() : null;
        public static bool IsIsolated => isolated != null;

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
        public static void Save() { if (isolated == null) PlayerPrefs.Save(); }
        public static void DeleteAll()
        {
            if (isolated == null) throw new InvalidOperationException("AIFren cannot clear normal preferences wholesale.");
            isolated.Clear();
        }
    }
}
