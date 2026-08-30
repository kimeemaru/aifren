using System;
using System.Collections.Generic;
using System.Linq;
using AIFren.UnityPoc.Protocol;

namespace AIFren.UnityPoc.UI
{
    public readonly struct HistoryDayKey : IEquatable<HistoryDayKey>
    {
        public readonly int Year;
        public readonly int Month;
        public readonly int Day;

        public HistoryDayKey(int year, int month, int day)
        {
            Year = year;
            Month = month;
            Day = day;
        }

        public bool IsDated => Year > 0 && Month > 0 && Day > 0;
        public bool Equals(HistoryDayKey other) =>
            Year == other.Year && Month == other.Month && Day == other.Day;
        public override bool Equals(object obj) => obj is HistoryDayKey other && Equals(other);
        public override int GetHashCode() => (Year * 397 ^ Month) * 397 ^ Day;
        public override string ToString() => IsDated
            ? new DateTime(Year, Month, Day).ToString("yyyy-MM-dd")
            : "undated";
    }

    public sealed class HistoryMessagePage
    {
        public HistoryDayKey Day { get; }
        public int PageIndex { get; }
        public int PageCount { get; }
        public int TotalMessages { get; }
        public IReadOnlyList<ConversationMessage> Messages { get; }

        public HistoryMessagePage(
            HistoryDayKey day, int pageIndex, int pageCount, int totalMessages,
            IReadOnlyList<ConversationMessage> messages)
        {
            Day = day;
            PageIndex = pageIndex;
            PageCount = pageCount;
            TotalMessages = totalMessages;
            Messages = messages;
        }
    }

    /// <summary>
    /// Lightweight date index over canonical message references. It creates
    /// no Unity objects and skips only non-renderable derived-view records.
    /// </summary>
    public sealed class ConversationHistoryIndex
    {
        private static readonly HistoryDayKey Undated = new HistoryDayKey(0, 0, 0);
        private readonly Dictionary<HistoryDayKey, List<ConversationMessage>> byDay =
            new Dictionary<HistoryDayKey, List<ConversationMessage>>();
        private int renderableCount;

        public int RenderableCount => renderableCount;
        public int DayCount => byDay.Count;

        public void Rebuild(IReadOnlyList<ConversationMessage> messages)
        {
            byDay.Clear();
            renderableCount = 0;
            if (messages == null) return;
            for (int index = 0; index < messages.Count; index++) Append(messages[index]);
        }

        public bool Append(ConversationMessage message)
        {
            if (message == null || string.IsNullOrWhiteSpace(message.content)) return false;
            HistoryDayKey key = DayKey(message.timestamp);
            if (!byDay.TryGetValue(key, out List<ConversationMessage> rows))
            {
                rows = new List<ConversationMessage>();
                byDay[key] = rows;
            }
            rows.Add(message);
            renderableCount++;
            return true;
        }

        public IReadOnlyList<int> Years()
        {
            List<int> values = byDay.Keys.Where(item => item.IsDated)
                .Select(item => item.Year).Distinct().OrderByDescending(item => item).ToList();
            if (byDay.ContainsKey(Undated)) values.Add(0);
            return values;
        }

        public IReadOnlyList<int> Months(int year)
        {
            if (year <= 0) return Array.Empty<int>();
            return byDay.Keys.Where(item => item.Year == year)
                .Select(item => item.Month).Distinct().OrderByDescending(item => item).ToArray();
        }

        public IReadOnlyList<HistoryDayKey> Days(int year, int month)
        {
            if (year <= 0) return byDay.ContainsKey(Undated)
                ? new[] { Undated } : Array.Empty<HistoryDayKey>();
            return byDay.Keys.Where(item => item.Year == year && item.Month == month)
                .OrderByDescending(item => item.Day).ToArray();
        }

        public int CountForYear(int year) => byDay
            .Where(item => year <= 0 ? !item.Key.IsDated : item.Key.Year == year)
            .Sum(item => item.Value.Count);

        public int CountForMonth(int year, int month) => byDay
            .Where(item => item.Key.Year == year && item.Key.Month == month)
            .Sum(item => item.Value.Count);

        public int CountForDay(HistoryDayKey day) =>
            byDay.TryGetValue(day, out List<ConversationMessage> rows) ? rows.Count : 0;

        public bool TryLatestDay(out HistoryDayKey day)
        {
            HistoryDayKey[] dated = byDay.Keys.Where(item => item.IsDated)
                .OrderByDescending(item => item.Year)
                .ThenByDescending(item => item.Month)
                .ThenByDescending(item => item.Day).ToArray();
            if (dated.Length > 0)
            {
                day = dated[0];
                return true;
            }
            if (byDay.ContainsKey(Undated))
            {
                day = Undated;
                return true;
            }
            day = default;
            return false;
        }

        public HistoryMessagePage Page(HistoryDayKey day, int pageIndex, int pageSize)
        {
            pageSize = Math.Max(1, Math.Min(100, pageSize));
            if (!byDay.TryGetValue(day, out List<ConversationMessage> rows) || rows.Count == 0)
                return new HistoryMessagePage(day, 0, 0, 0, Array.Empty<ConversationMessage>());
            int pageCount = (rows.Count + pageSize - 1) / pageSize;
            int selected = Math.Max(0, Math.Min(pageCount - 1, pageIndex));
            int start = selected * pageSize;
            int count = Math.Min(pageSize, rows.Count - start);
            return new HistoryMessagePage(
                day, selected, pageCount, rows.Count, rows.GetRange(start, count));
        }

        private static HistoryDayKey DayKey(string timestamp)
        {
            return PresentationHistoryTime.TryGetLocalTime(timestamp, out DateTime local)
                ? new HistoryDayKey(local.Year, local.Month, local.Day)
                : Undated;
        }
    }
}
