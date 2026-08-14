using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AIFren.UnityPoc.Editor
{
    /// <summary>Developer-only reproducible Windows build entry point.</summary>
    public static class BuildAIFrenPoc
    {
        public static void BuildWindows()
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            RefuseLocalPresentationAssetsByDefault();
            string outputDirectory = Path.Combine(projectRoot, "Builds", "Windows");
            Directory.CreateDirectory(outputDirectory);

            BuildPlayerOptions options = new BuildPlayerOptions
            {
                scenes = new[] { "Assets/Scenes/AIFrenPoc.unity" },
                locationPathName = Path.Combine(outputDirectory, "AIFrenPoc.exe"),
                target = BuildTarget.StandaloneWindows64,
                // Private visual-test builds should resemble a shipped player.
                // Unity's Development option adds its own bottom-right watermark.
                options = BuildOptions.None
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded)
            {
                throw new System.Exception("AIFren standalone build failed: " + report.summary.result);
            }

            Debug.Log("AIFren standalone build: " + options.locationPathName);
        }

        private static void RefuseLocalPresentationAssetsByDefault()
        {
            if (System.Environment.GetEnvironmentVariable("AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS") == "1")
            {
                return;
            }

            string resources = Path.Combine(Application.dataPath, "Resources");
            bool hasLocalCharacter = Directory.Exists(Path.Combine(resources, "LocalCharacter"));
            bool hasLocalBackground = Directory.Exists(Path.Combine(resources, "LocalBackground"));
            if (hasLocalCharacter || hasLocalBackground)
            {
                throw new System.Exception(
                    "Refusing to package ignored local avatar/background assets. " +
                    "Use a clean project copy for a shareable test build, or set " +
                    "AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 only for a private local build."
                );
            }
        }
    }
}
