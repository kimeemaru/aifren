using System;
using AIFren.UnityPoc.Protocol;

namespace AIFren.UnityPoc.UI
{
    /// <summary>Character-scoped paging/selection reducer for the Memory Viewer.</summary>
    public sealed class MemoryViewerState
    {
        public const int PageSize = 20;
        public const int DetailSize = 12;
        private static readonly string[] Lanes = { "v1", "v2_claims", "episodes", "open_threads" };
        private static readonly string[] StatusFilters = { "current", "historical", "all" };
        private static readonly string[] ScopeFilters = { "applicable", "all" };

        public string CharacterId { get; private set; } = string.Empty;
        public string Lane { get; private set; } = "v1";
        public string Query { get; set; } = string.Empty;
        public string StatusFilter { get; private set; } = "current";
        public string ScopeFilter { get; private set; } = "applicable";
        public int Offset { get; private set; }
        public readonly ViewRequestState PageRequest = new ViewRequestState();
        public readonly ViewRequestState DetailRequest = new ViewRequestState();
        public string PendingRequestId => PageRequest.RequestId;
        public bool SelectionPreserved { get; private set; }
        public string PendingDetailRequestId => DetailRequest.RequestId;
        public MemoryViewPage Page { get; private set; }
        public MemoryViewItem Selected { get; private set; }
        public MemoryViewDetail Detail { get; private set; }

        public void ChangeCharacter(string characterId)
        {
            string normalized = characterId ?? string.Empty;
            if (string.Equals(CharacterId, normalized, StringComparison.Ordinal)) return;
            CharacterId = normalized;
            InvalidatePage();
        }

        public void InvalidatePage()
        {
            Offset = 0;
            PageRequest.Reset();
            Page = null;
            ClearSelection();
        }

        public string BeginRequest(double now = 0, bool coalesce = false)
        {
            SelectionPreserved = false;
            return PageRequest.Begin(now, coalesce);
        }

        public bool Accept(string requestId, MemoryViewPage page)
        {
            if (page == null || string.IsNullOrWhiteSpace(requestId)
                || !string.Equals(requestId, PendingRequestId, StringComparison.Ordinal)
                || !string.Equals(page.character_id, CharacterId, StringComparison.Ordinal)
                || !string.Equals(page.lane, Lane, StringComparison.Ordinal))
                return false;
            if (page.items != null && page.items.Length > PageSize)
            {
                MemoryViewItem[] bounded = new MemoryViewItem[PageSize];
                Array.Copy(page.items, bounded, PageSize);
                page.items = bounded;
                page.has_more = true;
            }
            bool available = page.availability == "ready" && page.items != null;
            PageRequest.Complete(requestId, page.items?.Length ?? 0, available);
            SelectionPreserved = false;
            if (!available) return true; // Keep a same-owner last successful page, labelled stale.
            MemoryViewItem previous = Selected;
            Page = page;
            if (previous != null && page.items != null)
                foreach (var item in page.items)
                    if (SameRecord(previous, item))
                    { Selected = item; SelectionPreserved = true; break; }
            if (!SelectionPreserved) ClearSelection();
            return true;
        }

        public string BeginDetailRequest(double now = 0)
        {
            Detail = null;
            return DetailRequest.Begin(now);
        }

        public bool AcceptDetail(string requestId, MemoryViewDetail detail)
        {
            if (detail == null || Selected == null || string.IsNullOrWhiteSpace(requestId)
                || !string.Equals(requestId, PendingDetailRequestId, StringComparison.Ordinal)
                || !string.Equals(detail.character_id, CharacterId, StringComparison.Ordinal)
                || !string.Equals(detail.lane, Lane, StringComparison.Ordinal)
                || !string.Equals(detail.record_id, Selected.record_id, StringComparison.Ordinal))
                return false;
            if (detail.detail != null)
            {
                bool truncated = Bound(ref detail.detail.evidence, DetailSize);
                truncated |= Bound(ref detail.detail.status_history, DetailSize);
                truncated |= Bound(ref detail.detail.relations, DetailSize);
                truncated |= Bound(ref detail.detail.lower_episode_ids, 8);
                if (truncated) detail.has_more = true;
            }
            DetailRequest.Complete(requestId, 1, detail.availability == "ready");
            if (!DetailRequest.Failed) Detail = detail;
            return true;
        }

        public void ClearSelection()
        {
            SelectionPreserved = false;
            Selected = null;
            Detail = null;
            DetailRequest.Reset();
        }

        public void CycleLane()
        {
            Lane = Next(Lanes, Lane);
            InvalidatePage();
        }

        public void CycleStatus()
        {
            StatusFilter = Next(StatusFilters, StatusFilter);
            InvalidatePage();
        }

        public void CycleScope()
        {
            ScopeFilter = Next(ScopeFilters, ScopeFilter);
            InvalidatePage();
        }

        public bool PreviousPage()
        {
            if (Offset <= 0) return false;
            Offset = Math.Max(0, Offset - PageSize);
            Page = null; PageRequest.Reset(); ClearSelection();
            return true;
        }

        public bool NextPage()
        {
            if (Page == null || !Page.has_more) return false;
            Offset += PageSize;
            Page = null; PageRequest.Reset(); ClearSelection();
            return true;
        }

        public bool Select(MemoryViewItem item)
        {
            if (item == null || Page == null || Page.items == null) return false;
            foreach (MemoryViewItem candidate in Page.items)
            {
                if (candidate != null && ReferenceEquals(candidate, item))
                {
                    Selected = candidate;
                    Detail = null;
                    DetailRequest.Reset();
                    return true;
                }
            }
            return false;
        }

        public static string LaneLabel(string lane)
        {
            switch (lane)
            {
                case "v1": return "V1 Archive";
                case "v2_claims": return "V2 Facts / Claims";
                case "episodes": return "V2 Episodes";
                case "open_threads": return "Open Threads";
                default: return "Memory";
            }
        }

        public static string ItemLabel(MemoryViewItem item)
        {
            if (item == null) return string.Empty;
            string content = MemoryViewerTextExtensions.JoinSingleLine(item.content);
            if (content.Length > 74) content = content.Substring(0, 71) + "…";
            string status = string.IsNullOrWhiteSpace(item.status) ? "unknown" : item.status;
            return "[" + status + "] " + content;
        }

        private static bool SameRecord(MemoryViewItem a, MemoryViewItem b)
        {
            return b != null && a.record_id == b.record_id && a.lane == b.lane
                && a.content == b.content && a.updated_at == b.updated_at && a.status == b.status
                && a.category == b.category && a.importance == b.importance && a.scope == b.scope
                && a.editable == b.editable && a.retirable == b.retirable;
        }

        private static string Next(string[] values, string current)
        {
            int index = Array.IndexOf(values, current);
            return values[(index + 1 + values.Length) % values.Length];
        }

        private static bool Bound<T>(ref T[] values, int limit)
        {
            if (values == null || values.Length <= limit) return false;
            T[] bounded = new T[limit];
            Array.Copy(values, bounded, limit);
            values = bounded;
            return true;
        }
    }

    internal static class MemoryViewerTextExtensions
    {
        internal static string JoinSingleLine(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "(empty)";
            return string.Join(" ", value.Split(
                new[] { ' ', '\t', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries));
        }
    }
}
