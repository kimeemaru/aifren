using System;
using System.Collections.Generic;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterNativeQaReadinessTests
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private GameObject root, avatar;
        private AIFrenPocController controller;
        private AvatarLoader loader;
        private AIFrenWebSocketClient client;
        private TMP_InputField input;
        private Button send;
        private string character, next;
        private List<CharacterSummary> Characters => Get<List<CharacterSummary>>("availableCharacters");

        [SetUp]
        public void SetUp()
        {
            Assert.That(PresentationPreferences.IsIsolated, Is.True);
            character = Guid.NewGuid().ToString("D"); next = Guid.NewGuid().ToString("D");
            root = new GameObject("Synthetic QA readiness owner");
            controller = root.AddComponent<AIFrenPocController>(); controller.enabled = false;
            loader = root.AddComponent<AvatarLoader>(); loader.enabled = false;
            avatar = GameObject.CreatePrimitive(PrimitiveType.Cube); avatar.SetActive(false);
            typeof(AvatarLoader).GetProperty("ActiveAvatar").SetValue(loader, avatar);
            client = new AIFrenWebSocketClient();
            typeof(AIFrenWebSocketClient).GetProperty("State").SetValue(client, ConnectionState.Connected);
            input = Child<TMP_InputField>("Composer"); send = Child<Button>("Send");
            input.interactable = send.interactable = false;
            Set("client", client); Set("avatarLoader", loader); Set("messageInput", input); Set("sendButton", send);
            Set("qaSnapshots", 1); Set("characterStorageUnavailable", true);
            Set("avatarPresentationState", AvatarPresentationState.CreateUnbound(new AvatarConfiguration()));
            Bind("no-character");
        }

        [TearDown]
        public void TearDown()
        {
            Set("client", null); client.Dispose();
            UnityEngine.Object.DestroyImmediate(root); if (avatar != null) UnityEngine.Object.DestroyImmediate(avatar);
        }

        private T Child<T>(string name) where T : Component
        {
            var child = new GameObject(name, typeof(RectTransform)); child.transform.SetParent(root.transform); return child.AddComponent<T>();
        }
        private void Set(string field, object value) => typeof(AIFrenPocController).GetField(field, Private).SetValue(controller, value);
        private T Get<T>(string field) => (T)typeof(AIFrenPocController).GetField(field, Private).GetValue(controller);
        private object Call(string method, params object[] values) => typeof(AIFrenPocController).GetMethod(method, Private).Invoke(controller, values);
        private bool Empty() => (bool)Call("QaVerifiedEmptyCharacterState");
        private bool Ready(string selector) => (bool)Call("QaCharacterReady", selector);
        private bool Probe() => (bool)Call("QaProbeAvatar", "synthetic_test");

        private void Bind(string id)
        {
            string session = "synthetic-session-" + id;
            var fence = (CharacterSessionFence)typeof(AIFrenWebSocketClient).GetField("characterSession", Private).GetValue(client);
            fence.Reset();
            Assert.That(fence.Accept(new ServerMessage { type = "snapshot", character_id = id, character_session = session,
                character_generation = 3, data = new SnapshotData { character_id = id, character_session = session,
                    character_generation = 3, character = new CharacterIdentity { character_id = id } } }), Is.True);
            Set("activeCharacterId", id); Set("activeCharacterSession", session);
        }

        private void MakeBoundCharacter(string id, string name = "Synthetic")
        {
            Bind(id); Characters.Clear(); Characters.Add(new CharacterSummary { character_id = id, display_name = name, storage_status = "ready" });
            Set("characterStorageUnavailable", false); input.interactable = send.interactable = true; avatar.SetActive(true);
            Set("avatarPresentationState", AvatarPresentationState.LoadForCharacter(new AvatarConfiguration(), id,
                AvatarPresentationState.ManagedAssetIdentity(new string('a', 64))));
            Set("framingReadyAvatar", avatar);
        }

        [Test]
        public void VerifiedEmptyLibraryPassesWithExplicitAbsenceAndNoStartupAvatarRequirement()
        {
            Assert.That(Empty(), Is.True); Assert.That(Probe(), Is.True);
            Assert.That((bool)Call("QaStartupPresentationReady"), Is.True);
            Assert.That(Ready("Synthetic"), Is.False);
            typeof(AvatarLoader).GetProperty("ActiveAvatar").SetValue(loader, null);
            Assert.That(Empty(), Is.True, "A verified empty library does not need even an inactive avatar object.");
        }

        [Test]
        public void EmptyLibraryRejectsVisibleOutgoingAvatar()
        {
            avatar.SetActive(true);
            Assert.That(Empty(), Is.False); Assert.That(Probe(), Is.False);
            Assert.That((bool)Call("QaStartupPresentationReady"), Is.False);
        }

        [Test]
        public void EmptyLibraryRejectsEnabledComposerOrAbsentControls()
        {
            input.interactable = true; Assert.That(Empty(), Is.False); Assert.That(Probe(), Is.False);
            input.interactable = false; send.interactable = true; Assert.That(Empty(), Is.False);
            send.interactable = false; Set("messageInput", null); Assert.That(Empty(), Is.False);
        }

        [Test]
        public void EmptyLibraryRequiresAcceptedMatchingBindingAndUnavailableFlag()
        {
            Set("qaSnapshots", 0); Assert.That(Empty(), Is.False);
            Set("qaSnapshots", 1); Set("activeCharacterSession", "retired"); Assert.That(Empty(), Is.False);
            Bind("no-character"); Set("characterSwitchInFlight", true); Assert.That(Empty(), Is.False);
            Set("characterSwitchInFlight", false); Set("characterStorageUnavailable", false); Assert.That(Empty(), Is.False); Assert.That(Probe(), Is.False);
        }

        [Test]
        public void EmptyLibraryRejectsRemainingRegistryOrCanonicalRows()
        {
            Characters.Add(new CharacterSummary { character_id = character }); Assert.That(Empty(), Is.False);
            Characters.Clear(); Get<List<ConversationMessage>>("messages").Add(new ConversationMessage { role = "user", content = "Synthetic retained row" });
            Assert.That(Empty(), Is.False); Assert.That(Probe(), Is.False);
        }

        [Test]
        public void TechnicalUnavailableCharacterDoesNotMasqueradeAsEmptyLibrary()
        {
            Bind(character); Characters.Add(new CharacterSummary { character_id = character, storage_status = "ready" });
            Assert.That(Empty(), Is.False); Assert.That(Probe(), Is.False);
        }

        [Test]
        public void ReadyWaitDoesNotWeakenTheNormalGeometryProbe()
        {
            MakeBoundCharacter(character);
            Assert.That(Ready(character), Is.True); Assert.That(Ready("Synthetic"), Is.True);
            Assert.That(Probe(), Is.False, "A bound primitive is still not valid VRM geometry proof.");
            typeof(AvatarLoader).GetProperty("ActiveAvatar").SetValue(loader, null);
            Assert.That(Ready(character), Is.False); Assert.That(Probe(), Is.False);
        }

        [TestCase("characterCreateInFlight")]
        [TestCase("characterSwitchInFlight")]
        [TestCase("characterAvatarSwitchInFlight")]
        [TestCase("modelApplyInProgress")]
        public void ReadyWaitRejectsInFlightOwners(string field)
        {
            MakeBoundCharacter(character); Set(field, true);
            Assert.That(Ready(character), Is.False);
        }

        [Test]
        public void ReadyWaitRequiresMatchingIdentityUniqueNameAndReadyStorage()
        {
            MakeBoundCharacter(character); Assert.That(Ready(next), Is.False);
            Characters.Add(new CharacterSummary { character_id = next, display_name = "Synthetic", storage_status = "ready" });
            Assert.That(Ready("Synthetic"), Is.False); Assert.That(Ready(character), Is.True);
            Characters[0].operation_kind = "reset"; Assert.That(Ready(character), Is.False);
            Characters[0].operation_kind = ""; Characters[0].storage_status = "incomplete"; Assert.That(Ready(character), Is.False);
        }

        [Test]
        public void CreationReceiptCannotBeSatisfiedByAnOldCharacterWithTheSameName()
        {
            MakeBoundCharacter(character, "Same Name");
            Set("qaCreatedRequestId", "new-create"); Set("qaCreatedName", "Same Name");
            Assert.That(Ready("Same Name"), Is.False);
            Call("ObserveCharacterNativeQaEvent", new BackendEvent { type = "character_created", data = new BackendEventData {
                request_id = "retired-create", character_id = character, display_name = "Same Name" } });
            Assert.That(Ready("Same Name"), Is.False);
            Call("ObserveCharacterNativeQaEvent", new BackendEvent { type = "character_created", data = new BackendEventData {
                request_id = "new-create", character_id = next, display_name = "Same Name" } });
            Assert.That(Ready("Same Name"), Is.False, "Created event alone is not service/snapshot readiness.");
            MakeBoundCharacter(next, "Same Name"); Assert.That(Ready("Same Name"), Is.True);
        }

        [Test]
        public void WaitActionStagesNoMutationAndAllowsIdentityToArriveLater()
        {
            Assert.That((bool)Call("ApplyCharacterNativeQaStep", new NativeQaSession.Step { action = "character_wait_ready", value = "Future Synthetic", seconds = 45 }), Is.True);
            Assert.That(Characters.Count, Is.Zero); Assert.That(Get<string>("activeCharacterId"), Is.EqualTo("no-character"));
            Assert.That(Ready("Future Synthetic"), Is.False);
        }
    }
}
