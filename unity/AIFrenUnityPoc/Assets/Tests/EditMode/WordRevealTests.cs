using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class WordRevealTests
    {
        [Test]
        public void RevealAdvancesByWholeWordsAndPreservesWhitespace()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 2f };
            reveal.Begin("Hello there, friend.", false);

            Assert.IsTrue(reveal.Advance(0.5f));
            Assert.AreEqual("Hello ", reveal.VisibleText);
            Assert.IsTrue(reveal.Advance(0.5f));
            Assert.AreEqual("Hello there, ", reveal.VisibleText);
        }

        [Test]
        public void RevealAllShowsTheOriginalCompleteResponse()
        {
            WordReveal reveal = new WordReveal();
            reveal.Begin("One response, kept whole.", false);

            reveal.RevealAll();

            Assert.IsTrue(reveal.IsComplete);
            Assert.AreEqual("One response, kept whole.", reveal.VisibleText);
        }

        [Test]
        public void AudioDurationDerivesAnApproximateWordRevealRate()
        {
            Assert.AreEqual(2f, WordReveal.WordsPerSecondForDuration(10, 5f, 7f));
            Assert.AreEqual(7f, WordReveal.WordsPerSecondForDuration(10, 0f, 7f));
        }

        [Test]
        public void PresentationConfigurationRejectsNonPositiveRevealSpeed()
        {
            CompanionPresentationConfiguration configuration = new CompanionPresentationConfiguration
            {
                defaultRevealWordsPerSecond = 0f
            };

            Assert.IsFalse(configuration.IsValid(out string error));
            StringAssert.Contains("greater than zero", error);
        }
    }
}
