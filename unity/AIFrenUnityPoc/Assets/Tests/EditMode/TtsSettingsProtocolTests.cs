using AIFren.UnityPoc.Protocol;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class TtsSettingsProtocolTests
    {
        [Test]
        public void EarlySpeechCommandCarriesPersistedBoolean()
        {
            string command = AIFrenProtocol.SerializeCommand(new ClientCommand
            {
                command = "set_kokoro_early_speech",
                early_speech = false,
            });

            StringAssert.Contains("\"command\":\"set_kokoro_early_speech\"", command);
            StringAssert.Contains("\"early_speech\":false", command);
        }

        [Test]
        public void SnapshotDistinguishesPersistedEffectiveAndOverrideState()
        {
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"transport_version\":3,\"tts\":{" +
                "\"early_speech\":false,\"early_speech_configured\":true," +
                "\"early_speech_overridden\":true,\"early_speech_supported\":true}}}"
            );

            Assert.IsFalse(snapshot.data.tts.early_speech);
            Assert.IsTrue(snapshot.data.tts.early_speech_configured);
            Assert.IsTrue(snapshot.data.tts.early_speech_overridden);
            Assert.IsTrue(snapshot.data.tts.early_speech_supported);
        }

        [Test]
        public void ProactiveBehaviorCommandCarriesPersistedBoolean()
        {
            string command = AIFrenProtocol.SerializeCommand(new ClientCommand
            {
                command = "set_proactive_behavior",
                proactive_behavior = false,
            });

            StringAssert.Contains("\"command\":\"set_proactive_behavior\"", command);
            StringAssert.Contains("\"proactive_behavior\":false", command);
        }

        [Test]
        public void SnapshotCarriesAuthoritativeProactiveBehaviorSetting()
        {
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"transport_version\":5," +
                "\"companion\":{\"proactive_behavior\":true}}}"
            );

            Assert.IsTrue(snapshot.data.companion.proactive_behavior);
        }

        [Test]
        public void ProactiveIntervalCommandAndSnapshotCarryDiscreteMinimum()
        {
            string command = AIFrenProtocol.SerializeCommand(new ClientCommand
            {
                command = "set_proactive_interval",
                proactive_interval_seconds = 30,
            });
            StringAssert.Contains("\"proactive_interval_seconds\":30", command);

            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"companion\":{" +
                "\"proactive_behavior\":true,\"proactive_interval_seconds\":30}}}"
            );
            Assert.AreEqual(30, snapshot.data.companion.proactive_interval_seconds);
        }
    }
}
