using System;
using System.IO;
using System.Text;
using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterAvatarPreferenceTests
    {
        private string root;
        private ManagedAssetLibrary library;
        private string firstCharacter;
        private string secondCharacter;
        private bool hadLegacyPreference;
        private string legacyPreference;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "AIFrenCharacterAvatarTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            library = ManagedAssetLibrary.CreateForTesting(Path.Combine(root, "AssetLibrary"));
            firstCharacter = Guid.NewGuid().ToString("D");
            secondCharacter = Guid.NewGuid().ToString("D");
            hadLegacyPreference = PlayerPrefs.HasKey(AvatarLoader.CustomModelPathPreference);
            legacyPreference = PlayerPrefs.GetString(AvatarLoader.CustomModelPathPreference, string.Empty);
            AvatarLoader.ClearCustomModelPathPreference();
        }

        [TearDown]
        public void TearDown()
        {
            CharacterAvatarPreference.Delete(firstCharacter);
            CharacterAvatarPreference.Delete(secondCharacter);
            AvatarLoader.ClearCustomModelPathPreference();
            if (hadLegacyPreference)
                PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, legacyPreference);
            PlayerPrefs.Save();
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }

        [Test]
        public void DistinctCharactersRestoreDistinctManagedAvatarSelections()
        {
            ManagedAssetRecord first = ImportModel("first.vrm", 1);
            ManagedAssetRecord second = ImportModel("second.vrm", 2);
            CharacterAvatarPreference.SetManaged(firstCharacter, first.id);
            CharacterAvatarPreference.SetManaged(secondCharacter, second.id);

            Assert.AreEqual(first.id, CharacterAvatarPreference.Resolve(firstCharacter, library, string.Empty).Asset.id);
            Assert.AreEqual(second.id, CharacterAvatarPreference.Resolve(secondCharacter, library, string.Empty).Asset.id);
            Assert.AreEqual(first.id, CharacterAvatarPreference.Resolve(firstCharacter, library, string.Empty).Asset.id);
        }

        [Test]
        public void ChangingOneCharactersAvatarDoesNotChangeTheOtherCharacter()
        {
            ManagedAssetRecord first = ImportModel("first.vrm", 8);
            ManagedAssetRecord replacement = ImportModel("replacement.vrm", 9);
            ManagedAssetRecord second = ImportModel("second.vrm", 10);
            CharacterAvatarPreference.SetManaged(firstCharacter, first.id);
            CharacterAvatarPreference.SetManaged(secondCharacter, second.id);

            CharacterAvatarPreference.SetManaged(firstCharacter, replacement.id);

            Assert.AreEqual(replacement.id,
                CharacterAvatarPreference.Resolve(firstCharacter, library, string.Empty).Asset.id);
            Assert.AreEqual(second.id,
                CharacterAvatarPreference.Resolve(secondCharacter, library, string.Empty).Asset.id);
            Assert.AreEqual(replacement.id,
                CharacterAvatarPreference.Resolve(firstCharacter, library, string.Empty).Asset.id);
        }

        [Test]
        public void ReadingUnsavedCharacterUsesLegacyFallbackWithoutMigratingIt()
        {
            ManagedAssetRecord legacy = ImportModel("legacy.vrm", 3);

            CharacterAvatarPreference.Resolution resolved = CharacterAvatarPreference.Resolve(
                firstCharacter, library, legacy.path);

            Assert.AreEqual(legacy.id, resolved.Asset.id);
            Assert.IsFalse(resolved.IsExplicit);
            Assert.IsFalse(CharacterAvatarPreference.HasExplicit(firstCharacter));
        }

        [Test]
        public void ExplicitBundledSelectionOverridesLegacyGlobalModel()
        {
            ManagedAssetRecord legacy = ImportModel("legacy.vrm", 4);
            CharacterAvatarPreference.SetBundled(firstCharacter);

            CharacterAvatarPreference.Resolution resolved = CharacterAvatarPreference.Resolve(
                firstCharacter, library, legacy.path);

            Assert.IsTrue(resolved.IsExplicit);
            Assert.IsTrue(resolved.IsBundled);
            Assert.IsNull(resolved.Asset);
        }

        [Test]
        public void MissingExplicitAssetFallsBackWithoutRewritingPreferenceOnRead()
        {
            ManagedAssetRecord removed = ImportModel("removed.vrm", 5);
            CharacterAvatarPreference.SetManaged(firstCharacter, removed.id);
            library.Delete(ManagedAssetLibrary.ModelKind, new[] { removed.id });

            CharacterAvatarPreference.Resolution resolved = CharacterAvatarPreference.Resolve(
                firstCharacter, library, string.Empty);

            Assert.IsTrue(resolved.IsBundled);
            Assert.IsTrue(resolved.ExplicitAssetUnavailable);
            Assert.IsTrue(CharacterAvatarPreference.HasExplicit(firstCharacter));
        }

        [Test]
        public void DeletedAssetRepairDoesNotChangeAnotherCharactersSelection()
        {
            ManagedAssetRecord first = ImportModel("first.vrm", 6);
            ManagedAssetRecord second = ImportModel("second.vrm", 7);
            CharacterAvatarPreference.SetManaged(firstCharacter, first.id);
            CharacterAvatarPreference.SetManaged(secondCharacter, second.id);

            CharacterAvatarPreference.RepairDeletedAsset(
                new[] { firstCharacter, secondCharacter }, first.id);

            Assert.IsFalse(CharacterAvatarPreference.HasExplicit(firstCharacter));
            Assert.AreEqual(second.id, CharacterAvatarPreference.Resolve(secondCharacter, library, string.Empty).Asset.id);
        }

        private ManagedAssetRecord ImportModel(string name, byte marker)
        {
            string path = Path.Combine(root, name);
            byte[] json = Encoding.UTF8.GetBytes(
                "{\"asset\":{\"version\":\"2.0\"},\"extensions\":{\"VRM\":{\"marker\":" + marker + "}}}");
            int paddedLength = (json.Length + 3) & ~3;
            int originalLength = json.Length;
            Array.Resize(ref json, paddedLength);
            for (int index = originalLength; index < paddedLength; index++) json[index] = 0x20;
            using (var stream = File.Create(path))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(0x46546C67u); writer.Write(2u);
                writer.Write((uint)(12 + 8 + json.Length + 8 + 4));
                writer.Write((uint)json.Length); writer.Write(0x4E4F534Au); writer.Write(json);
                writer.Write(4u); writer.Write(0x004E4942u); writer.Write(new byte[4]);
            }
            Assert.IsTrue(library.TryImport(path, ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord model, out string error), error);
            return model;
        }
    }
}
