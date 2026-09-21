using System;
using System.IO;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private CharacterVoiceSnapshot savedCharacterVoice;
        private CharacterSessionOwner characterVoiceOwner;
        private string draftVoiceEngine = "kokoro", draftVoiceLanguage = "en";
        private string characterVoiceRequest, characterVoiceAction;
        private string characterVoiceOperation;
        private bool characterVoiceDirty, characterVoiceBusy;
        private float characterVoiceDeadline;
        private TMP_InputField voiceReferenceInput, voiceTranscriptInput;
        private TMP_Text characterVoiceStatus;
        private Button voiceEngineButton, voiceLanguageButton, voicePrepareButton, voicePreviewButton, voiceSaveButton, voiceImportButton;

        private void AddCharacterVoiceControls(Transform parent, ref float y)
        {
            AddSettingsHeading(parent, "CHARACTER VOICE", ref y);
            CompanionPreferenceHint(parent, "Saved to this character. Kokoro is the responsive built-in voice. Reference voice uses local GPT-SoVITS on CPU and needs more time and memory.", ref y, 78f);
            voiceEngineButton = CreateButton(parent, "Kokoro", Panel); voiceEngineButton.name = "Character Voice Engine";
            PlaceTop(voiceEngineButton.GetComponent<RectTransform>(), y, 42f); y -= 50f;
            voiceEngineButton.onClick.AddListener(() => { draftVoiceEngine = draftVoiceEngine == "kokoro" ? "gpt_sovits" : "kokoro"; characterVoiceDirty = true; RefreshCharacterVoiceControls(); });
            voiceLanguageButton = CreateButton(parent, "English", Panel); voiceLanguageButton.name = "Character Voice Language";
            PlaceTop(voiceLanguageButton.GetComponent<RectTransform>(), y, 38f); y -= 46f;
            voiceLanguageButton.onClick.AddListener(() => {
                string[] languages = { "en", "all_zh", "all_ja", "all_ko", "all_yue" };
                draftVoiceLanguage = languages[(Array.IndexOf(languages, draftVoiceLanguage) + 1) % languages.Length];
                characterVoiceDirty = true; RefreshCharacterVoiceControls();
            });
            CompanionPreferenceHint(parent, "Reference: a clean 3–10 second PCM WAV. Enter exactly the words in that excerpt below. Imported originals stay untouched; Save makes a character-owned copy.", ref y, 74f);
            voiceReferenceInput = CreateInputField(parent); voiceReferenceInput.name = "Voice Reference Path";
            voiceReferenceInput.gameObject.AddComponent<RectMask2D>();
            PlaceTop(voiceReferenceInput.GetComponent<RectTransform>(), y, 42f, .05f, .72f);
            voiceReferenceInput.characterLimit = 4096;
            voiceReferenceInput.onValueChanged.AddListener(_ => characterVoiceDirty = true);
            voiceImportButton = CreateButton(parent, "Browse", Panel); voiceImportButton.name = "Browse Voice Reference";
            PlaceTop(voiceImportButton.GetComponent<RectTransform>(), y, 42f, .75f, .95f); y -= 50f;
            voiceImportButton.onClick.AddListener(BrowseVoiceReference);
            voiceTranscriptInput = CreateInputField(parent, true); voiceTranscriptInput.name = "Voice Reference Transcript";
            voiceTranscriptInput.lineType = TMP_InputField.LineType.MultiLineNewline;
            voiceTranscriptInput.characterLimit = 2000;
            PlaceTop(voiceTranscriptInput.GetComponent<RectTransform>(), y, 104f); y -= 112f;
            voiceTranscriptInput.onValueChanged.AddListener(_ => characterVoiceDirty = true);
            voicePrepareButton = CreateButton(parent, "Prepare voice", Panel); voicePrepareButton.name = "Prepare Character Voice";
            PlaceTop(voicePrepareButton.GetComponent<RectTransform>(), y, 40f, .05f, .48f);
            voicePrepareButton.onClick.AddListener(() => SendCharacterVoice("prepare"));
            voicePreviewButton = CreateButton(parent, "Preview", Panel); voicePreviewButton.name = "Preview Character Voice";
            PlaceTop(voicePreviewButton.GetComponent<RectTransform>(), y, 40f, .52f, .95f); y -= 48f;
            voicePreviewButton.onClick.AddListener(() => SendCharacterVoice("preview"));
            Button stop = CreateButton(parent, "Stop preview / preparation", Panel); stop.name = "Stop Character Voice";
            PlaceTop(stop.GetComponent<RectTransform>(), y, 38f); y -= 46f;
            stop.onClick.AddListener(() => SendCharacterVoice("stop"));
            characterVoiceStatus = CompanionPreferenceHint(parent, "Loading saved voice…", ref y, 100f);
            characterVoiceStatus.richText = false;
            voiceSaveButton = CreateButton(parent, "Save voice", Accent); voiceSaveButton.name = "Save Character Voice";
            PlaceTop(voiceSaveButton.GetComponent<RectTransform>(), y, 40f, .05f, .48f);
            voiceSaveButton.onClick.AddListener(() => SendCharacterVoice("save"));
            Button cancel = CreateButton(parent, "Cancel", Panel); cancel.name = "Cancel Character Voice";
            PlaceTop(cancel.GetComponent<RectTransform>(), y, 40f, .52f, .95f); y -= 56f;
            cancel.onClick.AddListener(() => { characterVoiceDirty = false; RestoreCharacterVoiceDraft(); SendCharacterVoice("cancel"); });
            RefreshCharacterVoiceControls();
        }

        private void RestoreCharacterVoiceDraft()
        {
            draftVoiceEngine = savedCharacterVoice?.engine ?? "kokoro";
            draftVoiceLanguage = savedCharacterVoice?.language ?? "en";
            voiceReferenceInput?.SetTextWithoutNotify("");
            voiceTranscriptInput?.SetTextWithoutNotify(savedCharacterVoice?.transcript ?? "");
            RefreshCharacterVoiceControls();
        }

        private void RetireCharacterVoiceView()
        {
            characterVoiceOwner = null; savedCharacterVoice = null;
            characterVoiceRequest = characterVoiceAction = null;
            characterVoiceOperation = null;
            characterVoiceBusy = characterVoiceDirty = false;
            RestoreCharacterVoiceDraft();
        }

        private void ReceiveCharacterVoice(CharacterVoiceSnapshot value, string requestId = null)
        {
            CharacterSessionOwner owner = client?.CharacterOwner;
            if (owner == null || !owner.IsValid || !client.OwnsCharacter(owner)) return;
            if (characterVoiceOwner == null || !characterVoiceOwner.Matches(owner)) RetireCharacterVoiceView();
            characterVoiceOwner = owner;
            if (requestId != null && requestId != characterVoiceRequest) return;
            savedCharacterVoice = value;
            bool completed = value != null && value.state != "preparing";
            if (requestId != null && completed)
            {
                if (characterVoiceAction == "save" && (value.state == "ready" || value.state == "saved")) characterVoiceDirty = false;
                characterVoiceBusy = false; characterVoiceRequest = characterVoiceAction = null;
            }
            if (!characterVoiceDirty) RestoreCharacterVoiceDraft();
            RefreshCharacterVoiceControls();
        }

        private void RefreshCharacterVoiceControls()
        {
            bool bound = characterVoiceOwner != null && client != null && client.OwnsCharacter(characterVoiceOwner) && savedCharacterVoice != null;
            bool editable = bound && !characterVoiceBusy;
            bool clone = draftVoiceEngine == "gpt_sovits";
            foreach (Button b in new[] { voiceEngineButton, voiceLanguageButton, voiceSaveButton, voiceImportButton }) if (b != null) b.interactable = editable;
            if (voicePrepareButton != null) voicePrepareButton.interactable = editable && (!clone || savedCharacterVoice.installed);
            if (voicePreviewButton != null) voicePreviewButton.interactable = editable && (!clone || savedCharacterVoice.installed);
            if (voiceReferenceInput != null) voiceReferenceInput.interactable = editable && clone;
            if (voiceTranscriptInput != null) voiceTranscriptInput.interactable = editable && clone;
            if (voiceLanguageButton != null)
            {
                voiceLanguageButton.interactable = editable && clone;
                string label = draftVoiceLanguage == "all_zh" ? "Chinese" : draftVoiceLanguage == "all_ja" ? "Japanese" : draftVoiceLanguage == "all_ko" ? "Korean" : draftVoiceLanguage == "all_yue" ? "Cantonese" : "English";
                voiceLanguageButton.GetComponentInChildren<TMP_Text>().text = "Language: " + label;
            }
            if (voiceEngineButton != null) voiceEngineButton.GetComponentInChildren<TMP_Text>().text = clone ? "Reference voice · GPT-SoVITS (CPU)" : "Kokoro · built-in voice";
            if (characterVoiceStatus != null)
            {
                string state = !bound ? "Select a ready character." : characterVoiceBusy ? "Preparing… Stop is available." :
                    clone && !savedCharacterVoice.installed ? "Not installed. Register the reviewed local GPT-SoVITS runtime; Kokoro remains available." :
                    !string.IsNullOrEmpty(savedCharacterVoice.message) ? savedCharacterVoice.message :
                    savedCharacterVoice.state == "cancelled" ? "Cancelled. Saved voice unchanged." :
                    savedCharacterVoice.state == "saved" ? "Voice saved. Prepare or preview to check it. Conditioning is rebuilt once after restarting the runtime." :
                    "Ready. Save applies this voice to the character; previews are not conversation.";
                characterVoiceStatus.text = state + (bound ? "\nSaved engine: " + (savedCharacterVoice.engine == "gpt_sovits" ? "Reference voice" : "Kokoro") +
                    (string.IsNullOrEmpty(savedCharacterVoice.reference_name) ? "" : " · reference saved") : "");
            }
        }

        private async void BrowseVoiceReference()
        {
            CharacterSessionOwner owner = characterVoiceOwner;
            if (owner == null || client == null || !client.OwnsCharacter(owner)) return;
            var result = await LinuxNativeFilePicker.PickAsync("Choose voice reference", "PCM WAV | *.wav");
            if (!client.OwnsCharacter(owner) || characterVoiceOwner == null || !characterVoiceOwner.Matches(owner)) return;
            if (!string.IsNullOrEmpty(result.path)) { voiceReferenceInput.text = result.path; LinuxNativeFilePicker.Remember(result.path); }
            else if (!string.IsNullOrEmpty(result.error)) characterVoiceStatus.text = result.error + " You can also enter the WAV path.";
        }

        private async void SendCharacterVoice(string action)
        {
            if (client == null || characterVoiceOwner == null || !client.OwnsCharacter(characterVoiceOwner)) return;
            if (characterVoiceBusy && action != "cancel" && action != "stop") return;
            characterVoiceRequest = Guid.NewGuid().ToString("N"); characterVoiceAction = action;
            characterVoiceBusy = action != "cancel" && action != "stop" && action != "get";
            if (characterVoiceBusy) characterVoiceOperation = characterVoiceRequest;
            characterVoiceDeadline = Time.unscaledTime + 150f;
            RefreshCharacterVoiceControls();
            await client.CharacterVoiceAsync(action, characterVoiceRequest, characterVoiceOwner,
                draftVoiceEngine, draftVoiceLanguage, voiceTranscriptInput?.text ?? "", voiceReferenceInput?.text ?? "", savedCharacterVoice?.revision ?? "initial", characterVoiceOperation);
        }

        private bool HandleCharacterVoiceError(CommandError error)
        {
            if (error == null || error.request_id != characterVoiceRequest) return false;
            characterVoiceBusy = false; characterVoiceRequest = characterVoiceAction = null;
            RefreshCharacterVoiceControls();
            if (characterVoiceStatus != null) characterVoiceStatus.text = error.message + " Your draft is kept.";
            return true;
        }

        private void CheckCharacterVoiceDeadline()
        {
            if (characterVoiceOwner != null && (client == null || !client.OwnsCharacter(characterVoiceOwner)))
            { RetireCharacterVoiceView(); return; }
            if (characterVoiceRequest == null || Time.unscaledTime < characterVoiceDeadline) return;
            // Retire only this preparation/preview. A late timeout cannot stop
            // speech from a newer accepted conversational turn.
            _ = client.CharacterVoiceAsync("cancel", Guid.NewGuid().ToString("N"), characterVoiceOwner,
                "kokoro", "en", "", "", "", characterVoiceOperation);
            characterVoiceBusy = false; characterVoiceRequest = characterVoiceAction = null;
            RefreshCharacterVoiceControls();
            if (characterVoiceStatus != null) characterVoiceStatus.text = "Voice request timed out. Your draft is kept. Stop the operation, then retry Prepare or Save.";
        }
    }
}
