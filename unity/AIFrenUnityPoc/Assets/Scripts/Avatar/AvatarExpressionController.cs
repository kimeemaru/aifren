using System;
using System.Collections.Generic;
using UniVRM10;
using UnityEngine;

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

        public IReadOnlyList<AvatarExpressionCapability> AvailableExpressions => capabilities;
        public AvatarExpressionCapability ActiveExpression => active;
        public float ActiveIntensity => activeWeight;
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
        }

        public void ClearExpression()
        {
            if (runtime != null && target != null && target != active) runtime.SetWeight(target.Key, 0f);
            target = null;
            targetWeight = 0f;
            incomingWeight = 0f;
        }

        private void Update()
        {
            TickBlend(Time.unscaledDeltaTime);
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
