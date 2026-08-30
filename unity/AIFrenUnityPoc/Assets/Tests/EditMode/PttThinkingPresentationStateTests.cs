using NUnit.Framework;
using AIFren.UnityPoc.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PttThinkingPresentationStateTests
    {
        [Test]
        public void SuccessfulPttKeepsThinkingUntilAssistantTurnStarts()
        {
            PttThinkingPresentationState state = ReleasedAttempt("Previous answer");
            state.MarkTranscription("new request", true);

            Assert.IsFalse(state.TryRestoreOnVoiceReady(out _));

            state.MarkTurnStarted();
            Assert.IsFalse(state.TryRestoreOnVoiceReady(out _));
        }

        [Test]
        public void RecoveredFailureBeforeTranscriptionRestoresPreviousDialogue()
        {
            PttThinkingPresentationState state = ReleasedAttempt("Previous answer");
            state.MarkVoiceFailure();

            Assert.IsTrue(state.TryRestoreOnVoiceReady(out string restored));
            Assert.AreEqual("Previous answer", restored);
        }

        [Test]
        public void EmptyTranscriptionRestoresPreviousDialogueWithoutCreatingTurn()
        {
            PttThinkingPresentationState state = ReleasedAttempt("Previous answer");
            state.MarkTranscription(string.Empty, true);

            Assert.IsTrue(state.TryRestoreOnVoiceReady(out string restored));
            Assert.AreEqual("Previous answer", restored);
        }

        [Test]
        public void FailureWithoutPreviousDialogueClearsPlaceholder()
        {
            PttThinkingPresentationState state = ReleasedAttempt(string.Empty);
            state.MarkVoiceFailure();

            Assert.IsTrue(state.TryRestoreOnVoiceReady(out string restored));
            Assert.AreEqual(string.Empty, restored);
        }

        [Test]
        public void ReviewModeTranscriptionRestoresDialogueInsteadOfWaitingForTurn()
        {
            PttThinkingPresentationState state = ReleasedAttempt("Previous answer");
            state.MarkTranscription("review this", false);

            Assert.IsTrue(state.TryRestoreOnVoiceReady(out string restored));
            Assert.AreEqual("Previous answer", restored);
        }

        [Test]
        public void GenericReadyAndLateOldCleanupCannotRollbackNewerDialogue()
        {
            PttThinkingPresentationState state = ReleasedAttempt("Old answer");
            state.MarkTranscription("new request", true);
            state.MarkTurnStarted();

            state.MarkVoiceFailure();
            Assert.IsFalse(state.TryRestoreOnVoiceReady(out _));
        }

        private static PttThinkingPresentationState ReleasedAttempt(string dialogue)
        {
            PttThinkingPresentationState state = new PttThinkingPresentationState();
            state.BeginAttempt();
            state.MarkReleased();
            Assert.IsTrue(state.CaptureBeforeThinking(dialogue));
            return state;
        }
    }
}
