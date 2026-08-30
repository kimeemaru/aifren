using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AIFren.UnityPoc.Editor
{
    /// <summary>Developer-only reproducible standalone build entry points.</summary>
    public static class BuildAIFrenPoc
    {
        public static void BuildWindows()
        {
            BuildStandalone(BuildTarget.StandaloneWindows64, "Windows", "AIFrenPoc.exe");
        }

        public static void BuildLinux()
        {
            BuildStandalone(BuildTarget.StandaloneLinux64, "Linux", "AIFrenPoc.x86_64");
        }

        /// <summary>Local diagnostic build with Unity's DEVELOPMENT_BUILD define enabled.</summary>
        public static void BuildLinuxDevelopment()
        {
            BuildStandalone(
                BuildTarget.StandaloneLinux64,
                "LinuxDevelopment",
                "AIFrenPoc.x86_64",
                BuildOptions.Development);
        }

        private static void BuildStandalone(
            BuildTarget target,
            string platformDirectory,
            string playerName,
            BuildOptions buildOptions = BuildOptions.None)
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            RefuseLocalPresentationAssetsByDefault();
            string outputDirectory = Path.Combine(projectRoot, "Builds", platformDirectory);
            Directory.CreateDirectory(outputDirectory);

            BuildPlayerOptions options = new BuildPlayerOptions
            {
                scenes = new[] { "Assets/Scenes/AIFrenPoc.unity" },
                locationPathName = Path.Combine(outputDirectory, playerName),
                target = target,
                options = buildOptions
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
            string localCharacter = Path.Combine(resources, "LocalCharacter");
            bool hasLocalCharacter = Directory.Exists(localCharacter) &&
                Directory.EnumerateFileSystemEntries(localCharacter).Any(entry =>
                {
                    string name = Path.GetFileName(entry);
                    return name != "model.vrm" && name != "model.vrm.meta";
                });
            bool hasLocalBackground = Directory.Exists(Path.Combine(resources, "LocalBackground"));
            if (hasLocalCharacter || hasLocalBackground)
            {
                throw new System.Exception(
                    "Refusing to package ignored local avatar/background assets. " +
                    "Use a clean project copy for a shareable test build, or set " +
                    "AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 only for a local development build."
                );
            }
        }
    }
}
