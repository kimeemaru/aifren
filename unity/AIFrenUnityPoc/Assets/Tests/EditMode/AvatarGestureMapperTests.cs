using AIFren.UnityPoc.Avatar;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarGestureMapperTests
    {
        [TestCase("nods", AvatarGestureIntent.Nod)]
        [TestCase("nods slowly", AvatarGestureIntent.Nod)]
        [TestCase("shakes her head", AvatarGestureIntent.HeadShake)]
        [TestCase("waves hello", AvatarGestureIntent.Wave)]
        [TestCase("waves at you", AvatarGestureIntent.Wave)]
        [TestCase("raises a hand and waves", AvatarGestureIntent.Wave)]
        [TestCase("shrugs", AvatarGestureIntent.Shrug)]
        [TestCase("shrugs lightly", AvatarGestureIntent.Shrug)]
        [TestCase("shrugs her shoulders", AvatarGestureIntent.Shrug)]
        [TestCase("gives a small shrug", AvatarGestureIntent.Shrug)]
        [TestCase("tilts my head", AvatarGestureIntent.HeadTilt)]
        [TestCase("thinks for a moment", AvatarGestureIntent.Thinking)]
        [TestCase("thinking", AvatarGestureIntent.Thinking)]
        [TestCase("ponders", AvatarGestureIntent.Thinking)]
        [TestCase("ponders for a moment", AvatarGestureIntent.Thinking)]
        [TestCase("considers this", AvatarGestureIntent.Thinking)]
        [TestCase("looks thoughtful", AvatarGestureIntent.Thinking)]
        [TestCase("walks to the kitchen", AvatarGestureIntent.None)]
        public void MapsSupportedSemanticEmotes(string emote, AvatarGestureIntent expected)
        {
            Assert.AreEqual(expected, AvatarGestureMapper.Map(emote));
        }

        [Test]
        public void UsesOnlyTheFirstSupportedGestureInAResponse()
        {
            Assert.AreEqual(AvatarGestureIntent.Nod, AvatarGestureMapper.FirstSupported(
                new[] { "walks to the kitchen", "nods", "waves" }));
        }

        [Test]
        public void ReportsTheActualFirstMappedEmoteForDevelopmentLogging()
        {
            Assert.IsTrue(AvatarGestureMapper.TryFirstSupported(
                new[] { "walks away", "raises a hand and waves", "shrugs" },
                out AvatarGestureIntent intent, out string matched));
            Assert.AreEqual(AvatarGestureIntent.Wave, intent);
            Assert.AreEqual("raises a hand and waves", matched);
        }
    }
}
