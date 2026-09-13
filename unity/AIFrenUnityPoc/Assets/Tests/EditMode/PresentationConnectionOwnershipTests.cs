using System.Net.WebSockets;
using System.Reflection;
using System.Threading.Tasks;
using AIFren.UnityPoc.Protocol;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PresentationConnectionOwnershipTests
    {
        [Test]
        public void OldBackendSnapshotProducesVisibleWarningWithoutCharacterContentAdmission()
        {
            using var client = new AIFrenWebSocketClient();
            using var socket = new ClientWebSocket();
            typeof(AIFrenWebSocketClient).GetField("socket", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(client, socket);
            var oldSnapshot = new ServerMessage { type = "snapshot", data = new SnapshotData {
                transport_version = 8,
                character = new CharacterIdentity { character_id = "synthetic-old", name = "Synthetic Old" },
                conversation = new[] { new ConversationMessage { role = "assistant", content = "Old synthetic content must not publish." } },
            } };
            client.EnqueueReceived(socket, oldSnapshot);
            Assert.That(client.TryDequeue(out var warning), Is.True);
            Assert.That(warning.type, Is.EqualTo("command_error"));
            Assert.That(warning.error.code, Is.EqualTo("unsupported_backend_version"));
            StringAssert.Contains("current Development launcher", warning.error.message);
            Assert.That(warning.data, Is.Null);
            Assert.That(client.CharacterOwner, Is.Null);

            var current = CharacterSessionFenceTests.Snapshot("synthetic-current", "current-bind", 1);
            client.EnqueueReceived(socket, current);
            Assert.That(client.TryDequeue(out var accepted), Is.True);
            Assert.That(accepted, Is.SameAs(current));
            Assert.That(client.CharacterOwner.CharacterId, Is.EqualTo("synthetic-current"));

            client.EnqueueReceived(socket, CharacterSessionFenceTests.Snapshot("synthetic-newer", "newer-bind", 5));
            Assert.That(client.TryDequeue(out _), Is.True);
            client.EnqueueReceived(socket, oldSnapshot);
            Assert.That(client.TryDequeue(out warning), Is.True);
            Assert.That(client.CharacterOwner, Is.Null);
            client.EnqueueReceived(socket, current); // The warning cannot erase the connection's generation high-water mark.
            Assert.That(client.TryDequeue(out _), Is.False);
            client.EnqueueReceived(socket, CharacterSessionFenceTests.Snapshot("synthetic-newer", "fresh-bind", 6));
            Assert.That(client.TryDequeue(out _), Is.True);
            Assert.That(client.CharacterOwner.Generation, Is.EqualTo(6));
        }

        [Test] public async Task RetiredPacketsCannotReopenPresentationAfterReconnectToSameEndpoint()
        {
            using var client = new AIFrenWebSocketClient();
            using var oldSocket = new ClientWebSocket();
            using var replacement = new ClientWebSocket();
            var socketField = typeof(AIFrenWebSocketClient).GetField("socket", BindingFlags.NonPublic | BindingFlags.Instance);
            socketField.SetValue(client, oldSocket);
            var oldFinal = new ServerMessage { type = "event" };
            client.EnqueueReceived(oldSocket, oldFinal);
            await client.DisconnectAsync();
            Assert.That(client.TryDequeue(out _), Is.False);
            socketField.SetValue(client, replacement);
            client.EnqueueReceived(oldSocket, oldFinal); // late cancelled-worker completion
            var current = CharacterSessionFenceTests.Snapshot("synthetic-A", "new-bind", 1);
            client.EnqueueReceived(replacement, current);
            Assert.That(client.TryDequeue(out var received), Is.True);
            Assert.That(received, Is.SameAs(current));
            Assert.That(client.TryDequeue(out _), Is.False);
        }
    }
}
