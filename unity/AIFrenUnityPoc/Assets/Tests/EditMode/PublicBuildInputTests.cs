using System;
using System.IO;
using System.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PublicBuildInputTests
    {
        private static void Validate(string resources)
        {
            Type owner = AppDomain.CurrentDomain.GetAssemblies().Select(assembly =>
                assembly.GetType("AIFren.UnityPoc.Editor.BuildAIFrenPoc")).Single(type => type != null);
            var validate = (Action<string>)Delegate.CreateDelegate(typeof(Action<string>),
                owner.GetMethod("ValidatePublicPresentationInputs"));
            validate(resources);
        }

        [Test]
        public void ExistingPublicSamplePassesTheDistributionGuard()
        {
            Assert.DoesNotThrow(() => Validate(
                Path.Combine(Application.dataPath, "Resources")));
        }

        [Test]
        public void BundledSampleMustImportAsAHumanoidWithGeometryBeforeBuild()
        {
            Type owner = AppDomain.CurrentDomain.GetAssemblies().Select(assembly =>
                assembly.GetType("AIFren.UnityPoc.Editor.BuildAIFrenPoc")).Single(type => type != null);
            var prepare = (Action)Delegate.CreateDelegate(typeof(Action),
                owner.GetMethod("EnsureBundledAvatarImported"));
            prepare();
            GameObject avatar = Resources.Load<GameObject>("LocalCharacter/model");
            Assert.IsNotNull(avatar, "The reviewed public sample must be included, not silently dropped.");
            Assert.IsTrue(avatar.GetComponentInChildren<Animator>(true).avatar.isHuman);
            Assert.IsTrue(avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(
                renderer => renderer.sharedMesh != null && renderer.sharedMesh.vertexCount > 0));
        }

        [TestCase("avatar")]
        [TestCase("background")]
        [TestCase("extra")]
        public void UnreviewedPresentationInputsFailClosed(string kind)
        {
            string root = Path.Combine(Path.GetTempPath(), "public-build-input-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                string dir = Path.Combine(root, kind == "background" ? "LocalBackground" : "LocalCharacter");
                Directory.CreateDirectory(dir);
                File.WriteAllText(Path.Combine(dir, kind == "avatar" ? "model.vrm" : "unreviewed.txt"), "synthetic");
                Assert.Throws<IOException>(() => Validate(root));
            }
            finally { Directory.Delete(root, true); }
        }

        [Test]
        public void MissingOptionalLocalResourcesDoesNotRequirePrivateAssets()
        {
            string root = Path.Combine(Path.GetTempPath(), "public-build-empty-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try { Assert.DoesNotThrow(() => Validate(root)); }
            finally { Directory.Delete(root); }
        }
    }
}
