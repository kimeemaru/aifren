using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.Reflection;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class MemoryReadabilityTests
    {
        [Test] public void ProductionCompositionResizeReflowsExistingRowsWithoutDiscardingSelectionOrDraft()
        {
            var root = new GameObject("resizable viewer fixture", typeof(RectTransform), typeof(Canvas));
            var owner = new GameObject("synthetic viewer owner");
            try
            {
                root.GetComponent<RectTransform>().sizeDelta = new Vector2(1200, 1600);
                var controller = owner.AddComponent<AIFrenPocController>();
                const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                typeof(AIFrenPocController).GetField("font", flags).SetValue(controller, TMP_Settings.defaultFontAsset);
                typeof(AIFrenPocController).GetField("theme", flags).SetValue(controller, PresentationThemes.Dark);
                typeof(AIFrenPocController).GetMethod("CreateMemoryViewerPanel", flags).Invoke(controller, new object[] { root.transform });
                var state = (MemoryViewerState)typeof(AIFrenPocController).GetField("memoryViewerState", flags).GetValue(controller);
                state.ChangeCharacter("synthetic");
                var item = new AIFren.UnityPoc.Protocol.MemoryViewItem { record_id = "synthetic-row", lane = "v1", content = "A detailed synthetic preference with enough text to wrap differently after narrowing the current viewport.", editable = true };
                Assert.That(state.Accept(state.BeginRequest(), new AIFren.UnityPoc.Protocol.MemoryViewPage { character_id = "synthetic", lane = "v1", items = new[] { item } }), Is.True);
                var panel = (GameObject)typeof(AIFrenPocController).GetField("memoryViewerPanel", flags).GetValue(controller);
                panel.SetActive(true); Canvas.ForceUpdateCanvases();
                typeof(AIFrenPocController).GetMethod("RefreshMemoryViewerPage", flags).Invoke(controller, null);
                var rows = (Transform)typeof(AIFrenPocController).GetField("memoryViewerRows", flags).GetValue(controller);
                var row = rows.GetChild(0).GetComponent<RectTransform>();
                float before = row.sizeDelta.y;
                state.Select(item);
                var input = (TMP_InputField)typeof(AIFrenPocController).GetField("memoryViewerContentInput", flags).GetValue(controller);
                input.text = "Unsaved synthetic edit";
                root.GetComponent<RectTransform>().sizeDelta = new Vector2(560, 1600);
                Canvas.ForceUpdateCanvases();
                typeof(AIFrenPocController).GetMethod("UpdateCompositionLayout", flags).Invoke(controller, null);
                Assert.That(row.sizeDelta.y, Is.GreaterThan(before));
                Assert.That(rows.GetChild(0), Is.SameAs(row), "Resize reflows the existing bounded page.");
                Assert.That(state.Selected, Is.SameAs(item));
                Assert.That(input.text, Is.EqualTo("Unsaved synthetic edit"));
            }
            finally { Object.DestroyImmediate(owner); Object.DestroyImmediate(root); }
        }

        [TestCase("memoryViewerContentInput", true)]
        [TestCase("memoryViewerCategoryInput", false)]
        [TestCase("memoryViewerSearchInput", false)]
        public void ProductionViewerInputsClipLongValuesInsideTheirOwnViewport(string field, bool multiline)
        {
            var root = new GameObject("viewer viewport fixture", typeof(RectTransform), typeof(Canvas));
            var owner = new GameObject("synthetic viewer owner");
            try
            {
                var controller = owner.AddComponent<AIFrenPocController>();
                const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                typeof(AIFrenPocController).GetField("font", flags).SetValue(controller, TMP_Settings.defaultFontAsset);
                typeof(AIFrenPocController).GetField("theme", flags).SetValue(controller, PresentationThemes.Dark);
                typeof(AIFrenPocController).GetMethod("CreateMemoryViewerPanel", flags).Invoke(controller, new object[] { root.transform });
                var input = (TMP_InputField)typeof(AIFrenPocController).GetField(field, flags).GetValue(controller);
                string value = "Synthetic long correction and category value that must remain editable and cannot draw into the neighboring control.";
                input.SetTextWithoutNotify(value);
                input.GetComponent<RectTransform>().sizeDelta = new Vector2(210, 90);
                input.ForceLabelUpdate();
                Assert.That(input.textViewport.GetComponent<RectMask2D>(), Is.Not.Null,
                    "Long input meshes must be clipped, not merely sized to a narrow field.");
                Assert.That(input.textComponent.fontSize, Is.EqualTo(16));
                Assert.That(input.textComponent.enableWordWrapping, Is.EqualTo(multiline));
                Assert.That(input.text, Is.EqualTo(value), "Clipping must preserve the actual editable value.");
                var ordinary = (TMP_InputField)typeof(AIFrenPocController).GetMethod("CreateInputField", flags)
                    .Invoke(controller, new object[] { root.transform, false });
                Assert.That(ordinary.textComponent.fontSize, Is.EqualTo(22), "Only Viewer inputs receive this treatment.");
            }
            finally { Object.DestroyImmediate(owner); Object.DestroyImmediate(root); }
        }

        [Test] public void ProductionRowKeepsItsMeasuredFontAfterTmpGeneratesTheMesh()
        {
            var root = new GameObject("production row fixture", typeof(RectTransform), typeof(Canvas));
            var owner = new GameObject("synthetic viewer owner");
            try
            {
                var controller = owner.AddComponent<AIFrenPocController>();
                const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                typeof(AIFrenPocController).GetField("font", flags).SetValue(controller, TMP_Settings.defaultFontAsset);
                typeof(AIFrenPocController).GetField("theme", flags).SetValue(controller, PresentationThemes.Dark);
                var button = (Button)typeof(AIFrenPocController).GetMethod("CreateButton", flags).Invoke(controller,
                    new object[] { root.transform, "[current] A synthetic preference with a long but bounded readable description.", Color.white });
                float height = AIFrenPocController.LayoutMemoryRow(button, 260);
                button.GetComponent<RectTransform>().sizeDelta = new Vector2(260, height);
                var label = button.GetComponentInChildren<TMP_Text>();
                label.ForceMeshUpdate();
                Assert.That(label.fontSize, Is.EqualTo(16), "The generic button's auto-size must not override measured Viewer typography.");
                Assert.That(label.enableAutoSizing, Is.False);
            }
            finally { Object.DestroyImmediate(owner); Object.DestroyImmediate(root); }
        }

        [Test] public void DenseRowUsesActualTmpWrappingAndBoundedReadableHeight()
        {
            var row = new GameObject("safe dense row", typeof(RectTransform), typeof(Image), typeof(Button));
            var text = new GameObject("label", typeof(RectTransform), typeof(TextMeshProUGUI));
            text.transform.SetParent(row.transform);
            try
            {
                var label = text.GetComponent<TMP_Text>();
                label.font = TMP_Settings.defaultFontAsset;
                label.text = "[current] A synthetic favorite preference with enough descriptive text to require several readable lines.";
                float narrow = AIFrenPocController.LayoutMemoryRow(row.GetComponent<Button>(), 260);
                float wide = AIFrenPocController.LayoutMemoryRow(row.GetComponent<Button>(), 470);
                Assert.That(narrow, Is.GreaterThan(wide));
                Assert.That(narrow, Is.InRange(52, 150));
                Assert.That(label.enableWordWrapping, Is.True);
                Assert.That(label.richText, Is.False);
                Assert.That(label.fontSize, Is.EqualTo(16));
            }
            finally { Object.DestroyImmediate(row); }
        }
    }
}
