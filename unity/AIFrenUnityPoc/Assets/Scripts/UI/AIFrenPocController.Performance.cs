using AIFren.UnityPoc.Avatar;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private Toggle subtlePerformanceToggle;
        private Slider subtlePerformanceSlider;
        private TMP_Text performanceHint;
        private bool savedSubtlePerformance;
        private float savedSubtleIntensity = .6f;

        private void AddPerformanceControls(Transform parent, ref float y)
        {
            savedSubtlePerformance = PlayerPrefs.GetInt("AIFren.SubtlePerformance", 0) == 1;
            savedSubtleIntensity = Mathf.Clamp01(PlayerPrefs.GetFloat("AIFren.SubtlePerformanceIntensity", .6f));
            AddSettingsHeading(parent, "BODY PERFORMANCE", ref y);
            subtlePerformanceToggle = CreateToggle(parent, "Subtle breathing and attention", savedSubtlePerformance);
            subtlePerformanceToggle.name = "Subtle Performance";
            PlaceTop(subtlePerformanceToggle.GetComponent<RectTransform>(), y, 42f); y -= 50f;
            subtlePerformanceSlider = CreateSlider(parent, 0f, 1f, savedSubtleIntensity);
            subtlePerformanceSlider.name = "Performance Intensity";
            PlaceTop(subtlePerformanceSlider.GetComponent<RectTransform>(), y, 30f); y -= 40f;
            subtlePerformanceToggle.onValueChanged.AddListener(_ => PreviewSubtlePerformance());
            subtlePerformanceSlider.onValueChanged.AddListener(_ => PreviewSubtlePerformance());
            Button save = CreateButton(parent, "Save performance", Accent); save.name = "Save Performance";
            PlaceTop(save.GetComponent<RectTransform>(), y, 38f, .05f, .48f);
            save.onClick.AddListener(() => {
                savedSubtlePerformance = subtlePerformanceToggle.isOn; savedSubtleIntensity = subtlePerformanceSlider.value;
                PlayerPrefs.SetInt("AIFren.SubtlePerformance", savedSubtlePerformance ? 1 : 0);
                PlayerPrefs.SetFloat("AIFren.SubtlePerformanceIntensity", savedSubtleIntensity); PlayerPrefs.Save();
                performanceHint.text = "Performance saved. Gestures still require an admitted cue or your preview.";
            });
            Button cancel = CreateButton(parent, "Cancel", Panel);
            PlaceTop(cancel.GetComponent<RectTransform>(), y, 38f, .52f, .95f); y -= 48f;
            cancel.onClick.AddListener(() => {
                subtlePerformanceToggle.SetIsOnWithoutNotify(savedSubtlePerformance);
                subtlePerformanceSlider.SetValueWithoutNotify(savedSubtleIntensity); PreviewSubtlePerformance();
            });
            var intents = new[] { AvatarGestureIntent.Nod, AvatarGestureIntent.HeadShake, AvatarGestureIntent.Wave, AvatarGestureIntent.Thinking };
            string[] labels = { "Preview nod", "Head shake", "Greeting wave", "Thinking" };
            for (int i = 0; i < intents.Length; i++)
            {
                var intent = intents[i];
                Button button = CreateButton(parent, labels[i], Panel); button.name = "Preview Performance " + intent;
                PlaceTop(button.GetComponent<RectTransform>(), y, 38f, i % 2 == 0 ? .05f : .52f, i % 2 == 0 ? .48f : .95f);
                button.onClick.AddListener(() => PreviewPerformanceGesture(intent));
                if (i % 2 == 1) y -= 46f;
            }
            Button stop = CreateButton(parent, "Stop gesture", Panel); stop.name = "Stop Performance";
            PlaceTop(stop.GetComponent<RectTransform>(), y, 38f); y -= 46f;
            stop.onClick.AddListener(() => avatarAnimation?.RetireResponseMotion());
            performanceHint = CompanionPreferenceHint(parent, "A preview closes Settings so you can see the gesture. Reopen Settings to continue; your drafts stay here. Capabilities still apply.", ref y, 72f);
            PreviewSubtlePerformance();
        }

        private void PreviewSubtlePerformance() => avatarAnimation?.SetSubtlePerformance(
            subtlePerformanceToggle != null && subtlePerformanceToggle.isOn,
            subtlePerformanceSlider != null ? subtlePerformanceSlider.value : savedSubtleIntensity);

        private void PreviewPerformanceGesture(AvatarGestureIntent intent)
        {
            var resolver = avatarLoader != null ? avatarLoader.GetComponent<AvatarPresentationResolver>() : null;
            bool played = resolver != null && resolver.TryGesture(intent);
            if (performanceHint != null) performanceHint.text = played ? "Previewing " + intent + ". Returns to idle automatically." :
                "This gesture is unavailable, still playing, or limited by the current capability state.";
            if (played)
            {
                // Reveal the existing avatar without cancelling unrelated drafts,
                // moving its framing, or scheduling a stale panel reopen.
                settingsPanel?.SetActive(false);
                modalScrim?.SetActive(false);
                RefreshSceneDrawerAvailability();
            }
        }
    }
}
