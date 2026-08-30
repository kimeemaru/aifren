using System.Collections;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ConversationHistoryRefreshTests
    {
        private GameObject root;
        private AIFrenPocController controller;
        private GameObject historyPanel;
        private RectTransform initialHistoryContent;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("controller", typeof(AIFrenPocController));
            controller = root.GetComponent<AIFrenPocController>();

            historyPanel = new GameObject("history", typeof(RectTransform));
            historyPanel.transform.SetParent(root.transform, false);
            initialHistoryContent = new GameObject("content", typeof(RectTransform)).GetComponent<RectTransform>();
            initialHistoryContent.SetParent(historyPanel.transform, false);

            ScrollRect scroll = historyPanel.AddComponent<ScrollRect>();
            scroll.content = initialHistoryContent;
            Field("historyPanel").SetValue(controller, historyPanel);
            Field("historyContent").SetValue(controller, initialHistoryContent);
            Field("historyScroll").SetValue(controller, scroll);
            Field("modalScrim").SetValue(controller, new GameObject("scrim", typeof(RectTransform)));
            Field("theme").SetValue(controller, PresentationThemes.Dark);
            Field("font").SetValue(controller, TMP_Settings.defaultFontAsset);
            historyPanel.SetActive(false);
        }

        [TearDown]
        public void TearDown()
        {
            GameObject scrim = (GameObject)Field("modalScrim").GetValue(controller);
            if (scrim != null) Object.DestroyImmediate(scrim);
            Object.DestroyImmediate(root);
        }

        [Test]
        public void HiddenMessageUpdatesStateAndMarksDirtyWithoutRendering()
        {
            AddMessage("user", "First hidden update");

            Assert.AreEqual(1, Messages.Count);
            Assert.IsTrue(HistoryDirty);
            Assert.AreEqual(0, HistoryContent.childCount);
        }

        [Test]
        public void OpeningDirtyHistoryRendersOnlySelectedLatestDayAndClearsDirty()
        {
            AddMessage("user", "First hidden update");
            AddMessage("assistant", "Second hidden update");

            Method("ToggleHistoryPanel").Invoke(controller, null);

            Assert.IsTrue(historyPanel.activeSelf);
            Assert.IsFalse(HistoryDirty);
            string rendered = string.Join("\n", HistoryContent
                .GetComponentsInChildren<TMP_Text>(true)
                .Select(label => label.text));
            StringAssert.Contains("First hidden update", rendered);
            StringAssert.Contains("Second hidden update", rendered);
        }

        [Test]
        public void BackToBackHiddenMessagesPerformNoRebuilds()
        {
            AddMessage("user", "One");
            AddMessage("assistant", "Two");
            AddMessage("user", "Three");

            Assert.AreEqual(3, Messages.Count);
            Assert.IsTrue(HistoryDirty);
            Assert.AreEqual(0, HistoryContent.childCount);
        }

        [Test]
        public void VisibleBackToBackMessagesCoalesceIntoOneRefresh()
        {
            historyPanel.SetActive(true);
            AddMessage("user", "One visible update");
            AddMessage("assistant", "Two visible updates");

            Assert.IsTrue(HistoryDirty);
            Assert.AreEqual(0, HistoryContent.childCount,
                "Individual message handlers must not rebuild within the event burst.");

            Method("RefreshHistoryIfVisible").Invoke(controller, null);
            int renderedChildCount = HistoryContent.childCount;

            Assert.IsFalse(HistoryDirty);
            Assert.Greater(renderedChildCount, 0);
            Method("RefreshHistoryIfVisible").Invoke(controller, null);
            Assert.AreEqual(renderedChildCount, HistoryContent.childCount,
                "A clean visible history must not be rebuilt again.");
        }

        [Test]
        public void VisibleRefreshReusesTheBoundedContentHierarchy()
        {
            AddMessage("user", "First canonical message");
            Method("ToggleHistoryPanel").Invoke(controller, null);
            RectTransform firstGeneration = HistoryContent;

            AddMessage("assistant", "Second canonical message");
            Method("RefreshHistoryIfVisible").Invoke(controller, null);

            Assert.AreSame(firstGeneration, HistoryContent);
            Assert.IsTrue(HistoryContent.gameObject.activeSelf);
            Assert.AreSame(HistoryContent, ((ScrollRect)Field("historyScroll").GetValue(controller)).content);
        }

        [Test]
        public void OneCanonicalMessageRendersOnceAndIdenticalMessagesRemainDistinct()
        {
            AddMessage("user", "Legitimate repeated text");
            Method("ToggleHistoryPanel").Invoke(controller, null);
            Assert.AreEqual(1, RenderedOccurrences("Legitimate repeated text"));

            AddMessage("user", "Legitimate repeated text");
            Method("RefreshHistoryIfVisible").Invoke(controller, null);
            Assert.AreEqual(2, RenderedOccurrences("Legitimate repeated text"));
        }

        [Test]
        public void TwoThousandMessageArchiveAllocatesOnlyOneBoundedDayPage()
        {
            for (int index = 0; index < 2000; index++)
                AddMessage(index % 2 == 0 ? "user" : "assistant", "Synthetic " + index);

            Method("ToggleHistoryPanel").Invoke(controller, null);

            Assert.LessOrEqual(HistoryContent.childCount, 84,
                "History must never instantiate the lifetime archive.");
            Assert.AreEqual(1, RenderedOccurrences("Synthetic 1999"));
            Assert.AreEqual(0, RenderedOccurrences("Synthetic 0"));
        }

        [Test]
        public void TwoThousandMessageOpenHasBoundedLatencyAndManagedAllocation()
        {
            for (int index = 0; index < 2000; index++)
                AddMessage(index % 2 == 0 ? "user" : "assistant", "Measured " + index);

            long before = System.GC.GetAllocatedBytesForCurrentThread();
            long heapBefore = Profiler.GetMonoUsedSizeLong();
            Stopwatch watch = Stopwatch.StartNew();
            Method("ToggleHistoryPanel").Invoke(controller, null);
            watch.Stop();
            long allocated = System.GC.GetAllocatedBytesForCurrentThread() - before;
            long heapGrowth = System.Math.Max(0L, Profiler.GetMonoUsedSizeLong() - heapBefore);
            TestContext.WriteLine(
                "history_open_messages=2000 rows=" + HistoryContent.childCount
                + " elapsed_ms=" + watch.Elapsed.TotalMilliseconds.ToString("0.###")
                + " allocated_bytes=" + allocated
                + " managed_heap_growth_bytes=" + heapGrowth);

            Assert.LessOrEqual(HistoryContent.childCount, 84);
            Assert.Less(watch.Elapsed.TotalMilliseconds, 500.0,
                "Headless EditMode opening must remain comfortably below the prior 1.46s stall.");
            Assert.Less(allocated, 64L * 1024L * 1024L,
                "A bounded page must not approach the prior ~580 MB lifetime rebuild.");
            Assert.Less(heapGrowth, 64L * 1024L * 1024L,
                "The managed heap must grow with one page, not the archive lifetime.");
        }

        private IList Messages => (IList)Field("messages").GetValue(controller);
        private bool HistoryDirty => (bool)Field("historyDirty").GetValue(controller);
        private RectTransform HistoryContent => (RectTransform)Field("historyContent").GetValue(controller);

        private int RenderedOccurrences(string text)
        {
            return HistoryContent.GetComponentsInChildren<TMP_Text>(true)
                .Count(label => label.text.Contains(text));
        }

        private void AddMessage(string role, string content)
        {
            Method("AddMessage").Invoke(controller, new object[]
            {
                role, content, "2026-08-25T12:00:00Z", false, false
            });
        }

        private static FieldInfo Field(string name) => typeof(AIFrenPocController).GetField(
            name, BindingFlags.Instance | BindingFlags.NonPublic);

        private static MethodInfo Method(string name) => typeof(AIFrenPocController).GetMethod(
            name, BindingFlags.Instance | BindingFlags.NonPublic);
    }
}
