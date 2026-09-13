using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using Object = UnityEngine.Object;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterSceneIsolationTests
    {
        private GameObject root, scrim, settings;
        private AIFrenPocController controller;
        private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("synthetic scene owner", typeof(RectTransform));
            root.SetActive(false); // Never run normal application Awake/Start or read preferences.
            root.GetComponent<RectTransform>().sizeDelta = new Vector2(900, 1600);
            controller = root.AddComponent<AIFrenPocController>();
            controller.enabled = false;
            Set("theme", PresentationThemes.Dark);
            Set("font", TMP_Settings.defaultFontAsset);
            Set("showSceneOverlay", true);
            scrim = Child("synthetic modal scrim"); scrim.SetActive(false); Set("modalScrim", scrim);
            settings = Child("synthetic settings"); settings.SetActive(false); Set("settingsPanel", settings);
            foreach (string field in new[] { "memoryViewerLaneButton", "memoryViewerStatusButton", "memoryViewerScopeButton" })
                Set(field, Child(field).AddComponent<Button>());
            Call("BuildSceneOverlay", root.GetComponent<RectTransform>());
        }

        [TearDown]
        public void TearDown() => Object.DestroyImmediate(root);

        [Test]
        public void ClosingSettingsShowsLatestSceneAcrossAThenBThenEmptyCThenA()
        {
            var a = Scene("A", "blue scarf");
            var b = Scene("B", "green cap");
            var c = Scene("C");
            Call("ApplyContinuitySnapshot", a);
            StringAssert.Contains("blue scarf", Rows());

            foreach (var snapshot in new[] { b, c, a })
            {
                scrim.SetActive(true); settings.SetActive(true);
                Call("RefreshSceneDrawerAvailability");
                Call("ApplyContinuitySnapshot", snapshot);
                Call("CloseSettingsPanel");
                string expected = snapshot == a ? "blue scarf" : snapshot == b ? "green cap" : "No current scene items.";
                StringAssert.Contains(expected, Rows(), "Reopening the drawer must project the latest accepted character scene.");
                if (snapshot != a) StringAssert.DoesNotContain("blue scarf", Rows());
                if (snapshot != b) StringAssert.DoesNotContain("green cap", Rows());
            }
        }

        [Test]
        public void HiddenSceneBurstDoesNotResurrectPriorRowsWhenLatestSnapshotIsEmpty()
        {
            Call("ApplyContinuitySnapshot", Scene("A", "blue scarf"));
            scrim.SetActive(true); settings.SetActive(true);
            Call("RefreshSceneDrawerAvailability");
            Call("ApplyContinuitySnapshot", Scene("B", "green cap"));
            Call("ApplyContinuitySnapshot", Scene("C"));
            Call("CloseSettingsPanel");
            StringAssert.Contains("No current scene items.", Rows());
            StringAssert.DoesNotContain("blue scarf", Rows());
            StringAssert.DoesNotContain("green cap", Rows());
        }

        [Test]
        public void UnchangedVisibleSnapshotRetainsItsDerivedRows()
        {
            var a = Scene("A", "blue scarf");
            Call("ApplyContinuitySnapshot", a);
            var content = (Transform)Get("sceneOverlayContent");
            GameObject row = content.GetChild(0).gameObject;
            Call("ApplyContinuitySnapshot", a);
            Assert.AreSame(row, content.GetChild(0).gameObject);
        }

        [Test]
        public void NewRevisionRebindsVisuallyIdenticalRows()
        {
            Call("ApplyContinuitySnapshot", Scene("A-1", "blue scarf"));
            var content = (Transform)Get("sceneOverlayContent");
            GameObject row = content.GetChild(0).gameObject;
            Call("ApplyContinuitySnapshot", Scene("A-2", "blue scarf"));
            Assert.AreNotSame(row, content.GetChild(0).gameObject,
                "A visually unchanged row still needs its current immutable mutation revision.");
        }

        [Test]
        public void MissingContinuityClearsPreviouslyVisibleRows()
        {
            Call("ApplyContinuitySnapshot", Scene("A", "blue scarf"));
            Call("ApplyContinuitySnapshot", new object[] { null });
            StringAssert.DoesNotContain("blue scarf", Rows());
            StringAssert.Contains("No current scene items.", Rows());
        }

        [Test]
        public void ActualDrawerBuildsLocatedObjectRowsAndTheirRemovalControls()
        {
            var snapshot = JsonUtility.FromJson<ContinuitySnapshot>(LocatedSceneProjectionTests.SnapshotJson);
            Call("ApplyContinuitySnapshot", snapshot);
            string rendered = Rows();
            StringAssert.Contains("Companion — blue paint on right middle fingernail", rendered);
            StringAssert.Contains("Companion — silver ring on right ring finger", rendered);
            StringAssert.Contains("Companion — blue glove on left hand", rendered);
            StringAssert.Contains("Blindfold (not active)", rendered);
            var content = (Transform)Get("sceneOverlayContent");
            Assert.That(content.GetComponentsInChildren<Button>(true).Length, Is.EqualTo(4));
            Assert.That(content.GetComponentsInChildren<TMP_Text>(true).Where(label => label.text != "×").All(label => !label.richText), Is.True,
                "Current Scene descriptions stay literal display data.");
        }

        [Test]
        public void SwitchingClearsDerivedSceneViewerAndHistoryBeforeDestinationArrives()
        {
            Call("ApplyContinuitySnapshot", Scene("A", "blue scarf"));
            var messages = (List<ConversationMessage>)Get("messages");
            messages.Add(new ConversationMessage { role = "assistant", content = "Synthetic A dialogue." });
            var viewer = (MemoryViewerState)Get("memoryViewerState");
            viewer.ChangeCharacter("A");
            string request = viewer.BeginRequest();
            Assert.That(viewer.Accept(request, new MemoryViewPage { availability = "ready", character_id = "A", lane = viewer.Lane,
                items = new[] { new MemoryViewItem { record_id = "synthetic-row", lane = viewer.Lane } } }), Is.True);

            Call("BeginCharacterPresentationTransition");
            Assert.That(Get("authoritativeContinuity"), Is.Null);
            Assert.That(messages, Is.Empty);
            Assert.That(viewer.Page, Is.Null);
            Assert.That(viewer.Selected, Is.Null);
            Assert.That(Get("characterSwitchInFlight"), Is.True);
            Assert.That(Call("RefreshSceneDrawerAvailability"), Is.False);

            Set("characterSwitchInFlight", false); // An accepted empty destination snapshot settles the transition.
            Call("ApplyContinuitySnapshot", Scene("C"));
            StringAssert.DoesNotContain("blue scarf", Rows());
            StringAssert.Contains("No current scene items.", Rows());
        }

        [Test]
        public void CapturedSceneRowCannotMutateReboundSameIdCharacter()
        {
            using var client = new AIFrenWebSocketClient();
            Set("client", client);
            typeof(AIFrenWebSocketClient).GetProperty("State").SetValue(client, ConnectionState.Connected);
            var fence = (CharacterSessionFence)typeof(AIFrenWebSocketClient).GetField("characterSession", Private).GetValue(client);
            fence.Accept(CharacterSessionFenceTests.Snapshot("A", "old-A", 1));
            var snapshot = Scene("same-revision", "blue scarf");
            snapshot.scene_relations[0].can_clear = true; snapshot.scene_relations[0].clear_token = "synthetic-clear";
            Call("ApplyContinuitySnapshot", snapshot);
            var rowClick = ((Transform)Get("sceneOverlayContent")).GetComponentInChildren<Button>(true).onClick;

            fence.Accept(CharacterSessionFenceTests.Snapshot("A", "fresh-A", 3));
            rowClick.Invoke(); // Even matching ID/revision cannot substitute a fresh session into this old callback.
            Assert.That(Get("pendingContinuityCommandId"), Is.Null.Or.Empty);
            Assert.That(Get("authoritativeContinuity"), Is.SameAs(snapshot));
            Set("client", null);
        }

        private static ContinuitySnapshot Scene(string revision, string item = null) => new ContinuitySnapshot {
            revision = revision,
            scene_relations = item == null ? Array.Empty<ContinuitySceneRelation>() : new[] {
                new ContinuitySceneRelation { target="companion", predicate="wearing", cause=item }
            },
            scene_subjects = Array.Empty<ContinuitySceneSubject>(),
            open_threads = Array.Empty<ContinuityThread>(),
        };

        private GameObject Child(string name)
        {
            var child = new GameObject(name, typeof(RectTransform));
            child.transform.SetParent(root.transform, false); return child;
        }
        private string Rows() => string.Join("\n", ((Transform)Get("sceneOverlayContent"))
            .GetComponentsInChildren<TMP_Text>(true).Select(text => text.text));
        private object Get(string name) => typeof(AIFrenPocController).GetField(name, Private).GetValue(controller);
        private void Set(string name, object value) => typeof(AIFrenPocController).GetField(name, Private).SetValue(controller, value);
        private object Call(string name, params object[] values) =>
            typeof(AIFrenPocController).GetMethod(name, Private).Invoke(controller, values);
    }
}
