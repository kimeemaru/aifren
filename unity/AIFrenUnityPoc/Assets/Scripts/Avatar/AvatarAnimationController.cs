using System;
using System.Collections;
using System.Reflection;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>
    /// Small presentation-only layer over standard VRM 1.0 expressions.
    /// It never changes avatar framing, persistence, or the source VRM asset.
    /// </summary>
    [DefaultExecutionOrder(12010)]
    public sealed class AvatarAnimationController : MonoBehaviour
    {
        // The project intentionally has no assembly-definition dependency on
        // UniVRM. Cache the optional VRM 1.0 expression API by reflection so
        // the presentation layer compiles even when a future avatar package is
        // swapped or unavailable.
        private Component vrmInstance;
        private object runtimeExpression;
        private MethodInfo setWeightMethod;
        private object blinkKey;
        private object mouthKey;
        private object happyKey;
        private object surprisedKey;
        private Transform head;
        private Quaternion headBaseRotation;
        private bool hasBlink;
        private bool hasMouth;
        private bool hasHappy;
        private bool hasSurprised;
        private float[] speechEnvelope;
        private float speechStartedAt;
        private float speechDuration;
        private float mouthWeight;
        private float nextBlinkAt;
        private float blinkStartedAt = -1f;
        private float reactionUntil;
        private float reactionWeight;

        public void Configure(GameObject avatar)
        {
            ClearAvatar();
            if (avatar == null) return;

            vrmInstance = FindVrmInstance(avatar);
            Animator humanoidAnimator = avatar.GetComponentInChildren<Animator>();
            if (humanoidAnimator != null && humanoidAnimator.avatar != null && humanoidAnimator.avatar.isHuman)
            {
                head = humanoidAnimator.GetBoneTransform(HumanBodyBones.Head);
                if (head != null) headBaseRotation = head.localRotation;
            }
            if (!ConfigureExpressionRuntime())
            {
                Debug.Log("AIFren avatar animation: VRM expression runtime unavailable; using imported pose.");
                return;
            }

            hasBlink = TryGetExpressionKey("Blink", out blinkKey);
            hasMouth = TryGetExpressionKey("Aa", out mouthKey);
            hasHappy = TryGetExpressionKey("Happy", out happyKey);
            hasSurprised = TryGetExpressionKey("Surprised", out surprisedKey);
            ScheduleNextBlink();
            Debug.Log("AIFren avatar animation: blink=" + hasBlink + ", mouth=" + hasMouth +
                ", happy=" + hasHappy + ", surprised=" + hasSurprised + ".");
        }

        public void ClearAvatar()
        {
            StopSpeech();
            if (runtimeExpression != null)
            {
                SetWeight(blinkKey, 0f);
                SetWeight(mouthKey, 0f);
                SetWeight(happyKey, 0f);
                SetWeight(surprisedKey, 0f);
            }
            vrmInstance = null;
            runtimeExpression = null;
            setWeightMethod = null;
            blinkKey = mouthKey = happyKey = surprisedKey = null;
            head = null;
            hasBlink = hasMouth = hasHappy = hasSurprised = false;
            reactionUntil = 0f;
        }

        public void BeginSpeech(float durationSeconds, float[] envelope)
        {
            speechDuration = Mathf.Max(0f, durationSeconds);
            speechEnvelope = envelope ?? Array.Empty<float>();
            speechStartedAt = Time.unscaledTime;
            mouthWeight = 0f;
        }

        public void StopSpeech()
        {
            speechEnvelope = null;
            speechDuration = 0f;
            mouthWeight = 0f;
            SetWeight(mouthKey, 0f);
        }

        public void PlayAttentiveReaction()
        {
            // A deliberately tiny, non-semantic acknowledgement. Conversation
            // stays neutral unless a future intent layer supplies a reaction.
            reactionWeight = hasHappy ? .16f : hasSurprised ? .10f : 0f;
            reactionUntil = Time.unscaledTime + .85f;
        }

        private void Update()
        {
            if (runtimeExpression == null) return;
            UpdateMouth();
            UpdateBlink();
            UpdateReaction();
        }

        private void LateUpdate()
        {
            if (runtimeExpression == null || head == null) return;
            float time = Time.unscaledTime;
            // Tiny unscripted head life; it deliberately does not mouse-track
            // or replace UniVRM's optional look-at setup.
            float yaw = Mathf.Sin(time * .37f) * .7f;
            float pitch = Mathf.Sin(time * .23f + .8f) * .35f;
            head.localRotation = headBaseRotation * Quaternion.Euler(pitch, yaw, 0f);
        }

        private void UpdateMouth()
        {
            if (!hasMouth) return;
            float elapsed = Time.unscaledTime - speechStartedAt;
            bool speaking = speechEnvelope != null && elapsed >= 0f && elapsed <= speechDuration + .08f;
            float target = speaking ? AvatarAnimationMath.SampleEnvelope(speechEnvelope, elapsed, speechDuration) : 0f;
            mouthWeight = AvatarAnimationMath.SmoothMouth(mouthWeight, target, Time.unscaledDeltaTime);
            SetWeight(mouthKey, mouthWeight * .82f);
            if (!speaking && mouthWeight <= .001f) StopSpeech();
        }

        private void UpdateBlink()
        {
            if (!hasBlink) return;
            float now = Time.unscaledTime;
            if (blinkStartedAt < 0f && now >= nextBlinkAt) blinkStartedAt = now;
            if (blinkStartedAt < 0f) return;
            float phase = (now - blinkStartedAt) / .19f;
            if (phase >= 1f)
            {
                SetWeight(blinkKey, 0f);
                blinkStartedAt = -1f;
                ScheduleNextBlink();
                return;
            }
            SetWeight(blinkKey, Mathf.Sin(phase * Mathf.PI));
        }

        private void UpdateReaction()
        {
            if (reactionWeight <= 0f) return;
            float remaining = Mathf.Clamp01((reactionUntil - Time.unscaledTime) / .85f);
            if (hasHappy) SetWeight(happyKey, reactionWeight * remaining);
            else if (hasSurprised) SetWeight(surprisedKey, reactionWeight * remaining);
            if (remaining <= 0f) reactionWeight = 0f;
        }

        private static Component FindVrmInstance(GameObject avatar)
        {
            foreach (Component candidate in avatar.GetComponentsInChildren<Component>(true))
            {
                if (candidate != null && candidate.GetType().FullName == "UniVRM10.Vrm10Instance") return candidate;
            }
            return null;
        }

        private bool ConfigureExpressionRuntime()
        {
            if (vrmInstance == null) return false;
            PropertyInfo runtimeProperty = vrmInstance.GetType().GetProperty("Runtime");
            object runtime = runtimeProperty != null ? runtimeProperty.GetValue(vrmInstance, null) : null;
            PropertyInfo expressionProperty = runtime != null ? runtime.GetType().GetProperty("Expression") : null;
            runtimeExpression = expressionProperty != null ? expressionProperty.GetValue(runtime, null) : null;
            if (runtimeExpression == null) return false;
            foreach (MethodInfo method in runtimeExpression.GetType().GetMethods())
            {
                if (method.Name == "SetWeight" && method.GetParameters().Length == 2)
                {
                    setWeightMethod = method;
                    return true;
                }
            }
            return false;
        }

        private bool TryGetExpressionKey(string propertyName, out object key)
        {
            key = null;
            if (setWeightMethod == null || runtimeExpression == null) return false;
            Type keyType = setWeightMethod.GetParameters()[0].ParameterType;
            PropertyInfo keyProperty = keyType.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Static);
            key = keyProperty != null ? keyProperty.GetValue(null, null) : null;
            if (key == null) return false;
            PropertyInfo keysProperty = runtimeExpression.GetType().GetProperty("ExpressionKeys");
            IEnumerable keys = keysProperty != null ? keysProperty.GetValue(runtimeExpression, null) as IEnumerable : null;
            if (keys == null) return false;
            foreach (object candidate in keys)
            {
                if (key.Equals(candidate)) return true;
            }
            key = null;
            return false;
        }

        private void SetWeight(object key, float value)
        {
            if (runtimeExpression == null || setWeightMethod == null || key == null) return;
            setWeightMethod.Invoke(runtimeExpression, new object[] { key, Mathf.Clamp01(value) });
        }

        private void ScheduleNextBlink()
        {
            nextBlinkAt = Time.unscaledTime + UnityEngine.Random.Range(2.5f, 5.4f);
        }
    }

    public static class AvatarAnimationMath
    {
        public static float SampleEnvelope(float[] envelope, float elapsedSeconds, float durationSeconds)
        {
            if (envelope == null || envelope.Length == 0 || durationSeconds <= 0f) return 0f;
            float position = Mathf.Clamp01(elapsedSeconds / durationSeconds) * (envelope.Length - 1);
            int lower = Mathf.FloorToInt(position);
            int upper = Mathf.Min(envelope.Length - 1, lower + 1);
            return Mathf.Clamp01(Mathf.Lerp(envelope[lower], envelope[upper], position - lower));
        }

        public static float SmoothMouth(float current, float target, float deltaTime)
        {
            float speed = target > current ? 12f : 7f;
            return Mathf.MoveTowards(current, target, Mathf.Max(0f, deltaTime) * speed);
        }
    }
}
