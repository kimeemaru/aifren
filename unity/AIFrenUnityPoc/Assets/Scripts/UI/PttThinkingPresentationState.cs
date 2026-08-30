namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Tracks the dialogue placeholder owned by one PTT capture while it is
    /// waiting to either create a real assistant turn or end without one.
    /// </summary>
    internal sealed class PttThinkingPresentationState
    {
        private bool attemptActive;
        private bool releaseSeen;
        private bool transcriptionSeen;
        private bool noTurnOutcome;
        private bool placeholderActive;
        private string previousDialogue = string.Empty;

        public void BeginAttempt()
        {
            Reset();
            attemptActive = true;
        }

        public void MarkReleased()
        {
            if (attemptActive)
                releaseSeen = true;
        }

        public bool CaptureBeforeThinking(string visibleDialogue)
        {
            if (!attemptActive || !releaseSeen || placeholderActive)
                return false;

            previousDialogue = visibleDialogue ?? string.Empty;
            placeholderActive = true;
            return true;
        }

        public void MarkTranscription(string content, bool autoSubmit)
        {
            if (!attemptActive || !releaseSeen)
                return;

            transcriptionSeen = true;
            noTurnOutcome = !autoSubmit || string.IsNullOrWhiteSpace(content);
        }

        public void MarkVoiceFailure()
        {
            if (attemptActive && releaseSeen)
                noTurnOutcome = true;
        }

        public bool TryRestoreOnVoiceReady(out string dialogue)
        {
            dialogue = string.Empty;
            if (!attemptActive || !releaseSeen || !placeholderActive)
                return false;

            // A non-empty auto-submit transcription is followed by
            // turn_started. Its transient ready event must not roll the
            // legitimate generation placeholder back to the prior dialogue.
            if (transcriptionSeen && !noTurnOutcome)
                return false;

            dialogue = previousDialogue;
            Reset();
            return true;
        }

        public void MarkTurnStarted()
        {
            Reset();
        }

        private void Reset()
        {
            attemptActive = false;
            releaseSeen = false;
            transcriptionSeen = false;
            noTurnOutcome = false;
            placeholderActive = false;
            previousDialogue = string.Empty;
        }
    }
}
