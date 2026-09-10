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
            const string standaloneAction = "*very close indeed*";
            const string fourWordFallback = "*I really mean this*";

            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(longAction)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(subjectAction)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(standaloneAction)[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emote, DialoguePresentationParser.Parse(fourWordFallback)[0].Kind);
            Assert.AreEqual(string.Empty, DialoguePresentationParser.SpokenText(longAction));
            Assert.AreEqual(string.Empty, DialoguePresentationParser.FormatSubtitleText(DialoguePresentationParser.SubtitleSourceText(longAction)));
            Assert.AreEqual(string.Empty, DialoguePresentationParser.SpokenText(standaloneAction));

            const string doubleEmphasis = "I **really mean this very strongly**.";
            Assert.AreEqual(DialogueSpanKind.Emphasis, DialoguePresentationParser.Parse(doubleEmphasis)[1].Kind);
            Assert.AreEqual("I really mean this very strongly.", DialoguePresentationParser.SpokenText(doubleEmphasis));
        }

        [Test]
        public void ContextOwnsEmphasisWhileNestedFormattingCannotEscapeOuterAction()
        {
            var cases = new Dictionary<string, string>
            {
                { "I *really* don't like that.", "I really don't like that." },
                { "*smile*", string.Empty },
                { "*I look *really* confused.*", string.Empty },
                { "Hello. *I smile.* How are you?", "Hello. How are you?" },
                { "I **really** mean this.", "I really mean this." },
                { "I *very strongly mean this* today.", "I very strongly mean this today." },
                { "*I **carefully** put the book down.*", string.Empty },
            };
            foreach (var item in cases)
            {
                DialogueDocument document = DialoguePresentationParser.ParseDocument(item.Key);
                Assert.AreEqual(item.Value, document.SpokenText, item.Key);
                Assert.AreEqual(
                    DialoguePresentationParser.SpokenText(item.Key), document.SpokenText,
                    "Whole response and shared semantic document must agree.");
            }
        }

        [Test]
        public void CanonicalizedMalformedActionUsesExistingEmoteContract()
        {
            const string canonical = "*shakes her head slowly* I *really* remember.";
            IReadOnlyList<DialogueSpan> spans = DialoguePresentationParser.Parse(canonical);

            Assert.AreEqual(DialogueSpanKind.Emote, spans[0].Kind);
            Assert.AreEqual(DialogueSpanKind.Emphasis, spans[2].Kind);
            Assert.AreEqual("I really remember.", DialoguePresentationParser.SpokenText(canonical));
            StringAssert.Contains("<color=#74B8FF>*shakes her head slowly*</color>",
                DialoguePresentationParser.FormatVisible(canonical));
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
            foreach (string raw in new[] { "*smiles* Fine.", "*blinks* What?", "*pauses* I suppose so.", "*Lyra waves* Hello." })
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

        [Test]
        public void LongStreamedPartialActionRemainsSafeAndReadablyFormatted()
        {
            const string streamed = "*looks toward the window*\n\nA long answer with <size=200>literal model text</size> and an unfinished *waves";
            string formatted = DialoguePresentationParser.FormatVisible(streamed, true);

            StringAssert.Contains("<color=#74B8FF>*looks toward the window*</color>\nA long answer", formatted);
            StringAssert.Contains("&lt;size=200&gt;literal model text&lt;/size&gt;", formatted);
            StringAssert.Contains("<color=#74B8FF>*waves</color>", formatted);
        }

        [Test]
        public void CompletedStreamAndCanonicalFormattingUseTheSameActionDecoration()
        {
            const string raw = "*smiles* Hello.";
            string streamed = DialoguePresentationParser.FormatVisible(raw, true);
            string canonical = DialoguePresentationParser.FormatVisible(raw, false);

            Assert.AreEqual(canonical, streamed);
            StringAssert.Contains("<color=#74B8FF>*smiles*</color>", canonical);
            StringAssert.DoesNotContain("(*", canonical);
            StringAssert.DoesNotContain("*)", canonical);
        }

        [Test]
        public void NestedFormattingInsideOuterActionsNeverLeaksIntoSpokenProjection()
        {
            var cases = new[]
            {
                ("*I slowly *really* lean closer.*", ""),
                ("*I **carefully** put the book down.*", ""),
                ("*She pauses, looking *very* confused.*", ""),
                ("*I look at you.* Then I say hello.", "Then I say hello."),
                ("Hello. *I look *very* confused.* Are you okay?", "Hello. Are you okay?"),
                ("*Entire action containing multiple *emphasized* words and another *emphasized* phrase.*", ""),
                ("I *really* mean it.", "I really mean it."),
            };
            foreach (var item in cases)
            {
                Assert.AreEqual(item.Item2, DialoguePresentationParser.SpokenText(item.Item1), item.Item1);
                string subtitle = DialoguePresentationParser.FormatSubtitleText(
                    DialoguePresentationParser.SubtitleSourceText(item.Item1));
                if (string.IsNullOrEmpty(item.Item2)) Assert.That(subtitle, Is.Empty, item.Item1);
                StringAssert.DoesNotContain("lean closer", subtitle, item.Item1);
                StringAssert.DoesNotContain("put the book down", subtitle, item.Item1);
                StringAssert.DoesNotContain("looking very confused", subtitle, item.Item1);
            }
        }
    }
}
