using System.Collections.Generic;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PresentationDisplaySettingsTests
    {
        [Test]
        public void NormalizeUsesSafePersistedSettingBounds()
        {
            PresentationDisplaySettings result = PresentationDisplaySettingsPolicy.Normalize(new PresentationDisplaySettings
            {
                displayIndex = -1,
                width = 1,
                height = 1,
                uiScale = 9f,
                frameLimit = 37,
                antiAliasing = 3
            });

            Assert.AreEqual(0, result.displayIndex);
            Assert.AreEqual(640, result.width);
            Assert.AreEqual(480, result.height);
            Assert.AreEqual(1.50f, result.uiScale);
            Assert.AreEqual(60, result.frameLimit);
            Assert.AreEqual(4, result.antiAliasing);
        }

        [Test]
        public void AutoAndExplicitOrientationSelectExpectedComposition()
        {
            Assert.IsTrue(PresentationDisplaySettingsPolicy.IsPortrait(PresentationLayoutMode.Auto, 900, 1600));
            Assert.IsFalse(PresentationDisplaySettingsPolicy.IsPortrait(PresentationLayoutMode.Auto, 1920, 1080));
            Assert.IsTrue(PresentationDisplaySettingsPolicy.IsPortrait(PresentationLayoutMode.Portrait, 1920, 1080));
            Assert.IsFalse(PresentationDisplaySettingsPolicy.IsPortrait(PresentationLayoutMode.Landscape, 900, 1600));
        }

        [Test]
        public void ResolutionListRemovesRefreshRateDuplicatesAndKeepsCurrent()
        {
            List<Vector2Int> results = PresentationDisplaySettingsPolicy.DistinctResolutions(
                new[] { new Resolution { width = 1920, height = 1080 }, new Resolution { width = 1920, height = 1080 } },
                900,
                1600
            );

            CollectionAssert.AreEquivalent(new[] { new Vector2Int(1920, 1080), new Vector2Int(900, 1600) }, results);
        }

        [Test]
        public void CloneKeepsASeparateRevertSnapshot()
        {
            PresentationDisplaySettings saved = new PresentationDisplaySettings { width = 1920, height = 1080 };
            PresentationDisplaySettings pending = saved.Clone();
            pending.width = 900;
            pending.height = 1600;

            Assert.AreEqual(1920, saved.width);
            Assert.AreEqual(1080, saved.height);
        }

        [Test]
        public void ScreenNormalizationKeepsPortraitScaleWithinTheRecoveryGuard()
        {
            PresentationDisplaySettings result = PresentationDisplaySettingsPolicy.NormalizeForScreen(
                new PresentationDisplaySettings { uiScale = 9f }, 900, 1600);

            Assert.LessOrEqual(result.uiScale, PresentationDisplaySettingsPolicy.MaximumUiScale);
            Assert.GreaterOrEqual(result.uiScale, PresentationDisplaySettingsPolicy.MinimumUiScale);
        }

        [Test]
        public void UiScaleSupportsTheAccessiblePresentationRange()
        {
            PresentationDisplaySettings normalized = PresentationDisplaySettingsPolicy.Normalize(
                new PresentationDisplaySettings { uiScale = 1.5f });

            Assert.AreEqual(.75f, PresentationDisplaySettingsPolicy.MinimumUiScale);
            Assert.AreEqual(1.5f, normalized.uiScale);
        }

        [Test]
        public void HistoryTimesGroupByLocalCalendarDateAndRejectUndatedLegacyValues()
        {
            Assert.IsTrue(PresentationHistoryTime.TryGetLocalTime("2026-08-14T09:15:00-04:00", out System.DateTime first));
            Assert.IsTrue(PresentationHistoryTime.TryGetLocalTime("2026-08-14T23:59:00-04:00", out System.DateTime second));
            Assert.IsTrue(PresentationHistoryTime.TryGetLocalTime("2026-08-15T00:01:00-04:00", out System.DateTime nextDay));
            Assert.AreEqual(first.Date, second.Date);
            Assert.AreNotEqual(first.Date, nextDay.Date);
            Assert.IsFalse(PresentationHistoryTime.TryGetLocalTime(null, out _));
            Assert.IsFalse(PresentationHistoryTime.TryGetLocalTime("legacy timestamp unavailable", out _));
        }
    }
}
