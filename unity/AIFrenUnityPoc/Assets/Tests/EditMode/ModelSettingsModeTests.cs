using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ModelSettingsModeTests
    {
        [TestCase("online", "online")]
        [TestCase("ONLINE", "online")]
        [TestCase("local", "local")]
        [TestCase("LOCAL", "local")]
        [TestCase("invalid", "online")]
        [TestCase("", "online")]
        public void ModeControlNormalizesToTheOnlyTwoSupportedValues(string supplied, string expected)
        {
            Assert.AreEqual(expected, AIFrenPocController.NormalizeModelMode(supplied));
        }

        [Test]
        public void SnapshotRetainsModeAndRuntimeAvailabilitySeparately()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"transport_version\":3,\"models\":{\"current\":{\"mode\":\"local\",\"provider\":\"openai_compatible\",\"model\":\"test-local\",\"configured\":true,\"availability\":\"unavailable\"}}}}"
            );

            Assert.AreEqual("local", message.data.models.current.mode);
            Assert.AreEqual("unavailable", message.data.models.current.availability);
            Assert.AreEqual("test-local", message.data.models.current.model);
        }

        [Test]
        public void AutoStartCommandAndAuthoritativeSnapshotKeepTheTrueValue()
        {
            string command = AIFrenProtocol.SerializeCommand(new ClientCommand
            {
                command = "set_local_auto_start",
                local_auto_start = true,
            });
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"transport_version\":3,\"models\":{\"current\":{\"mode\":\"local\",\"local_auto_start\":true}}}}"
            );

            StringAssert.Contains("\"command\":\"set_local_auto_start\"", command);
            StringAssert.Contains("\"local_auto_start\":true", command);
            Assert.IsTrue(snapshot.data.models.current.local_auto_start);
        }
    }
}
