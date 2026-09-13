using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    [Serializable]
    public struct AvatarPresentationValues
    {
        public float x;
        public float y;
        public float scale;
    }

    /// <summary>Presentation-only transforms, owned by character, visual asset and layout.</summary>
    public sealed class AvatarPresentationState
    {
        private const string PortraitXKey = "AIFren.AvatarPresentation.Portrait.X";
        private const string PortraitYKey = "AIFren.AvatarPresentation.Portrait.Y";
        private const string PortraitScaleKey = "AIFren.AvatarPresentation.Portrait.Scale";
        private const string LandscapeXKey = "AIFren.AvatarPresentation.Landscape.X";
        private const string LandscapeYKey = "AIFren.AvatarPresentation.Landscape.Y";
        private const string LandscapeScaleKey = "AIFren.AvatarPresentation.Landscape.Scale";
        private const string CharacterKeyPrefix = "AIFren.CharacterPresentation.v1.";
        private const int MaximumDocumentChars = 262144; // Corrupt-input work bound, not a character/asset policy.

        [Serializable]
        private sealed class CharacterDocument
        {
            public int version = 1;
            public List<AssetValues> avatars = new List<AssetValues>();
        }

        [Serializable]
        private sealed class AssetValues
        {
            public string asset;
            public bool hasPortrait, hasLandscape;
            public AvatarPresentationValues portrait, landscape;
        }

        private readonly AvatarConfiguration configuration;
        private readonly bool legacyGlobal;
        public string CharacterId { get; private set; }
        public string AssetIdentity { get; private set; }
        public bool IsCharacterScoped => !string.IsNullOrEmpty(CharacterId);
        private AvatarPresentationValues portrait;
        private AvatarPresentationValues landscape;

        private AvatarPresentationState(AvatarConfiguration configuration, bool legacyGlobal = false,
            string characterId = null, string assetIdentity = null)
        {
            this.configuration = configuration ?? new AvatarConfiguration();
            this.legacyGlobal = legacyGlobal;
            CharacterId = characterId;
            AssetIdentity = assetIdentity;
            portrait = legacyGlobal ? LoadValues(true) : AuthoredValues(true);
            landscape = legacyGlobal ? LoadValues(false) : AuthoredValues(false);
            if (IsCharacterScoped && TryReadDocument(CharacterId, out CharacterDocument document))
            {
                AssetValues stored = document.avatars.Find(item => item.asset == AssetIdentity);
                if (stored != null)
                {
                    if (stored.hasPortrait) portrait = Normalize(stored.portrait);
                    if (stored.hasLandscape) landscape = Normalize(stored.landscape);
                }
            }
        }

        // Legacy compatibility only. Normal character startup does not attribute
        // these old global values to an arbitrary character or avatar.
        public static AvatarPresentationState Load(AvatarConfiguration configuration) => new AvatarPresentationState(configuration, true);

        public static AvatarPresentationState CreateUnbound(AvatarConfiguration configuration) => new AvatarPresentationState(configuration);

        public static AvatarPresentationState LoadForCharacter(AvatarConfiguration configuration, string characterId, string assetIdentity)
        {
            if (!Guid.TryParse(characterId, out Guid id)) throw new ArgumentException("Character ID must be a UUID.", nameof(characterId));
            if (!ValidAssetIdentity(assetIdentity)) throw new ArgumentException("Avatar identity is invalid.", nameof(assetIdentity));
            return new AvatarPresentationState(configuration, false, id.ToString("D"), assetIdentity.ToLowerInvariant());
        }

        public static string ManagedAssetIdentity(string contentId)
        {
            string value = "managed:" + (contentId ?? string.Empty).ToLowerInvariant();
            if (!ValidAssetIdentity(value)) throw new ArgumentException("Managed avatar identity is invalid.", nameof(contentId));
            return value;
        }

        public static string BundledAssetIdentity(AvatarConfiguration configuration)
        {
            // Resource identity is stable across imports/restarts, without storing
            // local paths or sharing mutable framing on the VRM itself.
            string resource = (configuration ?? new AvatarConfiguration()).avatarResourcePath ?? string.Empty;
            using (SHA256 sha = SHA256.Create())
                return "bundled:" + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(resource))).Replace("-", string.Empty).ToLowerInvariant();
        }

        public static void DeleteCharacter(string characterId)
        {
            if (!Guid.TryParse(characterId, out Guid id)) return;
            PlayerPrefs.DeleteKey(CharacterKeyPrefix + id.ToString("D"));
            PlayerPrefs.Save();
        }

        public static void DeletePersistedValues()
        {
            foreach (bool isPortrait in new[] { true, false })
            {
                PlayerPrefs.DeleteKey(Key(isPortrait, 'x'));
                PlayerPrefs.DeleteKey(Key(isPortrait, 'y'));
                PlayerPrefs.DeleteKey(Key(isPortrait, 's'));
            }
            PlayerPrefs.Save();
        }

        public AvatarPresentationValues GetValues(bool isPortrait) => isPortrait ? portrait : landscape;

        public void SetValues(bool isPortrait, AvatarPresentationValues values, bool persist)
        {
            values = Normalize(values);
            if (isPortrait) portrait = values; else landscape = values;
            if (persist) Commit(isPortrait);
        }

        public void Reset(bool isPortrait, bool persist)
        {
            SetValues(isPortrait, AuthoredValues(isPortrait), persist);
        }

        public void Commit(bool isPortrait)
        {
            AvatarPresentationValues values = GetValues(isPortrait);
            if (IsCharacterScoped)
            {
                // Re-read at Save so another asset/layout's newer saved values
                // survive an open editor. Unknown/corrupt records are not replaced.
                if (!TryReadDocument(CharacterId, out CharacterDocument document))
                    throw new InvalidOperationException("Saved character framing is unavailable; existing values were preserved.");
                AssetValues stored = document.avatars.Find(item => item.asset == AssetIdentity);
                if (stored == null) { stored = new AssetValues { asset = AssetIdentity }; document.avatars.Add(stored); }
                if (isPortrait) { stored.portrait = values; stored.hasPortrait = true; }
                else { stored.landscape = values; stored.hasLandscape = true; }
                string json = JsonUtility.ToJson(document);
                if (json.Length > MaximumDocumentChars) throw new InvalidOperationException("Saved character framing exceeds the supported storage size.");
                PlayerPrefs.SetString(CharacterKeyPrefix + CharacterId, json);
                PlayerPrefs.Save();
                return;
            }
            if (!legacyGlobal) throw new InvalidOperationException("Choose a character and wait for its avatar before saving framing.");
            PlayerPrefs.SetFloat(Key(isPortrait, 'x'), values.x);
            PlayerPrefs.SetFloat(Key(isPortrait, 'y'), values.y);
            PlayerPrefs.SetFloat(Key(isPortrait, 's'), values.scale);
            PlayerPrefs.Save();
        }

        private AvatarPresentationValues AuthoredValues(bool isPortrait)
        {
            AvatarPresentationTransform authored = configuration.PresentationTransform(isPortrait);
            return Normalize(new AvatarPresentationValues { x = authored != null ? authored.x : 0f,
                y = authored != null ? authored.y : 0f, scale = authored != null ? authored.scale : 1f });
        }

        private static bool TryReadDocument(string characterId, out CharacterDocument document)
        {
            string json = PlayerPrefs.GetString(CharacterKeyPrefix + characterId, string.Empty);
            document = null;
            if (string.IsNullOrEmpty(json)) { document = new CharacterDocument(); return true; }
            if (json.Length > MaximumDocumentChars) return false;
            try
            {
                document = JsonUtility.FromJson<CharacterDocument>(json);
                if (document == null || document.version != 1 || document.avatars == null) return false;
                var seen = new HashSet<string>(StringComparer.Ordinal);
                foreach (AssetValues item in document.avatars)
                    if (item == null || !ValidAssetIdentity(item.asset) || !seen.Add(item.asset)) return false;
                return true;
            }
            catch (ArgumentException) { return false; }
        }

        private static bool ValidAssetIdentity(string value)
        {
            if (string.IsNullOrEmpty(value) || (!value.StartsWith("managed:", StringComparison.Ordinal) &&
                !value.StartsWith("bundled:", StringComparison.Ordinal)) || value.Length != 72) return false;
            for (int index = 8; index < value.Length; index++)
                if (!Uri.IsHexDigit(value[index])) return false;
            return true;
        }

        private AvatarPresentationValues LoadValues(bool isPortrait)
        {
            AvatarPresentationTransform authored = configuration.PresentationTransform(isPortrait);
            return Normalize(new AvatarPresentationValues
            {
                x = PlayerPrefs.GetFloat(Key(isPortrait, 'x'), authored != null ? authored.x : 0f),
                y = PlayerPrefs.GetFloat(Key(isPortrait, 'y'), authored != null ? authored.y : 0f),
                scale = PlayerPrefs.GetFloat(Key(isPortrait, 's'), authored != null ? authored.scale : 1f)
            });
        }

        private static AvatarPresentationValues Normalize(AvatarPresentationValues values)
        {
            values.x = ClampFinite(values.x, -AvatarPresentationTransform.MaximumTranslation,
                AvatarPresentationTransform.MaximumTranslation, 0f);
            values.y = ClampFinite(values.y, -AvatarPresentationTransform.MaximumTranslation,
                AvatarPresentationTransform.MaximumTranslation, 0f);
            values.scale = ClampFinite(values.scale, 1f, AvatarPresentationTransform.MaximumScale, 1f);
            return values;
        }

        private static float ClampFinite(float value, float minimum, float maximum, float fallback)
        {
            return float.IsNaN(value) || float.IsInfinity(value)
                ? fallback
                : Mathf.Clamp(value, minimum, maximum);
        }

        private static string Key(bool portrait, char value)
        {
            if (portrait) return value == 'x' ? PortraitXKey : value == 'y' ? PortraitYKey : PortraitScaleKey;
            return value == 'x' ? LandscapeXKey : value == 'y' ? LandscapeYKey : LandscapeScaleKey;
        }
    }
}
