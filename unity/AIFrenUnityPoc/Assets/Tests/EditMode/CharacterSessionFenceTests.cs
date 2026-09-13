using AIFren.UnityPoc.Protocol;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterSessionFenceTests
    {
        [Test]
        public void BoundSnapshotAdmitsOnlyItsExactContentOwner()
        {
            var fence = new CharacterSessionFence();
            Assert.That(fence.Accept(Snapshot("A", "a-one", 1)), Is.True);
            Assert.That(fence.Accept(Event("A", "a-one", 1)), Is.True);
            Assert.That(fence.Accept(Event("B", "a-one", 1)), Is.False);
            Assert.That(fence.Accept(Event("A", "a-old", 1)), Is.False);
            Assert.That(fence.Accept(Event("A", "a-one", 0)), Is.False);
            Assert.That(fence.Accept(Event(null, null, 0)), Is.False);
        }

        [Test]
        public void AToBToEmptyCToADoesNotReacceptEarlierSameIdPackets()
        {
            var fence = new CharacterSessionFence();
            Assert.That(fence.Accept(Snapshot("A", "a-one", 1)), Is.True);
            var firstOwner = fence.Current;
            foreach (var next in new[] { Snapshot("B", "b-two", 2), Snapshot("C", "c-three", 3), Snapshot("A", "a-four", 4) })
            {
                Assert.That(fence.Accept(Switching(next.character_id, next.character_generation)), Is.True);
                Assert.That(fence.Switching, Is.True);
                Assert.That(fence.Current, Is.Null);
                Assert.That(fence.Accept(Event("A", "a-one", 1)), Is.False);
                Assert.That(fence.Accept(Snapshot("A", "a-one", 1)), Is.False);
                Assert.That(fence.Accept(next), Is.True);
                Assert.That(fence.Switching, Is.False);
            }
            Assert.That(fence.IsCurrent(firstOwner), Is.False);
            Assert.That(fence.Accept(Event("A", "a-four", 4)), Is.True);
        }

        [Test]
        public void FailedSwitchCanSettleOriginalIdWithFreshBinding()
        {
            var fence = new CharacterSessionFence();
            fence.Accept(Snapshot("A", "a-one", 1));
            Assert.That(fence.Accept(Switching("B", 2)), Is.True);
            Assert.That(fence.Accept(Snapshot("A", "a-recovered", 2)), Is.True);
            Assert.That(fence.Current.CharacterId, Is.EqualTo("A"));
            Assert.That(fence.Accept(Event("A", "a-one", 1)), Is.False);
        }

        [Test]
        public void NestedSnapshotIdentityMustMatchEnvelope()
        {
            var fence = new CharacterSessionFence();
            var snapshot = Snapshot("A", "a-one", 1);
            snapshot.data.character.character_id = "B";
            Assert.That(fence.Accept(snapshot), Is.False);
            snapshot = Snapshot("A", "a-one", 1); snapshot.data.character_session = "other";
            Assert.That(fence.Accept(snapshot), Is.False);
            snapshot = Snapshot("A", "a-one", 1); snapshot.data.character_generation = 2;
            Assert.That(fence.Accept(snapshot), Is.False);
            Assert.That(fence.Current, Is.Null);
        }

        [Test]
        public void SameGenerationCannotReplaceItsBindingWithoutTransition()
        {
            var fence = new CharacterSessionFence();
            fence.Accept(Snapshot("A", "a-one", 1));
            Assert.That(fence.Accept(Snapshot("A", "a-other", 1)), Is.False);
            Assert.That(fence.Accept(Switching("B", 1)), Is.False);
            Assert.That(fence.Accept(Snapshot("A", "a-one", 1)), Is.True);
        }

        [Test]
        public void NewConnectionResetsGenerationButRetiresOldOwner()
        {
            var fence = new CharacterSessionFence();
            fence.Accept(Snapshot("A", "previous-host", 90));
            var old = fence.Current;
            fence.Reset();
            Assert.That(fence.Accept(Snapshot("A", "replacement-host", 1)), Is.True);
            Assert.That(fence.IsCurrent(old), Is.False);
        }

        [Test]
        public void CapturedRowCommandCannotAcquireNewCharacterOrSameIdNewSession()
        {
            var old = new CharacterSessionOwner("A", "a-one", 1);
            var command = new ClientCommand { command = "continuity_control", action = "remove_scene_relation" };
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, old), Is.True);
            Assert.That(command.character_id, Is.EqualTo("A"));
            Assert.That(command.character_session, Is.EqualTo("a-one"));
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, new CharacterSessionOwner("B", "b-two", 2)), Is.False);
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, new CharacterSessionOwner("A", "a-three", 3)), Is.False);
            Assert.That(command.character_session, Is.EqualTo("a-one"));
        }

        [TestCase("submit_text")]
        [TestCase("continuity_control")]
        [TestCase("memory_view_query")]
        [TestCase("memory_view_detail")]
        [TestCase("memory_view_mutate")]
        [TestCase("ptt_press")]
        [TestCase("ptt_release")]
        [TestCase("stop_tts")]
        public void ScopedCommandCannotSendBeforeAuthoritativeBinding(string command)
        {
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(new ClientCommand { command = command }, null), Is.False);
        }

        [Test]
        public void GlobalSettingsRemainIndependentDuringTransition()
        {
            var fence = new CharacterSessionFence(); fence.Accept(Switching("B", 2));
            Assert.That(fence.Accept(new ServerMessage { type = "companion_preferences" }), Is.True);
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(new ClientCommand { command = "set_volume" }, null), Is.True);
            Assert.That(fence.Accept(new ServerMessage { type = "event", @event = new BackendEvent { type = "continuity_changed" } }), Is.False);
        }

        [TestCase("unknown_character")]
        [TestCase("character_create_failed")]
        [TestCase("character_select_failed")]
        [TestCase("invalid_character_personality")]
        public void ManagementFailureIsVisibleWithoutRebindingOrAdmittingOldContent(string code)
        {
            var fence = new CharacterSessionFence(); fence.Accept(Switching("B", 2));
            Assert.That(fence.Accept(new ServerMessage { type = "command_error", error = new CommandError { code = code } }), Is.True);
            Assert.That(fence.Switching, Is.True);
            Assert.That(fence.Current, Is.Null);
            Assert.That(fence.Accept(new ServerMessage { type = "command_error", error = new CommandError { code = "stale_character_control" } }), Is.False);
        }

        [Test]
        public void ResetCThenReturnToARejectsDelayedResetSnapshotAndViewerPage()
        {
            var fence = new CharacterSessionFence();
            var reset = Snapshot("C", "c-reset", 2);
            fence.Accept(Snapshot("C", "c-before", 1));
            Assert.That(fence.Accept(Switching("C", 2)), Is.True);
            Assert.That(fence.Accept(reset), Is.True);
            Assert.That(fence.Accept(Switching("A", 3)), Is.True);
            var a = Snapshot("A", "a-returned", 3);
            a.data.conversation = new[] { new ConversationMessage { role = "user", content = "Synthetic A canary." } };
            Assert.That(fence.Accept(a), Is.True);
            var owner = fence.Current;
            var page = Event("C", "c-reset", 2);
            page.@event = new BackendEvent { type = "memory_view_page", data = new BackendEventData {
                request_id = "old-c-request", memory_page = new MemoryViewPage { availability = "ready", character_id = "C", lane = "v2_claims" }
            } };
            foreach (var late in new[] { Switching("C", 2), reset, page, Snapshot("C", "c-before", 1) })
                Assert.That(fence.Accept(late), Is.False);
            Assert.That(fence.IsCurrent(owner), Is.True);
            Assert.That(fence.Accept(a), Is.True);
            Assert.That(a.data.conversation.Length, Is.EqualTo(1));
        }

        internal static ServerMessage Snapshot(string id, string session, long generation) => new ServerMessage {
            type = "snapshot", character_id = id, character_session = session, character_generation = generation,
            data = new SnapshotData {
                transport_version = 9, character_id = id, character_session = session, character_generation = generation,
                character = new CharacterIdentity { character_id = id, name = "Synthetic " + id },
                continuity = new ContinuitySnapshot { revision = session },
                conversation = System.Array.Empty<ConversationMessage>(),
            }
        };
        private static ServerMessage Event(string id, string session, long generation) => new ServerMessage {
            type = "event", character_id = id, character_session = session, character_generation = generation,
            @event = new BackendEvent { type = "continuity_changed" }
        };
        private static ServerMessage Switching(string id, long generation) => new ServerMessage {
            type = "event", character_id = id, character_session = "", character_generation = generation,
            @event = new BackendEvent { type = "character_switching" }
        };
    }
}
