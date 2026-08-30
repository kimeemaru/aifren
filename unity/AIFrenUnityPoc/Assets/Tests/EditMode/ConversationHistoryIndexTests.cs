using System;
using System.Collections.Generic;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ConversationHistoryIndexTests
    {
        [Test]
        public void TwoThousandMessagesIndexByYearMonthDayWithoutLifetimeRows()
        {
            List<ConversationMessage> messages = new List<ConversationMessage>();
            DateTimeOffset start = new DateTimeOffset(2024, 12, 15, 10, 0, 0, TimeSpan.Zero);
            for (int index = 0; index < 2000; index++)
            {
                DateTimeOffset timestamp = start.AddHours(index * 6);
                messages.Add(new ConversationMessage
                {
                    role = index % 2 == 0 ? "user" : "assistant",
                    content = index % 9 == 0 ? "First paragraph.\n\nSecond paragraph." : "Synthetic message " + index,
                    timestamp = timestamp.ToString("o"),
                });
            }
            messages.Insert(17, new ConversationMessage
            {
                role = "assistant", content = "   ", timestamp = start.ToString("o"),
            });

            ConversationHistoryIndex indexer = new ConversationHistoryIndex();
            indexer.Rebuild(messages);

            Assert.AreEqual(2000, indexer.RenderableCount);
            Assert.GreaterOrEqual(indexer.Years().Count, 2);
            Assert.IsTrue(indexer.TryLatestDay(out HistoryDayKey latest));
            HistoryMessagePage page = indexer.Page(latest, int.MaxValue, 80);
            Assert.LessOrEqual(page.Messages.Count, 80);
            Assert.LessOrEqual(page.PageIndex, Math.Max(0, page.PageCount - 1));
        }

        [Test]
        public void LargeSingleDayPagesAndPreservesIdenticalMessages()
        {
            ConversationHistoryIndex indexer = new ConversationHistoryIndex();
            for (int index = 0; index < 205; index++)
            {
                indexer.Append(new ConversationMessage
                {
                    role = "user", content = "Legitimate repeated message",
                    timestamp = "2026-08-28T12:00:00-04:00",
                });
            }
            Assert.IsTrue(indexer.TryLatestDay(out HistoryDayKey day));
            Assert.AreEqual(3, indexer.Page(day, 0, 80).PageCount);
            Assert.AreEqual(80, indexer.Page(day, 0, 80).Messages.Count);
            Assert.AreEqual(45, indexer.Page(day, 2, 80).Messages.Count);
            Assert.AreEqual(205, indexer.CountForDay(day));
        }
    }
}
