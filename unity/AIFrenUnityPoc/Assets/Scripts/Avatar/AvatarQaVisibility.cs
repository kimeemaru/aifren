using System;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>Single visibility owner for hidden avatar QA overlays.</summary>
    public static class AvatarQaVisibility
    {
        // Development builds retain the hidden 7777777 toggle, but QA frames
        // must never cover the companion until a developer asks for them.
        public static bool Visible { get; private set; } = false;
        public static event Action Changed;
        public static void Toggle()
        {
            Visible = !Visible;
            Changed?.Invoke();
        }
    }
}
