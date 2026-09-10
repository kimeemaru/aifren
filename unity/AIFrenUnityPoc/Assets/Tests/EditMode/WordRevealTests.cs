using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System.Reflection;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;

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
        public void StreamedTextExtendsWithoutReplayingAlreadyRevealedWords()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 2f };
            reveal.Begin(string.Empty, false);
            reveal.Append("One tw");
            reveal.Advance(.5f);

            reveal.Append("o three");
            Assert.AreEqual("One ", reveal.VisibleText);
            reveal.Advance(.5f);

            Assert.AreEqual("One two ", reveal.VisibleText);
            Assert.AreEqual(2, reveal.RevealedTokenCount);
        }

        [Test]
        public void CanonicalFinalTextReplacesStreamWithoutRestartingReveal()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 2f };
            reveal.Begin("One streamed", false);
            reveal.Advance(.5f);

            reveal.UpdateText("One streamed response.");

            Assert.AreEqual("One ", reveal.VisibleText);
            Assert.AreEqual(1, reveal.RevealedTokenCount);
            reveal.Advance(1f);
            Assert.AreEqual("One streamed response.", reveal.VisibleText);
        }

        [Test]
        public void InstantModeShowsAvailableStreamAndEachFutureDeltaImmediately()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 1f };
            reveal.Begin("Available text", false);
            reveal.Advance(.1f);

            reveal.RevealAll();
            Assert.AreEqual("Available text", reveal.VisibleText);

            reveal.UpdateText("Available text plus new delta", true);
            Assert.AreEqual("Available text plus new delta", reveal.VisibleText);
            Assert.IsTrue(reveal.IsComplete);
        }

        [Test]
        public void LeavingInstantModePacesOnlyNewlyArrivingText()
        {
            WordReveal reveal = new WordReveal { WordsPerSecond = 2f };
            reveal.Begin("Already visible", true);

            reveal.UpdateText("Already visible newly arriving", false);
            Assert.AreEqual("Already visible ", reveal.VisibleText);
            reveal.Advance(.5f);

            Assert.AreEqual("Already visible newly ", reveal.VisibleText);
        }

        [Test]
        public void InstantToggleFlushesPendingControllerStreamEvenWhenOldBufferIsComplete()
        {
            Assert.IsTrue(AIFrenPocController.InstantTextRequiresPresentationRefresh(true, true, true));
            Assert.IsTrue(AIFrenPocController.InstantTextRequiresPresentationRefresh(true, false, false));
            Assert.IsFalse(AIFrenPocController.InstantTextRequiresPresentationRefresh(false, true, false));
        }

        [Test]
        public void InstantSettingDrivesTheControllerStreamPathOnAndOff()
        {
            const string preference = "AIFren.InstantDialogueText";
            bool hadPreference = PlayerPrefs.HasKey(preference);
            int oldPreference = PlayerPrefs.GetInt(preference);
            GameObject root = new GameObject("controller", typeof(AIFrenPocController));
            AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
            TextMeshProUGUI label = new GameObject("dialogue", typeof(RectTransform),
                typeof(CanvasRenderer), typeof(TextMeshProUGUI)).GetComponent<TextMeshProUGUI>();
            label.transform.SetParent(root.transform, false);
            Field("dialogueTextLabel").SetValue(controller, label);
            WordReveal reveal = (WordReveal)Field("wordReveal").GetValue(controller);
            try
            {
                reveal.Begin("Already received", false);
                Method("SetInstantText").Invoke(controller, new object[] { true });
                Assert.AreEqual("Already received", reveal.VisibleText);
                Assert.AreEqual("Already received", label.text,
                    "The actual dialogue TMP must update when Instant Text is enabled mid-reveal.");
                Assert.AreEqual(1, PlayerPrefs.GetInt(preference));

                Field("assistantStreamVisible").SetValue(controller, true);
                Field("pendingAssistantContent").SetValue(controller, "Already received future delta");
                Method("RefreshStreamedAssistantDialogue").Invoke(controller, new object[] { false });
                Assert.AreEqual("Already received future delta", reveal.VisibleText);
                Assert.AreEqual("Already received future delta", label.text,
                    "Future provider deltas must reach the live dialogue label immediately.");

                Method("SetInstantText").Invoke(controller, new object[] { false });
                Field("assistantStreamVisible").SetValue(controller, true);
                Field("pendingAssistantContent").SetValue(controller, "Already received future delta paced words");
                Method("RefreshStreamedAssistantDialogue").Invoke(controller, new object[] { false });
                Assert.AreEqual("Already received future delta ", reveal.VisibleText);
                Assert.AreEqual("Already received future delta", label.text,
                    "Switching Instant Text off must retain visible text and pace only new words.");
                Assert.AreEqual(0, PlayerPrefs.GetInt(preference));
            }
            finally
            {
                if (hadPreference) PlayerPrefs.SetInt(preference, oldPreference);
                else PlayerPrefs.DeleteKey(preference);
                PlayerPrefs.Save();
                Object.DestroyImmediate(root);
            }
        }

        private static FieldInfo Field(string name) => typeof(AIFrenPocController).GetField(
            name, BindingFlags.Instance | BindingFlags.NonPublic);

        private static MethodInfo Method(string name) => typeof(AIFrenPocController).GetMethod(
            name, BindingFlags.Instance | BindingFlags.NonPublic);

        [Test]
        public void AudioDurationDerivesAnApproximateWordRevealRate()
        {
            Assert.AreEqual(2f, WordReveal.WordsPerSecondForDuration(10, 5f, 7f));
            Assert.AreEqual(7f, WordReveal.WordsPerSecondForDuration(100, 1f, 7f));
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
