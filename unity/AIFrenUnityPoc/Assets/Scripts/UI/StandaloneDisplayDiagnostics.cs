using System;
using System.Linq;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Development-only launch diagnostics for the standalone player's display
    /// selection. Unity writes these messages to the player's requested log
    /// file when the launcher includes -display-diagnostics.
    /// </summary>
    public static class StandaloneDisplayDiagnostics
    {
        private const string DiagnosticsArgument = "-display-diagnostics";

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void LogDisplayConfiguration()
        {
            string[] arguments = Environment.GetCommandLineArgs();
            if (!arguments.Contains(DiagnosticsArgument))
            {
                return;
            }

            string requestedMonitor = GetArgumentValue(arguments, "-monitor") ?? "(not supplied)";
            Debug.Log(
                "[AIFren Display] commandLine=" + string.Join(" ", arguments) +
                "; requestedMonitor=" + requestedMonitor +
                "; fullscreenMode=" + Screen.fullScreenMode +
                "; screen=" + Screen.width + "x" + Screen.height +
                "; graphicsDevice=" + SystemInfo.graphicsDeviceName +
                "; operatingSystem=" + SystemInfo.operatingSystem
            );
            DisplayInfo mainWindowDisplay = Screen.mainWindowDisplayInfo;
            Debug.Log(
                "[AIFren Display] mainWindowDisplay=" + mainWindowDisplay.name +
                "; dimensions=" + mainWindowDisplay.width + "x" + mainWindowDisplay.height +
                "; position=" + Screen.mainWindowPosition
            );

            Debug.Log("[AIFren Display] Unity reports " + Display.displays.Length + " connected display(s). " +
                "Display index 0 is Unity's primary display; these indices are unrelated to Windows display labels.");

            for (int index = 0; index < Display.displays.Length; index++)
            {
                Display display = Display.displays[index];
                Debug.Log(
                    "[AIFren Display] index=" + index +
                    "; active=" + display.active +
                    "; system=" + display.systemWidth + "x" + display.systemHeight +
                    "; rendering=" + display.renderingWidth + "x" + display.renderingHeight +
                    "; main=" + object.ReferenceEquals(display, Display.main)
                );
            }
        }

        private static string GetArgumentValue(string[] arguments, string name)
        {
            for (int index = 0; index + 1 < arguments.Length; index++)
            {
                if (string.Equals(arguments[index], name, StringComparison.OrdinalIgnoreCase))
                {
                    return arguments[index + 1];
                }
            }

            return null;
        }
    }
}
