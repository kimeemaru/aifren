using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private readonly Dictionary<string, Button> memoryRowButtons = new Dictionary<string, Button>();
        private readonly List<Button> memoryPageButtons = new List<Button>();
        private float memoryMeasuredWidth = -1;

        private void ReflowVisibleMemoryRows()
        {
            if (memoryViewerPanel == null || !memoryViewerPanel.activeInHierarchy || memoryPageButtons.Count == 0) return;
            var rows = memoryViewerRows as RectTransform;
            if (Mathf.Abs(memoryMeasuredWidth - rows.rect.width * .98f) < .5f) return;
            Canvas.ForceUpdateCanvases();
            memoryMeasuredWidth = rows.rect.width * .98f;
            float position = memoryViewerScroll != null ? memoryViewerScroll.verticalNormalizedPosition : 1;
            float top = 0;
            foreach (Button row in memoryPageButtons)
            {
                if (row == null) continue;
                float height = LayoutMemoryRow(row, memoryMeasuredWidth);
                PlaceTop(row.GetComponent<RectTransform>(), -top, height, .01f, .99f);
                top += height + 7;
            }
            rows.sizeDelta = new Vector2(0, Mathf.Max(40, top));
            if (memoryViewerScroll != null) memoryViewerScroll.verticalNormalizedPosition = position;
        }
        private TMP_InputField CreateMemoryInputField(Transform parent, bool multiline = false)
        {
            // Generic small single-line inputs do not own a clipped viewport.
            // Viewer values can be long, including read-only subject keys: keep
            // their full editable value while clipping the mesh/caret locally.
            TMP_InputField input = CreateInputField(parent);
            input.textViewport.gameObject.AddComponent<RectMask2D>();
            input.lineType = multiline ? TMP_InputField.LineType.MultiLineNewline : TMP_InputField.LineType.SingleLine;
            input.textComponent.enableAutoSizing = false;
            input.textComponent.fontSize = 16;
            input.textComponent.enableWordWrapping = multiline;
            input.textComponent.alignment = multiline ? TextAlignmentOptions.TopLeft : TextAlignmentOptions.MidlineLeft;
            input.textComponent.overflowMode = TextOverflowModes.Overflow;
            if (input.placeholder is TMP_Text placeholder)
            {
                placeholder.enableAutoSizing = false;
                placeholder.fontSize = 16;
                placeholder.enableWordWrapping = multiline;
                placeholder.overflowMode = TextOverflowModes.Ellipsis;
            }
            return input;
        }

        internal static float LayoutMemoryRow(Button button, float width)
        {
            TMP_Text label = button.GetComponentInChildren<TMP_Text>();
            label.enableAutoSizing = false;
            label.fontSize = 16;
            label.enableWordWrapping = true;
            label.richText = false;
            label.alignment = TextAlignmentOptions.MidlineLeft;
            label.lineSpacing = 2;
            label.margin = new Vector4(8, 6, 8, 6);
            return Mathf.Clamp(label.GetPreferredValues(label.text, Mathf.Max(100, width - 24), 0).y + 18, 52, 150);
        }
        private void RefreshMemorySelection()
        {
            foreach (var pair in memoryRowButtons)
            {
                if (pair.Value == null) continue;
                bool selected = memoryViewerState.Selected != null && pair.Key == memoryViewerState.Selected.record_id;
                pair.Value.GetComponent<Image>().color = selected ? theme.accent : theme.control;
            }
        }
        private void ApplyFocusedReadability()
        {
            if (memoryViewerPanel != null)
            {
                Color opaque = theme.surfaceStrong; opaque.a = 1;
                memoryViewerPanel.GetComponent<Image>().color = opaque;
                foreach (Button button in memoryViewerPanel.GetComponentsInChildren<Button>(true))
                {
                    var colors = button.colors;
                    colors.disabledColor = new Color(.56f, .56f, .56f, 1);
                    button.colors = colors;
                }
            }
            RefreshMemorySelection();
        }
    }
}
