#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private string qaCreatedRequestId, qaCreatedName, qaCreatedCharacterId;

        private void ObserveCharacterNativeQaEvent(BackendEvent evt)
        {
            if (evt.type == "character_created" && qaCreatedRequestId != null &&
                evt.data?.request_id == qaCreatedRequestId && Guid.TryParse(evt.data.character_id, out _))
            { qaCreatedCharacterId = evt.data.character_id; qaCreatedName = evt.data.display_name; }
        }

        private bool QaCurrentCharacterBinding() => qaSnapshots > 0 && client != null && client.State == ConnectionState.Connected &&
            !characterSwitchInFlight && !client.CharacterSwitching && client.CharacterOwner != null &&
            client.CharacterOwner.CharacterId == activeCharacterId && client.CharacterOwner.Session == activeCharacterSession;

        private bool QaOutgoingAvatarVisible() => (avatarLoader?.ActiveAvatar != null && avatarLoader.ActiveAvatar.activeInHierarchy) ||
            (!useDirectAvatarPresentation && avatarSurface != null && avatarSurface.enabled && avatarSurface.gameObject.activeInHierarchy && avatarSurface.color.a > .001f);

        private bool QaVerifiedEmptyCharacterState() => QaCurrentCharacterBinding() && characterStorageUnavailable &&
            activeCharacterId == "no-character" && availableCharacters.Count == 0 && messages.Count == 0 &&
            messageInput != null && sendButton != null && !messageInput.interactable && !sendButton.interactable &&
            avatarLoader != null && !QaOutgoingAvatarVisible() && avatarPresentationInitialization == null &&
            (avatarPresentationState == null || !avatarPresentationState.IsCharacterScoped);

        private bool QaStartupPresentationReady() => activeCharacterId == "no-character" ? QaVerifiedEmptyCharacterState() :
            qaSnapshots > 0 && avatarLoader?.ActiveAvatar != null && avatarPresentationInitialization == null;

        private bool QaCharacterReady(string selector)
        {
            if (!QaCurrentCharacterBinding() || characterStorageUnavailable || characterCreateInFlight || characterAvatarSwitchInFlight ||
                modelApplyInProgress || avatarPresentationInitialization != null || !HasCurrentFramingOwner ||
                !avatarLoader.ActiveAvatar.activeInHierarchy || messageInput == null || sendButton == null ||
                !messageInput.interactable || !sendButton.interactable) return false;
            CharacterSummary selected = availableCharacters.SingleOrDefault(item => item.character_id == selector);
            // A newly created character may share a display name with an old
            // one. Its matching creation receipt, not the old list row, owns
            // readiness until the new canonical snapshot/visual binding arrives.
            if (selected == null && qaCreatedRequestId != null && selector == qaCreatedName)
            {
                if (qaCreatedCharacterId == null) return false;
                selected = availableCharacters.SingleOrDefault(item => item.character_id == qaCreatedCharacterId);
                if (availableCharacters.Count(item => item.display_name == selector) != 1) return false;
            }
            else if (selected == null)
            {
                CharacterSummary[] matches = availableCharacters.Where(item => item.display_name == selector).Take(2).ToArray();
                if (matches.Length != 1) return false;
                selected = matches[0];
            }
            return selected != null && selected.character_id == activeCharacterId && selected.storage_status == "ready" &&
                string.IsNullOrEmpty(selected.operation_kind);
        }

        // Closed actions in the existing finite, isolated QA plan. These drive
        // ordinary controls; storage preview/confirmation still goes to the host.
        private bool ApplyCharacterNativeQaStep(NativeQaSession.Step step)
        {
            switch (step.action)
            {
                case "character_manage":
                    OpenCharacterManager(ResolveQaCharacter(availableCharacters, step.value)); return true;
                case "character_operation":
                    string label;
                    switch (step.value)
                    {
                        case "migrate": label = "Migrate to character folder…"; break;
                        case "reset": label = "Reset timeline…"; break;
                        case "delete": label = "Delete character…"; break;
                        case "cleanup": label = "Remove retained old copy…"; break;
                        case "resume": label = "Resume interrupted operation…"; break;
                        default: throw new InvalidOperationException();
                    }
                    InvokeQaCharacterButton(characterManagerActionContent.GetComponentsInChildren<Button>().Single(button =>
                        button.GetComponentInChildren<TMP_Text>()?.text == label)); return true;
                case "character_confirm": InvokeQaCharacterButton(characterManagerConfirm); return true;
                case "character_close": CloseCharacterManager(); return true;
                case "character_create_name": newCharacterNameInput.text = step.value; return true;
                case "character_create_personality": newCharacterPersonalityInput.text = step.value; return true;
                case "character_create":
                    InvokeQaCharacterButton(createCharacterButton);
                    qaCreatedRequestId = creationRequestId; qaCreatedName = creationName; qaCreatedCharacterId = null;
                    return true;
                case "character_wait_ready":
                    if (string.IsNullOrWhiteSpace(step.value)) throw new InvalidOperationException();
                    return true;
                case "character_feedback_assert":
                    if (characterCreationStatus == null || characterCreationStatus.text.IndexOf(step.value, StringComparison.OrdinalIgnoreCase) < 0)
                        throw new InvalidOperationException();
                    return true;
                case "character_active_assert":
                    if (activeCharacterId != ResolveQaCharacter(availableCharacters, step.value).character_id || characterSwitchInFlight)
                        throw new InvalidOperationException();
                    return true;
                case "character_count_assert":
                    if (!int.TryParse(step.value, out int count) || count != availableCharacters.Count) throw new InvalidOperationException();
                    return true;
                case "character_unavailable_assert":
                    if (!characterStorageUnavailable || messageInput.interactable || sendButton.interactable || settingsPanel == null)
                        throw new InvalidOperationException();
                    return true;
                case "avatar_value":
                    if (!HasCurrentFramingEdit || !TryParseQaFramingValue(step.value, out int field, out _)) throw new InvalidOperationException();
                    TMP_InputField input = field == 0 ? avatarViewXInput : field == 1 ? avatarViewYInput : avatarViewScaleInput;
                    input.onEndEdit.Invoke(step.value.Substring(step.value.IndexOf('=') + 1)); return true;
                case "avatar_save":
                    if (!HasCurrentFramingEdit) throw new InvalidOperationException();
                    InvokeQaCharacterButton(avatarViewPanel.GetComponentsInChildren<Button>().Single(button =>
                        button.GetComponentInChildren<TMP_Text>()?.text == "Save")); return true;
                case "avatar_assert":
                    if (!HasCurrentFramingOwner || !TryParseQaFramingValue(step.value, out int expectedField, out float expected)) throw new InvalidOperationException();
                    AvatarPresentationValues values = avatarPresentationState.GetValues(AvatarViewPortrait);
                    float actual = expectedField == 0 ? values.x : expectedField == 1 ? values.y : values.scale;
                    if (Mathf.Abs(actual - expected) > .001f) throw new InvalidOperationException();
                    qaAvatarTrace?.WriteLine(JsonUtility.ToJson(new QaCharacterFraming { stage = "framing_assert", step = qaStep,
                        character_id = avatarPresentationState.CharacterId, asset_identity = avatarPresentationState.AssetIdentity,
                        portrait = avatarPresentationState.GetValues(true), landscape = avatarPresentationState.GetValues(false),
                        portrait_layout = AvatarViewPortrait }));
                    return true;
                default: return false;
            }
        }

        internal static CharacterSummary ResolveQaCharacter(IEnumerable<CharacterSummary> characters, string selector)
        {
            CharacterSummary[] source = characters.ToArray();
            CharacterSummary exact = source.SingleOrDefault(item => item.character_id == selector);
            return exact ?? source.Single(item => item.display_name == selector);
        }

        internal static bool TryParseQaFramingValue(string text, out int field, out float value)
        {
            field = -1; value = 0f;
            string[] parts = (text ?? string.Empty).Split('=');
            if (parts.Length != 2) return false;
            field = parts[0] == "x" ? 0 : parts[0] == "y" ? 1 : parts[0] == "scale" ? 2 : -1;
            return field >= 0 && float.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out value) &&
                !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static void InvokeQaCharacterButton(Button button)
        {
            if (button == null || !button.interactable || !button.gameObject.activeInHierarchy) throw new InvalidOperationException();
            button.onClick.Invoke();
        }

        [Serializable]
        private sealed class QaCharacterFraming
        {
            public string stage, step, character_id, asset_identity;
            public bool portrait_layout;
            public AvatarPresentationValues portrait, landscape;
        }

        [Serializable]
        private sealed class QaEmptyCharacterVisual
        {
            public string stage, character, absence_kind;
            public bool verified, storage_unavailable, composer_disabled, outgoing_avatar_visible;
        }
    }
}
#endif
