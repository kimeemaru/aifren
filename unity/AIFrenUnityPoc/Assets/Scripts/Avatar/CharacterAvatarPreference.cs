using System;
using System.Collections.Generic;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>
    /// Character-keyed desired avatar selection. The managed asset remains a
    /// single reusable Unity-owned file; only its stable content identifier is
    /// associated with a character.
    /// </summary>
    internal static class CharacterAvatarPreference
    {
        private const string KeyPrefix = "AIFren.CharacterAvatar.v1.";
        private const string BundledValue = "bundled";
        private const string ManagedPrefix = "managed:";

        internal sealed class Resolution
        {
            internal bool IsExplicit;
            internal bool IsBundled;
            internal bool ExplicitAssetUnavailable;
            internal ManagedAssetRecord Asset;
        }

        internal static Resolution Resolve(
            string characterId,
            ManagedAssetLibrary library,
            string legacyGlobalPath)
        {
            if (library == null) throw new ArgumentNullException(nameof(library));
            string saved = Read(characterId);
            if (saved == BundledValue)
            {
                return new Resolution { IsExplicit = true, IsBundled = true };
            }
            if (saved.StartsWith(ManagedPrefix, StringComparison.Ordinal))
            {
                string assetId = saved.Substring(ManagedPrefix.Length);
                ManagedAssetRecord explicitAsset = Find(library, assetId);
                if (explicitAsset != null)
                {
                    return new Resolution { IsExplicit = true, Asset = explicitAsset };
                }
                return ResolveLegacy(library, legacyGlobalPath, true);
            }
            return ResolveLegacy(library, legacyGlobalPath, false);
        }

        internal static void SetManaged(string characterId, string assetId)
        {
            if (!IsCharacterId(characterId)) throw new ArgumentException("character ID must be a UUID", nameof(characterId));
            if (!IsManagedAssetId(assetId)) throw new ArgumentException("avatar asset ID is invalid", nameof(assetId));
            PlayerPrefs.SetString(Key(characterId), ManagedPrefix + assetId.ToLowerInvariant());
            PlayerPrefs.Save();
        }

        internal static void SetBundled(string characterId)
        {
            if (!IsCharacterId(characterId)) throw new ArgumentException("character ID must be a UUID", nameof(characterId));
            PlayerPrefs.SetString(Key(characterId), BundledValue);
            PlayerPrefs.Save();
        }

        internal static bool HasExplicit(string characterId)
        {
            return IsCharacterId(characterId) && PlayerPrefs.HasKey(Key(characterId));
        }

        internal static void Delete(string characterId)
        {
            if (!IsCharacterId(characterId)) return;
            PlayerPrefs.DeleteKey(Key(characterId));
            PlayerPrefs.Save();
        }

        internal static void RepairDeletedAsset(IEnumerable<string> characterIds, string assetId)
        {
            if (!IsManagedAssetId(assetId) || characterIds == null) return;
            string value = ManagedPrefix + assetId.ToLowerInvariant();
            bool changed = false;
            foreach (string characterId in characterIds)
            {
                if (!IsCharacterId(characterId) || PlayerPrefs.GetString(Key(characterId), string.Empty) != value) continue;
                PlayerPrefs.DeleteKey(Key(characterId));
                changed = true;
            }
            if (changed) PlayerPrefs.Save();
        }

        private static Resolution ResolveLegacy(
            ManagedAssetLibrary library,
            string legacyGlobalPath,
            bool explicitAssetUnavailable)
        {
            ManagedAssetRecord legacy = null;
            if (!string.IsNullOrWhiteSpace(legacyGlobalPath))
            {
                legacy = library.Assets(ManagedAssetLibrary.ModelKind).Find(
                    item => string.Equals(item.path, legacyGlobalPath, StringComparison.Ordinal));
            }
            return new Resolution
            {
                IsBundled = legacy == null,
                Asset = legacy,
                ExplicitAssetUnavailable = explicitAssetUnavailable,
            };
        }

        private static ManagedAssetRecord Find(ManagedAssetLibrary library, string assetId)
        {
            if (!IsManagedAssetId(assetId)) return null;
            return library.Assets(ManagedAssetLibrary.ModelKind).Find(
                item => string.Equals(item.id, assetId, StringComparison.OrdinalIgnoreCase));
        }

        private static string Read(string characterId)
        {
            return IsCharacterId(characterId)
                ? PlayerPrefs.GetString(Key(characterId), string.Empty).Trim().ToLowerInvariant()
                : string.Empty;
        }

        private static string Key(string characterId)
        {
            return KeyPrefix + Guid.Parse(characterId).ToString("D");
        }

        private static bool IsCharacterId(string value)
        {
            return Guid.TryParse(value, out _);
        }

        private static bool IsManagedAssetId(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.Length != 64) return false;
            foreach (char character in value)
            {
                bool hexadecimal = (character >= '0' && character <= '9') ||
                    (character >= 'a' && character <= 'f') ||
                    (character >= 'A' && character <= 'F');
                if (!hexadecimal) return false;
            }
            return true;
        }
    }
}
