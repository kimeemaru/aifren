namespace AIFren.UnityPoc.UI
{
    /// <summary>Small, platform-neutral safeguards for Unity-delivered PTT input.</summary>
    public static class PresentationPttInputPolicy
    {
        public static bool ShouldStart(bool keyDown)
        {
            return keyDown;
        }

        public static bool ShouldRelease(bool pttPressed, bool keyHeld)
        {
            return pttPressed && !keyHeld;
        }
    }
}
