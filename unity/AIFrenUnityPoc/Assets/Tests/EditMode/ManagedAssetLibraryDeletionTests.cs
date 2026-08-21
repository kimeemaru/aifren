using System;
using System.IO;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ManagedAssetLibraryDeletionTests
    {
        private string root;
        private ManagedAssetLibrary library;
        private bool hadCustomModelPath;
        private string savedCustomModelPath;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "AIFrenAssetDeleteTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
            hadCustomModelPath = PlayerPrefs.HasKey(AvatarLoader.CustomModelPathPreference);
            savedCustomModelPath = PlayerPrefs.GetString(AvatarLoader.CustomModelPathPreference, string.Empty);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
            AvatarLoader.ClearCustomModelPathPreference();
            if (hadCustomModelPath) PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, savedCustomModelPath);
            PlayerPrefs.Save();
        }

        [Test]
        public void DeletesNonActiveModelAndThumbnail()
        {
            ManagedAssetRecord model = ImportModel("one.vrm", 1);
            string thumbnail = library.ThumbnailPath(model.id);
            Directory.CreateDirectory(Path.GetDirectoryName(thumbnail));
            File.WriteAllBytes(thumbnail, new byte[] { 1 });
            library.SetThumbnailPath(model.id, thumbnail);

            Assert.IsTrue(library.Delete(ManagedAssetLibrary.ModelKind, new[] { model.id }));
            Assert.IsFalse(File.Exists(model.path));
            Assert.IsFalse(File.Exists(thumbnail));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void BulkDeletionDoesNotSkipModels()
        {
            ManagedAssetRecord first = ImportModel("one.vrm", 1);
            ManagedAssetRecord second = ImportModel("two.vrm", 2);
            Assert.IsTrue(library.Delete(ManagedAssetLibrary.ModelKind, new[] { first.id, second.id }));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void DeletingOneKindPreservesSameHashInOtherKind()
        {
            string source = WriteVrm("shared.vrm", 3);
            Assert.IsTrue(library.TryImport(source, ManagedAssetLibrary.ModelKind, out ManagedAssetRecord model, out _));
            Assert.IsTrue(library.TryImport(source, ManagedAssetLibrary.BackgroundKind, out ManagedAssetRecord background, out _));
            Assert.AreEqual(model.id, background.id);

            library.Delete(ManagedAssetLibrary.ModelKind, new[] { model.id });
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
            Assert.AreEqual(1, library.Records(ManagedAssetLibrary.BackgroundKind).Count);
            Assert.IsTrue(File.Exists(background.path));
        }

        [Test]
        public void MissingManagedFileStillRemovesStaleRecord()
        {
            string source = Path.Combine(root, "background.png");
            File.WriteAllBytes(source, new byte[] { 137, 80, 78, 71 });
            Assert.IsTrue(library.TryImport(source, ManagedAssetLibrary.BackgroundKind, out ManagedAssetRecord background, out _));
            File.Delete(background.path);

            Assert.IsTrue(library.Delete(ManagedAssetLibrary.BackgroundKind, new[] { background.id }));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.BackgroundKind).Count);
        }

        [Test]
        public void ActiveModelPreferenceCanBeClearedBeforeItsManagedAssetIsRemoved()
        {
            ManagedAssetRecord model = ImportModel("active.vrm", 4);
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, model.path);
            PlayerPrefs.Save();

            AvatarLoader.ClearCustomModelPathPreference();
            library.Delete(ManagedAssetLibrary.ModelKind, new[] { model.id });

            Assert.IsFalse(PlayerPrefs.HasKey(AvatarLoader.CustomModelPathPreference));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void TraversalRecordIsRepairedWithoutTouchingExternalFile()
        {
            string external = WriteBytes("outside.vrm", new byte[] { 9, 8, 7 });
            string escaped = Path.Combine(root, "AssetLibrary", "Models", "..", "..", "outside.vrm");
            LoadTampered(new ManagedAssetRecord { id = "traversal", kind = ManagedAssetLibrary.ModelKind, path = escaped });

            Assert.IsTrue(File.Exists(external));
            CollectionAssert.AreEqual(new byte[] { 9, 8, 7 }, File.ReadAllBytes(external));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void AbsoluteExternalRecordIsRepairedWithoutTouchingExternalFile()
        {
            string external = WriteBytes("Downloads/external.vrm", new byte[] { 4, 5, 6 });
            LoadTampered(new ManagedAssetRecord { id = "external", kind = ManagedAssetLibrary.ModelKind, path = external });

            Assert.IsTrue(File.Exists(external));
            CollectionAssert.AreEqual(new byte[] { 4, 5, 6 }, File.ReadAllBytes(external));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void SimilarlyPrefixedAssetLibraryDirectoryIsNotAccepted()
        {
            string external = WriteBytes("AssetLibrary2/other.png", new byte[] { 1, 2, 3 });
            LoadTampered(new ManagedAssetRecord { id = "prefix", kind = ManagedAssetLibrary.BackgroundKind, path = external });

            Assert.IsTrue(File.Exists(external));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.BackgroundKind).Count);
        }

        [Test]
        public void WrongKindDirectoryRecordIsRepairedWithoutDeletingOtherKindFile()
        {
            string background = Path.Combine(root, "AssetLibrary", "Backgrounds", "image.png");
            Directory.CreateDirectory(Path.GetDirectoryName(background));
            File.WriteAllBytes(background, new byte[] { 7, 7 });
            LoadTampered(new ManagedAssetRecord { id = "wrong-kind", kind = ManagedAssetLibrary.ModelKind, path = background });

            Assert.IsTrue(File.Exists(background));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void BackgroundPointingIntoModelsIsRepairedWithoutDeletingModelFile()
        {
            string model = Path.Combine(root, "AssetLibrary", "Models", "model.vrm");
            Directory.CreateDirectory(Path.GetDirectoryName(model));
            File.WriteAllBytes(model, new byte[] { 5, 5 });
            LoadTampered(new ManagedAssetRecord { id = "background-in-models", kind = ManagedAssetLibrary.BackgroundKind, path = model });

            Assert.IsTrue(File.Exists(model));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.BackgroundKind).Count);
        }

        [Test]
        public void DirectoryRecordIsRepairedWithoutDeletingDirectory()
        {
            string directory = Path.Combine(root, "AssetLibrary", "Models", "folder-as-asset");
            Directory.CreateDirectory(directory);
            LoadTampered(new ManagedAssetRecord { id = "directory", kind = ManagedAssetLibrary.ModelKind, path = directory });

            Assert.IsTrue(Directory.Exists(directory));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void ExternalThumbnailMetadataIsClearedWithoutTouchingExternalFile()
        {
            ManagedAssetRecord model = ImportModel("thumbnail.vrm", 8);
            string external = WriteBytes("outside-thumbnail.png", new byte[] { 3, 2, 1 });
            LoadTampered(new ManagedAssetRecord { id = model.id, kind = ManagedAssetLibrary.ModelKind, path = model.path, thumbnailPath = external });

            Assert.IsTrue(File.Exists(external));
            CollectionAssert.AreEqual(new byte[] { 3, 2, 1 }, File.ReadAllBytes(external));
            Assert.AreEqual(library.ThumbnailPath(model.id), library.Records(ManagedAssetLibrary.ModelKind)[0].thumbnailPath);
        }

        [Test]
        public void ManagedDeleteNeverDeletesOriginalImportSource()
        {
            string source = WriteVrm("Downloads/source.vrm", 9);
            byte[] original = File.ReadAllBytes(source);
            Assert.IsTrue(library.TryImport(source, ManagedAssetLibrary.ModelKind, out ManagedAssetRecord model, out _));

            library.Delete(ManagedAssetLibrary.ModelKind, new[] { model.id });

            Assert.IsFalse(File.Exists(model.path));
            Assert.IsTrue(File.Exists(source));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
        }

        [Test]
        public void BulkDeleteStillDeletesSafeAssetWhenUnsafeRecordIsRepaired()
        {
            ManagedAssetRecord safe = ImportModel("safe.vrm", 10);
            string external = WriteBytes("external-bulk.vrm", new byte[] { 1, 4, 9 });
            LoadTampered(
                new ManagedAssetRecord { id = safe.id, kind = ManagedAssetLibrary.ModelKind, path = safe.path },
                new ManagedAssetRecord { id = "unsafe", kind = ManagedAssetLibrary.ModelKind, path = external });

            Assert.IsTrue(library.Delete(ManagedAssetLibrary.ModelKind, new[] { safe.id, "unsafe" }));
            Assert.IsFalse(File.Exists(safe.path));
            Assert.IsTrue(File.Exists(external));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        [Test]
        public void SymlinkedManagedPathIsRepairedWithoutTouchingLinkTarget()
        {
            string external = WriteBytes("external-link.vrm", new byte[] { 8, 8, 8 });
            string link = Path.Combine(root, "AssetLibrary", "Models", "linked.vrm");
            Directory.CreateDirectory(Path.GetDirectoryName(link));
            MethodInfo createLink = typeof(File).GetMethod("CreateSymbolicLink", BindingFlags.Public | BindingFlags.Static);
            if (createLink == null) Assert.Ignore("This Unity runtime does not expose File.CreateSymbolicLink.");
            createLink.Invoke(null, new object[] { link, external });
            LoadTampered(new ManagedAssetRecord { id = "symlink", kind = ManagedAssetLibrary.ModelKind, path = link });

            Assert.IsTrue(File.Exists(external));
            CollectionAssert.AreEqual(new byte[] { 8, 8, 8 }, File.ReadAllBytes(external));
            Assert.AreEqual(0, library.Records(ManagedAssetLibrary.ModelKind).Count);
        }

        private ManagedAssetRecord ImportModel(string name, byte marker)
        {
            Assert.IsTrue(library.TryImport(WriteVrm(name, marker), ManagedAssetLibrary.ModelKind, out ManagedAssetRecord model, out string error), error);
            return model;
        }

        private string WriteVrm(string name, byte marker)
        {
            string path = Path.Combine(root, name);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, new byte[] { 0x67, 0x6c, 0x54, 0x46, 2, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, marker });
            return path;
        }

        private string WriteBytes(string name, byte[] bytes)
        {
            string path = Path.Combine(root, name);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, bytes);
            return path;
        }

        private void LoadTampered(params ManagedAssetRecord[] records)
        {
            string indexPath = Path.Combine(root, "AssetLibrary", "library.json");
            Directory.CreateDirectory(Path.GetDirectoryName(indexPath));
            File.WriteAllText(indexPath, JsonUtility.ToJson(new ManagedAssetIndex { assets = new System.Collections.Generic.List<ManagedAssetRecord>(records) }, true));
            library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
        }
    }
}
