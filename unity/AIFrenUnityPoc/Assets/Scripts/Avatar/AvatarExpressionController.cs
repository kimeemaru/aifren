#define DEVELOPMENT_BUILD // Private release builds intentionally retain hidden avatar QA.

using System;
using System.Collections.Generic;
using System.Linq;
using TMPro;
using UniVRM10;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Avatar
{
    public enum AvatarExpressionCategory
    {
        Emotion,
        Mouth,
        Blink,
        LookAt,
        Neutral,
        Custom,
    }

    /// <summary>One concrete expression capability exposed by the active VRM.</summary>
    public sealed class AvatarExpressionCapability
    {
        public string Id { get; }
        public string Name { get; }
        public AvatarExpressionCategory Category { get; }
        public bool OverrideBlink { get; }
        public bool OverrideLookAt { get; }
        public bool OverrideMouth { get; }
        public bool IsProcedural => Key.IsProcedual;
        public bool CanApplyAsPersistentExpression => AvatarExpressionMath.IsPersistentExpressionPreset(Key.Preset);

        internal ExpressionKey Key { get; }

        internal AvatarExpressionCapability(ExpressionKey key, VRM10Expression clip)
        {
            Key = key;
            Id = key.Preset + ":" + key.Name;
            Name = key.Name;
            Category = AvatarExpressionMath.CategoryFor(key.Preset);
            OverrideBlink = clip != null && clip.OverrideBlink != UniGLTF.Extensions.VRMC_vrm.ExpressionOverrideType.none;
            OverrideLookAt = clip != null && clip.OverrideLookAt != UniGLTF.Extensions.VRMC_vrm.ExpressionOverrideType.none;
            OverrideMouth = clip != null && clip.OverrideMouth != UniGLTF.Extensions.VRMC_vrm.ExpressionOverrideType.none;
        }
    }

    /// <summary>
    /// Persistent facial-expression presentation channel. It deliberately owns
    /// no body animation, blink, lip-sync, or gaze scheduling.
    /// </summary>
    [DefaultExecutionOrder(12005)]
    public sealed class AvatarExpressionController : MonoBehaviour
    {
        private const float BlendSeconds = .20f;
        private readonly List<AvatarExpressionCapability> capabilities = new List<AvatarExpressionCapability>();
        private Vrm10Instance instance;
        private Vrm10RuntimeExpression runtime;
        private AvatarExpressionCapability active;
        private AvatarExpressionCapability target;
        private float activeWeight;
        private float targetWeight;
        private float incomingWeight;

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void OnEnable() => AvatarQaVisibility.Changed += RefreshQaOverlay;
        private void OnDisable() => AvatarQaVisibility.Changed -= RefreshQaOverlay;

        private int qaSelection = -1;
        private float qaWeight = 1f;
        private GameObject qaRoot;
        private TextMeshProUGUI qaText;
#endif

        public IReadOnlyList<AvatarExpressionCapability> AvailableExpressions => capabilities;
        public AvatarExpressionCapability ActiveExpression => active;
        public float ActiveIntensity => activeWeight;
        internal bool OwnsPreset(ExpressionPreset preset) =>
            (active != null && active.Key.Preset == preset && activeWeight > .001f) ||
            (target != null && target.Key.Preset == preset && targetWeight > .001f);
        public bool HasActiveExpression => active != null && (activeWeight > .001f || targetWeight > .001f);

        public void Configure(GameObject avatar)
        {
            ClearAvatar();
            instance = avatar != null ? avatar.GetComponentInChildren<Vrm10Instance>() : null;
            runtime = instance != null ? instance.Runtime.Expression : null;
            if (instance == null || runtime == null) return;

            HashSet<ExpressionKey> runtimeKeys = new HashSet<ExpressionKey>(runtime.ExpressionKeys, ExpressionKey.Comparer);
            foreach (var entry in instance.Vrm.Expression.Clips)
            {
                ExpressionKey key = instance.Vrm.Expression.CreateKey(entry.Clip);
                if (runtimeKeys.Contains(key)) capabilities.Add(new AvatarExpressionCapability(key, entry.Clip));
            }
            capabilities.Sort((left, right) =>
            {
                int category = left.Category.CompareTo(right.Category);
                return category != 0 ? category : string.Compare(left.Name, right.Name, StringComparison.OrdinalIgnoreCase);
            });
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            qaSelection = capabilities.Count > 0 ? 0 : -1;
            LogInventory();
            RefreshQaOverlay();
#endif
        }

        public void ClearAvatar()
        {
            if (runtime != null)
            {
                if (active != null) runtime.SetWeight(active.Key, 0f);
                if (target != null && target != active) runtime.SetWeight(target.Key, 0f);
            }
            instance = null;
            runtime = null;
            active = null;
            target = null;
            activeWeight = targetWeight = incomingWeight = 0f;
            capabilities.Clear();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            qaSelection = -1;
            RefreshQaOverlay();
#endif
        }

        public bool SetExpression(string id, float intensity)
        {
            AvatarExpressionCapability capability = capabilities.Find(item => item.Id == id);
            if (capability == null || !capability.CanApplyAsPersistentExpression) return false;
            SetExpression(capability, intensity);
            return true;
        }

        /// <summary>Applies one available standard UniVRM preset without exposing concrete keys to callers.</summary>
        public bool TrySetPresetExpression(ExpressionPreset preset, float intensity)
        {
            AvatarExpressionCapability capability = capabilities.Find(item =>
                item.Key.Preset == preset && item.CanApplyAsPersistentExpression);
            if (capability == null) return false;
            SetExpression(capability, intensity);
            return true;
        }

        public void SetExpression(AvatarExpressionCapability capability, float intensity)
        {
            if (runtime == null || capability == null || !capability.CanApplyAsPersistentExpression || !capabilities.Contains(capability)) return;
            if (target != capability) incomingWeight = 0f;
            target = capability;
            targetWeight = Mathf.Clamp01(intensity);
            if (active == null)
            {
                active = capability;
                activeWeight = 0f;
            }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            Debug.Log("[AvatarExpression QA] target=" + capability.Name + " (" + capability.Category + ") weight=" + targetWeight.ToString("0.00") + ".");
            RefreshQaOverlay();
#endif
        }

        public void ClearExpression()
        {
            if (runtime != null && target != null && target != active) runtime.SetWeight(target.Key, 0f);
            target = null;
            targetWeight = 0f;
            incomingWeight = 0f;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            Debug.Log("[AvatarExpression QA] clearing active expression.");
            RefreshQaOverlay();
#endif
        }

        private void Update()
        {
            TickBlend(Time.unscaledDeltaTime);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            UpdateQaInput();
#endif
        }

        private void TickBlend(float deltaTime)
        {
            if (runtime == null || active == null) return;
            if (target == active)
            {
                activeWeight = AvatarExpressionMath.BlendWeight(activeWeight, targetWeight, deltaTime, BlendSeconds);
                runtime.SetWeight(active.Key, activeWeight);
                return;
            }

            activeWeight = AvatarExpressionMath.BlendWeight(activeWeight, 0f, deltaTime, BlendSeconds);
            runtime.SetWeight(active.Key, activeWeight);
            if (target == null)
            {
                if (activeWeight <= .001f)
                {
                    runtime.SetWeight(active.Key, 0f);
                    active = null;
                }
                return;
            }
            incomingWeight = AvatarExpressionMath.BlendWeight(incomingWeight, targetWeight, deltaTime, BlendSeconds);
            runtime.SetWeight(target.Key, incomingWeight);
            if (activeWeight > .001f || incomingWeight + .001f < targetWeight) return;
            runtime.SetWeight(active.Key, 0f);
            active = target;
            activeWeight = incomingWeight;
            target = active;
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void UpdateQaInput()
        {
            if (!AvatarQaVisibility.Visible) return;
            if (Input.GetKeyDown(KeyCode.F10)) SelectQaExpression(-1);
            if (Input.GetKeyDown(KeyCode.F11)) SelectQaExpression(1);
            if (Input.GetKeyDown(KeyCode.F12)) ApplyQaExpression();
            if (Input.GetKeyDown(KeyCode.Insert)) ClearExpression();
            if (Input.GetKeyDown(KeyCode.PageUp)) { qaWeight = Mathf.Clamp01(qaWeight + .25f); RefreshQaOverlay(); }
            if (Input.GetKeyDown(KeyCode.PageDown)) { qaWeight = Mathf.Clamp01(qaWeight - .25f); RefreshQaOverlay(); }
        }

        private void SelectQaExpression(int step)
        {
            if (capabilities.Count == 0) return;
            qaSelection = (qaSelection + step + capabilities.Count) % capabilities.Count;
            Debug.Log("[AvatarExpression QA] selected " + (qaSelection + 1) + "/" + capabilities.Count + ": " + capabilities[qaSelection].Name + ".");
            RefreshQaOverlay();
        }

        private void ApplyQaExpression()
        {
            if (qaSelection < 0 || qaSelection >= capabilities.Count) return;
            if (!capabilities[qaSelection].CanApplyAsPersistentExpression)
            {
                Debug.Log("[AvatarExpression QA] " + capabilities[qaSelection].Name + " is driven by the " +
                    capabilities[qaSelection].Category + " channel and is not a persistent expression.");
                RefreshQaOverlay();
                return;
            }
            SetExpression(capabilities[qaSelection], qaWeight);
        }

        private void LogInventory()
        {
            string inventory = string.Join("; ", capabilities.Select(item => item.Category + ":" + item.Name +
                " [blink=" + item.OverrideBlink + ", lookAt=" + item.OverrideLookAt + ", mouth=" + item.OverrideMouth + "]"));
            Debug.Log("[AvatarExpression] " + capabilities.Count + " capability/capabilities: " + inventory + ".");
        }

        private void RefreshQaOverlay()
        {
            EnsureQaOverlay();
            if (qaText == null) return;
            string selected = qaSelection >= 0 && qaSelection < capabilities.Count
                ? capabilities[qaSelection].Category + " — " + capabilities[qaSelection].Name +
                    (capabilities[qaSelection].CanApplyAsPersistentExpression ? string.Empty : " (runtime-driven)")
                : "No expression";
            string activeLabel = active != null ? active.Name + " " + activeWeight.ToString("0.00") : "Neutral";
            qaText.text = "Expression QA  " + (qaSelection + 1) + "/" + capabilities.Count + "\n" +
                selected + "  |  weight " + qaWeight.ToString("0.00") + "\n" +
                "F10/F11 select · F12 apply · PgUp/PgDn weight · Ins clear\n" +
                "Blink/mouth/gaze are runtime-driven · Active: " + activeLabel;
            qaRoot.SetActive(AvatarQaVisibility.Visible);
        }

        private void EnsureQaOverlay()
        {
            if (qaText != null) return;
            TextMeshProUGUI sample = FindObjectsOfType<TextMeshProUGUI>(true).FirstOrDefault();
            if (sample == null || sample.canvas == null) return;
            qaRoot = new GameObject("Development Expression QA", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            qaRoot.transform.SetParent(sample.canvas.transform, false);
            Image backing = qaRoot.GetComponent<Image>();
            backing.color = new Color(.025f, .035f, .065f, .84f);
            backing.raycastTarget = false;
            RectTransform backingRect = qaRoot.GetComponent<RectTransform>();
            backingRect.anchorMin = new Vector2(.5f, .66f);
            backingRect.anchorMax = new Vector2(.5f, .66f);
            backingRect.pivot = new Vector2(.5f, .5f);
            backingRect.sizeDelta = new Vector2(520f, 104f);
            GameObject textObject = new GameObject("Text", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            textObject.transform.SetParent(qaRoot.transform, false);
            qaText = textObject.GetComponent<TextMeshProUGUI>();
            qaText.font = sample.font;
            qaText.fontSize = 14f;
            qaText.color = new Color(.88f, .92f, 1f, .96f);
            qaText.alignment = TextAlignmentOptions.Center;
            qaText.raycastTarget = false;
            RectTransform rect = qaText.rectTransform;
            rect.anchorMin = Vector2.zero; rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(12f, 8f); rect.offsetMax = new Vector2(-12f, -8f);
        }
#endif
    }

    public static class AvatarExpressionMath
    {
        public static bool IsPersistentExpressionPreset(ExpressionPreset preset)
        {
            return CategoryFor(preset) != AvatarExpressionCategory.Mouth &&
                CategoryFor(preset) != AvatarExpressionCategory.Blink &&
                CategoryFor(preset) != AvatarExpressionCategory.LookAt;
        }

        public static AvatarExpressionCategory CategoryFor(ExpressionPreset preset)
        {
            switch (preset)
            {
                case ExpressionPreset.aa: case ExpressionPreset.ih: case ExpressionPreset.ou: case ExpressionPreset.ee: case ExpressionPreset.oh:
                    return AvatarExpressionCategory.Mouth;
                case ExpressionPreset.blink: case ExpressionPreset.blinkLeft: case ExpressionPreset.blinkRight:
                    return AvatarExpressionCategory.Blink;
                case ExpressionPreset.lookUp: case ExpressionPreset.lookDown: case ExpressionPreset.lookLeft: case ExpressionPreset.lookRight:
                    return AvatarExpressionCategory.LookAt;
                case ExpressionPreset.neutral: return AvatarExpressionCategory.Neutral;
                case ExpressionPreset.custom: return AvatarExpressionCategory.Custom;
                default: return AvatarExpressionCategory.Emotion;
            }
        }

        public static float BlendWeight(float current, float target, float deltaTime, float duration) =>
            Mathf.MoveTowards(current, target, Mathf.Max(0f, deltaTime) / Mathf.Max(.001f, duration));
    }
}
