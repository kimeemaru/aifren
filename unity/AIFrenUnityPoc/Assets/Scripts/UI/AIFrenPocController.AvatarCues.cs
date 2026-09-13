using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UniVRM10;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private Toggle avatarCuesToggle;
        private TMP_Text avatarCuesHint;
        private Button avatarCuesSave;
        private bool savedAvatarCues, avatarCuesDirty, avatarCuesSaving;

        private void AddAvatarCueControls(Transform parent, ref float y)
        {
            AddSettingsHeading(parent, "AVATAR CUES", ref y);
            avatarCuesToggle = CreateToggle(parent, "Explicit avatar cues (ACT preview)", false);
            avatarCuesToggle.name = "Explicit Avatar Cues";
            PlaceTop(avatarCuesToggle.GetComponent<RectTransform>(), y, 44f); y -= 50f;
            avatarCuesToggle.onValueChanged.AddListener(_ => { avatarCuesDirty = true; });
            avatarCuesHint = CreateText(parent,
                "Optional local-model cues. Expression choices can be imperfect. Save applies to future ordinary replies.",
                15f, theme.mutedText, TextAlignmentOptions.TopLeft);
            avatarCuesHint.enableWordWrapping = true;
            PlaceTop(avatarCuesHint.rectTransform, y, 68f); y -= 76f;
            avatarCuesSave = CreateButton(parent, "Save cues", Accent); avatarCuesSave.name = "Save Avatar Cues";
            PlaceTop(avatarCuesSave.GetComponent<RectTransform>(), y, 38f, .05f, .48f);
            avatarCuesSave.onClick.AddListener(SaveAvatarCues);
            Button cancel = CreateButton(parent, "Cancel", Panel); cancel.name = "Cancel Avatar Cues";
            PlaceTop(cancel.GetComponent<RectTransform>(), y, 38f, .52f, .95f);
            cancel.onClick.AddListener(CancelAvatarCuesDraft); y -= 48f;
            Button preview = CreateButton(parent, "Preview smile", Panel); preview.name = "Preview Avatar Smile";
            PlaceTop(preview.GetComponent<RectTransform>(), y, 38f, .05f, .48f);
            preview.onClick.AddListener(PreviewAvatarSmile);
            Button reset = CreateButton(parent, "Reset expression", Panel); reset.name = "Reset Avatar Expression";
            PlaceTop(reset.GetComponent<RectTransform>(), y, 38f, .52f, .95f);
            reset.onClick.AddListener(ResetAvatarExpression); y -= 48f;
            TMP_Text hint = CreateText(parent, "Preview holds the face until Reset. It changes no conversation or saved cue setting.",
                15f, theme.mutedText, TextAlignmentOptions.TopLeft);
            hint.enableWordWrapping = true; PlaceTop(hint.rectTransform, y, 58f); y -= 66f;
            CancelAvatarCuesDraft();
        }

        private void ReceiveAvatarCuesSetting(bool enabled, bool acknowledgement)
        {
            savedAvatarCues = enabled;
            if (acknowledgement) { avatarCuesSaving = false; avatarCuesDirty = false; }
            if (!avatarCuesDirty && avatarCuesToggle != null) avatarCuesToggle.SetIsOnWithoutNotify(enabled);
            if (avatarCuesSave != null) avatarCuesSave.interactable = !avatarCuesSaving;
            if (avatarCuesToggle != null) avatarCuesToggle.interactable = !avatarCuesSaving;
            if (acknowledgement && avatarCuesHint != null) avatarCuesHint.text = "Cue setting saved. Applies to eligible ordinary local replies.";
        }

        private async void SaveAvatarCues()
        {
            if (avatarCuesToggle == null || avatarCuesSaving) return;
            if (client == null || client.State != ConnectionState.Connected)
            { AvatarCuesSaveFailed("Connect to the backend before saving cues."); return; }
            avatarCuesSaving = true;
            avatarCuesSave.interactable = false; avatarCuesToggle.interactable = false;
            try
            {
                await client.SetExplicitAvatarCuesAsync(avatarCuesToggle.isOn);
                if (client.State != ConnectionState.Connected)
                    AvatarCuesSaveFailed("Could not save. Reconnect and try again.");
            }
            catch { AvatarCuesSaveFailed("Could not save. Reconnect and try again."); }
        }

        private void AvatarCuesSaveFailed(string message)
        {
            avatarCuesSaving = false;
            CancelAvatarCuesDraft();
            if (avatarCuesHint != null) avatarCuesHint.text = message;
        }

        private void CancelAvatarCuesDraft()
        {
            if (avatarCuesSaving) return; // An already-sent Save belongs to its acknowledgement.
            avatarCuesDirty = false;
            if (avatarCuesToggle != null) { avatarCuesToggle.SetIsOnWithoutNotify(savedAvatarCues); avatarCuesToggle.interactable = true; }
            if (avatarCuesSave != null) avatarCuesSave.interactable = true;
        }

        private void PreviewAvatarSmile()
        {
            var face = avatarLoader != null ? avatarLoader.GetComponent<AvatarExpressionController>() : null;
            if (face == null) return;
            foreach (var capability in face.AvailableExpressions)
                if (capability.Key.Preset == ExpressionPreset.happy)
                { face.SetExpression(capability, .6f); return; }
            if (avatarCuesHint != null) avatarCuesHint.text = "This avatar has no smile preset to preview.";
        }

        private void ResetAvatarExpression()
        {
            var face = avatarLoader != null ? avatarLoader.GetComponent<AvatarExpressionController>() : null;
            face?.ClearExpression();
        }
    }
}
