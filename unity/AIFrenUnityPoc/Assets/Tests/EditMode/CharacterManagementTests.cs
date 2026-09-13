using System;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using System.Linq;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterManagementTests
    {
        private GameObject root;
        private AIFrenPocController controller;
        private TMP_InputField name, personality;
        private string first, second;

        [SetUp]
        public void SetUp()
        {
            first = Guid.NewGuid().ToString("D"); second = Guid.NewGuid().ToString("D");
            root = new GameObject("Synthetic character management fixture");
            controller = root.AddComponent<AIFrenPocController>(); controller.enabled = false;
            name = Child<TMP_InputField>("Name"); personality = Child<TMP_InputField>("Personality");
            name.text = "Same Name"; personality.text = "Synthetic authored personality.";
            Set("newCharacterNameInput", name); Set("newCharacterPersonalityInput", personality);
            Set("characterCreationStatus", Child<TextMeshProUGUI>("Creation status"));
            Set("creationName", name.text); Set("creationPersonality", personality.text);
            Set("creationRequestId", "request-a"); Set("characterCreateInFlight", true);
            Set("theme", PresentationThemes.Dark); Set("font", TMP_Settings.defaultFontAsset);
        }

        [TearDown]
        public void TearDown()
        {
            CharacterAvatarPreference.Delete(first); CharacterAvatarPreference.Delete(second);
            UnityEngine.Object.DestroyImmediate(root);
        }

        private T Child<T>(string label) where T : Component
        {
            GameObject item = new GameObject(label, typeof(RectTransform)); item.transform.SetParent(root.transform);
            return item.AddComponent<T>();
        }
        private void Set(string field, object value) => typeof(AIFrenPocController).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(controller, value);
        private T Get<T>(string field) => (T)typeof(AIFrenPocController).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
        private object Invoke(string method, params object[] values) => typeof(AIFrenPocController).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(controller, values);
        private BackendEventData Preview(string action = "delete") => new BackendEventData { character_id = first, display_name = "Same Name",
            action = action, token = "review-token", revision = 8, scope_text = "This synthetic character only.", files = new[] { "owned/conversation.json" } };

        [Test]
        public void SnapshotDecodesStorageOwnershipAndEmptyShellFlag()
        {
            var message = AIFrenProtocol.ParseServerMessage("{\"type\":\"snapshot\",\"data\":{\"registry_revision\":9,\"storage_unavailable\":true,\"characters\":[{\"character_id\":\"" + first + "\",\"display_name\":\"Same Name\",\"storage_layout\":\"local\",\"storage_status\":\"ready\",\"timeline_generation\":\"timeline-a\",\"operation_kind\":\"reset\",\"has_retained_copy\":true}]}}");
            Assert.That(message.data.registry_revision, Is.EqualTo(9)); Assert.That(message.data.storage_unavailable, Is.True);
            CharacterSummary item = message.data.characters[0];
            Assert.That(item.storage_layout, Is.EqualTo("local")); Assert.That(item.timeline_generation, Is.EqualTo("timeline-a"));
            Assert.That(item.operation_kind, Is.EqualTo("reset")); Assert.That(item.has_retained_copy, Is.True);
        }

        [Test]
        public void ConfirmationUsesImmutablePreviewTargetRatherThanCurrentCharacter()
        {
            BackendEventData data = Preview();
            CharacterOperationReceipt receipt = CharacterOperationReceipt.Capture(data, first, "delete");
            Set("activeCharacterId", second);
            data.character_id = second; data.revision = 99; data.token = "other"; data.files[0] = "wrong";
            ClientCommand command = receipt.ConfirmCommand();
            Assert.That(command.character_id, Is.EqualTo(first)); Assert.That(command.token, Is.EqualTo("review-token"));
            Assert.That(command.revision, Is.EqualTo(8)); Assert.That(receipt.Files[0], Is.EqualTo("owned/conversation.json"));
            string json = AIFrenProtocol.SerializeCommand(command);
            StringAssert.Contains("\"revision\":8", json); StringAssert.Contains("character_operation_confirm", json);
        }

        [Test]
        public void WrongTargetOrActionPreviewAndUnknownKindsFailClosed()
        {
            Assert.That(CharacterOperationReceipt.Capture(Preview(), second, "delete"), Is.Null);
            Assert.That(CharacterOperationReceipt.Capture(Preview(), first, "reset"), Is.Null);
            Assert.That(CharacterOperationReceipt.Capture(Preview("arbitrary_command"), first, "arbitrary_command"), Is.Null);
            BackendEventData missing = Preview(); missing.token = "";
            Assert.That(CharacterOperationReceipt.Capture(missing, first, "delete"), Is.Null);
        }

        [Test]
        public void RetainedCopyFlagDescribesCurrentStateWithoutContradictingCleanupScope()
        {
            BackendEventData data = Preview("cleanup"); data.old_copy_retained = true;
            data.scope_text = "Remove this character's retained old copy after verifying its ownership.";
            data.files = Enumerable.Range(0, 25).Select(index => "retained/synthetic-" + index + ".json").ToArray();
            var receipt = CharacterOperationReceipt.Capture(data, first, "cleanup");
            string text = AIFrenPocController.CharacterOperationPreviewText(receipt);
            StringAssert.Contains(data.scope_text, text);
            foreach (string file in data.files) StringAssert.Contains(file, text);
            StringAssert.Contains("currently exists", text);
            StringAssert.DoesNotContain("will be retained", text);
        }

        [Test]
        public void CancelledPreviewCannotBeReactivatedByLateResult()
        {
            Set("characterPreviewTarget", first); Set("characterPreviewAction", "delete");
            Invoke("CloseCharacterManager");
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_operation_preview", data = Preview() });
            Assert.That(Get<CharacterOperationReceipt>("characterOperationReceipt"), Is.Null);
        }

        [Test]
        public void DelayedResetAcknowledgementCannotClearCurrentHistoryOrViewer()
        {
            var history = Get<System.Collections.Generic.List<ConversationMessage>>("messages");
            history.Add(new ConversationMessage { role = "user", content = "Synthetic A history." });
            var viewer = Get<MemoryViewerState>("memoryViewerState");
            viewer.ChangeCharacter(second);
            string request = viewer.BeginRequest();
            var page = new MemoryViewPage { availability = "ready", character_id = second, lane = viewer.Lane,
                items = new[] { new MemoryViewItem { record_id = "synthetic-a-record" } } };
            Assert.That(viewer.Accept(request, page), Is.True);
            Set("activeCharacterId", second);
            Set("characterOperationInFlight", CharacterOperationReceipt.Capture(Preview("reset"), first, "reset"));
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_operation_result",
                data = new BackendEventData { character_id = first, action = "reset", status = "completed" } });
            Assert.That(history.Count, Is.EqualTo(1));
            Assert.That(viewer.Page, Is.SameAs(page));
            Assert.That(Get<string>("activeCharacterId"), Is.EqualTo(second));
        }

        [Test]
        public void DeletionResultMustNameExactlyTheConfirmedCharacter()
        {
            CharacterOperationReceipt receipt = CharacterOperationReceipt.Capture(Preview(), first, "delete");
            var result = new BackendEventData { character_id = first, action = "delete", status = "completed", deleted_character_id = second };
            Assert.That(receipt.MatchesResult(result), Is.False);
            result.deleted_character_id = first; Assert.That(receipt.MatchesResult(result), Is.True);
            result.status = "already_local"; Assert.That(receipt.MatchesResult(result), Is.False);
        }

        [Test]
        public void SuccessfulDeletionClearsOnlyMatchingCharacterVisualPreferences()
        {
            CharacterAvatarPreference.SetBundled(first); CharacterAvatarPreference.SetBundled(second);
            string visual = AvatarPresentationState.ManagedAssetIdentity(new string('a', 64));
            AvatarPresentationState.LoadForCharacter(new AvatarConfiguration(), first, visual).SetValues(true, new AvatarPresentationValues { x = .4f, scale = 2f }, true);
            AvatarPresentationState.LoadForCharacter(new AvatarConfiguration(), second, visual).SetValues(true, new AvatarPresentationValues { x = .7f, scale = 2f }, true);
            Set("characterOperationInFlight", CharacterOperationReceipt.Capture(Preview(), first, "delete"));
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_operation_result", data = new BackendEventData {
                character_id = first, action = "delete", status = "completed", deleted_character_id = first } });
            Assert.That(CharacterAvatarPreference.HasExplicit(first), Is.False); Assert.That(CharacterAvatarPreference.HasExplicit(second), Is.True);
            Assert.That(AvatarPresentationState.LoadForCharacter(new AvatarConfiguration(), first, visual).GetValues(true).x, Is.Zero);
            Assert.That(AvatarPresentationState.LoadForCharacter(new AvatarConfiguration(), second, visual).GetValues(true).x, Is.EqualTo(.7f));
        }

        [Test]
        public void MatchingCreationSuccessClearsSubmittedFormAndShowsIdentity()
        {
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_created", data = new BackendEventData {
                request_id = "request-a", character_id = first, display_name = "Same Name" } });
            Assert.That(name.text, Is.Empty); Assert.That(personality.text, Is.Empty);
            Assert.That(Get<bool>("characterCreateInFlight"), Is.False);
            StringAssert.Contains("Created Same Name", Get<TMP_Text>("characterCreationStatus").text);
        }

        [Test]
        public void LateCreationResultAndErrorCannotClearNewerForm()
        {
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_created", data = new BackendEventData {
                request_id = "retired-request", character_id = first, display_name = "Old" } });
            Invoke("HandleCharacterManagementError", new CommandError { code = "character_create_failed", request_id = "retired-request", message = "Old failure" });
            Assert.That(name.text, Is.EqualTo("Same Name")); Assert.That(Get<bool>("characterCreateInFlight"), Is.True);
        }

        [Test]
        public void CreationFailureRetainsAuthoredFormAndRetryIdentity()
        {
            Invoke("HandleCharacterManagementError", new CommandError { code = "character_create_failed", request_id = "request-a", message = "Synthetic failure" });
            Assert.That(name.text, Is.EqualTo("Same Name")); Assert.That(personality.text, Is.EqualTo("Synthetic authored personality."));
            Assert.That(Get<string>("creationRequestId"), Is.EqualTo("request-a")); Assert.That(Get<bool>("characterCreateInFlight"), Is.False);
        }

        [Test]
        public void SuccessfulCreationPreservesUserEditsMadeWhileWaiting()
        {
            name.text = "New draft"; personality.text = "Edited personality.";
            Invoke("HandleCharacterManagementEvent", new BackendEvent { type = "character_created", data = new BackendEventData {
                request_id = "request-a", character_id = first, display_name = "Same Name" } });
            Assert.That(name.text, Is.EqualTo("New draft")); Assert.That(personality.text, Is.EqualTo("Edited personality."));
        }

        [Test]
        public void DisconnectedCreateDoesNotEraseTheForm()
        {
            using (var client = new AIFrenWebSocketClient())
            {
                Set("client", client); Set("characterCreateInFlight", false);
                Invoke("CreateCharacterFromSettings");
                Assert.That(name.text, Is.EqualTo("Same Name")); Assert.That(personality.text, Is.EqualTo("Synthetic authored personality."));
                Set("client", null);
            }
        }

        [Test]
        public void UnavailableStorageDisablesComposerButLeavesSettingsAccessible()
        {
            using (var client = new AIFrenWebSocketClient())
            {
                typeof(AIFrenWebSocketClient).GetProperty("State").SetValue(client, ConnectionState.Connected);
                Set("client", client); Set("characterStorageUnavailable", true);
                var input = Child<TMP_InputField>("Composer"); var send = Child<Button>("Send");
                Set("messageInput", input); Set("sendButton", send);
                GameObject settings = new GameObject("Settings"); settings.transform.SetParent(root.transform); Set("settingsPanel", settings);
                Invoke("RefreshInputAvailability");
                Assert.That(input.interactable, Is.False); Assert.That(send.interactable, Is.False);
                Assert.That(settings.activeSelf, Is.True);
                Set("client", null);
            }
        }

        [TestCase("local", "ready", false)]
        [TestCase("legacy_shared", "ready", true)]
        public void ActualManagerShowsApplicableStorageActions(string layout, string status, bool migration)
        {
            Set("characterCreateInFlight", false);
            GameObject settings = new GameObject("Synthetic settings", typeof(RectTransform)); settings.transform.SetParent(root.transform);
            settings.GetComponent<RectTransform>().sizeDelta = new Vector2(800, 1200); Set("settingsPanel", settings);
            Invoke("OpenCharacterManager", new CharacterSummary { character_id = first, display_name = "<b>Synthetic</b>", storage_layout = layout, storage_status = status });
            var panel = Get<GameObject>("characterManagerPanel");
            string[] labels = panel.GetComponentsInChildren<Button>().Select(item => item.GetComponentInChildren<TMP_Text>()?.text).ToArray();
            Assert.That(labels.Contains("Migrate to character folder…"), Is.EqualTo(migration));
            Assert.That(labels.Contains("Reset timeline…"), Is.True); Assert.That(labels.Contains("Delete character…"), Is.True);
            Assert.That(Get<TMP_Text>("characterManagerTitle").richText, Is.False);
            StringAssert.Contains("<b>Synthetic</b>", Get<TMP_Text>("characterManagerTitle").text);
        }

        [Test]
        public void IncompleteCreationOffersOnlyDeletionMaintenance()
        {
            Set("characterCreateInFlight", false);
            GameObject settings = new GameObject("Synthetic settings", typeof(RectTransform)); settings.transform.SetParent(root.transform); Set("settingsPanel", settings);
            Invoke("OpenCharacterManager", new CharacterSummary { character_id = first, display_name = "Incomplete", storage_layout = "local", storage_status = "incomplete" });
            string[] labels = Get<GameObject>("characterManagerPanel").GetComponentsInChildren<Button>().Select(item => item.GetComponentInChildren<TMP_Text>()?.text).ToArray();
            Assert.That(labels.Contains("Delete character…"), Is.True); Assert.That(labels.Contains("Reset timeline…"), Is.False);
            Assert.That(labels.Contains("Open character folder"), Is.False);
        }

        [Test]
        public void RecordedOperationTakesPrecedenceOverIncompleteCreationActions()
        {
            Set("characterCreateInFlight", false);
            GameObject settings = new GameObject("Synthetic settings", typeof(RectTransform)); settings.transform.SetParent(root.transform); Set("settingsPanel", settings);
            Invoke("OpenCharacterManager", new CharacterSummary { character_id = first, display_name = "Interrupted", storage_layout = "local", storage_status = "incomplete", operation_kind = "delete" });
            string[] labels = Get<GameObject>("characterManagerPanel").GetComponentsInChildren<Button>().Select(item => item.GetComponentInChildren<TMP_Text>()?.text).ToArray();
            Assert.That(labels.Contains("Resume interrupted operation…"), Is.True);
            Assert.That(labels.Contains("Delete character…"), Is.False); Assert.That(labels.Contains("Reset timeline…"), Is.False);
        }

        [Test]
        public void FiniteQaSelectionRequiresExactUuidOrUnambiguousName()
        {
            var items = new[] { new CharacterSummary { character_id = first, display_name = "Duplicate" },
                new CharacterSummary { character_id = second, display_name = "Duplicate" } };
            Assert.That(AIFrenPocController.ResolveQaCharacter(items, second).character_id, Is.EqualTo(second));
            Assert.Throws<InvalidOperationException>(() => AIFrenPocController.ResolveQaCharacter(items, "Duplicate"));
            items[1].display_name = "Unique";
            Assert.That(AIFrenPocController.ResolveQaCharacter(items, "Unique").character_id, Is.EqualTo(second));
        }

        [TestCase("x=.25", 0, .25f)]
        [TestCase("y=-.4", 1, -.4f)]
        [TestCase("scale=1.5", 2, 1.5f)]
        public void FiniteQaFramingUsesOnlyExistingNumericControls(string text, int expectedField, float expectedValue)
        {
            Assert.That(AIFrenPocController.TryParseQaFramingValue(text, out int field, out float value), Is.True);
            Assert.That(field, Is.EqualTo(expectedField)); Assert.That(value, Is.EqualTo(expectedValue));
        }

        [TestCase("emotion=happy")]
        [TestCase("x=NaN")]
        [TestCase("scale=Infinity")]
        [TestCase("x=0=1")]
        public void FiniteQaFramingRejectsUnrelatedOrUnboundedValues(string text)
        {
            Assert.That(AIFrenPocController.TryParseQaFramingValue(text, out _, out _), Is.False);
        }

        [Test]
        public void FiniteQaCreationInvokesTheActualEnabledButtonCallback()
        {
            Button button = Child<Button>("Create callback"); Set("createCharacterButton", button);
            int clicked = 0; button.onClick.AddListener(() => clicked++);
            Invoke("ApplyCharacterNativeQaStep", new NativeQaSession.Step { action = "character_create" });
            Assert.That(clicked, Is.EqualTo(1));
            button.interactable = false;
            Assert.Throws<TargetInvocationException>(() => Invoke("ApplyCharacterNativeQaStep", new NativeQaSession.Step { action = "character_create" }));
            Assert.That(clicked, Is.EqualTo(1));
        }

        [TestCase("character_operation_failed")]
        [TestCase("invalid_character_operation")]
        [TestCase("character_folder_failed")]
        public void ClosedManagementErrorsRemainVisibleWithoutCharacterContentBinding(string code)
        {
            Assert.That(new CharacterSessionFence().Accept(new ServerMessage { type = "command_error", error = new CommandError { code = code } }), Is.True);
        }

        [TestCase("https://example.invalid/character")]
        [TestCase("relative/folder")]
        [TestCase("/tmp/folder\ncommand")]
        public void FolderOpenAcceptsOnlyBackendSuppliedAbsoluteLocalPath(string invalid)
        {
            Assert.That(AIFrenPocController.ValidatedLocalFolderUri(invalid), Is.Null);
            Assert.That(AIFrenPocController.ValidatedLocalFolderUri("/tmp/synthetic character"), Is.EqualTo("file:///tmp/synthetic%20character"));
        }
    }
}
