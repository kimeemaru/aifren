namespace AIFren.UnityPoc.UI
{
    /// <summary>Small, platform-neutral safeguards for focused PTT input.</summary>
    public static class PresentationPttInputPolicy
    {
        public static bool ShouldStart(bool applicationFocused, bool keyDown)
        {
            return applicationFocused && keyDown;
        }

        public static bool ShouldRelease(bool pttPressed, bool applicationFocused, bool keyHeld)
        {
            return pttPressed && (!applicationFocused || !keyHeld);
        }
    }
}
