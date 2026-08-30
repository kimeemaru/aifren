using System;
using System.IO;
using System.Reflection;
using System.Text;
using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarModelFormatClassifierTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Application.temporaryCachePath, "AIFrenAvatarFormatTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }

        [Test]
        public void Vrm0MetadataInGlbIsClassifiedAsVrm0()
        {
            Assert.AreEqual("Vrm0", Classify(WriteGlb("avatar.glb", "VRM")).format);
        }

        [Test]
        public void Vrm1MetadataInGlbIsClassifiedAsVrm1()
        {
            Assert.AreEqual("Vrm1", Classify(WriteGlb("avatar.glb", "VRMC_vrm")).format);
        }

        [Test]
        public void GenericGlbIsRejectedWithoutCreatingALibraryRecord()
        {
            string generic = WriteGlb("ordinary.glb", null);
            var result = Classify(generic);
            Assert.IsFalse(result.supported);
            Assert.AreEqual("Unsupported", result.format);
            Assert.AreEqual("This GLB does not contain VRM avatar metadata.", result.error);

            ManagedAssetLibrary library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
            Assert.IsFalse(library.TryImport(generic, ManagedAssetLibrary.ModelKind, out _, out string error));
            Assert.AreEqual("This GLB does not contain VRM avatar metadata.", error);
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void ExistingVrmContainerRemainsSupported()
        {
            var result = Classify(WriteGlb("existing.vrm", "VRM"));
            Assert.IsTrue(result.supported);
            Assert.AreEqual("Vrm0", result.format);
        }

        [Test]
        public void CompatibleGlbKeepsItsExtensionInManagedStorage()
        {
            ManagedAssetLibrary library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
            Assert.IsTrue(library.TryImport(WriteGlb("avatar.glb", "VRM"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord record, out string error), error);

            Assert.AreEqual(".glb", Path.GetExtension(record.path));
            Assert.IsTrue(File.Exists(record.path));
        }

        [Test]
        public void BundledAvatarThumbnailUsesTheSameManagedThumbnailCache()
        {
            ManagedAssetLibrary library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
            string thumbnail = library.BundledAvatarThumbnailPath();

            Assert.AreEqual(library.ThumbnailPath(ManagedAssetLibrary.BundledAvatarThumbnailId), thumbnail);
            Type generator = typeof(AvatarLoader).Assembly.GetType("AIFren.UnityPoc.Avatar.VrmThumbnailGenerator");
            MethodInfo needsGeneration = generator.GetMethod("NeedsGeneration", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            Assert.IsTrue((bool)needsGeneration.Invoke(null, new object[] { thumbnail }));
        }

        private (bool supported, string format, string error) Classify(string path)
        {
            Type classifier = typeof(AvatarLoader).Assembly.GetType("AIFren.UnityPoc.Avatar.AvatarModelFormatClassifier");
            MethodInfo method = classifier.GetMethod("TryClassify", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            object[] arguments = { path, null, null };
            bool supported = (bool)method.Invoke(null, arguments);
            return (supported, arguments[1].ToString(), (string)arguments[2]);
        }

        private string WriteGlb(string filename, string extension)
        {
            string extensions = extension == null ? string.Empty : ",\"extensions\":{\"" + extension + "\":{}}";
            byte[] json = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"}" + extensions + "}");
            int originalJsonLength = json.Length;
            int paddedJsonLength = (json.Length + 3) & ~3;
            Array.Resize(ref json, paddedJsonLength);
            for (int index = originalJsonLength; index < paddedJsonLength; index++) json[index] = 0x20;

            string path = Path.Combine(root, filename);
            using (var stream = File.Create(path))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(0x46546C67u);
                writer.Write(2u);
                writer.Write((uint)(12 + 8 + json.Length + 8 + 4));
                writer.Write((uint)json.Length);
                writer.Write(0x4E4F534Au);
                writer.Write(json);
                writer.Write(4u);
                writer.Write(0x004E4942u);
                writer.Write(new byte[4]);
            }
            return path;
        }
    }
}
