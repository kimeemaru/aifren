using System;
using System.Net.WebSockets;
using System.Reflection;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ViewRecoveryTests
    {
        [Test]
        public void CoalescingTimeoutRetryAndLatestRequestAreBounded()
        {
            var state = new ViewRequestState();
            string first = state.Begin(0, true);
            Assert.That(state.Begin(1, true), Is.Null);
            Assert.That(state.Expire(14), Is.False);
            Assert.That(state.Expire(15), Is.True);
            Assert.That(state.Status, Is.EqualTo(ViewLoadStatus.TimedOut));
            string retry = state.Begin(16);
            Assert.That(state.Complete(first, 4, true), Is.False);
            Assert.That(state.Complete(retry, 4, true), Is.True);
            Assert.That(state.Status, Is.EqualTo(ViewLoadStatus.Ready));
            string replaced = state.Begin(20);
            string latest = state.Begin(21);
            Assert.That(state.Fail(replaced), Is.False);
            Assert.That(state.Complete(latest, 0, true), Is.True);
            Assert.That(state.Status, Is.EqualTo(ViewLoadStatus.Empty));
        }

        [Test]
        public void UnavailableIsNotAnEmptyFilteredPageAndRetryRestoresIt()
        {
            var state = new MemoryViewerState(); state.ChangeCharacter("A"); state.CycleLane();
            string id = state.BeginRequest();
            Assert.That(state.Accept(id, Page("A", "v2_claims", "degraded")), Is.True);
            Assert.That(state.PageRequest.Status, Is.EqualTo(ViewLoadStatus.Unavailable));
            id = state.BeginRequest();
            Assert.That(state.Accept(id, Page("A", "v2_claims", "ready")), Is.True);
            Assert.That(state.PageRequest.Status, Is.EqualTo(ViewLoadStatus.Empty));
            Assert.That(state.Page, Is.Not.Null);
        }

        [Test]
        public void SameRecordRefreshPreservesSelectionButFilterResetAndChangedRecordRetireIt()
        {
            var state = new MemoryViewerState(); state.ChangeCharacter("A");
            var item = Item("First");
            state.Accept(state.BeginRequest(), Page("A", "v1", "ready", item)); state.Select(item);
            state.Accept(state.BeginRequest(), Page("A", "v1", "ready", Item("First")));
            Assert.That(state.SelectionPreserved, Is.True);
            state.Accept(state.BeginRequest(), Page("A", "v1", "ready", Item("Corrected")));
            Assert.That(state.Selected, Is.Null);
            string retired = state.BeginRequest(); state.InvalidatePage();
            Assert.That(state.Accept(retired, Page("A", "v1", "ready", item)), Is.False);
            state.Accept(state.BeginRequest(), Page("A", "v1", "ready", item)); state.Select(item);
            state.CycleScope();
            Assert.That(state.Page, Is.Null); Assert.That(state.Selected, Is.Null);
            Assert.That(state.PendingRequestId, Is.Empty);
        }

        [Test]
        public void SameOwnerEditorDraftSurvivesRefreshAndCrossOwnerClearsIt()
        {
            var root = new GameObject("synthetic-view", typeof(AIFrenPocController));
            var controller = root.GetComponent<AIFrenPocController>();
            try
            {
                Set(controller, "theme", PresentationThemes.Dark);
                Set(controller, "font", TMP_Settings.defaultFontAsset);
                Call(controller, "CreateMemoryViewerPanel", root.transform);
                // No layout destruction in this synchronous EditMode fixture; player covers actual rows.
                Set(controller, "memoryViewerRows", null);
                var state = (MemoryViewerState)Get(controller, "memoryViewerState");
                state.ChangeCharacter("A"); var item = Item("Persisted record");
                state.Accept(state.BeginRequest(), Page("A", "v1", "ready", item));
                Call(controller, "SelectMemoryViewerItem", item);
                var input = (TMP_InputField)Get(controller, "memoryViewerContentInput");
                input.text = "Unsaved synthetic draft";
                state.Accept(state.BeginRequest(), Page("A", "v1", "ready", Item("Persisted record")));
                Call(controller, "RefreshMemoryViewerPage");
                Assert.That(input.text, Is.EqualTo("Unsaved synthetic draft"));
                state.ChangeCharacter("B"); Call(controller, "RefreshMemoryViewerPage");
                Assert.That(input.text, Is.Empty);
                Assert.That(state.Selected, Is.Null);
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        [Test]
        public void SupersededSnapshotAndOldOwnerCannotBindOrReplaceFreshModel()
        {
            using var client = new AIFrenWebSocketClient(); using var socket = new ClientWebSocket();
            Set(client, "socket", socket);
            client.EnqueueReceived(socket, CharacterSessionFenceTests.Snapshot("A", "a", 1));
            Assert.That(client.TryDequeue(out _), Is.True);
            Set(client, "snapshotRequestId", "latest");
            var old = CharacterSessionFenceTests.Snapshot("B", "b", 2); old.request_id = "old";
            client.EnqueueReceived(socket, old);
            Assert.That(client.TryDequeue(out _), Is.False);
            Assert.That(client.CharacterOwner.CharacterId, Is.EqualTo("A"));
            var fresh = CharacterSessionFenceTests.Snapshot("A", "a", 1); fresh.request_id = "latest";
            client.EnqueueReceived(socket, fresh); Assert.That(client.TryDequeue(out _), Is.True);
            client.EnqueueReceived(socket, new ServerMessage { type = "event", character_id = "B", character_generation = 2, @event = new BackendEvent { type = "character_switching" } });
            Assert.That(client.TryDequeue(out _), Is.True);
            client.EnqueueReceived(socket, fresh); Assert.That(client.TryDequeue(out _), Is.False);
        }

        [Test]
        public void FenceDiagnosticsExposeReasonWithoutIdentity()
        {
            var fence = new CharacterSessionFence(); fence.Accept(CharacterSessionFenceTests.Snapshot("A", "a", 2));
            var delayed = new ServerMessage { type = "event", character_id = "A", character_session = "old",
                character_generation = 1, @event = new BackendEvent { type = "memory_view_page" } };
            Assert.That(fence.Accept(delayed), Is.False);
            Assert.That(fence.RejectionReason, Is.EqualTo("generation"));
            delayed.character_generation = 2;
            Assert.That(fence.Accept(delayed), Is.False);
            Assert.That(fence.RejectionReason, Is.EqualTo("session"));
        }

        private static MemoryViewItem Item(string text) => new MemoryViewItem {
            record_id = "record", lane = "v1", content = text, status = "current", editable = true, retirable = true };
        private static MemoryViewPage Page(string owner, string lane, string availability, params MemoryViewItem[] items)
            => new MemoryViewPage { character_id = owner, lane = lane, availability = availability, items = items };
        private static object Get(object target, string name) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(target);
        private static void Set(object target, string name, object value) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value);
        private static void Call(object target, string name, params object[] values) => target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, values);
    }
}
