namespace AIFren.UnityPoc.UI
{
    // Response-only presentation identity, downstream of backend turn authority.
    // Never queues metadata across reconnects, snapshots or asset/character retirement.
    internal sealed class ResponsePresentationTurn
    {
        private int current;
        private bool retired = true, published;
        internal void Begin(int turn) { current = turn; retired = false; published = false; }
        internal bool TryPublish(int turn)
        {
            if (retired || published || turn != current) return false;
            published = true; return true;
        }
        internal bool PublishFinal(int turn, Avatar.AvatarPresentationResolver resolver,
            Protocol.PresentationMetadata metadata, string dialogue, string selfName)
        {
            if (!TryPublish(turn)) return false;
            resolver?.ApplyDialogueReply(metadata, DialoguePresentationParser.ParseDocument(dialogue), selfName);
            return true;
        }
        internal bool Retire(int turn)
        {
            if (turn != current || retired) return false;
            Reset(); return true;
        }
        internal void Reset() { retired = true; }
    }
}
