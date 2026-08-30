using System;
using System.Collections.Generic;

namespace AIFren.UnityPoc.UI
{
    /// <summary>Identity-only admission for snapshot/event conversation projection.</summary>
    internal static class CanonicalMessageProjection
    {
        internal static bool TryAdmit(ISet<string> knownIds, string messageId)
        {
            if (string.IsNullOrWhiteSpace(messageId)) return true;
            return knownIds.Add(messageId);
        }
    }
}
