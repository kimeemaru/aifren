using System;
using System.Reflection;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class DialogueScrollLayoutTests
    {
        private GameObject root;
        private AIFrenPocController controller;
        private RectTransform viewport;
        private TextMeshProUGUI label;
        private ScrollRect scroll;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("dialogue-layout-test", typeof(RectTransform), typeof(Canvas),
                typeof(AIFrenPocController));
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            controller = root.GetComponent<AIFrenPocController>();

            RectTransform card = NewRect("card", root.transform, new Vector2(820f, 252f));
            viewport = NewRect("viewport", card, new Vector2(760f, 181f));
            viewport.pivot = new Vector2(.5f, .5f);
            viewport.gameObject.AddComponent<RectMask2D>();

            label = new GameObject("dialogue", typeof(RectTransform), typeof(CanvasRenderer),
                typeof(TextMeshProUGUI)).GetComponent<TextMeshProUGUI>();
            label.transform.SetParent(viewport, false);
            label.font = TMP_Settings.defaultFontAsset;
            label.fontSize = 29f;
            label.fontStyle = FontStyles.Bold;
            label.enableWordWrapping = true;
            label.enableAutoSizing = false;
            label.lineSpacing = -5f;
            label.paragraphSpacing = -7f;
            label.overflowMode = TextOverflowModes.Overflow;
            label.margin = new Vector4(14f, 10f, 14f, 10f);
            label.rectTransform.anchorMin = new Vector2(0f, 1f);
            label.rectTransform.anchorMax = new Vector2(1f, 1f);
            label.rectTransform.pivot = new Vector2(.5f, 1f);
            label.rectTransform.anchoredPosition = Vector2.zero;
            label.rectTransform.sizeDelta = new Vector2(-28f, 186f);

            scroll = card.gameObject.AddComponent<ScrollRect>();
            scroll.viewport = viewport;
            scroll.content = label.rectTransform;
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;

            Field("dialogueCardRect").SetValue(controller, card);
            Field("dialogueViewportRect").SetValue(controller, viewport);
            Field("dialogueTextLabel").SetValue(controller, label);
            Field("dialogueScroll").SetValue(controller, scroll);
            Field("dialogueAutoFollow").SetValue(controller, true);
            Field("revealWordsPerSecond").SetValue(controller, 7f);
        }

        [TearDown]
        public void TearDown()
        {
            UnityEngine.Object.DestroyImmediate(root);
        }

        [Test]
        public void IncrementalFinalReconciliationKeepsLongResponseTailReachable()
        {
            string response = BuildProductionLongResponse();
            int split = response.Length * 2 / 3;
            Field("assistantStreamVisible").SetValue(controller, true);
            Field("pendingAssistantContent").SetValue(controller, response.Substring(0, split));
            Method("RefreshStreamedAssistantDialogue").Invoke(controller, new object[] { true });

            WordReveal reveal = (WordReveal)Field("wordReveal").GetValue(controller);
            while (reveal.RevealedTokenCount < Math.Min(40, reveal.WordCount))
                AdvanceOneWord(reveal);

            Method("FinalizeStreamedAssistantDialogue").Invoke(controller, new object[] { response });
            while (!reveal.IsComplete) AdvanceOneWord(reveal);
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.AreEqual(reveal.WordCount, reveal.RevealedTokenCount);
            Assert.AreEqual(DialoguePresentationParser.FormatVisible(response), label.text);
            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, RequiredHeight(response));
            Assert.LessOrEqual(scroll.verticalNormalizedPosition, .001f);
            AssertRenderedMeshInsideContent();
            Assert.IsTrue(IsFinalCharacterInsideViewport(),
                "The final visible character must be inside the clipped viewport after reveal completion.");
        }

        [Test]
        public void ShortCompletedResponseFitsWithoutScrolling()
        {
            const string response = "A short synthetic response remains fully visible.";

            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, true, 0f });
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.AreEqual(DialoguePresentationParser.FormatVisible(response), label.text);
            Assert.AreEqual(viewport.rect.height, label.rectTransform.rect.height, .5f);
            Assert.IsTrue(IsFinalCharacterInsideViewport());
        }

        [Test]
        public void ResponseSlightlyTallerThanViewportShowsFinalLineAfterSkip()
        {
            string response = BuildResponse(44);
            while (RequiredHeight(response) <= viewport.rect.height + 1f)
                response = BuildResponse(response.Split(' ').Length + 2);
            Assert.Less(RequiredHeight(response), viewport.rect.height * 1.75f,
                "The fixture should be a small overflow, not another long-response case.");

            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, false, 0f });
            Method("SkipCurrentReveal").Invoke(controller, null);
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, RequiredHeight(response));
            Assert.LessOrEqual(scroll.verticalNormalizedPosition, .001f);
            Assert.IsTrue(IsFinalCharacterInsideViewport());
        }

        [Test]
        public void SkippingIncrementalRevealMakesTheCompleteLongResponseReachable()
        {
            string response = BuildProductionLongResponse();
            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, false, 0f });
            WordReveal reveal = (WordReveal)Field("wordReveal").GetValue(controller);
            for (int index = 0; index < 24; index++) AdvanceOneWord(reveal);
            float partialHeight = label.rectTransform.rect.height;

            Method("SkipCurrentReveal").Invoke(controller, null);
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();
            float requiredHeight = RequiredHeight(response);

            Assert.IsTrue(reveal.IsComplete);
            Assert.AreEqual(DialoguePresentationParser.FormatVisible(response), label.text);
            Assert.Greater(requiredHeight, partialHeight + 1f,
                "The fixture must cross additional lines when the reveal is skipped.");
            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, requiredHeight,
                $"Complete text needs {requiredHeight:F1}px, but the scroll content remained {label.rectTransform.rect.height:F1}px.");
            Assert.LessOrEqual(scroll.verticalNormalizedPosition, .001f);
            AssertRenderedMeshInsideContent();
            Assert.IsTrue(IsFinalCharacterInsideViewport());
        }

        [Test]
        public void RestoredPersistedLongResponseShowsItsCompleteFinalLineAtBottom()
        {
            string response = BuildProductionLongResponse();

            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, true, 0f });
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.AreEqual(DialoguePresentationParser.FormatVisible(response), label.text);
            Assert.IsTrue(((WordReveal)Field("wordReveal").GetValue(controller)).IsComplete);
            Assert.LessOrEqual(scroll.verticalNormalizedPosition, .001f,
                "Restoring the latest completed response should present its tail, not reset to its first line.");
            AssertRenderedMeshInsideContent();
            Assert.IsTrue(IsFinalCharacterInsideViewport(),
                "The complete persisted final line must be inside the viewport at the true ScrollRect bottom.");
        }

        [Test]
        public void PersistedLongResponseFinalLineFitsInsideOwnedContentAtMathematicalBottom()
        {
            string response = BuildProductionLongResponse();
            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, true, 0f });
            Canvas.ForceUpdateCanvases();
            scroll.verticalNormalizedPosition = 0f;
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.IsTrue(IsFinalCharacterInsideViewport(),
                "Even a manual true-bottom scroll must expose the final rendered character.");
            AssertRenderedMeshInsideContent();
        }

        [Test]
        public void ResizeAfterCompletionKeepsFinalContentReachable()
        {
            string response = BuildResponse(94);
            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, false, 0f });
            Method("SkipCurrentReveal").Invoke(controller, null);
            viewport.sizeDelta = new Vector2(viewport.sizeDelta.x, viewport.sizeDelta.y - 45f);

            Method("UpdateDialogueLayout").Invoke(controller, new object[] { false });
            Method("RefreshDialogueScrollableContent").Invoke(controller, null);
            Method("FollowScrollIfNearBottom").Invoke(null, new object[] { scroll });
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, RequiredHeight(response));
            Assert.IsTrue(IsFinalCharacterInsideViewport());
        }

        [Test]
        public void HideShowAfterCompletionPreservesFullResponseAndReachableTail()
        {
            string response = BuildResponse(94);
            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, false, 0f });
            Method("SkipCurrentReveal").Invoke(controller, null);
            RectTransform card = (RectTransform)Field("dialogueCardRect").GetValue(controller);

            card.gameObject.SetActive(false);
            card.gameObject.SetActive(true);
            Method("UpdateDialogueLayout").Invoke(controller, new object[] { false });
            Method("RefreshDialogueScrollableContent").Invoke(controller, null);
            Method("FollowScrollIfNearBottom").Invoke(null, new object[] { scroll });
            Canvas.ForceUpdateCanvases();
            label.ForceMeshUpdate();

            Assert.AreEqual(DialoguePresentationParser.FormatVisible(response), label.text);
            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, RequiredHeight(response));
            Assert.IsTrue(IsFinalCharacterInsideViewport());
        }

        [Test]
        public void SkipDoesNotForceBottomWhenUserDisabledAutoFollow()
        {
            string response = BuildResponse(94);
            Method("ShowAssistantDialogue").Invoke(controller, new object[] { response, false, 0f });
            WordReveal reveal = (WordReveal)Field("wordReveal").GetValue(controller);
            for (int index = 0; index < 35; index++) AdvanceOneWord(reveal);
            scroll.verticalNormalizedPosition = .8f;
            Field("dialogueAutoFollow").SetValue(controller, false);

            Method("SkipCurrentReveal").Invoke(controller, null);
            Canvas.ForceUpdateCanvases();

            Assert.IsTrue(reveal.IsComplete);
            Assert.Greater(scroll.verticalNormalizedPosition, .05f,
                "A user who scrolled away from the bottom must not be forcibly returned there.");
            Assert.GreaterOrEqual(label.rectTransform.rect.height + .5f, RequiredHeight(response));
        }

        private void AdvanceOneWord(WordReveal reveal)
        {
            Assert.IsTrue(reveal.Advance(1f / reveal.WordsPerSecond + .001f));
            Method("RefreshDialogueRevealText").Invoke(controller,
                new object[] { !reveal.IsComplete || (bool)Field("assistantStreamVisible").GetValue(controller) });
            bool resized = (bool)Method("RefreshDialogueScrollableContent").Invoke(controller, null);
            if ((bool)Field("dialogueAutoFollow").GetValue(controller) && resized)
                Method("FollowScrollIfNearBottom").Invoke(null, new object[] { scroll });
        }

        private bool IsFinalCharacterInsideViewport()
        {
            TMP_CharacterInfo character = label.textInfo.characterInfo[label.textInfo.characterCount - 1];
            Vector3 lowerWorld = label.rectTransform.TransformPoint(character.bottomLeft);
            Vector3 upperWorld = label.rectTransform.TransformPoint(character.topRight);
            Vector3[] corners = new Vector3[4];
            viewport.GetWorldCorners(corners);
            return lowerWorld.y >= corners[0].y - .5f && upperWorld.y <= corners[1].y + .5f;
        }

        private float RequiredHeight(string response)
        {
            string formatted = DialoguePresentationParser.FormatVisible(response);
            float innerWidth = label.rectTransform.rect.width - label.margin.x - label.margin.z;
            return label.GetPreferredValues(formatted, innerWidth, 0f).y;
        }

        private void AssertRenderedMeshInsideContent()
        {
            Assert.GreaterOrEqual(label.textBounds.min.y,
                label.rectTransform.rect.yMin + label.margin.w - .5f,
                "The TMP mesh must not extend below the content RectTransform owned by ScrollRect.");
        }

        private static string BuildResponse(int words)
        {
            string[] vocabulary = { "Sample", "text", "keeps", "every", "final", "phrase", "clear", "while", "lines", "extend", "in", "view" };
            var parts = new string[words];
            for (int index = 0; index < words; index++) parts[index] = vocabulary[index % vocabulary.Length];
            parts[words - 6] = "Does";
            parts[words - 5] = "that";
            parts[words - 4] = "final";
            parts[words - 3] = "sentence";
            parts[words - 2] = "remain";
            parts[words - 1] = "visible?";
            return string.Join(" ", parts);
        }

        private static string BuildProductionLongResponse()
        {
            string plain = BuildResponse(219);
            string[] words = plain.Split(' ');
            string first = string.Join(" ", words, 0, 70);
            string second = string.Join(" ", words, 70, 72);
            string third = string.Join(" ", words, 142, words.Length - 142);
            return first + ".\n\n*(I pause and look toward the synthetic horizon with a calm smile)*\n\n" +
                second + "!\n\n*(I nod once before continuing the harmless layout fixture)*\n\n" +
                third;
        }

        private static RectTransform NewRect(string name, Transform parent, Vector2 size)
        {
            RectTransform rect = new GameObject(name, typeof(RectTransform)).GetComponent<RectTransform>();
            rect.SetParent(parent, false);
            rect.anchorMin = rect.anchorMax = new Vector2(.5f, .5f);
            rect.sizeDelta = size;
            return rect;
        }

        private static FieldInfo Field(string name) => typeof(AIFrenPocController).GetField(
            name, BindingFlags.Instance | BindingFlags.NonPublic);

        private static MethodInfo Method(string name) => typeof(AIFrenPocController).GetMethod(
            name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Static);
    }
}
