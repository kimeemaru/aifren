using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SpeechProjectionParityTests
    {
        [Serializable] public sealed class Corpus { public int schema_version; public Case[] cases; }
        [Serializable] public sealed class Case { public string id, raw, canonical, spoken; }

        private static IEnumerable<TestCaseData> Cases()
        {
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "tests", "fixtures", "dialogue_speech_projection.json"));
            Corpus corpus = JsonUtility.FromJson<Corpus>(File.ReadAllText(path));
            if (corpus.schema_version != 1) throw new InvalidDataException("Unknown synthetic speech fixture schema.");
            return corpus.cases.Select(value => new TestCaseData(value).SetName("CanonicalSpeechParity_" + value.id));
        }

        [TestCaseSource(nameof(Cases))]
        public void CanonicalSpeechMatchesBackendOwnedFixture(Case value)
        {
            var document = DialoguePresentationParser.ParseDocument(value.canonical);
            Assert.That(document.SpokenText, Is.EqualTo(value.spoken));
            foreach (int maximum in new[] { 1, 4, 28 })
            {
                var pages = SubtitlePagination.SplitOwned(document, maximum,
                    page => SubtitleTimingPlan.WordCount(page.SpokenText) <= maximum);
                var spoken = pages.ConvertAll(page => page.SpokenText);
                var ranges = SubtitleTimingPlan.BuildPageWordRanges(spoken);
                Assert.That(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(value.spoken, spoken, ranges, out string error), Is.True, error);
                Assert.That(string.Join(" ", spoken), Is.EqualTo(value.spoken));
            }
        }

        [Test]
        public void LiteralAndEmphasisOwnershipSurvivesEverySmallPageBoundary()
        {
            const string raw = "The example \"*I smile.* with <b>literal</b> syntax\" is data. I **really** agree.";
            var document = DialoguePresentationParser.ParseDocument(raw);
            foreach (int maximum in new[] { 1, 2, 3, 7 })
            {
                var pages = SubtitlePagination.SplitOwned(document, maximum,
                    page => SubtitleTimingPlan.WordCount(page.SpokenText) <= maximum);
                string formatted = string.Join(" ", pages.ConvertAll(page => page.FormattedText));
                StringAssert.Contains("*I smile.*", formatted);
                StringAssert.Contains("<i>really</i>", formatted);
                StringAssert.Contains("&lt;b&gt;literal&lt;/b&gt;", formatted);
                StringAssert.DoesNotContain("<color", formatted);
            }
        }

        [TestCase("The example is `*I smile.*`.")]
        [TestCase("The example is \"*I smile.*\".")]
        [TestCase("The example is ‘*I smile.*’.")]
        [TestCase("The example is “*I smile.*”.")]
        public void QuotedSyntaxNeverBecomesFaceOrGesture(string canonical)
        {
            Assert.That(DialoguePresentationParser.EmoteTexts(canonical), Is.Empty);
            Assert.That(DialoguePresentationParser.SpokenText(canonical), Is.EqualTo(canonical));
        }

        [Test]
        public void ProductionSubtitlePlanningKeepsLiteralOwnershipAcrossPageBoundary()
        {
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var root = new GameObject("synthetic spoken-page owner", typeof(RectTransform)); root.SetActive(false);
            bool priorIgnore = LogAssert.ignoreFailingMessages;
            try
            {
                var controller = root.AddComponent<AIFrenPocController>(); controller.enabled = false;
                string leading = string.Join(" ", Enumerable.Repeat("word", 31));
                string trailing = string.Join(" ", Enumerable.Repeat("word", 31));
                string raw = "The quoted syntax is \"" + leading + " *I smile.* " + trailing + "\".";
                LogAssert.ignoreFailingMessages = true; // The before-fix error is retained; assertion is the gate.
                object plan = typeof(AIFrenPocController).GetMethod("BuildSubtitlePlan", flags).Invoke(controller, new object[] { raw });
                Assert.That(plan, Is.Not.Null, "Page fragments cannot reinterpret a complete quoted literal as a fresh action.");
                Assert.That(plan.GetType().GetField("Spoken", flags).GetValue(plan), Is.EqualTo(raw));
            }
            finally { LogAssert.ignoreFailingMessages = priorIgnore; Object.DestroyImmediate(root); }
        }
    }
}
