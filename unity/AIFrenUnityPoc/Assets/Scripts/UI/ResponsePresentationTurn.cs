namespace AIFren.UnityPoc.UI
{
    // Response-only presentation identity, downstream of backend turn authority.
    // Never queues metadata across reconnects, snapshots or asset/character retirement.
    internal sealed class ResponsePresentationTurn
    {
        private int current;
        private bool retired = true, published, automaticPending;
        private Avatar.AvatarPresentationResolver automaticResolver;
        private Avatar.AutomaticExpressionLease automaticLease;
        internal void Begin(int turn) { if (!retired && current == turn) return; current = turn; retired = false; published = false; automaticPending = false; }
        internal bool TryPublish(int turn)
        {
            if (retired || published || turn != current) return false;
            published = true; return true;
        }
        internal bool PublishFinal(int turn, Avatar.AvatarPresentationResolver resolver,
            Protocol.PresentationMetadata metadata, string dialogue, string selfName, bool automaticExpressionPending = false)
        {
            if (!TryPublish(turn)) return false;
            CancelAutomatic(); // The newly accepted reply retires only the previous optional face.
            automaticResolver = resolver;
            automaticLease = resolver != null ? resolver.BeginAutomaticExpressionLease() : null;
            // Explicit provider requests own the face even when this avatar lacks
            // that preset. A reserved automatic proposal never replays the body.
            automaticPending = automaticExpressionPending && string.IsNullOrWhiteSpace(metadata?.emotion);
            resolver?.ApplyDialogueReply(metadata, DialoguePresentationParser.ParseDocument(dialogue), selfName, automaticPending);
            return true;
        }
        internal bool PublishAutomatic(int turn, Avatar.AvatarPresentationResolver resolver,
            Protocol.PresentationMetadata metadata)
        {
            if (retired || !published || !automaticPending || turn != current) return false;
            automaticPending = false; // Uncertainty/unsupported proposals spend this one opportunity too.
            return resolver != null && ReferenceEquals(resolver, automaticResolver)
                && resolver.ApplyAutomaticExpression(metadata, automaticLease);
        }
        // Disabling optional faces or stopping speech closes the pending
        // opportunity and releases its layer, preserving semantic/manual/base owners.
        internal void CancelAutomatic()
        {
            automaticPending = false;
            automaticResolver?.RetireAutomaticExpression(automaticLease);
            automaticResolver = null; automaticLease = null;
        }
        internal bool FinishSpeech(int turn, bool interrupted = false)
        {
            if (retired || !published || turn != current) return false;
            if (interrupted) { CancelAutomatic(); return true; }
            automaticPending = false;
            return automaticResolver != null && automaticResolver.FinishAutomaticExpression(automaticLease);
        }
        internal bool Retire(int turn)
        {
            if (turn != current || retired) return false;
            Reset(); return true;
        }
        internal void Reset() { retired = true; CancelAutomatic(); }
    }
}
