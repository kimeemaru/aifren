using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    /// <summary>A reviewed server preview freezes the mutation target, never the current row selection.</summary>
    internal sealed class CharacterOperationReceipt
    {
        internal readonly string CharacterId, DisplayName, Action, Token, ScopeText;
        internal readonly long Revision;
        internal readonly string[] Files;
        internal readonly bool OldCopyRetained;
        private CharacterOperationReceipt(BackendEventData data)
        {
            CharacterId = data.character_id; DisplayName = data.display_name; Action = data.action;
            Token = data.token; Revision = data.revision; ScopeText = data.scope_text;
            Files = data.files != null ? (string[])data.files.Clone() : Array.Empty<string>();
            OldCopyRetained = data.old_copy_retained;
        }
        internal static bool AllowedAction(string action) => action == "migrate" || action == "reset" ||
            action == "delete" || action == "cleanup" || action == "resume";
        internal static CharacterOperationReceipt Capture(BackendEventData data, string requestedId, string requestedAction)
        {
            return data != null && Guid.TryParse(data.character_id, out _) && data.character_id == requestedId &&
                data.action == requestedAction && AllowedAction(data.action) && !string.IsNullOrWhiteSpace(data.token) &&
                data.revision >= 0 && !string.IsNullOrWhiteSpace(data.scope_text)
                ? new CharacterOperationReceipt(data) : null;
        }
        internal ClientCommand ConfirmCommand() => new ClientCommand { command = "character_operation_confirm",
            character_id = CharacterId, token = Token, revision = Revision };
        internal bool MatchesResult(BackendEventData data) => data != null && data.character_id == CharacterId && data.action == Action &&
            ((data.status == "completed" && (Action != "delete" || data.deleted_character_id == CharacterId)) ||
             (data.status == "already_local" && Action == "migrate") || (data.status == "no_retained_copy" && Action == "cleanup"));
    }

    public sealed partial class AIFrenPocController
    {
        private bool characterStorageUnavailable;
        private long characterRegistryRevision;
        private Button createCharacterButton;
        private TMP_Text characterCreationStatus;
        private string creationRequestId, creationName, creationPersonality;
        private bool characterCreateInFlight;
        private GameObject characterManagerPanel;
        private TMP_Text characterManagerTitle, characterManagerBody;
        private RectTransform characterManagerBodyRect;
        private GameObject characterManagerActions;
        private RectTransform characterManagerActionContent;
        private Button characterManagerConfirm;
        private string characterManagerTarget, characterPreviewTarget, characterPreviewAction, characterFolderTarget;
        private CharacterOperationReceipt characterOperationReceipt, characterOperationInFlight;

        private bool CharacterMaintenanceBusy => characterOperationInFlight != null || characterPreviewTarget != null;

        private static string ShortCharacterId(string value) => !string.IsNullOrEmpty(value) && value.Length > 8 ? value.Substring(0, 8) : value;

        private void RefreshCharacterCreationUi()
        {
            if (createCharacterButton != null)
            {
                createCharacterButton.interactable = !characterCreateInFlight && !CharacterMaintenanceBusy && !characterSwitchInFlight;
                TMP_Text label = createCharacterButton.GetComponentInChildren<TMP_Text>();
                if (label != null) label.text = characterCreateInFlight ? "Creating…" :
                    creationRequestId != null ? "Retry creation" : "Create Character";
            }
        }

        private async void SubmitCharacterCreation(string name, string personality)
        {
            if (characterCreateInFlight) return;
            if (creationRequestId == null || name != creationName || personality != creationPersonality)
            { creationRequestId = Guid.NewGuid().ToString("N"); creationName = name; creationPersonality = personality; }
            string request = creationRequestId;
            characterCreateInFlight = true;
            SetCharacterCreationStatus("Creating " + name + "…");
            RefreshCharacterSettings();
            await client.CreateCharacterAsync(name, personality, request);
            if (request == creationRequestId && client.State != ConnectionState.Connected)
            {
                characterCreateInFlight = false;
                SetCharacterCreationStatus("Connection interrupted. Your form is kept; retry uses the same creation request.");
                RefreshCharacterSettings();
            }
        }

        private void SetCharacterCreationStatus(string message)
        {
            if (characterCreationStatus != null) { characterCreationStatus.richText = false; characterCreationStatus.text = message ?? string.Empty; }
        }

        private bool HandleCharacterManagementEvent(BackendEvent evt)
        {
            BackendEventData data = evt?.data;
            if (data == null) return false;
            if (evt.type == "character_created")
            {
                if (data.request_id == creationRequestId && creationRequestId != null && Guid.TryParse(data.character_id, out _))
                {
                    if (newCharacterNameInput != null && newCharacterNameInput.text.Trim() == creationName) newCharacterNameInput.text = string.Empty;
                    if (newCharacterPersonalityInput != null && newCharacterPersonalityInput.text == creationPersonality) newCharacterPersonalityInput.text = string.Empty;
                    creationRequestId = null; characterCreateInFlight = false;
                    SetCharacterCreationStatus("Created " + data.display_name + " · " + ShortCharacterId(data.character_id));
                    RefreshCharacterSettings();
                    if (client != null) _ = client.RequestSnapshotAsync();
                }
                return true;
            }
            if (evt.type == "character_operation_preview")
            {
                CharacterOperationReceipt receipt = CharacterOperationReceipt.Capture(data, characterPreviewTarget, characterPreviewAction);
                if (receipt != null)
                {
                    characterPreviewTarget = characterPreviewAction = null;
                    characterOperationReceipt = receipt;
                    ShowCharacterOperationPreview(receipt);
                    RefreshCharacterSettings();
                }
                return true;
            }
            if (evt.type == "character_operation_result")
            {
                CharacterOperationReceipt completed = characterOperationInFlight;
                if (completed != null && completed.MatchesResult(data))
                {
                    if (completed.Action == "delete" && data.status == "completed" && data.deleted_character_id == completed.CharacterId)
                        CharacterAvatarPreference.Delete(completed.CharacterId);
                    characterOperationInFlight = characterOperationReceipt = null;
                    SetCharacterManagerBody(OperationLabel(completed.Action) + " — " + data.status.Replace('_', ' ') + ".\n" +
                        completed.DisplayName + " · " + ShortCharacterId(completed.CharacterId));
                    if (characterManagerConfirm != null) characterManagerConfirm.gameObject.SetActive(false);
                    RefreshCharacterSettings();
                    if (client != null) _ = client.RequestSnapshotAsync();
                }
                return true;
            }
            if (evt.type == "character_folder")
            {
                if (data.character_id == characterFolderTarget && characterFolderTarget != null)
                {
                    characterFolderTarget = null;
                    string uri = ValidatedLocalFolderUri(data.folder_path);
                    if (uri != null) Application.OpenURL(uri);
                    else SetCharacterManagerBody("The backend did not return a valid local character folder.");
                }
                return true;
            }
            return false;
        }

        private bool HandleCharacterManagementError(CommandError error)
        {
            if (error == null) return false;
            if (creationRequestId != null && error.request_id == creationRequestId)
            {
                characterCreateInFlight = false;
                SetCharacterCreationStatus(error.message + " Your form is kept.");
                RefreshCharacterSettings(); return true;
            }
            if (error.code == "character_operation_failed" || error.code == "invalid_character_operation" || error.code == "character_folder_failed")
            {
                characterPreviewTarget = characterPreviewAction = characterFolderTarget = null;
                characterOperationReceipt = characterOperationInFlight = null;
                SetCharacterManagerBody(error.message ?? "The operation was not completed.");
                if (characterManagerConfirm != null) characterManagerConfirm.gameObject.SetActive(false);
                RefreshCharacterSettings(); return true;
            }
            return false;
        }

        private void CharacterManagementDisconnected()
        {
            if (characterCreateInFlight)
            {
                characterCreateInFlight = false;
                SetCharacterCreationStatus("Connection interrupted. Your form is kept; retry uses the same creation request.");
            }
            if (CharacterMaintenanceBusy || characterOperationReceipt != null)
                SetCharacterManagerBody("Connection interrupted. Refresh this character's status before another operation.");
            characterPreviewTarget = characterPreviewAction = characterFolderTarget = null;
            characterOperationReceipt = characterOperationInFlight = null;
            if (characterManagerConfirm != null) characterManagerConfirm.gameObject.SetActive(false);
            RefreshCharacterSettings();
        }

        internal static string ValidatedLocalFolderUri(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path) || path.IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0) return null;
            try
            {
                var uri = new Uri(Path.GetFullPath(path));
                return uri.IsFile && string.IsNullOrEmpty(uri.Host) ? uri.AbsoluteUri : null;
            }
            catch (Exception error) when (error is ArgumentException || error is UriFormatException || error is NotSupportedException) { return null; }
        }

        private void OpenCharacterManager(CharacterSummary summary)
        {
            if (summary == null || !Guid.TryParse(summary.character_id, out _) || CharacterMaintenanceBusy || characterCreateInFlight) return;
            EnsureCharacterManagerPanel();
            characterManagerTarget = summary.character_id;
            string selectedId = summary.character_id;
            characterOperationReceipt = null;
            characterManagerPanel.SetActive(true); characterManagerPanel.transform.SetAsLastSibling();
            characterManagerTitle.text = summary.display_name + " · " + ShortCharacterId(summary.character_id);
            characterManagerConfirm.gameObject.SetActive(false);
            SetCharacterManagerBody("Character: " + summary.character_id + "\nStorage: " + (summary.storage_layout ?? "unknown") +
                "\nStatus: " + (summary.storage_status ?? "unknown") + "\nActions below first show an exact preview. Shared visual assets stay separate.");
            foreach (Transform child in characterManagerActionContent) Destroy(child.gameObject);
            characterManagerActions.SetActive(true);
            int row = 0;
            bool incomplete = summary.storage_status == "creating" || summary.storage_status == "incomplete";
            bool pendingOperation = !string.IsNullOrEmpty(summary.operation_kind);
            if (pendingOperation)
                AddCharacterManagerAction("Resume interrupted operation…", row++, () => PreviewCharacterOperation(selectedId, "resume"));
            else if (!incomplete)
            {
                AddCharacterManagerAction("Open character folder", row++, () => RequestCharacterFolder(selectedId));
                if (summary.storage_layout != "local") AddCharacterManagerAction("Migrate to character folder…", row++, () => PreviewCharacterOperation(selectedId, "migrate"));
                if (summary.has_retained_copy) AddCharacterManagerAction("Remove retained old copy…", row++, () => PreviewCharacterOperation(selectedId, "cleanup"));
                AddCharacterManagerAction("Reset timeline…", row++, () => PreviewCharacterOperation(selectedId, "reset"));
            }
            if (!pendingOperation) AddCharacterManagerAction("Delete character…", row++, () => PreviewCharacterOperation(selectedId, "delete"));
            characterManagerActionContent.sizeDelta = new Vector2(0f, row * 43f);
        }

        private void AddCharacterManagerAction(string label, int row, UnityEngine.Events.UnityAction action)
        {
            Button button = CreateButton(characterManagerActionContent, label, Panel);
            PlaceTop(button.GetComponent<RectTransform>(), -row * 43f, 36f, 0f, 1f);
            button.onClick.AddListener(action);
        }

        private void EnsureCharacterManagerPanel()
        {
            if (characterManagerPanel != null) return;
            characterManagerPanel = CreatePanel(settingsPanel.transform, "Character Maintenance", new Color(.08f, .065f, .13f, .99f));
            Stretch(characterManagerPanel.GetComponent<RectTransform>(), new Vector2(.16f, .08f), new Vector2(.96f, .94f), Vector2.zero, Vector2.zero);
            characterManagerTitle = CreateText(characterManagerPanel.transform, "Character", 22f, Ink, TextAlignmentOptions.MidlineLeft);
            characterManagerTitle.richText = false;
            Stretch(characterManagerTitle.rectTransform, new Vector2(.06f, .89f), new Vector2(.94f, .98f), Vector2.zero, Vector2.zero);
            GameObject viewport = CreatePanel(characterManagerPanel.transform, "Operation Preview Scroll", Color.clear);
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(.06f, .43f), new Vector2(.94f, .88f), Vector2.zero, Vector2.zero);
            viewport.AddComponent<RectMask2D>();
            characterManagerBody = CreateText(viewport.transform, string.Empty, 16f, Ink, TextAlignmentOptions.TopLeft);
            characterManagerBody.richText = false; characterManagerBody.enableWordWrapping = true;
            characterManagerBodyRect = characterManagerBody.rectTransform;
            characterManagerBodyRect.anchorMin = new Vector2(0f, 1f); characterManagerBodyRect.anchorMax = new Vector2(1f, 1f);
            characterManagerBodyRect.pivot = new Vector2(.5f, 1f); characterManagerBodyRect.anchoredPosition = Vector2.zero;
            ScrollRect scroll = viewport.AddComponent<ScrollRect>(); scroll.viewport = viewport.GetComponent<RectTransform>();
            scroll.content = characterManagerBodyRect; scroll.horizontal = false; scroll.vertical = true; scroll.scrollSensitivity = 28f;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            characterManagerActions = new GameObject("Character Maintenance Choices", typeof(RectTransform));
            characterManagerActions.transform.SetParent(characterManagerPanel.transform, false);
            Stretch(characterManagerActions.GetComponent<RectTransform>(), new Vector2(.06f, .11f), new Vector2(.94f, .42f), Vector2.zero, Vector2.zero);
            characterManagerActions.AddComponent<RectMask2D>();
            characterManagerActionContent = new GameObject("Maintenance Actions", typeof(RectTransform)).GetComponent<RectTransform>();
            characterManagerActionContent.SetParent(characterManagerActions.transform, false);
            characterManagerActionContent.anchorMin = new Vector2(0f, 1f); characterManagerActionContent.anchorMax = new Vector2(1f, 1f);
            characterManagerActionContent.pivot = new Vector2(.5f, 1f);
            ScrollRect actionScroll = characterManagerActions.AddComponent<ScrollRect>();
            actionScroll.viewport = characterManagerActions.GetComponent<RectTransform>(); actionScroll.content = characterManagerActionContent;
            actionScroll.horizontal = false; actionScroll.vertical = true; actionScroll.scrollSensitivity = 28f;
            actionScroll.movementType = ScrollRect.MovementType.Clamped;
            Button cancel = CreateButton(characterManagerPanel.transform, "Cancel / Close", Panel);
            Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.06f, .025f), new Vector2(.48f, .09f), Vector2.zero, Vector2.zero);
            cancel.onClick.AddListener(CloseCharacterManager);
            characterManagerConfirm = CreateButton(characterManagerPanel.transform, "Confirm", new Color(.42f, .16f, .22f));
            Stretch(characterManagerConfirm.GetComponent<RectTransform>(), new Vector2(.52f, .025f), new Vector2(.94f, .09f), Vector2.zero, Vector2.zero);
        }

        private void SetCharacterManagerBody(string text)
        {
            if (characterManagerBody == null) return;
            characterManagerBody.text = text ?? string.Empty;
            Canvas.ForceUpdateCanvases();
            float width = Mathf.Max(100f, characterManagerBodyRect.rect.width);
            characterManagerBodyRect.sizeDelta = new Vector2(0f, Mathf.Max(200f, characterManagerBody.GetPreferredValues(characterManagerBody.text, width, float.PositiveInfinity).y + 12f));
            characterManagerBodyRect.anchoredPosition = Vector2.zero;
        }

        private void CloseCharacterManager()
        {
            characterPreviewTarget = characterPreviewAction = characterFolderTarget = null;
            characterOperationReceipt = null;
            if (characterManagerPanel != null) characterManagerPanel.SetActive(false);
            RefreshCharacterSettings();
        }

        private void PreviewCharacterOperation(string id, string action)
        {
            if (client == null || client.State != ConnectionState.Connected || CharacterMaintenanceBusy ||
                id != characterManagerTarget || !CharacterOperationReceipt.AllowedAction(action)) return;
            characterPreviewTarget = id; characterPreviewAction = action; characterOperationReceipt = null;
            characterManagerActions.SetActive(false); characterManagerConfirm.gameObject.SetActive(false);
            SetCharacterManagerBody("Preparing the exact operation preview…");
            RefreshCharacterSettings();
            _ = client.PreviewCharacterOperationAsync(id, action);
        }

        private void ShowCharacterOperationPreview(CharacterOperationReceipt receipt)
        {
            if (characterManagerPanel == null || !characterManagerPanel.activeSelf) return;
            characterManagerTitle.text = OperationLabel(receipt.Action) + " — " + receipt.DisplayName;
            SetCharacterManagerBody(CharacterOperationPreviewText(receipt));
            characterManagerActions.SetActive(false);
            characterManagerConfirm.onClick.RemoveAllListeners();
            characterManagerConfirm.onClick.AddListener(() => ConfirmCharacterOperation(receipt));
            characterManagerConfirm.gameObject.SetActive(true);
            characterManagerConfirm.interactable = receipt.Revision >= characterRegistryRevision;
            if (!characterManagerConfirm.interactable) SetCharacterManagerBody(characterManagerBody.text + "\n\nThe character list changed. Close this dialog and request a fresh preview.");
        }

        internal static string CharacterOperationPreviewText(CharacterOperationReceipt receipt)
        {
            string files = string.Join("\n", receipt.Files.Select(path => "• " + path));
            return receipt.DisplayName + "\n" + receipt.CharacterId + "\n\n" + receipt.ScopeText + "\n\n" + files +
                (receipt.OldCopyRetained ? "\n\nA retained old copy currently exists. The operation scope above describes what happens to it." : string.Empty);
        }

        private void ConfirmCharacterOperation(CharacterOperationReceipt receipt)
        {
            if (client == null || client.State != ConnectionState.Connected || !ReferenceEquals(receipt, characterOperationReceipt) ||
                CharacterMaintenanceBusy || receipt.Revision < characterRegistryRevision) return;
            characterOperationInFlight = receipt;
            characterOperationReceipt = null;
            characterManagerConfirm.interactable = false;
            SetCharacterManagerBody(characterManagerBody.text + "\n\nApplying the confirmed operation…");
            RefreshCharacterSettings();
            _ = client.ConfirmCharacterOperationAsync(receipt.CharacterId, receipt.Token, receipt.Revision);
        }

        private void RequestCharacterFolder(string id)
        {
            if (client == null || client.State != ConnectionState.Connected || id != characterManagerTarget) return;
            characterFolderTarget = id;
            _ = client.OpenCharacterFolderAsync(id);
        }

        private static string OperationLabel(string action)
        {
            switch (action)
            {
                case "migrate": return "Migrate character storage";
                case "reset": return "Reset timeline";
                case "delete": return "Delete character";
                case "cleanup": return "Remove retained old copy";
                case "resume": return "Resume operation";
                default: return "Character operation";
            }
        }
    }
}
