#define DEVELOPMENT_BUILD // Private release builds intentionally retain hidden avatar QA.

using System.Linq;
using TMPro;
using UniVRM10;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>
    /// Development-only manual QA owner for UniVRM's LookAt input. It never
    /// writes expression weights or eye-bone transforms directly.
    /// </summary>
    public sealed class AvatarGazeController : MonoBehaviour
    {
        private Vrm10Instance instance;
        private Vrm10RuntimeLookAt lookAt;
        private VRM10ObjectLookAt.LookAtTargetTypes originalTargetType;
        private bool capturedTargetType;
        private bool presentationSuppressed;

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void OnEnable() => AvatarQaVisibility.Changed += RefreshQaVisibility;
        private void OnDisable() => AvatarQaVisibility.Changed -= RefreshQaVisibility;

        private const float HorizontalDegrees = 25f;
        private const float VerticalDegrees = 18f;
        private const float TransitionSeconds = .20f;
        private GameObject qaToastRoot;
        private TextMeshProUGUI qaToast;
        private float qaToastUntil;
        private Vector2 currentYawPitch;
        private Vector2 targetYawPitch;
        private float yawPitchDegreesPerSecond;
#endif

        public bool IsAvailable => lookAt != null;
        public bool PresentationSuppressed => presentationSuppressed;

        public void Configure(GameObject avatar)
        {
            ClearAvatar();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            instance = avatar != null ? avatar.GetComponentInChildren<Vrm10Instance>() : null;
            if (instance == null || instance.Vrm == null || instance.Vrm.LookAt == null)
            {
                Debug.Log("[AvatarGaze QA] gaze unavailable for the active avatar.");
                ShowStatus("Unavailable");
                return;
            }
            lookAt = instance.Runtime.LookAt;
            if (lookAt == null)
            {
                Debug.Log("[AvatarGaze QA] gaze unavailable for the active avatar.");
                ShowStatus("Unavailable");
                return;
            }

            originalTargetType = instance.LookAtTargetType;
            capturedTargetType = true;
            CenterGaze();
#endif
        }

        public void SetManualGaze(float yaw, float pitch)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (lookAt == null || instance == null)
            {
                ShowStatus("Unavailable");
                return;
            }
            instance.LookAtTargetType = VRM10ObjectLookAt.LookAtTargetTypes.YawPitchValue;
            targetYawPitch = new Vector2(yaw, pitch);
            yawPitchDegreesPerSecond = AvatarGazeMath.TransitionSpeed(currentYawPitch, targetYawPitch, TransitionSeconds);
            string direction = AvatarGazeMath.LabelFor(yaw, pitch);
            Debug.Log("[AvatarGaze QA] " + direction + " (yaw=" + yaw.ToString("0.#") + ", pitch=" + pitch.ToString("0.#") + ").");
            ShowStatus(direction + "  " + yaw.ToString("0.#") + "° / " + pitch.ToString("0.#") + "°");
#endif
        }

        public void CenterGaze()
        {
            SetManualGaze(0f, 0f);
        }

        public void SetPresentationSuppressed(bool suppressed)
        {
            presentationSuppressed = suppressed;
            if (suppressed) CenterGaze();
        }

        public void ClearAvatar()
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (instance != null && capturedTargetType)
            {
                instance.LookAtTargetType = originalTargetType;
            }
            instance = null;
            lookAt = null;
            capturedTargetType = false;
            presentationSuppressed = false;
            currentYawPitch = Vector2.zero;
            targetYawPitch = Vector2.zero;
            yawPitchDegreesPerSecond = 0f;
#endif
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void Update()
        {
            UpdateManualGaze(Time.unscaledDeltaTime);
            if (presentationSuppressed || !AvatarQaVisibility.Visible || !IsControlHeld() || IsTextInputFocused()) return;
            if (Input.GetKeyDown(KeyCode.LeftArrow)) SetManualGaze(-HorizontalDegrees, 0f);
            if (Input.GetKeyDown(KeyCode.RightArrow)) SetManualGaze(HorizontalDegrees, 0f);
            if (Input.GetKeyDown(KeyCode.UpArrow)) SetManualGaze(0f, VerticalDegrees);
            if (Input.GetKeyDown(KeyCode.DownArrow)) SetManualGaze(0f, -VerticalDegrees);
            if (Input.GetKeyDown(KeyCode.Home)) CenterGaze();
        }

        private void UpdateManualGaze(float deltaTime)
        {
            if (lookAt == null) return;
            currentYawPitch = AvatarGazeMath.MoveToward(
                currentYawPitch, targetYawPitch, yawPitchDegreesPerSecond, deltaTime);
            lookAt.SetYawPitchManually(currentYawPitch.x, currentYawPitch.y);
        }

        private static bool IsControlHeld() => Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl);

        private static bool IsTextInputFocused()
        {
            GameObject selected = EventSystem.current != null ? EventSystem.current.currentSelectedGameObject : null;
            return selected != null && selected.GetComponentInParent<TMP_InputField>() != null;
        }

        private void ShowStatus(string state)
        {
            EnsureToast();
            if (qaToast == null) return;
            qaToast.text = "Gaze: " + state + "\nCtrl + arrows · Ctrl + Home center";
            qaToastRoot.SetActive(AvatarQaVisibility.Visible);
            qaToastUntil = Time.unscaledTime + 2.5f;
        }

        private void RefreshQaVisibility()
        {
            if (qaToastRoot != null) qaToastRoot.SetActive(AvatarQaVisibility.Visible);
        }

        private void EnsureToast()
        {
            if (qaToast != null) return;
            TextMeshProUGUI sample = FindObjectsOfType<TextMeshProUGUI>(true).FirstOrDefault();
            if (sample == null || sample.canvas == null) return;
            qaToastRoot = new GameObject("Development Gaze QA Toast", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            qaToastRoot.transform.SetParent(sample.canvas.transform, false);
            Image backing = qaToastRoot.GetComponent<Image>();
            backing.color = new Color(.025f, .035f, .065f, .84f);
            backing.raycastTarget = false;
            RectTransform backingRect = qaToastRoot.GetComponent<RectTransform>();
            backingRect.anchorMin = new Vector2(.5f, .73f);
            backingRect.anchorMax = new Vector2(.5f, .73f);
            backingRect.pivot = new Vector2(.5f, .5f);
            backingRect.sizeDelta = new Vector2(420f, 58f);

            GameObject textObject = new GameObject("Text", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            textObject.transform.SetParent(qaToastRoot.transform, false);
            qaToast = textObject.GetComponent<TextMeshProUGUI>();
            qaToast.font = sample.font;
            qaToast.fontSize = 14f;
            qaToast.color = new Color(.88f, .92f, 1f, .96f);
            qaToast.alignment = TextAlignmentOptions.Center;
            qaToast.raycastTarget = false;
            RectTransform rect = qaToast.rectTransform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(12f, 8f);
            rect.offsetMax = new Vector2(-12f, -8f);
            qaToastRoot.SetActive(false);
        }
#endif
    }

    public static class AvatarGazeMath
    {
        public static float TransitionSpeed(Vector2 from, Vector2 target, float duration)
        {
            return Vector2.Distance(from, target) / Mathf.Max(.001f, duration);
        }

        public static Vector2 MoveToward(Vector2 current, Vector2 target, float speed, float deltaTime)
        {
            return Vector2.MoveTowards(current, target, Mathf.Max(0f, speed) * Mathf.Max(0f, deltaTime));
        }

        public static string LabelFor(float yaw, float pitch)
        {
            if (yaw < 0f) return "Left";
            if (yaw > 0f) return "Right";
            if (pitch < 0f) return "Down";
            if (pitch > 0f) return "Up";
            return "Center";
        }
    }
}
