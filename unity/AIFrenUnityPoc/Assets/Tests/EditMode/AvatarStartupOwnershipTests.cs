using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UI;
using TMPro;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarStartupOwnershipTests
    {
        private GameObject root, prefab;
        private AvatarLoader loader;
        private AIFrenPocController controller;
        private string directory, character, saved;
        private string secondCharacter;
        private bool hadSaved;

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "aifren-avatar-native-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            character = Guid.NewGuid().ToString("D");
            secondCharacter = Guid.NewGuid().ToString("D");
            hadSaved = PlayerPrefs.HasKey(AvatarLoader.CustomModelPathPreference);
            saved = PlayerPrefs.GetString(AvatarLoader.CustomModelPathPreference);
            root = new GameObject("owned component fixture");
            loader = root.AddComponent<AvatarLoader>(); loader.enabled = false;
            controller = root.AddComponent<AIFrenPocController>(); controller.enabled = false;
            // Avoid starting transport/UI discovery; drive the production selection/import methods.
            Set("avatarLoader", loader);
            Set("managedAssetLibrary", ManagedAssetLibrary.CreateForTesting(Path.Combine(directory, "library")));
            Set("activeCharacterId", character);
            Set("theme", PresentationThemes.Load());
            Set("statusDot", Child<Image>("status"));
            Set("statusLabel", Child<TextMeshProUGUI>("status label"));
            Set("statusDetailLabel", Child<TextMeshProUGUI>("detail label"));
            Set("messageInput", Child<TMP_InputField>("input"));
            Set("sendButton", Child<Button>("send"));
            foreach (string name in new[] { "memoryViewerLaneButton", "memoryViewerStatusButton", "memoryViewerScopeButton" })
                Set(name, Child<Button>(name));
            prefab = GameObject.CreatePrimitive(PrimitiveType.Cube);
            prefab.SetActive(false);
            loader.BundledForTesting = () => prefab;
        }

        [TearDown]
        public void TearDown()
        {
            CharacterAvatarPreference.Delete(character);
            CharacterAvatarPreference.Delete(secondCharacter);
            if (hadSaved) PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, saved);
            else PlayerPrefs.DeleteKey(AvatarLoader.CustomModelPathPreference);
            PlayerPrefs.Save();
            if (loader.ActiveAvatar != null) UnityEngine.Object.DestroyImmediate(loader.ActiveAvatar);
            UnityEngine.Object.DestroyImmediate(root);
            UnityEngine.Object.DestroyImmediate(prefab);
            Directory.Delete(directory, true);
        }

        [UnityTest]
        public IEnumerator SelectedBundledCharacterRetiresBlockedGlobalStartup()
        {
            string path = WriteModel("global.vrm");
            var completion = new TaskCompletionSource<GameObject>();
            loader.ImportForTesting = _ => completion.Task;
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, path);
            Invoke(loader, "Start");
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            completion.SetResult(GameObject.CreatePrimitive(PrimitiveType.Cube));
            for (int frame = 0; frame < 10; frame++) yield return null;
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"),
                "A selected bundled character must not inherit the retired global import.");
            Assert.That(loader.ActiveAvatar, Is.Not.Null);
        }

        [Test]
        public void SelectionBeforeStartPreventsIndependentGlobalImport()
        {
            int imports = 0;
            loader.ImportForTesting = _ => { imports++; throw new Exception("must not import"); };
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, WriteModel("global.vrm"));
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Invoke(loader, "Start");
            Assert.That(imports, Is.Zero);
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"));
            Assert.That(loader.ActiveAvatar, Is.Not.Null);
        }

        [UnityTest]
        public IEnumerator ReturningToBundledCharacterRejectsPendingOtherCharacter()
        {
            var library = (ManagedAssetLibrary)typeof(AIFrenPocController).GetField(
                "managedAssetLibrary", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
            Assert.That(library.TryImport(WriteModel("second.vrm"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord asset, out string error), Is.True, error);
            CharacterAvatarPreference.SetBundled(character);
            CharacterAvatarPreference.SetManaged(secondCharacter, asset.id);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            var completion = new TaskCompletionSource<GameObject>();
            loader.ImportForTesting = _ => completion.Task;
            Set("activeCharacterId", secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", secondCharacter);
            Set("activeCharacterId", character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            completion.SetResult(GameObject.CreatePrimitive(PrimitiveType.Cube));
            for (int frame = 0; frame < 10; frame++) yield return null;
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"));
            Assert.That(loader.ActiveAvatar.activeSelf, Is.True);
            Assert.That(CharacterAvatarPreference.Resolve(secondCharacter, library, "").Asset.id, Is.EqualTo(asset.id));
        }

        [Test]
        public void MissingExplicitAssetCreatesBundledAvatarInsteadOfAcceptingEmptyPath()
        {
            CharacterAvatarPreference.SetManaged(character, new string('a', 64));
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Assert.That(loader.ActiveAvatar, Is.Not.Null);
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"));
            Assert.That(CharacterAvatarPreference.HasExplicit(character), Is.True);
        }

        [UnityTest]
        public IEnumerator ManagedCharacterCompletionsUseSelectionOwnershipNotModelNames()
        {
            var library = (ManagedAssetLibrary)typeof(AIFrenPocController).GetField(
                "managedAssetLibrary", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
            Assert.That(library.TryImport(WriteModel("first.vrm"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord first, out string error), Is.True, error);
            Assert.That(library.TryImport(WriteModel("second.vrm"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord second, out error), Is.True, error);
            CharacterAvatarPreference.SetManaged(character, first.id);
            CharacterAvatarPreference.SetManaged(secondCharacter, second.id);
            var early = new TaskCompletionSource<GameObject>();
            var late = new TaskCompletionSource<GameObject>();
            int imports = 0;
            loader.ImportForTesting = _ => ++imports == 1 ? early.Task : late.Task;
            // The independent legacy global startup can overlap the first
            // character request. Character-to-character requests themselves
            // remain serialized by the existing controller queue.
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, first.path);
            Invoke(loader, "Start");
            Set("activeCharacterId", secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", secondCharacter);
            late.SetResult(GameObject.CreatePrimitive(PrimitiveType.Cube));
            for (int frame = 0; frame < 10; frame++) yield return null;
            string selectedPath = loader.ActiveModelPath;
            Assert.That(selectedPath, Is.Not.Empty);
            early.SetException(new InvalidOperationException("PRIVATE synthetic import failure"));
            for (int frame = 0; frame < 10; frame++) yield return null;
            Assert.That(loader.ActiveModelPath, Is.EqualTo(selectedPath));
            Assert.That(loader.ActiveAvatar, Is.Not.Null);
            Assert.That((string)typeof(AIFrenPocController).GetField("activeCharacterId",
                BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller), Is.EqualTo(secondCharacter));
            Assert.That(CharacterAvatarPreference.Resolve(character, library, "").Asset.id, Is.EqualTo(first.id));
        }

        [Test]
        public void RecreatedLoaderUsesPersistedCharacterSelectionBeforeGlobalStartup()
        {
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, WriteModel("global.vrm"));
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            UnityEngine.Object.DestroyImmediate(loader.ActiveAvatar);
            UnityEngine.Object.DestroyImmediate(loader);
            loader = root.AddComponent<AvatarLoader>(); loader.enabled = false;
            loader.BundledForTesting = () => prefab;
            loader.ImportForTesting = _ => throw new InvalidOperationException("Retired global selection must not run");
            Set("avatarLoader", loader);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Invoke(loader, "Start");
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"));
            Assert.That(loader.ActiveAvatar, Is.Not.Null);
        }

        [Test]
        public void SharedBundledAvatarRestoresEachCharactersOwnFraming()
        {
            Set("avatarPresentationState", AvatarPresentationState.Load(new AvatarConfiguration()));
            CharacterAvatarPreference.SetBundled(character);
            CharacterAvatarPreference.SetBundled(secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Framing().SetValues(true, new AvatarPresentationValues { x = .35f, y = -.1f, scale = 1.8f }, true);
            Set("activeCharacterId", secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", secondCharacter);
            Assert.That(Framing().GetValues(true).x, Is.EqualTo(0f), "An unsaved character must not inherit the prior character's framing.");
            Framing().SetValues(true, new AvatarPresentationValues { x = -.4f, y = .2f, scale = 1.3f }, true);
            Set("activeCharacterId", character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Assert.That(Framing().GetValues(true).x, Is.EqualTo(.35f));
            Assert.That(Framing().GetValues(true).scale, Is.EqualTo(1.8f));
        }

        [UnityTest]
        public IEnumerator SwitchStartRetiresPendingAvatarBeforeDestinationSnapshot()
        {
            var library = (ManagedAssetLibrary)typeof(AIFrenPocController).GetField(
                "managedAssetLibrary", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
            Assert.That(library.TryImport(WriteModel("pending.vrm"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord asset, out string error), Is.True, error);
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            var completion = new TaskCompletionSource<GameObject>();
            loader.ImportForTesting = _ => completion.Task;
            Invoke(controller, "RequestManagedAvatarModel", asset, false, true, character);
            Invoke(controller, "BeginCharacterPresentationTransition");
            completion.SetResult(GameObject.CreatePrimitive(PrimitiveType.Cube));
            for (int frame = 0; frame < 10; frame++) yield return null;
            Assert.That(loader.ActiveModelPath, Is.EqualTo("Bundled model"), "An import completing in the transition gap cannot activate the prior selection.");
            Assert.That(CharacterAvatarPreference.Resolve(character, library, "").IsBundled, Is.True,
                "Late visual completion must not write the retired character's preference.");
        }

        [Test]
        public void FramingCancelRestoresOnlyItsCapturedDraftWithoutSaving()
        {
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Framing().SetValues(false, new AvatarPresentationValues { x = .2f, scale = 1.5f }, true);
            BeginFramingEdit();
            Framing().SetValues(false, new AvatarPresentationValues { x = .6f, scale = 2f }, false);
            Invoke(controller, "CancelAvatarViewEditor");
            Assert.That(Framing().GetValues(false).x, Is.EqualTo(.2f));
            Assert.That(ReloadFraming(character).GetValues(false).x, Is.EqualTo(.2f));
        }

        [Test]
        public void FramingSaveCommitsOnlyTheCapturedCharacterAndCurrentLayout()
        {
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            BeginFramingEdit();
            Framing().SetValues(false, new AvatarPresentationValues { x = .6f, scale = 2f }, false);
            Framing().SetValues(true, new AvatarPresentationValues { x = .8f, scale = 2f }, false);
            Invoke(controller, "SaveAvatarViewEditor"); // No display override: landscape is the current layout.
            Assert.That(ReloadFraming(character).GetValues(false).x, Is.EqualTo(.6f));
            Assert.That(ReloadFraming(character).GetValues(true).x, Is.EqualTo(0f));
            Assert.That(ReloadFraming(secondCharacter).GetValues(false).x, Is.EqualTo(0f));
        }

        [Test]
        public void RetiredEditorSaveCannotWriteTheNextCharacterOrItsOriginalDraft()
        {
            CharacterAvatarPreference.SetBundled(character);
            CharacterAvatarPreference.SetBundled(secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            BeginFramingEdit();
            Framing().SetValues(false, new AvatarPresentationValues { x = .6f, scale = 2f }, false);
            Set("activeCharacterId", secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", secondCharacter);
            Framing().SetValues(false, new AvatarPresentationValues { x = -.4f, scale = 1.3f }, true);
            Invoke(controller, "SaveAvatarViewEditor");
            Assert.That(ReloadFraming(character).GetValues(false).x, Is.EqualTo(0f));
            Assert.That(ReloadFraming(secondCharacter).GetValues(false).x, Is.EqualTo(-.4f));
        }

        [Test]
        public void SameCharacterRebindAfterFailedSwitchRestoresReadyFraming()
        {
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Framing().SetValues(false, new AvatarPresentationValues { x = .4f, scale = 1.5f }, true);
            Invoke(controller, "BeginCharacterPresentationTransition");
            Assert.That(loader.ActiveAvatar.activeSelf, Is.False);
            Set("characterSwitchInFlight", false); // The original owner's new authoritative snapshot settles failure.
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Assert.That(loader.ActiveAvatar.activeSelf, Is.True);
            Assert.That(Framing().GetValues(false).x, Is.EqualTo(.4f));
        }

        [Test]
        public void EmptyLibraryShellRetiresVisualWithoutCreatingCharacterPreferences()
        {
            CharacterAvatarPreference.SetBundled(character);
            Invoke(controller, "RequestCharacterAvatarPreference", character);
            Set("activeCharacterId", "no-character");
            Invoke(controller, "RequestCharacterAvatarPreference", "no-character");
            Assert.That(loader.ActiveAvatar.activeSelf, Is.False);
            Assert.That(Framing().IsCharacterScoped, Is.False);
            Assert.That(CharacterAvatarPreference.HasExplicit(character), Is.True);
            Assert.That(CharacterAvatarPreference.HasExplicit("no-character"), Is.False);
        }

        [Test]
        public void StaleExplicitAvatarRequestCannotReachLoaderOrWriteRetiredPreference()
        {
            var library = (ManagedAssetLibrary)typeof(AIFrenPocController).GetField(
                "managedAssetLibrary", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
            Assert.That(library.TryImport(WriteModel("retired.vrm"), ManagedAssetLibrary.ModelKind,
                out ManagedAssetRecord asset, out string error), Is.True, error);
            CharacterAvatarPreference.SetBundled(character); CharacterAvatarPreference.SetBundled(secondCharacter);
            Set("activeCharacterId", secondCharacter);
            Invoke(controller, "RequestCharacterAvatarPreference", secondCharacter);
            GameObject currentAvatar = loader.ActiveAvatar; AvatarPresentationState currentFraming = Framing();
            int imports = 0;
            loader.ImportForTesting = _ => { imports++; return Task.FromResult(GameObject.CreatePrimitive(PrimitiveType.Cube)); };
            Invoke(controller, "RequestManagedAvatarModel", asset, false, true, character);
            Invoke(controller, "RequestBundledAvatarModel", true, character);
            Assert.That(imports, Is.Zero); Assert.That(loader.ActiveAvatar, Is.SameAs(currentAvatar));
            Assert.That(Framing(), Is.SameAs(currentFraming)); Assert.That(loader.ActiveAvatar.activeSelf, Is.True);
            Assert.That(CharacterAvatarPreference.Resolve(character, library, "").IsBundled, Is.True);
        }

        [Test]
        public void PickerCompletionRequiresOriginalCharacterGenerationAndNoTransition()
        {
            Assert.That(AIFrenPocController.IsAvatarPickerCompletionCurrent(3, 3, character, character, false), Is.True);
            Assert.That(AIFrenPocController.IsAvatarPickerCompletionCurrent(3, 3, character, secondCharacter, false), Is.False);
            Assert.That(AIFrenPocController.IsAvatarPickerCompletionCurrent(3, 5, character, character, false), Is.False,
                "Returning to A after B does not restore an old file picker's ownership.");
            Assert.That(AIFrenPocController.IsAvatarPickerCompletionCurrent(3, 3, character, character, true), Is.False);
        }

        private AvatarPresentationState ReloadFraming(string owner) => AvatarPresentationState.LoadForCharacter(
            AvatarConfiguration.Load(), owner, AvatarPresentationState.BundledAssetIdentity(AvatarConfiguration.Load()));

        private void BeginFramingEdit()
        {
            Set("avatarViewEditOwner", Framing());
            Set("avatarViewEditGeneration", (int)typeof(AIFrenPocController).GetField(
                "modelApplyGeneration", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller));
            Set("avatarViewPortraitSnapshot", Framing().GetValues(true));
            Set("avatarViewLandscapeSnapshot", Framing().GetValues(false));
            Set("avatarViewEditing", true);
        }

        private AvatarPresentationState Framing() => (AvatarPresentationState)typeof(AIFrenPocController)
            .GetField("avatarPresentationState", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);

        private string WriteModel(string name)
        {
            string path = Path.Combine(directory, name);
            byte[] json = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\",\"generator\":\"" + name +
                "\"},\"extensions\":{\"VRM\":{}}}");
            int length = json.Length; Array.Resize(ref json, (length + 3) & ~3);
            for (int i = length; i < json.Length; i++) json[i] = 32;
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0x46546C67u); writer.Write(2u); writer.Write((uint)(28 + json.Length));
                writer.Write((uint)json.Length); writer.Write(0x4E4F534Au); writer.Write(json);
                writer.Write(0u); writer.Write(0x004E4942u);
            }
            return path;
        }

        private void Set(string field, object value) => typeof(AIFrenPocController)
            .GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(controller, value);
        private T Child<T>(string name) where T : Component
        {
            var child = new GameObject(name, typeof(RectTransform));
            child.transform.SetParent(root.transform);
            return child.AddComponent<T>();
        }
        private static void Invoke(object target, string method, params object[] args) => target.GetType()
            .GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, args);
    }
}
