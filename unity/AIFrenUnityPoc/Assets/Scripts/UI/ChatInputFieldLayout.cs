using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    internal static class ChatInputFieldLayout
    {
        internal static void Configure(TMP_InputField input, RectTransform viewport, TextMeshProUGUI text, TextMeshProUGUI placeholder)
        {
            input.lineType = TMP_InputField.LineType.MultiLineNewline;
            input.textViewport = viewport;
            input.textComponent = text;
            input.placeholder = placeholder;
            input.scrollSensitivity = 1.25f;

            if (viewport.GetComponent<RectMask2D>() == null) viewport.gameObject.AddComponent<RectMask2D>();
            text.enableWordWrapping = true;
            text.overflowMode = TextOverflowModes.Overflow;
            placeholder.enableWordWrapping = true;
            placeholder.overflowMode = TextOverflowModes.Ellipsis;
            ChatInputFieldPresentation presentation = input.GetComponent<ChatInputFieldPresentation>();
            if (presentation == null) presentation = input.gameObject.AddComponent<ChatInputFieldPresentation>();
            presentation.Initialize(input, viewport, text, placeholder);
        }

        internal static bool RequiresVerticalScrolling(float contentHeight, float viewportHeight) =>
            contentHeight > viewportHeight + .5f;
    }

    /// <summary>Keeps short chat text visually centered without sacrificing TMP's multiline scroll behavior.</summary>
    internal sealed class ChatInputFieldPresentation : MonoBehaviour
    {
        private TMP_InputField input;
        private RectTransform viewport;
        private TextMeshProUGUI text;
        private TextMeshProUGUI placeholder;
        private bool scrollingPresentation;

        internal void Initialize(TMP_InputField field, RectTransform textViewport, TextMeshProUGUI inputText, TextMeshProUGUI inputPlaceholder)
        {
            if (input != null) input.onValueChanged.RemoveListener(HandleValueChanged);
            input = field;
            viewport = textViewport;
            text = inputText;
            placeholder = inputPlaceholder;
            input.onValueChanged.AddListener(HandleValueChanged);
            RefreshPresentation();
        }

        private void OnDestroy()
        {
            if (input != null) input.onValueChanged.RemoveListener(HandleValueChanged);
        }

        private void LateUpdate() => RefreshPresentation();

        private void HandleValueChanged(string _) => RefreshPresentation();

        private void RefreshPresentation()
        {
            if (input == null || viewport == null || text == null || placeholder == null) return;
            float availableWidth = Mathf.Max(1f, viewport.rect.width);
            float contentHeight = text.GetPreferredValues(input.text ?? string.Empty, availableWidth, 0f).y;
            bool shouldScroll = ChatInputFieldLayout.RequiresVerticalScrolling(contentHeight, viewport.rect.height);
            TextAlignmentOptions desiredAlignment = shouldScroll ? TextAlignmentOptions.TopLeft : TextAlignmentOptions.MidlineLeft;
            if (scrollingPresentation == shouldScroll && text.alignment == desiredAlignment &&
                placeholder.alignment == TextAlignmentOptions.MidlineLeft) return;

            scrollingPresentation = shouldScroll;
            text.alignment = desiredAlignment;
            placeholder.alignment = TextAlignmentOptions.MidlineLeft;
            input.ForceLabelUpdate();
        }
    }
}
