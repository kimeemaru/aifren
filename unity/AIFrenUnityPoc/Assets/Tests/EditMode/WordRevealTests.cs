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
        public void SeededWordFadesBeforeBecomingOpaque()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 5f };
            reveal.Begin("Hello there", false);

            Assert.IsTrue(reveal.RevealNext());
            Assert.AreEqual("Hello ", reveal.VisibleText);
            Assert.Less(reveal.LatestTokenAlpha, 0.01f);

            reveal.Advance(.12f);
            Assert.GreaterOrEqual(reveal.LatestTokenAlpha, .99f);
        }

        [Test]
        public void TimestampDrivenFadeDoesNotRevealAnExtraWord()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 100f };
            reveal.Begin("One two", false);
            reveal.RevealTo(1);

            reveal.AdvanceLatestTokenFade(1f);

            Assert.AreEqual(1, reveal.RevealedTokenCount);
            Assert.GreaterOrEqual(reveal.LatestTokenAlpha, .99f);
        }

        [Test]
        public void TimestampDueFinalTokenCompletesItsVisualRamp()
        {
            WordReveal reveal = new WordReveal();
            reveal.Begin("Last word", false);

            reveal.RevealTo(2);

            Assert.IsTrue(reveal.IsComplete);
            Assert.IsTrue(reveal.LatestTokenIsFading);
            reveal.AdvanceLatestTokenFade(.12f);
            Assert.IsFalse(reveal.LatestTokenIsFading);
        }

        [Test]
        public void TransitionDueOpeningPhraseRemainsPendingUntilPresentedInOrder()
        {
            WordReveal reveal = new WordReveal();
            reveal.Begin("Once upon a time there was", false);

            // Four timestamps may elapse while this page is non-renderable.
            // They remain unpresented until the page commits.
            Assert.AreEqual(0, reveal.RevealedTokenCount);
            string[] expected = { "Once ", "Once upon ", "Once upon a ", "Once upon a time " };
            for (int index = 0; index < expected.Length; index++)
            {
                reveal.RevealNext();
                reveal.AdvanceLatestTokenFade(.12f);
                Assert.AreEqual(expected[index], reveal.VisibleText);
                Assert.IsFalse(reveal.LatestTokenIsFading);
            }
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
