using NUnit.Framework;
using AIFren.UnityPoc.UI;
using AIFren.UnityPoc.Protocol;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PresentationInputPolicyTests
    {
        [Test]
        public void OverlayPreventsGlobalEnterFromOpeningInput()
        {
            Assert.IsFalse(PresentationInputPolicy.CanOpenInput(true, false));
        }

        [Test]
        public void EmptyOpenInputDismissesOnEnterOrEscape()
        {
            Assert.IsTrue(PresentationInputPolicy.ShouldDismissEmptyInput(true, false));
            Assert.IsFalse(PresentationInputPolicy.ShouldDismissEmptyInput(true, true));
        }

        [Test]
        public void SnapshotAndConsoleDiagnosticPayloadsParseWithTheirLiveFields()
        {
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"voice\":{\"state\":\"ready\"},\"tts\":{\"provider\":\"audio8\",\"voice\":\"reference\",\"device\":\"local\"},\"models\":{\"current\":{\"mode\":\"local\",\"provider\":\"openai_compatible\",\"configured\":true,\"model\":\"local-test\"}}}}"
            );
            ServerMessage console = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"event\",\"event\":{\"type\":\"console_log\",\"data\":{\"lines\":[\"Backend listening\"]}}}"
            );

            Assert.AreEqual("audio8", snapshot.data.tts.provider);
            Assert.AreEqual("local-test", snapshot.data.models.current.model);
            Assert.AreEqual("ready", snapshot.data.voice.state);
            Assert.AreEqual("Backend listening", console.@event.data.lines[0]);
        }

        [Test]
        public void VoicePayloadPreservesGlobalListenerAvailability()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"event\",\"event\":{\"type\":\"voice_state\",\"data\":{\"state\":\"ready\",\"global_listener\":true}}}"
            );

            Assert.IsTrue(message.@event.data.global_listener);
        }
    }
}
