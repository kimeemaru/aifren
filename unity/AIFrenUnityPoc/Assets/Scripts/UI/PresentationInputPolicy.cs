namespace AIFren.UnityPoc.UI
{
    /// <summary>Routes global shortcuts around overlays and the active text field.</summary>
    public static class PresentationInputPolicy
    {
        public static bool CanOpenInput(bool overlayOpen, bool inputOpen)
        {
            return !overlayOpen && !inputOpen;
        }

        public static bool ShouldDismissEmptyInput(bool inputOpen, bool hasText)
        {
            return inputOpen && !hasText;
        }
    }
}
