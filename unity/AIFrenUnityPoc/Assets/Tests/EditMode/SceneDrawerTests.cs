using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.Reflection;
using System.Collections;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using UnityEngine.TestTools;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SceneDrawerTests
    {
        private GameObject root;
        private SceneDrawerPresenter drawer;
        private RectTransform panel;
        private Button tab;
        [SetUp] public void SetUp()
        {
            root = new GameObject("synthetic drawer", typeof(RectTransform));
            var b = new GameObject("tab", typeof(RectTransform), typeof(Image), typeof(Button));
            b.transform.SetParent(root.transform); tab = b.GetComponent<Button>();
            var p = new GameObject("panel", typeof(RectTransform)); p.transform.SetParent(root.transform);
            panel = p.GetComponent<RectTransform>(); panel.sizeDelta = new Vector2(350, 200);
            drawer = root.AddComponent<SceneDrawerPresenter>(); drawer.Initialize(tab, panel); drawer.SetAvailable(true);
        }
        [TearDown] public void TearDown() => Object.DestroyImmediate(root);
        [Test] public void HoverBridgeAndFocusKeepControlsReachableWithoutPinning()
        {
            drawer.Pointer(true, true, 0); drawer.Tick(0, .2f, false); Assert.IsTrue(drawer.Expanded);
            drawer.Pointer(true, false, .1f); drawer.Tick(.2f, .2f, false); Assert.IsTrue(drawer.Expanded);
            drawer.Pointer(false, true, .25f); drawer.Tick(.5f, .2f, false); Assert.IsTrue(drawer.Expanded);
            drawer.Pointer(false, false, .6f); drawer.Tick(.95f, .2f, true); Assert.IsTrue(drawer.Expanded);
            drawer.Tick(1.4f, .2f, false); Assert.IsFalse(panel.gameObject.activeSelf);
            Assert.IsFalse(panel.GetComponent<CanvasGroup>().blocksRaycasts);
        }
        [Test] public void IdenticalLabelsDoNotDiscardIndependentRemovalIdentities()
        {
            var snapshot = new AIFren.UnityPoc.Protocol.ContinuitySnapshot {
                scene_relations = new[] {
                    new AIFren.UnityPoc.Protocol.ContinuitySceneRelation { target = "companion", facet = "eyes", predicate = "covered_by", cause = "cloth", effect = "vision obstruction", can_clear = true, clear_token = "relation:0" },
                    new AIFren.UnityPoc.Protocol.ContinuitySceneRelation { target = "companion", facet = "eyes", predicate = "covered_by", cause = "cloth", effect = "vision obstruction", can_clear = true, clear_token = "relation:1" }
                }};
            var rows = SceneOverlayState.Rows(snapshot);
            Assert.That(rows.Length, Is.EqualTo(2));
            Assert.That(rows[0].actionToken, Is.Not.EqualTo(rows[1].actionToken));
            Assert.That(SceneOverlayState.ShouldShow(true, 0, true), Is.True);
        }
        [Test] public void ClickKeyboardAndHideAreOnlyPresentationOperations()
        {
            tab.onClick.Invoke(); drawer.Tick(1, .2f, false); Assert.IsTrue(drawer.Pinned);
            drawer.Tick(20, .2f, false); Assert.IsTrue(drawer.Expanded);
            drawer.SetAvailable(false); Assert.IsFalse(tab.gameObject.activeSelf); Assert.IsFalse(panel.gameObject.activeSelf);
            drawer.SetAvailable(true); Assert.IsFalse(drawer.Pinned); Assert.IsFalse(drawer.Expanded);
            drawer.Tick(21, .2f, true); Assert.IsTrue(drawer.Expanded);
            drawer.CloseImmediately(); Assert.IsFalse(drawer.Expanded);
        }

        [Test] public void ReturnOnSelectedDrawerControlDoesNotOpenConversationInput()
        {
            var events = new GameObject("synthetic events", typeof(EventSystem));
            var owner = new GameObject("synthetic input owner");
            try
            {
                var controller = owner.AddComponent<AIFrenPocController>();
                const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
                typeof(AIFrenPocController).GetField("sceneDrawer", flags).SetValue(controller, drawer);
                var system = events.GetComponent<EventSystem>();
                typeof(EventSystem).GetMethod("OnEnable", flags).Invoke(system, null);
                system.SetSelectedGameObject(tab.gameObject);
                // Both dispatch orders occur across Update owners: the controller
                // must leave Return to the selected native Button in either order.
                typeof(AIFrenPocController).GetMethod("HandlePresentationReturn", flags).Invoke(controller, new object[] { false });
                Assert.That(typeof(AIFrenPocController).GetField("inputRequested", flags).GetValue(controller), Is.False);
                ExecuteEvents.Execute(tab.gameObject, new BaseEventData(EventSystem.current), ExecuteEvents.submitHandler);
                Assert.That(drawer.Pinned, Is.True);
                Assert.That(EventSystem.current.currentSelectedGameObject, Is.SameAs(tab.gameObject));
                ExecuteEvents.ExecuteHierarchy(tab.gameObject, new BaseEventData(system), ExecuteEvents.cancelHandler);
                Assert.That(drawer.Pinned, Is.False);
                Assert.That(EventSystem.current.currentSelectedGameObject, Is.Null);
            }
            finally
            {
                typeof(EventSystem).GetMethod("OnDisable", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(events.GetComponent<EventSystem>(), null);
                Object.DestroyImmediate(owner); Object.DestroyImmediate(events);
            }
        }

        [UnityTest] public IEnumerator CanvasRaycastRoutesHoverAndClickAndRejectsCollapsedPanel()
        {
            var events = new GameObject("finite pointer events", typeof(EventSystem));
            var system = events.GetComponent<EventSystem>();
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
            typeof(EventSystem).GetMethod("OnEnable", flags).Invoke(system, null);
            var input = events.AddComponent<NativeQaPointer>();
            typeof(BaseInputModule).GetMethod("OnEnable", flags).Invoke(input, null);
            var canvas = root.AddComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var raycaster = root.AddComponent<GraphicRaycaster>();
            typeof(BaseRaycaster).GetMethod("OnEnable", flags).Invoke(raycaster, null);
            var buttonRect = tab.GetComponent<RectTransform>();
            buttonRect.anchorMin = buttonRect.anchorMax = new Vector2(.5f, .8f);
            buttonRect.sizeDelta = new Vector2(90, 34); buttonRect.anchoredPosition = Vector2.zero;
            panel.anchorMin = panel.anchorMax = new Vector2(.5f, .5f);
            panel.gameObject.AddComponent<Image>();
            try
            {
                yield return null; Canvas.ForceUpdateCanvases();
                input.Target(buttonRect); drawer.Tick(Time.unscaledTime, .2f, false);
                Assert.That(drawer.Expanded, Is.True);
                yield return null;
                input.Target(panel); drawer.Tick(Time.unscaledTime, .2f, false);
                Assert.That(drawer.Expanded, Is.True, "Hover must cross into the real raycastable drawer.");
                input.Move(new Vector2(-100, -100)); drawer.Tick(Time.unscaledTime + 1, .2f, false);
                Assert.That(panel.gameObject.activeSelf, Is.False);
                input.Target(buttonRect, true); drawer.Tick(Time.unscaledTime, .2f, false);
                Assert.That(drawer.Pinned, Is.True);
                Assert.That(system.currentSelectedGameObject, Is.SameAs(tab.gameObject));
                input.SubmitSelected(true);
                Assert.That(drawer.Pinned, Is.False);
                Assert.Throws<System.InvalidOperationException>(() => input.Target(panel),
                    "A collapsed panel must not pass a pointer target check.");
            }
            finally
            {
                typeof(BaseRaycaster).GetMethod("OnDisable", flags).Invoke(raycaster, null);
                typeof(EventSystem).GetMethod("OnDisable", flags).Invoke(system, null);
                Object.DestroyImmediate(events);
            }
        }
    }
}
