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
        private Transform leftShoulder;
        private Transform rightShoulder;
        private Transform leftUpperArm;
        private Transform rightUpperArm;
        private Transform leftLowerArm;
        private Transform rightLowerArm;
        private Quaternion headBaseRotation;
        private Quaternion leftShoulderBaseRotation;
        private Quaternion rightShoulderBaseRotation;
        private Quaternion leftUpperArmBaseRotation;
        private Quaternion rightUpperArmBaseRotation;
        private Quaternion leftLowerArmBaseRotation;
        private Quaternion rightLowerArmBaseRotation;
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
        private AvatarGestureIntent activeGesture;
        private float gestureStartedAt;
        private float gestureDuration;
        private float nextGestureAt;
        private AvatarGestureIntent lastGesture;

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
                leftShoulder = humanoidAnimator.GetBoneTransform(HumanBodyBones.LeftShoulder);
                rightShoulder = humanoidAnimator.GetBoneTransform(HumanBodyBones.RightShoulder);
                if (leftShoulder != null) leftShoulderBaseRotation = leftShoulder.localRotation;
                if (rightShoulder != null) rightShoulderBaseRotation = rightShoulder.localRotation;
                leftUpperArm = humanoidAnimator.GetBoneTransform(HumanBodyBones.LeftUpperArm);
                rightUpperArm = humanoidAnimator.GetBoneTransform(HumanBodyBones.RightUpperArm);
                leftLowerArm = humanoidAnimator.GetBoneTransform(HumanBodyBones.LeftLowerArm);
                rightLowerArm = humanoidAnimator.GetBoneTransform(HumanBodyBones.RightLowerArm);
                if (leftUpperArm != null) leftUpperArmBaseRotation = leftUpperArm.localRotation;
                if (rightUpperArm != null) rightUpperArmBaseRotation = rightUpperArm.localRotation;
                if (leftLowerArm != null) leftLowerArmBaseRotation = leftLowerArm.localRotation;
                if (rightLowerArm != null) rightLowerArmBaseRotation = rightLowerArm.localRotation;
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
            ResetGestureBones();
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
            leftShoulder = rightShoulder = null;
            leftUpperArm = rightUpperArm = leftLowerArm = rightLowerArm = null;
            hasBlink = hasMouth = hasHappy = hasSurprised = false;
            reactionUntil = 0f;
            activeGesture = AvatarGestureIntent.None;
            lastGesture = AvatarGestureIntent.None;
            gestureStartedAt = gestureDuration = nextGestureAt = 0f;
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

        /// <summary>Plays one brief semantic gesture using Humanoid bones, never model-specific clips.</summary>
        public bool PlayGesture(AvatarGestureIntent intent)
        {
            float now = Time.unscaledTime;
            if (intent == AvatarGestureIntent.None) return false;
            if (activeGesture != AvatarGestureIntent.None)
            {
                Debug.Log("[AvatarGesture] ignored " + intent + "; " + activeGesture + " is still active.");
                return false;
            }
            if (AvatarAnimationMath.IsSameGestureCoolingDown(intent, lastGesture, now, nextGestureAt))
            {
                Debug.Log("[AvatarGesture] ignored " + intent + " due to cooldown.");
                return false;
            }
            if (!CanPlay(intent))
            {
                Debug.LogWarning("[AvatarGesture] cannot start " + intent + "; required Humanoid bones are unavailable.");
                return false;
            }
            activeGesture = intent;
            lastGesture = intent;
            gestureStartedAt = now;
            gestureDuration = GestureDuration(intent);
            nextGestureAt = now + gestureDuration + 1.1f;
            Debug.Log("[AvatarGesture] started " + intent + ".");
            return true;
        }

        private void Update()
        {
            if (runtimeExpression != null)
            {
                UpdateMouth();
                UpdateBlink();
                UpdateReaction();
            }
        }

        private void LateUpdate()
        {
            float time = Time.unscaledTime;
            // Tiny unscripted head life; it deliberately does not mouse-track
            // or replace UniVRM's optional look-at setup.
            float yaw = Mathf.Sin(time * .37f) * .7f;
            float pitch = Mathf.Sin(time * .23f + .8f) * .35f;
            if (head != null) head.localRotation = headBaseRotation * Quaternion.Euler(pitch, yaw, 0f);
            ApplyGesture(time, pitch, yaw);
        }

        private void ApplyGesture(float now, float idlePitch, float idleYaw)
        {
            if (activeGesture == AvatarGestureIntent.None) return;
            float progress = (now - gestureStartedAt) / Mathf.Max(.01f, gestureDuration);
            if (progress >= 1f)
            {
                ResetGestureBones();
                activeGesture = AvatarGestureIntent.None;
                return;
            }

            float pulse = AvatarAnimationMath.GestureEnvelope(progress);
            if (activeGesture == AvatarGestureIntent.Nod && head != null)
                head.localRotation = headBaseRotation * Quaternion.Euler(idlePitch + Mathf.Sin(progress * Mathf.PI * 2f) * 10f * pulse, idleYaw, 0f);
            else if (activeGesture == AvatarGestureIntent.HeadShake && head != null)
                head.localRotation = headBaseRotation * Quaternion.Euler(idlePitch, idleYaw + Mathf.Sin(progress * Mathf.PI * 3f) * 13f * pulse, 0f);
            else if (activeGesture == AvatarGestureIntent.HeadTilt && head != null)
                head.localRotation = headBaseRotation * Quaternion.Euler(idlePitch, idleYaw, -11f * pulse);
            else if (activeGesture == AvatarGestureIntent.Thinking)
            {
                if (head != null) head.localRotation = headBaseRotation * Quaternion.Euler(idlePitch - 4f * pulse, idleYaw + 7f * pulse, -7f * pulse);
                if (rightUpperArm != null) rightUpperArm.localRotation = rightUpperArmBaseRotation * Quaternion.Euler(-14f * pulse, 4f * pulse, -16f * pulse);
                if (rightLowerArm != null) rightLowerArm.localRotation = rightLowerArmBaseRotation * Quaternion.Euler(-18f * pulse, 0f, 12f * pulse);
            }
            else if (activeGesture == AvatarGestureIntent.Wave)
            {
                bool useRightArm = rightUpperArm != null;
                Transform upperArm = useRightArm ? rightUpperArm : leftUpperArm;
                Transform lowerArm = useRightArm ? rightLowerArm : leftLowerArm;
                Quaternion upperArmBase = useRightArm ? rightUpperArmBaseRotation : leftUpperArmBaseRotation;
                Quaternion lowerArmBase = useRightArm ? rightLowerArmBaseRotation : leftLowerArmBaseRotation;
                float side = useRightArm ? 1f : -1f;
                if (upperArm != null) upperArm.localRotation = upperArmBase * Quaternion.Euler(-42f * pulse, 8f * pulse, -38f * side * pulse);
                if (lowerArm != null) lowerArm.localRotation = lowerArmBase * Quaternion.Euler(-18f * pulse, 0f, Mathf.Sin(progress * Mathf.PI * 5f) * 34f * side * pulse);
            }
            else if (activeGesture == AvatarGestureIntent.Shrug)
            {
                if (leftShoulder != null) leftShoulder.localRotation = leftShoulderBaseRotation * Quaternion.Euler(0f, 0f, 13f * pulse);
                if (rightShoulder != null) rightShoulder.localRotation = rightShoulderBaseRotation * Quaternion.Euler(0f, 0f, -13f * pulse);
                if (leftUpperArm != null) leftUpperArm.localRotation = leftUpperArmBaseRotation * Quaternion.Euler(-6f * pulse, 0f, 13f * pulse);
                if (rightUpperArm != null) rightUpperArm.localRotation = rightUpperArmBaseRotation * Quaternion.Euler(-6f * pulse, 0f, -13f * pulse);
            }
        }

        private bool CanPlay(AvatarGestureIntent intent)
        {
            if (intent == AvatarGestureIntent.Wave) return rightUpperArm != null || leftUpperArm != null;
            if (intent == AvatarGestureIntent.Shrug) return leftShoulder != null || rightShoulder != null || leftUpperArm != null || rightUpperArm != null;
            if (intent == AvatarGestureIntent.Thinking) return head != null || rightUpperArm != null;
            return head != null;
        }

        private static float GestureDuration(AvatarGestureIntent intent)
        {
            switch (intent)
            {
                case AvatarGestureIntent.Nod: return .72f;
                case AvatarGestureIntent.HeadShake: return .82f;
                case AvatarGestureIntent.Wave: return 1.0f;
                case AvatarGestureIntent.Shrug: return .74f;
                case AvatarGestureIntent.HeadTilt: return .76f;
                case AvatarGestureIntent.Thinking: return 1.05f;
                default: return .7f;
            }
        }

        private void ResetGestureBones()
        {
            if (head != null) head.localRotation = headBaseRotation;
            if (leftShoulder != null) leftShoulder.localRotation = leftShoulderBaseRotation;
            if (rightShoulder != null) rightShoulder.localRotation = rightShoulderBaseRotation;
            if (leftUpperArm != null) leftUpperArm.localRotation = leftUpperArmBaseRotation;
            if (rightUpperArm != null) rightUpperArm.localRotation = rightUpperArmBaseRotation;
            if (leftLowerArm != null) leftLowerArm.localRotation = leftLowerArmBaseRotation;
            if (rightLowerArm != null) rightLowerArm.localRotation = rightLowerArmBaseRotation;
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
        public static bool IsSameGestureCoolingDown(AvatarGestureIntent requested, AvatarGestureIntent previous, float now, float cooldownEndsAt) =>
            requested == previous && now < cooldownEndsAt;

        public static float GestureEnvelope(float normalizedTime)
        {
            float time = Mathf.Clamp01(normalizedTime);
            float easeIn = Mathf.SmoothStep(0f, 1f, Mathf.Clamp01(time / .18f));
            float easeOut = 1f - Mathf.SmoothStep(0f, 1f, Mathf.Clamp01((time - .72f) / .28f));
            return easeIn * easeOut;
        }

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
