using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ChatInputFieldLayoutTests
    {
        [Test]
        public void ChatInputUsesMaskedMultilineViewport()
        {
            GameObject root = new GameObject("Input", typeof(RectTransform), typeof(TMP_InputField));
            GameObject viewportObject = new GameObject("Text Area", typeof(RectTransform), typeof(RectMask2D));
            viewportObject.transform.SetParent(root.transform, false);
            GameObject textObject = new GameObject("Text", typeof(RectTransform), typeof(TextMeshProUGUI));
            textObject.transform.SetParent(viewportObject.transform, false);
            GameObject placeholderObject = new GameObject("Placeholder", typeof(RectTransform), typeof(TextMeshProUGUI));
            placeholderObject.transform.SetParent(viewportObject.transform, false);
            try
            {
                TMP_InputField input = root.GetComponent<TMP_InputField>();
                ChatInputFieldLayout.Configure(input, viewportObject.GetComponent<RectTransform>(),
                    textObject.GetComponent<TextMeshProUGUI>(), placeholderObject.GetComponent<TextMeshProUGUI>());

                Assert.AreEqual(TMP_InputField.LineType.MultiLineNewline, input.lineType);
                Assert.AreSame(viewportObject.GetComponent<RectTransform>(), input.textViewport);
                Assert.IsNotNull(viewportObject.GetComponent<RectMask2D>());
                Assert.IsTrue(input.textComponent.enableWordWrapping);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void VerticalScrollingBeginsOnlyAfterRenderedTextExceedsViewport()
        {
            Assert.IsFalse(ChatInputFieldLayout.RequiresVerticalScrolling(44f, 44f));
            Assert.IsFalse(ChatInputFieldLayout.RequiresVerticalScrolling(44.4f, 44f));
            Assert.IsTrue(ChatInputFieldLayout.RequiresVerticalScrolling(44.6f, 44f));
        }
    }
}
