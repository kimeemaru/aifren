using System.Net.WebSockets;
using System.Reflection;
using System.Threading.Tasks;
using AIFren.UnityPoc.Protocol;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PresentationConnectionOwnershipTests
    {
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
            var current = new ServerMessage { type = "snapshot" };
            client.EnqueueReceived(replacement, current);
            Assert.That(client.TryDequeue(out var received), Is.True);
            Assert.That(received, Is.SameAs(current));
            Assert.That(client.TryDequeue(out _), Is.False);
        }
    }
}
