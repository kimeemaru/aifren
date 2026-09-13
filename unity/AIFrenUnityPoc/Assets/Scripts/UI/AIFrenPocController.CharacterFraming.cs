using System;
using AIFren.UnityPoc.Avatar;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private GameObject framingReadyAvatar;
        private AvatarPresentationState avatarViewEditOwner;
        private int avatarViewEditGeneration;

        private bool HasCurrentFramingOwner => avatarPresentationState != null && avatarPresentationState.IsCharacterScoped &&
            string.Equals(avatarPresentationState.CharacterId, activeCharacterId, StringComparison.OrdinalIgnoreCase) &&
            avatarLoader != null && framingReadyAvatar != null && avatarLoader.ActiveAvatar == framingReadyAvatar &&
            !characterAvatarSwitchInFlight && !characterSwitchInFlight;

        private bool HasCurrentFramingEdit => avatarViewEditing && HasCurrentFramingOwner &&
            ReferenceEquals(avatarViewEditOwner, avatarPresentationState) && avatarViewEditGeneration == modelApplyGeneration;

        private bool CanStartCharacterAvatarSelection(string characterId) => Guid.TryParse(characterId, out _) &&
            !characterSwitchInFlight && string.Equals(characterId, activeCharacterId, StringComparison.OrdinalIgnoreCase);

        internal static bool IsAvatarPickerCompletionCurrent(int capturedGeneration, int currentGeneration,
            string capturedCharacter, string currentCharacter, bool switching) => !switching &&
            IsCharacterAvatarApplyAuthoritative(capturedGeneration, currentGeneration, capturedCharacter, currentCharacter);

        private void RetireCharacterAvatarRequests()
        {
            ++modelApplyGeneration;
            pendingModelApply = null;
            pendingBundledModelApply = false;
            pendingModelApplyCharacterId = null;
            avatarLoader?.ClaimCharacterSelection();
            RetireAvatarFramingOwner();
        }

        private void RetireAvatarFramingOwner()
        {
            // Discard only the captured editor draft. No preference is saved and
            // the arriving character never receives the old editor's snapshots.
            if (avatarViewEditing) CancelAvatarViewEditor();
            framingReadyAvatar = null;
            avatarPresentationState = AvatarPresentationState.CreateUnbound(AvatarConfiguration.Load());
            characterAvatarSwitchInFlight = true;
            avatarLoader?.SetAvatarVisible(false);
            if (avatarPresentationInitialization != null)
            {
                StopCoroutine(avatarPresentationInitialization);
                avatarPresentationInitialization = null;
            }
        }

        private bool BindReadyCharacterFraming(string characterId)
        {
            if (!Guid.TryParse(characterId, out Guid id) ||
                !string.Equals(characterId, activeCharacterId, StringComparison.OrdinalIgnoreCase) ||
                avatarLoader == null || avatarLoader.ActiveAvatar == null) return false;
            AvatarConfiguration configuration = AvatarConfiguration.Load();
            string identity;
            if (avatarLoader.ActiveModelPath == "Bundled model")
                identity = AvatarPresentationState.BundledAssetIdentity(configuration);
            else
            {
                ManagedAssetRecord asset = managedAssetLibrary?.Assets(ManagedAssetLibrary.ModelKind).Find(
                    item => string.Equals(item.path, avatarLoader.ActiveModelPath, StringComparison.Ordinal));
                if (asset == null) return false;
                identity = AvatarPresentationState.ManagedAssetIdentity(asset.id);
            }
            string canonicalId = id.ToString("D");
            if (avatarPresentationState == null || !avatarPresentationState.IsCharacterScoped ||
                avatarPresentationState.CharacterId != canonicalId || avatarPresentationState.AssetIdentity != identity ||
                framingReadyAvatar != avatarLoader.ActiveAvatar)
            {
                if (avatarViewEditing) CancelAvatarViewEditor();
                avatarPresentationState = AvatarPresentationState.LoadForCharacter(configuration, canonicalId, identity);
            }
            framingReadyAvatar = avatarLoader.ActiveAvatar;
            characterAvatarSwitchInFlight = false;
            ApplyAvatarPresentationTransform(AvatarViewPortrait);
            return true;
        }
    }
}
