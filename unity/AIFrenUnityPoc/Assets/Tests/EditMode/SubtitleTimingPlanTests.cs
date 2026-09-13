using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.Collections.Generic;
using System.Linq;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SubtitleTimingPlanTests
    {
        [Test]
        public void PlanIsImmutableAndMonotonicForKnownDuration()
        {
            List<float> first = SubtitleTimingPlan.Build("One two, three Four", 4f, 7f);
            List<float> second = SubtitleTimingPlan.Build("One two, three Four", 4f, 7f);

            Assert.AreEqual(4, first.Count);
            CollectionAssert.AreEqual(first, second);
            for (int index = 1; index < first.Count; index++) Assert.Greater(first[index], first[index - 1]);
            // The comma reserves more time than ordinary words.
            Assert.Greater(first[2] - first[1], first[1] - first[0]);
        }

        [Test]
        public void FallbackPlanUsesAllWordsWithoutRemainingTimeRecalculation()
        {
            List<float> plan = SubtitleTimingPlan.Build("Short final page words", 0f, 5f);
            Assert.AreEqual(4, plan.Count);
            Assert.AreEqual(0f, plan[0]);
            Assert.Less(plan[plan.Count - 1], 2f);
        }

        [Test]
        public void PageRangesCoverEveryWordExactlyOnce()
        {
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(
                new[] { "One two", "three four five", "six" });

            Assert.IsTrue(SubtitleTimingPlan.CoversAllWordsExactlyOnce(ranges, 6));
            Assert.AreEqual(0, ranges[0].FirstWordIndex);
            Assert.AreEqual(1, ranges[0].LastWordIndex);
            Assert.AreEqual(2, ranges[1].FirstWordIndex);
            Assert.AreEqual(5, ranges[2].LastWordIndex);
        }

        [Test]
        public void FinalExclusiveEndEqualToTotalWordsIsValidAfterSpokenProjection()
        {
            const int totalWords = 3;
            List<string> pages = new List<string> { "one *waves* two", "three" };
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(
                pages, DialoguePresentationParser.SpokenText);

            Assert.AreEqual(totalWords, ranges[ranges.Count - 1].LastWordIndex + 1);
            Assert.IsTrue(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                "one two three", pages, ranges, DialoguePresentationParser.SpokenText, out string error), error);
        }

        [Test]
        public void PageOwnershipStillRejectsOverrunsGapsOverlapsAndReversedRanges()
        {
            string[] pages = { "one two", "three" };

            Assert.IsFalse(SubtitleTimingPlan.TryValidatePageDefinitions(pages,
                new[] { new SubtitlePageWordRange(0, 1), new SubtitlePageWordRange(2, 3) }, 3, out _));
            Assert.IsFalse(SubtitleTimingPlan.TryValidatePageDefinitions(pages,
                new[] { new SubtitlePageWordRange(0, 1), new SubtitlePageWordRange(3, 3) }, 3, out _));
            Assert.IsFalse(SubtitleTimingPlan.TryValidatePageDefinitions(pages,
                new[] { new SubtitlePageWordRange(0, 1), new SubtitlePageWordRange(1, 1) }, 3, out _));
            Assert.IsFalse(SubtitleTimingPlan.TryValidatePageDefinitions(new[] { "one" },
                new[] { new SubtitlePageWordRange(0, -1) }, 1, out _));
            Assert.IsFalse(SubtitleTimingPlan.TryValidatePageDefinitions(new[] { "one" },
                new[] { new SubtitlePageWordRange(-1, 0) }, 1, out _));
        }

        [Test]
        public void PageCannotAdvanceBeforeItsFinalWordTimestamp()
        {
            SubtitlePageWordRange page = new SubtitlePageWordRange(2, 4);
            List<float> starts = new List<float> { 0f, .3f, .7f, 1.1f, 1.6f };

            Assert.IsFalse(SubtitleTimingPlan.IsPageFinalWordDue(page, starts, 1.59f));
            Assert.IsTrue(SubtitleTimingPlan.IsPageFinalWordDue(page, starts, 1.6f));
        }

        [Test]
        public void FallbackScheduleUsesTheSamePageFinalWordOwnershipRule()
        {
            string[] pages = { "One two three", "four five" };
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(pages);
            List<float> starts = SubtitleTimingPlan.Build(string.Join(" ", pages), 3f, 6f);

            foreach (SubtitlePageWordRange range in ranges)
            {
                float finalWordTime = starts[range.LastWordIndex];
                Assert.IsFalse(SubtitleTimingPlan.IsPageFinalWordDue(range, starts, finalWordTime - .001f));
                Assert.IsTrue(SubtitleTimingPlan.IsPageFinalWordDue(range, starts, finalWordTime));
            }
        }

        [Test]
        public void FixedLeadPreservesScheduleOrderingAndIntervals()
        {
            List<float> starts = new List<float> { .05f, .35f, .9f };

            SubtitleTimingPlan.ApplyLead(starts, .1f);

            Assert.That(starts[0], Is.EqualTo(0f).Within(.0001f));
            Assert.That(starts[1], Is.EqualTo(.25f).Within(.0001f));
            Assert.That(starts[2], Is.EqualTo(.8f).Within(.0001f));
            Assert.Greater(starts[2] - starts[1], starts[1] - starts[0]);
        }

        [Test]
        public void PaginationAdvancesByActualShortenedPageCountWithoutSkippingWords()
        {
            string spoken = "w0 w1 w2 w3. w4 w5 w6 w7 w8 w9 w10";
            List<string> pages = SubtitlePagination.Split(spoken, 6);
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(pages);

            CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(spoken),
                SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages)));
            Assert.IsTrue(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(spoken, pages, ranges, out string error), error);
            Assert.AreEqual("w4", SubtitleTimingPlan.TokenizeWords(pages[1])[0]);
        }

        [Test]
        public void PaginationDoesNotCreateAnOverlappingPageAfterAbsorbingAnOrphan()
        {
            string spoken = "w0 w1 w2 w3 w4 w5 w6 w7 w8 w9";
            List<string> pages = SubtitlePagination.Split(spoken, 6);

            Assert.AreEqual(1, pages.Count);
            CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(spoken),
                SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages)));
        }

        [Test]
        public void CanonicalPageValidationRejectsCorrectLookingRangesWithWrongPageWords()
        {
            string spoken = "w0 w1 w2 w3 w4 w5";
            List<string> pages = new List<string> { "w0 w1 w2", "w4 w5 w3" };
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(pages);

            Assert.IsFalse(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(spoken, pages, ranges, out string error));
            StringAssert.Contains("does not match canonical", error);
        }

        [Test]
        public void EmphasisSubtitleSourcePreservesSpokenWordOwnership()
        {
            const string raw = "*nods* I **really** mean *that*.";
            string spoken = DialoguePresentationParser.SpokenText(raw);
            List<string> pages = SubtitlePagination.Split(DialoguePresentationParser.SubtitleSourceText(raw), 3);
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(pages);

            Assert.IsTrue(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                spoken, pages, ranges, DialoguePresentationParser.SpokenText, out string error), error);
            CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(spoken),
                SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages.Select(DialoguePresentationParser.SpokenText))));
        }

        [Test]
        public void MultiwordEmphasisCanCrossPagesWithoutMalformedMarkers()
        {
            const string raw = "Before **these deliberately emphasized words span pages safely** after.";
            string spoken = DialoguePresentationParser.SpokenText(raw);
            List<string> pages = SubtitlePagination.Split(DialoguePresentationParser.SubtitleSourceText(raw), 3);

            Assert.Greater(pages.Count, 1);
            foreach (string page in pages)
            {
                int markers = page.Split(new[] { "**" }, System.StringSplitOptions.None).Length - 1;
                Assert.AreEqual(0, markers % 2, page);
            }
            CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(spoken),
                SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages.Select(DialoguePresentationParser.SpokenText))));
        }

        [TestCase("It is *working*—running smoothly.", "It is working—running smoothly.")]
        [TestCase("The **pre**fix is intact.", "The prefix is intact.")]
        [TestCase("The end**ing** is intact.", "The ending is intact.")]
        [TestCase("A **well**-**known** name.", "A well-known name.")]
        [TestCase("Use (**that**) label.", "Use (that) label.")]
        [TestCase("A **word**. Another **word**!", "A word. Another word!")]
        [TestCase("One **two three four** five.", "One two three four five.")]
        [TestCase("*nods* Keep **this**—not that.", "Keep this—not that.")]
        [TestCase("Keep **this** — separate.", "Keep this — separate.")]
        [TestCase("**That**'s fine.", "That's fine.")]
        public void EmphasisBoundariesNeverCreateOrMergeSpokenWords(string raw, string expectedSpoken)
        {
            Assert.AreEqual(expectedSpoken, DialoguePresentationParser.SpokenText(raw));
            foreach (int maximumWords in new[] { 1, 2, 3, 28 })
            {
                List<string> pages = SubtitlePagination.Split(
                    DialoguePresentationParser.SubtitleSourceText(raw), maximumWords);
                List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(
                    pages, DialoguePresentationParser.SpokenText);
                Assert.IsTrue(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                    expectedSpoken, pages, ranges, DialoguePresentationParser.SpokenText, out string error), error);
                CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(expectedSpoken),
                    SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages.Select(DialoguePresentationParser.SpokenText))));
            }
        }

        [TestCase("It is **working**—running smoothly.", "<i>working</i>—running")]
        [TestCase("The **pre**fix is intact.", "<i>pre</i>fix")]
        [TestCase("The end**ing** is intact.", "end<i>ing</i>")]
        [TestCase("A **well**-**known** name.", "<i>well</i>-<i>known</i>")]
        [TestCase("Use (**that**) label.", "(<i>that</i>)")]
        public void AttachedFragmentsKeepOnlyTheirOriginalLettersEmphasized(string raw, string expectedFragment)
        {
            List<string> pages = SubtitlePagination.Split(DialoguePresentationParser.SubtitleSourceText(raw), 2);
            StringAssert.Contains(expectedFragment,
                string.Join(" ", pages.Select(DialoguePresentationParser.FormatSubtitleText)));
        }

        [Test]
        public void CanonicalOwnershipStillRejectsAnInventedBoundaryOrAlteredAttachedWord()
        {
            const string spoken = "It is working—running smoothly.";
            foreach (string corrupt in new[] { "It is **working** —running smoothly.", "It is **working**—walking smoothly." })
            {
                List<string> pages = new List<string> { corrupt };
                var ranges = SubtitleTimingPlan.BuildPageWordRanges(pages, DialoguePresentationParser.SpokenText);
                Assert.IsFalse(SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                    spoken, pages, ranges, DialoguePresentationParser.SpokenText, out string error));
                Assert.IsNotEmpty(error);
            }
        }

        [Test]
        public void LayoutFitPredicateReducesPagesAndNeverReabsorbsAnOverflowingOrphan()
        {
            const string spoken = "one two three four five six seven eight nine ten eleven";
            List<string> pages = SubtitlePagination.Split(spoken, 8,
                page => SubtitleTimingPlan.TokenizeWords(page).Count <= 4);

            Assert.Greater(pages.Count, 1);
            Assert.IsTrue(pages.All(page => SubtitleTimingPlan.TokenizeWords(page).Count <= 4));
            CollectionAssert.AreEqual(SubtitleTimingPlan.TokenizeWords(spoken),
                SubtitleTimingPlan.TokenizeWords(string.Join(" ", pages)));
        }
    }
}
