using System;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public static class PresentationPttBinding
    {
        // Mouse4 is the intended companion-client default.  F8 remains a
        // valid explicit binding for compatibility, but is not the fallback.
        public const KeyCode DefaultKey = KeyCode.Mouse4;

        public static bool IsValid(KeyCode key) => key != KeyCode.None && key != KeyCode.Escape;

        public static KeyCode Load(string serialized)
        {
            return Enum.TryParse(serialized, out KeyCode key) && IsValid(key) ? key : DefaultKey;
        }

        public static string Save(KeyCode key) => IsValid(key) ? key.ToString() : DefaultKey.ToString();
    }
}
