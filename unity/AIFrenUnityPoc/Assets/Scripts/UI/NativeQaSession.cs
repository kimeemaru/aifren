using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System;
using System.IO;
using System.Linq;
using System.Globalization;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    // No command listener. Development accepts an explicit, bounded local file plan.
    internal static class NativeQaSession
    {
        internal static bool Active { get; private set; }
        internal static string Endpoint { get; private set; }
        internal static bool Requested => Application.productName == "AIFren QA" || PlayerPrefs.FinitePlayerRequest;
        internal static bool RejectOrdinaryStartup => Requested && !Active;
        internal static string AssetDataRoot
        {
            get
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (Requested)
                {
                    if (!Active) throw new InvalidOperationException("Native QA has not established its data boundary.");
                    return Path.Combine(Output, "application-data");
                }
#endif
                return PlayerPrefs.ManagedDataRoot ?? Application.persistentDataPath;
            }
        }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
        [Serializable] internal sealed class Plan
        {
            public int version;
            public int width = 900, height = 1600;
            public string endpoint;
            // Full companion plans require real visible geometry by default.
            // Only deliberately named component-only plans can waive that gate.
            public bool componentOnly;
            public bool visualOnly;
            public Preference[] presentation;
            public TargetDisplay display;
            public Step[] steps;
        }
        [Serializable] internal sealed class Preference { public string name, type, value; }
        [Serializable] internal sealed class TargetDisplay { public string output, model; public int width, height; }
        internal static int DisplayIndex;
        [Serializable] internal sealed class Step
        {
            public string action, name, value;
            public float seconds;
        }
        internal static Plan Current;
        internal static string Output;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Initialize()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-aifren-qa-plan");
            if (!Requested) return;
            try
            {
                if (!Debug.isDebugBuild || !PlayerPrefs.IsIsolated || index < 0 || index + 1 >= args.Length)
                    throw new InvalidOperationException();
                string path = Path.GetFullPath(args[index + 1]);
                if (new FileInfo(path).Length > 65536 || HasLink(path)) throw new InvalidOperationException();
                Current = JsonUtility.FromJson<Plan>(File.ReadAllText(path));
                if (Current.version != 1 || Current.steps == null || Current.steps.Length > 100 ||
                    !((Current.width == 900 && Current.height == 1600) ||
                      (Current.width == 1920 && Current.height == 1080))) throw new InvalidOperationException();
                if (Current.display == null || Current.display.width != Current.width || Current.display.height != Current.height)
                    throw new InvalidOperationException();
                var displays = new System.Collections.Generic.List<DisplayInfo>();
                Screen.GetDisplayLayout(displays);
                int[] matches = displays.Select((display, number) => new { display, number }).Where(item =>
                    item.display.width == Current.display.width && item.display.height == Current.display.height)
                    .Select(item => item.number).ToArray();
                if (matches.Length != 1) throw new InvalidOperationException();
                DisplayIndex = matches[0];
                if (Current.steps.Any(step => step == null || string.IsNullOrEmpty(step.action) ||
                    step.action.Length > 32 || (step.name ?? "").Length > 64 || (step.value ?? "").Length > 4096 ||
                    float.IsNaN(step.seconds) || float.IsInfinity(step.seconds))) throw new InvalidOperationException();
                if (!Uri.TryCreate(Current.endpoint, UriKind.Absolute, out Uri uri) ||
                    uri.Scheme != "ws" || uri.Host != "127.0.0.1" || !string.IsNullOrEmpty(uri.UserInfo) ||
                    uri.Port <= 1024 || uri.AbsolutePath != "/") throw new InvalidOperationException();
                Output = Path.Combine(Path.GetDirectoryName(path), "capture");
                if (Directory.Exists(Output)) throw new InvalidOperationException();
                Directory.CreateDirectory(Output);
                Endpoint = Current.endpoint;
                Active = true;
                AudioListener.volume = 0f;
                // Separate in-memory application preferences, including before
                // Awake/Start. Unity engine window keys are separate from this owner.
                PlayerPrefs.DeleteAll();
                ApplyPresentation(Current.presentation);
                PlayerPrefs.SetString("AIFren.PresentationDisplaySettings.v1", JsonUtility.ToJson(
                    new PresentationDisplaySettings { displayIndex = DisplayIndex, width = Current.width, height = Current.height,
                        displayMode = PresentationDisplayMode.BorderlessFullscreen, frameLimit = 60 }));
                PlayerPrefs.SetInt("AIFren.ShowDialogueWhenHidden", 1);
                PlayerPrefs.SetInt("AIFren.SceneOverlay", 1);
                PlayerPrefs.SetInt("AIFren.ConsoleUnlocked", 1);
                PlayerPrefs.SetInt(PresentationAudio.SfxMutedKey, 1);
                PlayerPrefs.SetInt(PresentationAudio.BgmMutedKey, 1);
                PlayerPrefs.Save();
                Screen.SetResolution(Current.width, Current.height, FullScreenMode.FullScreenWindow);
            }
            catch
            {
                Active = false;
                Debug.LogError("Native QA plan rejected before application startup.");
                Application.Quit(3);
            }
        }

        internal static void ApplyPresentation(Preference[] preferences)
        {
            if (!PlayerPrefs.IsIsolated || (preferences?.Length ?? 0) > 32) throw new InvalidOperationException();
            if (preferences == null) return;
            string[] keys = { "DialogueRevealSpeed", "InstantDialogueText", "PresentationDisplaySettings.v1",
                "PushToTalkBinding", "PttAutoSend", "AvatarRenderScale", "GraphicsQuality", "ShowDialogueWhenHidden",
                "AlwaysOnTop", "AvatarLighting", "SceneOverlay", "ConsoleUnlocked", "PresentationTheme",
                "UiSfxMuted", "UiSfxVolume", "BgmMuted", "BgmVolume", "HiddenSubtitle.TextColor.v1" };
            foreach (Preference item in preferences)
            {
                bool allowed = item != null && keys.Any(key => item.name == "AIFren." + key);
                foreach (string orientation in new[] { "Portrait", "Landscape" })
                {
                    allowed |= item != null && new[] { "X", "Y", "Scale" }.Any(axis =>
                        item.name == "AIFren.AvatarPresentation." + orientation + "." + axis);
                    allowed |= item != null && (item.name == "AIFren.AvatarViewerBackground." + orientation ||
                        item.name == "AIFren.AvatarViewerBackground.CustomPath." + orientation);
                }
                if (!allowed || item.value == null || item.value.Length > 2048) throw new InvalidOperationException();
                switch (item.type)
                {
                    case "string": PlayerPrefs.SetString(item.name, item.value); break;
                    case "int": PlayerPrefs.SetInt(item.name, int.Parse(item.value, CultureInfo.InvariantCulture)); break;
                    case "float":
                        float value = float.Parse(item.value, CultureInfo.InvariantCulture);
                        if (float.IsNaN(value) || float.IsInfinity(value)) throw new InvalidOperationException();
                        PlayerPrefs.SetFloat(item.name, value); break;
                    default: throw new InvalidOperationException();
                }
            }
        }

        private static bool HasLink(string path)
        {
            for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return true;
            return false;
        }
#endif
    }
}
