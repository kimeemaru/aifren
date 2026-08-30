using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class TruthScopeIndicatorStateTests
    {
        [TestCase("real_world", "Gensokyo")]
        [TestCase(null, "Gensokyo")]
        [TestCase("", "")]
        public void RealWorldOrMissingAuthorityHasNoIndicator(string kind, string label)
        {
            Assert.That(TruthScopeIndicatorState.DisplayText(kind, label), Is.Empty);
        }

        [Test]
        public void ScenarioUsesSafeCompactBackendLabel()
        {
            Assert.That(TruthScopeIndicatorState.DisplayText("scenario", "  Gensokyo  "), Is.EqualTo("RP · Gensokyo"));
            Assert.That(TruthScopeIndicatorState.DisplayText("scenario", ""), Is.EqualTo("RP"));
            Assert.That(TruthScopeIndicatorState.DisplayText("scenario", "bad\nlabel"), Is.EqualTo("RP"));
        }

        [Test]
        public void ScenarioLabelIsBounded()
        {
            string rendered = TruthScopeIndicatorState.DisplayText("scenario", new string('x', 80));
            Assert.That(rendered, Does.StartWith("RP · "));
            Assert.That(rendered.Length, Is.LessThanOrEqualTo(37));
            Assert.That(rendered, Does.EndWith("…"));
        }

        [Test]
        public void IndicatorOccupiesBottomLeftSafeMargin()
        {
            var portrait = TruthScopeIndicatorState.SafeAreaAnchor(
                new UnityEngine.Rect(0f, 40f, 1080f, 1880f), new UnityEngine.Vector2(1080f, 1920f));
            var landscape = TruthScopeIndicatorState.SafeAreaAnchor(
                new UnityEngine.Rect(24f, 0f, 1896f, 1080f), new UnityEngine.Vector2(1920f, 1080f));
            Assert.That(portrait.x, Is.EqualTo(0f));
            Assert.That(portrait.y, Is.EqualTo(40f / 1920f).Within(.0001f));
            Assert.That(landscape.x, Is.EqualTo(24f / 1920f).Within(.0001f));
            Assert.That(landscape.y, Is.EqualTo(0f));
            Assert.That(TruthScopeIndicatorState.SafeMargin().x, Is.GreaterThan(0f));
            Assert.That(TruthScopeIndicatorState.SafeMargin().y, Is.InRange(0f, 3f));
            Assert.That(TruthScopeIndicatorState.Size(true).y, Is.LessThanOrEqualTo(22f));
            Assert.That(TruthScopeIndicatorState.Size(true).y, Is.LessThan(40f));
        }

        [Test]
        public void RootCornerLayoutIsIndependentOfMainUiAndOverlayState()
        {
            var safe = new UnityEngine.Rect(0f, 32f, 1080f, 1888f);
            var screen = new UnityEngine.Vector2(1080f, 1920f);
            var expected = TruthScopeIndicatorState.SafeAreaAnchor(safe, screen);
            foreach (bool mainUiVisible in new[] { true, false })
            foreach (bool inputPresent in new[] { true, false })
            foreach (bool overlayEnabled in new[] { true, false })
            {
                Assert.That(TruthScopeIndicatorState.SafeAreaAnchor(safe, screen), Is.EqualTo(expected),
                    $"ui={mainUiVisible}, input={inputPresent}, overlay={overlayEnabled}");
            }
        }

        [Test]
        public void IndicatorUsesSubtleBlackOutlineWithoutChangingLayoutPolicy()
        {
            UnityEngine.Color32 color = TruthScopeIndicatorState.OutlineColor();
            Assert.That(color.r, Is.EqualTo(0));
            Assert.That(color.g, Is.EqualTo(0));
            Assert.That(color.b, Is.EqualTo(0));
            Assert.That(color.a, Is.InRange(200, 255));
            Assert.That(TruthScopeIndicatorState.OutlineWidth, Is.InRange(0.22f, 0.26f));
            Assert.That(TruthScopeIndicatorState.SafeMargin(), Is.EqualTo(new UnityEngine.Vector2(12f, 2f)));
        }
    }
}
