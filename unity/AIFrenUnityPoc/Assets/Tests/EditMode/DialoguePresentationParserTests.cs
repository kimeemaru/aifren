using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.Collections.Generic;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class DialoguePresentationParserTests
    {
        [Test]
        public void EmoteAndDialogueUseRichVisibleTextAndCompactSpokenText()
        {
            const string raw = "*cross my arms*\n\nModel check? Yep.";
            StringAssert.Contains("<color=#74B8FF>*cross my arms*</color>", DialoguePresentationParser.FormatVisible(raw));
            Assert.AreEqual("Model check? Yep.", DialoguePresentationParser.SpokenText(raw));
        }

        [Test]
        public void InlineAndMultipleEmotesAreRemovedOnlyFromSpokenText()
        {
            const string raw = "Hello *waves* there. *smiles* Ready.";
            StringAssert.Contains("waves", DialoguePresentationParser.FormatVisible(raw));
            Assert.AreEqual("Hello there. Ready.", DialoguePresentationParser.SpokenText(raw));
        }

        [Test]
        public void InlineEmphasisIsSpokenAndItalicWhileActionsRemainEmotes()
        {
            const string raw = "*nods* I *really* mean *that*.";
            string visible = DialoguePresentationParser.FormatVisible(raw);
            string subtitle = DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(raw));

            Assert.AreEqual("I really mean that.", DialoguePresentationParser.SpokenText(raw));
            StringAssert.Contains("<color=#74B8FF>*nods*</color>", visible);
            StringAssert.Contains("<i>really</i>", visible);
            StringAssert.Contains("<i>that</i>", subtitle);
            StringAssert.DoesNotContain("nods", subtitle);
        }

        [Test]
        public void SingleWordActionVocabularyDoesNotBecomeEmphasis()
        {
            foreach (string action in new[] { "nods", "smiles", "waves", "shrugs", "sighs" })
            {
                IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse("*" + action + "*");
                Assert.AreEqual(DialogueSpanKind.Emote, spans[0].Kind, action);
            }
            foreach (string emphasis in new[] { "really", "that", "no", "absolutely" })
            {
                IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse("I *" + emphasis + "* mean it.");
                Assert.AreEqual(DialogueSpanKind.Emphasis, spans[1].Kind, emphasis);
            }
        }

        [Test]
        public void RequiredInlineExamplesRemainEmphasis()
        {
            foreach (string raw in new[]
            {
                "I *really* mean it.", "You chose *that*?", "That is *absolutely* ridiculous.",
                "I said *no*.", "It was *very close*."
            })
            {
                IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse(raw);
                bool hasEmphasis = false;
                foreach (DialogueSpan span in spans) if (span.Kind == DialogueSpanKind.Emphasis) hasEmphasis = true;
                Assert.IsTrue(hasEmphasis, raw);
                StringAssert.DoesNotContain("*", DialoguePresentationParser.SpokenText(raw));
            }
        }

        [Test]
        public void LongSingleMarkerRoleplayBeatsFallBackToEmotes()
        {
            const string longAction = "*let out a soft, teasing huff and lean back on my heels*";
            const string subjectAction = "*I cross my arms*";
            const string threeWordEmphasis = "*very close indeed*";
            const string fourWordFallback = "*I really mean this*";

            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(longAction)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(subjectAction)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emphasis, DialoguePresentationParser.Parse(threeWordEmphasis)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(fourWordFallback)[0].Kind);
            Assert.AreEqual(string.Empty, DialoguePresentationParser.SpokenText(longAction));
            Assert.AreEqual(string.Empty, DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(longAction)));
            Assert.AreEqual("very close indeed", DialoguePresentationParser.SpokenText(threeWordEmphasis));

            const string doubleEmphasis = "I **really mean this very strongly**.";
            Assert.AreEqual(DialogueSpanKind.Emphasis, DialoguePresentationParser.Parse(doubleEmphasis)[1].Kind);
            Assert.AreEqual("I really mean this very strongly.", DialoguePresentationParser.SpokenText(doubleEmphasis));
        }

        [Test]
        public void EmoteSpansRemainAvailableForSemanticMapping()
        {
            var emotes = DialoguePresentationParser.EmoteTexts("*walks to the kitchen* *nods slowly* I *really* agree.");
            CollectionAssert.AreEqual(new[] { "walks to the kitchen", "nods slowly" }, emotes);
            Assert.AreEqual("I really agree.", DialoguePresentationParser.SpokenText("*walks to the kitchen* *nods slowly* I *really* agree."));
        }

        [Test]
        public void ActionAndEmphasisRouteToTheirCorrectPresentationConsumers()
        {
            const string emphasis = "I am *not* doing that.";
            const string emote = "*smiles* Fine.";
            const string mixed = "*smiles* I am *not* doing that. *nods*";

            Assert.AreEqual(DialogueSpanKind.Emphasis, DialoguePresentationParser.Parse(emphasis)[1].Kind);
            StringAssert.Contains("<i>not</i>", DialoguePresentationParser.FormatVisible(emphasis));
            StringAssert.Contains("not", DialoguePresentationParser.SpokenText(emphasis));
            StringAssert.Contains("not", DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(emphasis)));

            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(emote)[0].Kind);
            StringAssert.Contains("<color=#74B8FF>*smiles*</color>", DialoguePresentationParser.FormatVisible(emote));
            StringAssert.DoesNotContain("smiles", DialoguePresentationParser.SpokenText(emote));
            StringAssert.DoesNotContain("smiles", DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(emote)));

            CollectionAssert.AreEqual(new[] { "smiles", "nods" }, DialoguePresentationParser.EmoteTexts(mixed));
            Assert.AreEqual("I am not doing that.", DialoguePresentationParser.SpokenText(mixed));
        }

        [Test]
        public void LeadingActionsAreEmotesAndDoubleMarkersAreAlwaysEmphasis()
        {
            foreach (string raw in new[] { "*smiles* Fine.", "*blinks* What?", "*pauses* I suppose so.", "*AIFren waves* Hello." })
            {
                IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse(raw);
                Assert.AreEqual(DialogueSpanKind.Emote, spans[0].Kind, raw);
                StringAssert.Contains("<color=#74B8FF>", DialoguePresentationParser.FormatVisible(raw));
                StringAssert.DoesNotContain(spans[0].Text, DialoguePresentationParser.SpokenText(raw));
                StringAssert.DoesNotContain(spans[0].Text, DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(raw)));
            }

            const string doubleEmphasis = "I **really** mean it.";
            IReadOnlyList<DialogueSpan> emphasisSpans = DialoguePresentationParser.Parse(doubleEmphasis);
            Assert.AreEqual(DialogueSpanKind.Emphasis, emphasisSpans[1].Kind);
            Assert.AreEqual("I really mean it.", DialoguePresentationParser.SpokenText(doubleEmphasis));
            StringAssert.Contains("<i>really</i>", DialoguePresentationParser.FormatVisible(doubleEmphasis));
            StringAssert.Contains("<i>really</i>", DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(doubleEmphasis)));

            IReadOnlyList<DialogueSpan> doubleSmile = DialoguePresentationParser.Parse("**smiles**");
            Assert.AreEqual(DialogueSpanKind.Emphasis, doubleSmile[0].Kind);
            Assert.AreEqual("smiles", DialoguePresentationParser.SpokenText("**smiles**"));
        }

        [Test]
        public void MixedSingleAndDoubleMarkupUsesOneTypedInterpretation()
        {
            const string raw = "*smiles* I **really** am *not* kidding. *nods*";
            IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse(raw);

            Assert.AreEqual(DialogueSpanKind.Emote, spans[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emphasis, spans[2].Kind);
            Assert.AreEqual(DialogueSpanKind.Emphasis, spans[4].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, spans[6].Kind);
            Assert.AreEqual("I really am not kidding.", DialoguePresentationParser.SpokenText(raw));
            CollectionAssert.AreEqual(new[] { "smiles", "nods" }, DialoguePresentationParser.EmoteTexts(raw));
            Assert.AreEqual(raw, "*smiles* I **really** am *not* kidding. *nods*", "The canonical source is never changed.");
        }

        [TestCase("blinks")]
        [TestCase("pauses")]
        [TestCase("looks away")]
        [TestCase("tilts her head")]
        [TestCase("crosses her arms")]
        public void StageDirectionVocabularyCannotFallThroughToEmphasis(string action)
        {
            IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse("*" + action + "* Fine.");
            Assert.AreEqual(DialogueSpanKind.Emote, spans[0].Kind, action);
        }

        [Test]
        public void EmoteOnlyAndMalformedAsterisksAreHandledConservatively()
        {
            Assert.AreEqual(string.Empty, DialoguePresentationParser.SpokenText("*looks away*"));
            Assert.AreEqual("Unclosed *asterisk", DialoguePresentationParser.SpokenText("Unclosed *asterisk"));
            Assert.AreEqual("Unclosed *asterisk", DialoguePresentationParser.FormatVisible("Unclosed *asterisk"));
        }

        [Test]
        public void WhitespaceIsNormalizedWithoutFlatteningParagraphs()
        {
            Assert.AreEqual("One two\n\nThree", DialoguePresentationParser.SpokenText("  One   two \n\n\n Three  "));
        }

        [Test]
        public void MalformedAsterisksAndTmpMarkupRemainLiteralAndSafe()
        {
            const string raw = "2 * 3; *.vrm; \\*escaped\\*; <size=200>safe</size> 😊";
            Assert.AreEqual(raw, DialoguePresentationParser.SpokenText(raw));
            StringAssert.Contains("&lt;size=200&gt;safe&lt;/size&gt;", DialoguePresentationParser.FormatVisible(raw));
        }

        [Test]
        public void PartialRevealStylesAnOpenEmoteButCompletedMalformedTextIsLiteral()
        {
            StringAssert.Contains("<color=#74B8FF>*cross my ar</color>", DialoguePresentationParser.FormatVisible("*cross my ar", true));
            Assert.AreEqual("*cross my ar", DialoguePresentationParser.FormatVisible("*cross my ar", false));
        }

        [Test]
        public void SubtitleTextIsPlainEscapedTextWithoutGeneratedRevealMarkup()
        {
            const string raw = "First <size=200>middle</size> final";
            string formatted = DialoguePresentationParser.FormatSubtitleText(raw);

            StringAssert.Contains("&lt;size=200&gt;middle&lt;/size&gt;", formatted);
            StringAssert.DoesNotContain("<size=200>", formatted);
            StringAssert.DoesNotContain("<alpha", formatted);
            StringAssert.DoesNotContain("</alpha>", formatted);
        }
    }
}
