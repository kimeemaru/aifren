using System;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private readonly ViewRequestState historyViewRequest = new ViewRequestState();
        private CharacterSessionOwner historyRefreshOwner;
        private Button historyRefreshButton, memoryViewerRefreshButton;
        private TMP_Text historyViewStatus;
        private bool memoryRefreshAfterResync;
        private string historyModelRequestId, memoryModelRequestId, historyLastStatusText;

        private void RequestHistoryRefresh()
        {
            string id = historyViewRequest.Begin(Time.realtimeSinceStartup, true);
            if (id == null) return;
            historyRefreshOwner = client?.CharacterOwner;
            MarkView("history_requested", id, messages.Count);
            UpdateHistoryViewStatus();
            if (client == null) { historyViewRequest.Fail(id); UpdateHistoryViewStatus(); return; }
            if (client.State != ConnectionState.Connected)
            {
                // Existing connection handshake only; never restart/recreate a backend to refresh a view.
                if (client.State != ConnectionState.Connecting) _ = ConnectAsync();
                return;
            }
            _ = client.RequestSnapshotAsync(id);
        }

        private bool HandleViewSnapshot(ServerMessage message)
        {
            if (string.IsNullOrEmpty(message.request_id)) return false;
            if (!historyViewRequest.Matches(message.request_id))
            { MarkView("history_reject_request", message.request_id, 0); return true; }
            if (client != null && client.OwnsCharacter(historyRefreshOwner))
            {
                // Same binding: replace only the read model. No speech, framing, settings or state dispatch.
                ApplyHistoryView(message.data, true, message.request_id);
                ResumeMemoryRefresh();
                return true;
            }
            return false; // Only the transport's accepted authoritative handshake may bind a new owner.
        }

        private void ApplyHistoryView(SnapshotData snapshot, bool preserveNavigation, string requestId = null)
        {
            bool available = snapshot != null && !snapshot.storage_unavailable && snapshot.conversation != null;
            if (available)
            {
                messages.Clear(); canonicalMessageIds.Clear();
                messages.AddRange(snapshot.conversation);
                foreach (var message in snapshot.conversation)
                    if (message != null && !string.IsNullOrWhiteSpace(message.message_id))
                        canonicalMessageIds.Add(message.message_id);
                historyIndex.Rebuild(messages);
                if (!preserveNavigation || historyIndex.CountForDay(selectedHistoryDay) == 0)
                    SelectLatestHistoryDay();
                historyDirty = true;
            }
            historyModelRequestId = requestId ?? historyViewRequest.RequestId;
            client?.RetireSnapshotRequest(historyViewRequest.RequestId);
            historyViewRequest.Observe(messages.Count, available);
            MarkView(available ? "history_model" : "history_unavailable", historyModelRequestId, messages.Count);
            if (historyPanel == null || !historyPanel.activeInHierarchy)
                MarkView("history_deferred", historyModelRequestId, historyIndex.RenderableCount);
            UpdateHistoryViewStatus();
        }

        private void ResumeMemoryRefresh()
        {
            if (!memoryRefreshAfterResync) return;
            memoryRefreshAfterResync = false;
            RequestMemoryViewerPage();
        }

        private void RetireViewRequests()
        {
            client?.RetireSnapshotRequest(historyViewRequest.RequestId);
            historyViewRequest.Reset(true);
            historyRefreshOwner = null;
            memoryRefreshAfterResync = false;
            UpdateHistoryViewStatus();
        }

        private void ClearOutgoingViews()
        {
            RetireViewRequests();
            memoryViewerState.InvalidatePage();
            memoryViewerRetireConfirmation = false;
            RefreshMemoryViewerPage();
            messages.Clear(); canonicalMessageIds.Clear(); historyIndex.Rebuild(messages);
            historyDirty = true; UpdateHistoryViewStatus(); RefreshHistoryIfVisible();
        }

        private void CheckViewRequests(double now)
        {
            string historyId = historyViewRequest.RequestId;
            if (historyViewRequest.Expire(now))
            {
                client?.RetireSnapshotRequest(historyId);
                MarkView("history_timeout", historyId, messages.Count, true);
                UpdateHistoryViewStatus();
                if (memoryRefreshAfterResync)
                { memoryRefreshAfterResync = false; memoryViewerState.PageRequest.Observe(0, false); UpdateMemoryViewStatus(); }
            }
            string pageId = memoryViewerState.PendingRequestId;
            string detailId = memoryViewerState.PendingDetailRequestId;
            if (memoryViewerState.PageRequest.Expire(now))
            { MarkView("memory_timeout", pageId, 0, true); UpdateMemoryViewStatus(); }
            if (memoryViewerState.DetailRequest.Expire(now))
            { MarkView("detail_timeout", detailId, 0, true); UpdateMemoryViewStatus(); }
            if (client != null && (client.State == ConnectionState.Disconnected || client.State == ConnectionState.Error))
            {
                if (historyViewRequest.Fail(historyId))
                {
                    client.RetireSnapshotRequest(historyId); UpdateHistoryViewStatus();
                    if (memoryRefreshAfterResync)
                    { memoryRefreshAfterResync = false; memoryViewerState.PageRequest.Observe(0, false); UpdateMemoryViewStatus(); }
                }
                bool failed = memoryViewerState.PageRequest.Fail(pageId);
                failed |= memoryViewerState.DetailRequest.Fail(detailId);
                if (failed) UpdateMemoryViewStatus();
            }
        }

        private void UpdateHistoryViewStatus()
        {
            string status = historyViewRequest.Describe("history", messages.Count);
            if (string.IsNullOrEmpty(status)) status = messages.Count == 0
                ? "No conversation messages in this timeline."
                : messages.Count + " conversation messages · " + historyIndex.RenderableCount + " displayable";
            if (status != historyLastStatusText && historyIndex.RenderableCount == 0) historyDirty = true;
            historyLastStatusText = status;
            if (historyViewStatus != null) historyViewStatus.text = status;
            if (historyRefreshButton != null) SetTopControlLabel(historyRefreshButton, historyViewRequest.Failed ? "Retry" : "Refresh");
            if (historyRefreshButton != null) historyRefreshButton.interactable = !historyViewRequest.Pending;
        }

        private void UpdateMemoryViewStatus()
        {
            var state = memoryViewerState;
            int count = state.Page?.items?.Length ?? 0;
            string status = state.PageRequest.Describe(MemoryViewerState.LaneLabel(state.Lane), count);
            if (string.IsNullOrEmpty(status))
            {
                status = count > 0 ? count + (count == 1 ? " record on this page." : " records on this page.")
                    : "No matches in " + MemoryViewerState.LaneLabel(state.Lane) + " with these filters.";
                if (state.Lane == "v1") status += " This archive does not represent V2 memory.";
                if (!string.IsNullOrEmpty(state.Page?.warning)) status += " " + state.Page.warning;
            }
            if (state.DetailRequest.Pending || state.DetailRequest.Failed)
                status += " " + state.DetailRequest.Describe("record detail", 0);
            SetMemoryViewerWarning(status);
            if (memoryViewerRefreshButton != null) SetTopControlLabel(memoryViewerRefreshButton,
                state.PageRequest.Failed || state.DetailRequest.Failed ? "Retry"
                : memoryViewerSearchInput != null && memoryViewerSearchInput.text != state.Query ? "Search" : "Refresh");
            if (memoryViewerRefreshButton != null) memoryViewerRefreshButton.interactable = !state.PageRequest.Pending && !memoryRefreshAfterResync;
            bool editable = client?.CharacterOwner != null && !characterSwitchInFlight
                && !state.PageRequest.Pending && !state.PageRequest.Failed;
            if (memoryViewerSaveButton != null) memoryViewerSaveButton.interactable = editable && state.Selected?.editable == true;
            if (memoryViewerRetireButton != null) memoryViewerRetireButton.interactable = editable && state.Selected?.retirable == true;
        }

        private bool HandleViewError(CommandError error)
        {
            if (error == null) return false;
            if (historyViewRequest.Fail(error.request_id))
            {
                client?.RetireSnapshotRequest(error.request_id); UpdateHistoryViewStatus();
                if (memoryRefreshAfterResync)
                { memoryRefreshAfterResync = false; memoryViewerState.PageRequest.Observe(0, false); UpdateMemoryViewStatus(); }
                return true;
            }
            bool page = memoryViewerState.PageRequest.Fail(error.request_id);
            bool detail = memoryViewerState.DetailRequest.Fail(error.request_id);
            if (page || detail) { UpdateMemoryViewStatus(); return true; }
            // Superseded read failures must not replace a newer status.
            return error.code != null && error.code.Contains("memory_view");
        }

        private void MarkView(string stage, string requestId, int count, bool incident = false)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFlightRecorder?.MarkView(stage, requestId, count);
            if (incident) developmentFlightRecorder?.AutomaticTrigger("view_request_timeout");
#endif
        }
    }
}
