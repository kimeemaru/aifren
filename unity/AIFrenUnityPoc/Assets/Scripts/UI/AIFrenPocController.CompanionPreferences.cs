using System;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private enum CompanionPreference { None, Style, Speech, Face }
        private string savedConversationStyle = "roleplay", draftConversationStyle = "roleplay";
        private bool savedResponsiveSpeech = true, savedAutomaticExpressions;
        private bool stylePreferenceDirty, speechPreferenceDirty, facePreferenceDirty;
        private CompanionPreference savingCompanionPreference;
        private Button conversationStyleChoice, conversationStyleSave, responsiveSpeechSave, automaticExpressionSave;
        private Toggle responsiveSpeechToggle, automaticExpressionToggle;
        private TMP_Text conversationStyleHint, responsiveSpeechHint, automaticExpressionHint;
        private string automaticExpressionStatus = "off";

        private void AddConversationStyleControls(Transform parent, ref float y)
        {
            AddSettingsHeading(parent, "CONVERSATION STYLE", ref y);
            conversationStyleChoice = CreateButton(parent, "Roleplay", Panel);
            conversationStyleChoice.name = "Conversation Style";
            PlaceTop(conversationStyleChoice.GetComponent<RectTransform>(), y, 42f); y -= 50f;
            conversationStyleChoice.onClick.AddListener(() => SetConversationStyleDraft(
                draftConversationStyle == "natural" ? "roleplay" : "natural"));
            conversationStyleHint = CompanionPreferenceHint(parent,
                "Natural conversation follows the conversational beat. Roleplay keeps the existing delivery. Neither changes your character.", ref y, 76f);
            conversationStyleSave = AddCompanionPreferenceButtons(parent, "Style", CompanionPreference.Style, ref y);
            RefreshCompanionPreferenceControls();
        }

        private void AddResponsiveSpeechControls(Transform parent, ref float y)
        {
            responsiveSpeechToggle = CreateToggle(parent, "Responsive speech", true);
            responsiveSpeechToggle.name = "Responsive Speech";
            PlaceTop(responsiveSpeechToggle.GetComponent<RectTransform>(), y, 48f); y -= 56f;
            responsiveSpeechToggle.onValueChanged.AddListener(_ => speechPreferenceDirty = true);
            responsiveSpeechHint = CompanionPreferenceHint(parent,
                "Start speaking sooner, while the rest of the reply is prepared. Turn off to prepare the full reply first.", ref y, 72f);
            responsiveSpeechSave = AddCompanionPreferenceButtons(parent, "Speech", CompanionPreference.Speech, ref y);
            RefreshCompanionPreferenceControls();
        }

        private void AddAutomaticExpressionControls(Transform parent, ref float y)
        {
            AddSettingsHeading(parent, "AUTOMATIC EXPRESSIONS", ref y);
            automaticExpressionToggle = CreateToggle(parent, "Automatic expressions (local CPU)", false);
            automaticExpressionToggle.name = "Automatic Expressions";
            PlaceTop(automaticExpressionToggle.GetComponent<RectTransform>(), y, 48f); y -= 56f;
            automaticExpressionToggle.onValueChanged.AddListener(_ => facePreferenceDirty = true);
            automaticExpressionHint = CompanionPreferenceHint(parent, "", ref y, 116f);
            automaticExpressionSave = AddCompanionPreferenceButtons(parent, "Expressions", CompanionPreference.Face, ref y);
            RefreshCompanionPreferenceControls();
        }

        private TMP_Text CompanionPreferenceHint(Transform parent, string text, ref float y, float height)
        {
            TMP_Text hint = CreateText(parent, text, 15f, theme.mutedText, TextAlignmentOptions.TopLeft);
            hint.enableWordWrapping = true; PlaceTop(hint.rectTransform, y, height); y -= height + 8f;
            return hint;
        }

        private Button AddCompanionPreferenceButtons(Transform parent, string label, CompanionPreference kind, ref float y)
        {
            Button save = CreateButton(parent, "Save", Accent); save.name = "Save Companion " + label;
            PlaceTop(save.GetComponent<RectTransform>(), y, 38f, .05f, .48f);
            save.onClick.AddListener(() => SaveCompanionPreference(kind));
            Button cancel = CreateButton(parent, "Cancel", Panel); cancel.name = "Cancel Companion " + label;
            PlaceTop(cancel.GetComponent<RectTransform>(), y, 38f, .52f, .95f);
            cancel.onClick.AddListener(() => CancelCompanionPreferenceDraft(kind)); y -= 50f;
            return save;
        }

        private void SetConversationStyleDraft(string style)
        {
            if (savingCompanionPreference != CompanionPreference.None || (style != "natural" && style != "roleplay")) return;
            draftConversationStyle = style; stylePreferenceDirty = true;
            RefreshCompanionPreferenceControls();
        }

        private void ReceiveCompanionPreferences(string style, bool responsive, bool automatic, string status, bool acknowledgement)
        {
            savedConversationStyle = style == "natural" ? "natural" : "roleplay";
            savedResponsiveSpeech = responsive; savedAutomaticExpressions = automatic;
            if (!automatic) presentationTurn.CancelAutomatic();
            automaticExpressionStatus = string.IsNullOrWhiteSpace(status) ? (automatic ? "not ready" : "off") : status;
            if (acknowledgement)
            {
                switch (savingCompanionPreference)
                {
                    case CompanionPreference.Style: stylePreferenceDirty = false; break;
                    case CompanionPreference.Speech: speechPreferenceDirty = false; break;
                    case CompanionPreference.Face: facePreferenceDirty = false; break;
                }
                savingCompanionPreference = CompanionPreference.None;
            }
            if (!stylePreferenceDirty) draftConversationStyle = savedConversationStyle;
            if (!speechPreferenceDirty) responsiveSpeechToggle?.SetIsOnWithoutNotify(savedResponsiveSpeech);
            if (!facePreferenceDirty) automaticExpressionToggle?.SetIsOnWithoutNotify(savedAutomaticExpressions);
            RefreshCompanionPreferenceControls();
        }

        private void RefreshCompanionPreferenceControls()
        {
            bool ready = savingCompanionPreference == CompanionPreference.None;
            if (conversationStyleChoice != null)
            {
                conversationStyleChoice.interactable = ready;
                conversationStyleChoice.GetComponentInChildren<TMP_Text>().text =
                    draftConversationStyle == "natural" ? "Natural conversation" : "Roleplay";
            }
            if (responsiveSpeechToggle != null) responsiveSpeechToggle.interactable = ready;
            if (automaticExpressionToggle != null) automaticExpressionToggle.interactable = ready;
            if (conversationStyleSave != null) conversationStyleSave.interactable = ready;
            if (responsiveSpeechSave != null) responsiveSpeechSave.interactable = ready;
            if (automaticExpressionSave != null) automaticExpressionSave.interactable = ready;
            if (automaticExpressionHint != null)
                automaticExpressionHint.text = "Status: " + automaticExpressionStatus.TrimEnd('.') +
                    ". CPU only; about 128 MB on disk and 240 MB RAM. Choices are tentative; uncertainty keeps the face. Manual and explicit cues take priority.";
        }

        private async void SaveCompanionPreference(CompanionPreference kind)
        {
            if (savingCompanionPreference != CompanionPreference.None) return;
            if (client == null || client.State != ConnectionState.Connected)
            { CompanionPreferencesSaveFailed("Connect to the backend before saving."); return; }
            savingCompanionPreference = kind; RefreshCompanionPreferenceControls();
            string style = kind == CompanionPreference.Style ? draftConversationStyle : savedConversationStyle;
            bool speech = kind == CompanionPreference.Speech && responsiveSpeechToggle != null ? responsiveSpeechToggle.isOn : savedResponsiveSpeech;
            bool face = kind == CompanionPreference.Face && automaticExpressionToggle != null ? automaticExpressionToggle.isOn : savedAutomaticExpressions;
            try
            {
                await client.SetCompanionPreferencesAsync(style, speech, face);
                if (client.State != ConnectionState.Connected) CompanionPreferencesSaveFailed("Could not save. Reconnect and try again.");
            }
            catch { CompanionPreferencesSaveFailed("Could not save. Reconnect and try again."); }
        }

        private void CompanionPreferencesSaveFailed(string message)
        {
            CompanionPreference failed = savingCompanionPreference;
            savingCompanionPreference = CompanionPreference.None;
            RefreshCompanionPreferenceControls();
            TMP_Text hint = failed == CompanionPreference.Speech ? responsiveSpeechHint :
                failed == CompanionPreference.Face ? automaticExpressionHint : conversationStyleHint;
            if (hint != null) hint.text = message;
        }

        private void CancelCompanionPreferenceDraft(CompanionPreference kind = CompanionPreference.None)
        {
            if (savingCompanionPreference != CompanionPreference.None) return;
            if (kind == CompanionPreference.None || kind == CompanionPreference.Style)
            { stylePreferenceDirty = false; draftConversationStyle = savedConversationStyle; }
            if (kind == CompanionPreference.None || kind == CompanionPreference.Speech)
            { speechPreferenceDirty = false; responsiveSpeechToggle?.SetIsOnWithoutNotify(savedResponsiveSpeech); }
            if (kind == CompanionPreference.None || kind == CompanionPreference.Face)
            { facePreferenceDirty = false; automaticExpressionToggle?.SetIsOnWithoutNotify(savedAutomaticExpressions); }
            RefreshCompanionPreferenceControls();
        }
    }
}
