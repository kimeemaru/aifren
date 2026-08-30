using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterProtocolTests
    {
        [Test]
        public void CharacterCommandsSerializeOnlyFrontendNeutralFields()
        {
            ClientCommand command = new ClientCommand
            {
                command = "create_character",
                display_name = "Second",
                personality = "A separate companion.",
            };

            string json = AIFrenProtocol.SerializeCommand(command);

            StringAssert.Contains("create_character", json);
            StringAssert.Contains("display_name", json);
            StringAssert.Contains("personality", json);
            StringAssert.DoesNotContain("memories.json", json);
        }

        [Test]
        public void SnapshotDeserializesCharacterListAndActiveIdentity()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"transport_version\":3,\"character\":{\"character_id\":\"legacy\",\"name\":\"AIFren\"},\"characters\":[{\"character_id\":\"legacy\",\"display_name\":\"AIFren\",\"is_active\":true},{\"character_id\":\"second\",\"display_name\":\"Second\",\"is_active\":false}]}}"
            );

            Assert.AreEqual("legacy", message.data.character.character_id);
            Assert.AreEqual(2, message.data.characters.Length);
            Assert.IsTrue(message.data.characters[0].is_active);
            Assert.AreEqual("Second", message.data.characters[1].display_name);
        }

        [Test]
        public void CharacterScopedPresentationResetsOnlyForAnActualIdentityChange()
        {
            Assert.IsFalse(AIFrenPocController.HasCharacterChanged(null, "character-a"));
            Assert.IsFalse(AIFrenPocController.HasCharacterChanged("character-a", "character-a"));
            Assert.IsTrue(AIFrenPocController.HasCharacterChanged("character-a", "character-b"));
        }

        [Test]
        public void AvatarApplyRequiresBothLatestRequestAndActiveCharacterIdentity()
        {
            Assert.IsTrue(AIFrenPocController.IsCharacterAvatarApplyAuthoritative(
                7, 7, "character-b", "character-b"));
            Assert.IsFalse(AIFrenPocController.IsCharacterAvatarApplyAuthoritative(
                6, 7, "character-b", "character-b"));
            Assert.IsFalse(AIFrenPocController.IsCharacterAvatarApplyAuthoritative(
                7, 7, "character-a", "character-b"));
        }
    }
}
