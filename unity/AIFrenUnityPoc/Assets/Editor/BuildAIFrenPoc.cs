using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AIFren.UnityPoc.Editor
{
    /// <summary>Developer-only reproducible standalone build entry points.</summary>
    public static class BuildAIFrenPoc
    {
        /// <summary>Prove licensed project execution before a long native pass, without replacing a player.</summary>
        public static void PreflightEditorExecution()
        {
            RefuseLocalPresentationAssetsByDefault();
            EnsureBundledAvatarImported();
            Debug.Log("AIFren Editor execution preflight passed.");
        }

        public static void BuildWindows()
        {
            BuildStandalone(BuildTarget.StandaloneWindows64, "Windows", "AIFrenPoc.exe");
        }

        /// <summary>Tester build with the existing bounded, local flight recorder.</summary>
        public static void BuildWindowsDevelopment()
        {
            BuildStandalone(BuildTarget.StandaloneWindows64, "Windows", "AIFrenPoc.exe",
                BuildOptions.Development);
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

        /// <summary>Same production scene/resources; separate application preferences for finite QA.</summary>
        public static void BuildLinuxDevelopmentQa()
        {
            string product = PlayerSettings.productName;
            try
            {
                PlayerSettings.productName = "AIFren QA";
                BuildStandalone(BuildTarget.StandaloneLinux64, "LinuxDevelopmentQA",
                    "AIFrenPoc.x86_64", BuildOptions.Development);
            }
            finally
            {
                PlayerSettings.productName = product;
                AssetDatabase.SaveAssets();
            }
        }

        private static void BuildStandalone(
            BuildTarget target,
            string platformDirectory,
            string playerName,
            BuildOptions buildOptions = BuildOptions.None)
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            RefuseLocalPresentationAssetsByDefault();
            EnsureBundledAvatarImported();
            string outputDirectory = Path.Combine(projectRoot, "Builds", platformDirectory);
            BuildWithLastGoodRetention(outputDirectory, playerName, staging =>
            {
                BuildPlayerOptions options = new BuildPlayerOptions
                {
                    scenes = new[] { "Assets/Scenes/AIFrenPoc.unity" },
                    locationPathName = Path.Combine(staging, playerName),
                    target = target,
                    options = buildOptions
                };
                BuildReport report = BuildPipeline.BuildPlayer(options);
                if (report.summary.result != BuildResult.Succeeded)
                    throw new System.Exception("AIFren standalone build failed: " + report.summary.result);
            });
            Debug.Log("AIFren standalone build completed.");
        }

        // Build only into a fresh owned sibling. Failed compilation/import/build
        // cannot damage the last completed manual-QA target.
        public static void BuildWithLastGoodRetention(string destination, string playerName, System.Action<string> build)
        {
            if (Directory.Exists(destination) && (File.GetAttributes(destination) & FileAttributes.ReparsePoint) != 0)
                throw new System.IO.IOException("Build target cannot be a link.");
            string staging = destination + ".building-" + System.Guid.NewGuid().ToString("N");
            string previous = destination + ".previous-" + System.Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(staging);
            try
            {
                build(staging);
                if (!File.Exists(Path.Combine(staging, playerName))) throw new IOException("Completed player is missing.");
                if (Directory.Exists(destination)) Directory.Move(destination, previous);
                try { Directory.Move(staging, destination); }
                catch
                {
                    if (Directory.Exists(previous) && !Directory.Exists(destination)) Directory.Move(previous, destination);
                    throw;
                }
                if (Directory.Exists(previous)) Directory.Delete(previous, true);
            }
            finally { if (Directory.Exists(staging)) Directory.Delete(staging, true); }
        }

        private static void RefuseLocalPresentationAssetsByDefault()
        {
            string resources = Path.Combine(Application.dataPath, "Resources");
            ValidatePublicPresentationInputs(resources);
        }

        // A first package import can reach the VRM before its shader is available.
        // Unity can otherwise report a successful build with no avatar resource.
        public static void EnsureBundledAvatarImported()
        {
            const string assetPath = "Assets/Resources/LocalCharacter/model.vrm";
            if (!File.Exists(assetPath)) return;
            GameObject avatar = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (!HasRenderableAvatar(avatar))
            {
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate |
                    ImportAssetOptions.ForceSynchronousImport);
                avatar = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            }
            if (!HasRenderableAvatar(avatar))
                throw new IOException("Bundled avatar import is incomplete; refusing an avatar-free build.");
        }

        private static bool HasRenderableAvatar(GameObject avatar)
        {
            if (avatar == null) return false;
            Animator animator = avatar.GetComponentInChildren<Animator>(true);
            if (animator == null || animator.avatar == null || !animator.avatar.isHuman) return false;
            foreach (SkinnedMeshRenderer renderer in avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (renderer.sharedMesh != null && renderer.sharedMesh.vertexCount > 0 &&
                    renderer.sharedMaterials.Length > 0 &&
                    System.Array.TrueForAll(renderer.sharedMaterials, material =>
                        material != null && material.shader != null)) return true;
            }
            return false;
        }

        public static void ValidatePublicPresentationInputs(string resources)
        {
            string character = Path.Combine(resources, "LocalCharacter");
            if (Directory.Exists(Path.Combine(resources, "LocalBackground")))
                throw new IOException("Local backgrounds must not be included in a public build.");
            if (!Directory.Exists(character)) return; // Avatar import remains optional.
            if ((File.GetAttributes(character) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Bundled avatar directory cannot be a link.");
            foreach (string entry in Directory.EnumerateFileSystemEntries(character))
            {
                string name = Path.GetFileName(entry);
                if ((File.GetAttributes(entry) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0 ||
                    (name != "model.vrm" && name != "model.vrm.meta"))
                    throw new IOException("Unreviewed local presentation input in build.");
                string expected = name == "model.vrm" ? "831c87cf61f60071426b92978f43cfbacb3df19c83ed6d0a8d7629d2a6f2d1d2" : "0aab561cff42ba07cdbd4368456d43b2f94cbc6e454609120550a45934819cc0";
                using var input = File.OpenRead(entry);
                using var sha = System.Security.Cryptography.SHA256.Create();
                string actual = System.BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
                if (actual != expected) throw new IOException("Bundled sample differs from the reviewed public resource.");
            }
        }
    }
}
