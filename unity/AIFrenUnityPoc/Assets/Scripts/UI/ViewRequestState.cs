using System;

namespace AIFren.UnityPoc.UI
{
    public enum ViewLoadStatus { Idle, Loading, Ready, Empty, Unavailable, TimedOut }

    /// <summary>A bounded read request, never a storage or character authority.</summary>
    public sealed class ViewRequestState
    {
        public const double DeadlineSeconds = 15;
        public string RequestId { get; private set; } = string.Empty;
        public ViewLoadStatus Status { get; private set; }
        public bool Pending => !string.IsNullOrEmpty(RequestId);
        public bool Failed => Status == ViewLoadStatus.Unavailable || Status == ViewLoadStatus.TimedOut;
        private double deadline;

        public string Begin(double now, bool coalesce = false)
        {
            if (coalesce && Pending) return null;
            RequestId = Guid.NewGuid().ToString();
            deadline = now + DeadlineSeconds;
            Status = ViewLoadStatus.Loading;
            return RequestId;
        }

        public bool Matches(string id) => Pending && !string.IsNullOrEmpty(id) && id == RequestId;
        public bool Complete(string id, int count, bool available)
        {
            if (!Matches(id)) return false;
            Observe(count, available);
            return true;
        }
        public void Observe(int count, bool available)
        {
            RequestId = string.Empty;
            Status = !available ? ViewLoadStatus.Unavailable : count > 0 ? ViewLoadStatus.Ready : ViewLoadStatus.Empty;
        }
        public bool Fail(string id)
        {
            if (!Matches(id)) return false;
            Observe(0, false);
            return true;
        }
        public bool Expire(double now)
        {
            if (!Pending || now < deadline) return false;
            RequestId = string.Empty;
            Status = ViewLoadStatus.TimedOut;
            return true;
        }
        public void Reset(bool loading = false)
        {
            RequestId = string.Empty;
            Status = loading ? ViewLoadStatus.Loading : ViewLoadStatus.Idle;
        }
        public string Describe(string view, int retainedCount)
        {
            string retained = retainedCount > 0 ? " Showing the last loaded view." : string.Empty;
            switch (Status)
            {
                case ViewLoadStatus.Loading: return (retainedCount > 0 ? "Refreshing " : "Loading ") + view + "…" + retained;
                case ViewLoadStatus.Unavailable: return "Couldn't load " + view + ". Retry to refresh." + retained;
                case ViewLoadStatus.TimedOut: return "Loading " + view + " timed out. Retry to refresh." + retained;
                case ViewLoadStatus.Idle: return "Refresh to load " + view + ".";
                default: return string.Empty;
            }
        }
    }
}
