using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private TMP_InputField subtitleColorInput;
        private TMP_Text subtitleColorPreview, subtitleColorHint;
        private Button subtitleColorSave;
        private void AddSubtitleColorControls(Transform parent, ref float y)
        {
            AddSettingsHeading(parent, "HIDDEN SUBTITLES", ref y);
            TMP_Text label = CreateText(parent, "Text color", 18f, theme.text, TextAlignmentOptions.MidlineLeft);
            PlaceTop(label.rectTransform, y, 30f); y -= 36f;
            Button white = CreateButton(parent, "Current white", Panel); white.name = "Subtitle Current White";
            PlaceTop(white.GetComponent<RectTransform>(), y, 38f, .05f, .48f);
            white.onClick.AddListener(() => SetSubtitleColorDraft("white"));
            Button pink = CreateButton(parent, "Classic pink", Panel); pink.name = "Subtitle Classic Pink";
            PlaceTop(pink.GetComponent<RectTransform>(), y, 38f, .52f, .95f);
            pink.onClick.AddListener(() => SetSubtitleColorDraft("pink")); y -= 48f;
            subtitleColorInput = CreateInputField(parent); subtitleColorInput.name = "Subtitle Text Color";
            subtitleColorInput.characterLimit = 7;
            ((TMP_Text)subtitleColorInput.placeholder).text = "Custom #RRGGBB";
            PlaceTop(subtitleColorInput.GetComponent<RectTransform>(), y, 38f);
            subtitleColorInput.onValueChanged.AddListener(_ => RefreshSubtitleColorPreview()); y -= 46f;
            subtitleColorPreview = CreateText(parent, "A quiet moment together.", 28f, SubtitleStyle.Face, TextAlignmentOptions.Center);
            subtitleColorPreview.name = "Subtitle Color Preview";
            if (hiddenSubtitleFont != null) subtitleColorPreview.font = hiddenSubtitleFont;
            if (hiddenSubtitleMaterial != null) subtitleColorPreview.fontSharedMaterial = hiddenSubtitleMaterial;
            PlaceTop(subtitleColorPreview.rectTransform, y, 56f); y -= 60f;
            subtitleColorHint = CreateText(parent, "", 15f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
            PlaceTop(subtitleColorHint.rectTransform, y, 32f); y -= 40f;
            subtitleColorSave = CreateButton(parent, "Save color", Accent); subtitleColorSave.name = "Subtitle Save Color";
            PlaceTop(subtitleColorSave.GetComponent<RectTransform>(), y, 38f, .05f, .33f);
            subtitleColorSave.onClick.AddListener(SaveSubtitleColor);
            Button cancel = CreateButton(parent, "Cancel", Panel); cancel.name = "Subtitle Cancel Color";
            PlaceTop(cancel.GetComponent<RectTransform>(), y, 38f, .36f, .64f);
            cancel.onClick.AddListener(CancelSubtitleColor);
            Button reset = CreateButton(parent, "Reset color", Panel); reset.name = "Subtitle Reset Color";
            PlaceTop(reset.GetComponent<RectTransform>(), y, 38f, .67f, .95f);
            reset.onClick.AddListener(() => SetSubtitleColorDraft("white")); y -= 52f;
            CancelSubtitleColor();
        }
        private void SetSubtitleColorDraft(string value)
        {
            if (subtitleColorInput == null) return;
            subtitleColorInput.SetTextWithoutNotify(value); RefreshSubtitleColorPreview();
        }
        private void RefreshSubtitleColorPreview()
        {
            if (subtitleColorInput == null || subtitleColorPreview == null) return;
            bool valid = SubtitleTextColor.TryParse(subtitleColorInput.text, out Color color);
            subtitleColorPreview.color = valid ? color : SubtitleTextColor.Current;
            subtitleColorHint.text = valid ? "Preview only · Save applies this color." : "Use six RGB hex digits, such as #B8E6FF.";
            if (subtitleColorSave != null) subtitleColorSave.interactable = valid;
        }
        private void SaveSubtitleColor()
        {
            if (subtitleColorInput == null || !SubtitleTextColor.Save(subtitleColorInput.text)) return;
            EnsureHiddenSubtitlePresentation(); CancelSubtitleColor(); subtitleColorHint.text = "Text color saved.";
        }
        private void CancelSubtitleColor() => SetSubtitleColorDraft(SubtitleTextColor.Saved);
    }
}
