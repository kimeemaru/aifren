#define DEVELOPMENT_BUILD // Private release builds intentionally retain hidden avatar QA.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using TMPro;
using UniGLTF;
using UniVRM10;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Avatar
{
    /// <summary>Development-only external VRMA proof player. It never owns facial channels.</summary>
    public sealed class AvatarVrmaGesturePlayer : MonoBehaviour
    {
        private Vrm10Instance target;
        private AvatarGestureIntent sourceIntent = AvatarGestureIntent.Wave;
        public bool OriginalNodTrialSelected { get; private set; }
        public bool OriginalNodTrialReady => OriginalNodTrialSelected && nodSource != null;
        private Vrm10AnimationInstance source;
        private Vrm10AnimationInstance waveSource, nodSource;
        private Animation animationComponent;
        private AnimationState sourceAnimationState;
        private float startedAt;
        private PlaybackState playbackState;
        private readonly List<CapturedBonePose> capturedBaseline = new List<CapturedBonePose>();
        private readonly List<CapturedBonePose> exitPose = new List<CapturedBonePose>();
        private bool hasCapturedBaseline;
        private float transitionStartedAt;
        private HipsInPlaceCalibration hipsCalibration;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private bool exitHipsLogged;
#endif

        // These are deliberately internal presentation constants, not product settings.
        // They keep a brief authored gesture from popping over AIFren's relaxed pose.
        private const float EntryBlendSeconds = .18f;
        private const float ExitBlendSeconds = .20f;

        private enum PlaybackState
        {
            Idle,
            Entering,
            Playing,
            FinalFramePending,
            Exiting,
        }

        private readonly struct HipsInPlaceCalibration
        {
            public readonly Vector3 SourceInitial;
            public readonly Vector3 SourceReference;
            public readonly Vector3 TargetBaseline;
            public readonly Vector3 TargetReference;
            public readonly float Scale;
            public readonly Transform TargetControlRigRoot;

            public HipsInPlaceCalibration(
                Vector3 sourceInitial,
                Vector3 sourceReference,
                Vector3 targetBaseline,
                Vector3 targetReference,
                float scale,
                Transform targetControlRigRoot)
            {
                SourceInitial = sourceInitial;
                SourceReference = sourceReference;
                TargetBaseline = targetBaseline;
                TargetReference = targetReference;
                Scale = scale;
                TargetControlRigRoot = targetControlRigRoot;
            }

            public Vector3 SourceBaselineOffset => (TargetBaseline - TargetReference) / Scale;
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private static readonly HumanBodyBones[] QaPoseBones =
        {
            HumanBodyBones.Hips, HumanBodyBones.Spine, HumanBodyBones.Chest, HumanBodyBones.UpperChest,
            HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg, HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg,
            HumanBodyBones.LeftShoulder, HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand,
            HumanBodyBones.RightShoulder, HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand,
        };
        private readonly List<string> qaFiles = new List<string>();
        private int qaFileIndex = -1;
        private int loadGeneration;
        private TextMeshProUGUI qaToast;
        private GameObject qaToastRoot;
        private float qaToastUntil;
#endif

        private readonly struct CapturedBonePose
        {
            public readonly Transform Transform;
            public readonly Vector3 LocalPosition;
            public readonly Quaternion LocalRotation;

            public CapturedBonePose(Transform transform)
            {
                Transform = transform;
                LocalPosition = transform.localPosition;
                LocalRotation = transform.localRotation;
            }
        }

        public float Duration => animationComponent != null && animationComponent.clip != null ? animationComponent.clip.length : 0f;
        public bool IsActive => playbackState != PlaybackState.Idle;

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        public string QaSelectionLabel => sourceIntent == AvatarGestureIntent.Nod ? "AIFren original acknowledgment Nod" :
            qaFileIndex >= 0 && qaFileIndex < qaFiles.Count ? Path.GetFileName(qaFiles[qaFileIndex]) : "None";
#endif

        public void Configure(GameObject avatar)
        {
            Unload();
            target = avatar != null ? avatar.GetComponentInChildren<Vrm10Instance>() : null;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogTargetArmMappings();
            OriginalNodTrialSelected = false; ReloadQaSource();
#endif
        }

        public void ClearAvatar()
        {
            Unload();
            target = null;
        }

        public bool TryPlay(AvatarGestureIntent intent)
        {
            Vrm10AnimationInstance candidate = intent == AvatarGestureIntent.Wave ? waveSource :
                intent == AvatarGestureIntent.Nod && OriginalNodTrialSelected ? nodSource : null;
            if (candidate == null) return false;
            if (candidate != source) ActivateSource(candidate, intent);
            if (intent != sourceIntent || target == null || source == null || animationComponent == null || animationComponent.clip == null)
                return false;
            StopImmediately();
            CaptureBaseline();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogHipsBeforePlayback();
#endif
            // Follow the current UniVRM runtime-VRMA documentation: start the
            // source animation, then connect its sampled pose to the target.
            // Assignment has no immediate pose side effect, but this keeps the
            // source sampling lifecycle explicit for QA.
            // Keep the source first sample for diagnostics. The in-place
            // anchor itself is the VRMA reference hips pose: a gesture may
            // deliberately begin crouched, so zeroing that authored pose would
            // lift the target body out of its intended crouch.
            AnimationState sourceState = animationComponent[animationComponent.clip.name];
            if (sourceState == null)
            {
                RestoreBaseline();
                Debug.LogWarning("[AvatarVrma] source VRMA has no playable AnimationState; Wave was not played.");
                return false;
            }
            sourceAnimationState = sourceState;
            sourceAnimationState.wrapMode = WrapMode.ClampForever;
            sourceAnimationState.time = 0f;
            sourceAnimationState.speed = 0f;
            animationComponent.Play(sourceAnimationState.name);
            animationComponent.Sample();
            if (!TryCreateHipsInPlaceCalibration(out hipsCalibration))
            {
                animationComponent.Stop();
                RestoreBaseline();
                Debug.LogWarning("[AvatarVrma] cannot establish an in-place Humanoid hips baseline; Wave was not played.");
                return false;
            }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogHips("after source first-frame sample");
#endif
            target.Runtime.VrmAnimation = new BodyOnlyVrma(source, hipsCalibration, sourceIntent == AvatarGestureIntent.Nod ? CaptureStationaryChannels() : null);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogHips("after VrmAnimation assignment");
            StartCoroutine(LogHipsTimeline());
#endif
            startedAt = Time.unscaledTime;
            transitionStartedAt = startedAt;
            playbackState = PlaybackState.Entering;
            Debug.Log("[AvatarVrma] started body-only " + intent + ".");
            return true;
        }

        public void Tick()
        {
            switch (playbackState)
            {
                case PlaybackState.Entering:
                    BlendBaselineToCurrent(BlendProgress(transitionStartedAt, EntryBlendSeconds));
                    if (Time.unscaledTime >= transitionStartedAt + EntryBlendSeconds)
                        BeginSourcePlayback();
                    break;
                case PlaybackState.Playing:
                    if (HasReachedOneShotEnd())
                    {
                        FreezeSourceAt(Duration);
                        playbackState = PlaybackState.FinalFramePending;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                        LogHips("final source sample clamped; target updates next frame");
#endif
                    }
                    break;
                case PlaybackState.FinalFramePending:
                    // Runtime.Process sees the clamped final source sample on
                    // this frame before AvatarAnimationController.Tick runs.
                    // Capture that actual target pose for the exit blend.
                    BeginExit();
                    break;
                case PlaybackState.Exiting:
                    BlendExitToBaseline(BlendProgress(transitionStartedAt, ExitBlendSeconds));
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    if (!exitHipsLogged && Time.unscaledTime >= transitionStartedAt + ExitBlendSeconds * .5f)
                    {
                        LogHips("during exit");
                        exitHipsLogged = true;
                    }
#endif
                    if (Time.unscaledTime >= transitionStartedAt + ExitBlendSeconds)
                    {
                        // Keep the provider connected during the complete
                        // visual blend. Detaching it first lets UniVRM/sample
                        // lifecycle changes replace the final authored pose
                        // before this player can blend that displayed pose.
                        if (animationComponent != null) animationComponent.Stop();
                        if (target != null) target.Runtime.VrmAnimation = null;
                        RestoreBaseline();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                        LogHips("after exact baseline restoration");
#endif
                        playbackState = PlaybackState.Idle;
                    }
                    break;
            }
        }

        public void Stop()
        {
            if (playbackState == PlaybackState.Idle) return;
            BeginExit();
        }

        private void BeginExit()
        {
            if (playbackState == PlaybackState.Exiting || playbackState == PlaybackState.Idle) return;
            CaptureCurrentPose(exitPose);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogHips("before exit from displayed pose");
            exitHipsLogged = false;
#endif
            // Freeze the source only after capturing the pose that was actually
            // displayed. Keep the provider attached during exit so no source
            // reset/wrap can replace that pose before the blend consumes it.
            FreezeSourceAtCurrentTime();
            transitionStartedAt = Time.unscaledTime;
            playbackState = PlaybackState.Exiting;
        }

        private void StopImmediately()
        {
            if (animationComponent != null) animationComponent.Stop();
            if (target != null) target.Runtime.VrmAnimation = null;
            RestoreBaseline();
            exitPose.Clear();
            playbackState = PlaybackState.Idle;
        }

        private void Unload()
        {
            StopImmediately();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            loadGeneration++;
#endif
            if (waveSource != null) Destroy(waveSource.gameObject);
            if (nodSource != null && nodSource != waveSource) Destroy(nodSource.gameObject);
            waveSource = nodSource = source = null;
            animationComponent = null;
            sourceAnimationState = null;
            hipsCalibration = default;
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        public void ReloadQaSource()
        {
            string directPath = NormalizeFile(Environment.GetEnvironmentVariable("AIFREN_VRMA_QA_PATH"));
            if (!string.IsNullOrEmpty(directPath))
            {
                SetQaFiles(new[] { directPath });
                return;
            }

            string folder = NormalizeDirectory(Environment.GetEnvironmentVariable("AIFREN_VRMA_QA_DIR"));
            if (string.IsNullOrEmpty(folder))
            {
                folder = NormalizeDirectory(Path.Combine(Application.streamingAssetsPath, "AvatarQaVrma"));
            }

            List<string> files = DiscoverQaFiles(folder);
            SetQaFiles(files);
        }

        public void SelectOriginalNodTrial(bool enabled)
        {
            GetComponent<AvatarAnimationController>()?.RetireResponseMotion();
            StopImmediately();
            OriginalNodTrialSelected = enabled;
            int generation = ++loadGeneration;
            if (!enabled) { if (waveSource != null) ActivateSource(waveSource, AvatarGestureIntent.Wave); else ReloadQaSource(); return; }
            if (nodSource != null) { ActivateSource(nodSource, AvatarGestureIntent.Nod); return; }
            _ = LoadAsync(Path.Combine(Application.streamingAssetsPath, "OriginalMotionTrials/AcknowledgmentNod.vrma"),
                generation, -1, AvatarGestureIntent.Nod);
        }

        public void SelectPreviousQaVrma()
        {
            SelectQaVrma(qaFileIndex - 1);
        }

        public void SelectNextQaVrma()
        {
            SelectQaVrma(qaFileIndex + 1);
        }

        private void SelectQaVrma(int index)
        {
            if (qaFiles.Count == 0) return;
            if (index < 0) index = qaFiles.Count - 1;
            if (index >= qaFiles.Count) index = 0;
            RequestQaVrma(index);
        }

        private void SetQaFiles(IEnumerable<string> files)
        {
            List<string> nextFiles = files.ToList();
            qaFiles.Clear();
            qaFiles.AddRange(nextFiles);
            if (qaFiles.Count == 0)
            {
                qaFileIndex = -1;
                Unload();
                Debug.Log("[AvatarVrma QA] no usable .vrma files are configured.");
                return;
            }
            int selected = qaFileIndex >= 0 && qaFileIndex < qaFiles.Count ? qaFileIndex : 0;
            RequestQaVrma(selected);
        }

        private void RequestQaVrma(int index)
        {
            if (target == null || index < 0 || index >= qaFiles.Count) return;
            OriginalNodTrialSelected = false;
            sourceIntent = AvatarGestureIntent.None;
            int generation = ++loadGeneration;
            string fileName = Path.GetFileName(qaFiles[index]);
            Debug.Log("[AvatarVrma QA] loading " + (index + 1) + "/" + qaFiles.Count + ": " + fileName + ".");
            ShowQaToast(index, fileName, "Loading…");
            _ = LoadAsync(qaFiles[index], generation, index);
        }

        /// <summary>Development-only non-recursive QA-file inventory.</summary>
        public static List<string> DiscoverQaFiles(string folder)
        {
            string normalized = NormalizeDirectory(folder);
            if (string.IsNullOrEmpty(normalized)) return new List<string>();
            try
            {
                return Directory.EnumerateFiles(normalized, "*", SearchOption.TopDirectoryOnly)
                    .Where(path => string.Equals(Path.GetExtension(path), ".vrma", StringComparison.OrdinalIgnoreCase))
                    .Where(IsOrdinaryFile)
                    .OrderBy(Path.GetFileName, StringComparer.OrdinalIgnoreCase)
                    .ThenBy(Path.GetFileName, StringComparer.Ordinal)
                    .ToList();
            }
            catch (IOException) { return new List<string>(); }
            catch (UnauthorizedAccessException) { return new List<string>(); }
        }

        private static bool IsOrdinaryFile(string path)
        {
            try { return (File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) == 0; }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
        }

        private static string NormalizeDirectory(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return string.Empty;
            try
            {
                string normalized = Path.GetFullPath(path);
                return Directory.Exists(normalized) ? normalized : string.Empty;
            }
            catch (Exception exception) when (exception is ArgumentException || exception is NotSupportedException) { return string.Empty; }
        }

        private static string NormalizeFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) return string.Empty;
            try
            {
                string normalized = Path.GetFullPath(path);
                return string.Equals(Path.GetExtension(normalized), ".vrma", StringComparison.OrdinalIgnoreCase) &&
                    File.Exists(normalized) && IsOrdinaryFile(normalized) ? normalized : string.Empty;
            }
            catch (Exception exception) when (exception is ArgumentException || exception is NotSupportedException) { return string.Empty; }
        }
#endif

        private async Task LoadAsync(string path, int generation = 0, int requestedIndex = -1, AvatarGestureIntent intent = AvatarGestureIntent.Wave)
        {
            if (!File.Exists(path))
            {
                Debug.LogWarning("[AvatarVrma] QA file does not exist; no VRMA was loaded.");
                return;
            }
            RuntimeGltfInstance instance = null;
            try
            {
                using GltfData data = new AutoGltfFileParser(path).Parse();
                var vrmaData = new VrmAnimationData(data);
                using var loader = new VrmAnimationImporter(vrmaData);
                instance = await loader.LoadAsync(new ImmediateCaller());
                if (generation != 0 && generation != loadGeneration)
                {
                    Destroy(instance.gameObject);
                    return;
                }
                Vrm10AnimationInstance candidateSource = instance.GetComponent<Vrm10AnimationInstance>();
                Animation candidateAnimation = candidateSource != null ? candidateSource.GetComponent<Animation>() : null;
                if (candidateSource == null || candidateAnimation == null || candidateAnimation.clip == null)
                    throw new InvalidOperationException("UniVRM did not create a playable Vrm10AnimationInstance.");
                ReplaceSource(candidateSource, candidateAnimation, intent);
                instance = null;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (requestedIndex >= 0 && requestedIndex < qaFiles.Count)
                {
                    qaFileIndex = requestedIndex;
                    Debug.Log("[AvatarVrma QA] selected " + (qaFileIndex + 1) + "/" + qaFiles.Count + ": " + QaSelectionLabel + ".");
                    ShowQaToast(qaFileIndex, QaSelectionLabel, "Ready — F1 to play");
                }
#endif
                source.ShowBoxMan(false);
                Debug.Log("[AvatarVrma] loaded external QA VRMA; duration=" + Duration.ToString("F3") +
                    ", expressionTracks=" + source.ExpressionMap.Count + ", gazeTrack=" + source.LookAt.HasValue + ".");
            }
            catch (Exception exception)
            {
                if (instance != null) Destroy(instance.gameObject);
                Debug.LogWarning("[AvatarVrma] unable to load QA VRMA (" + exception.GetType().Name + ").");
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (generation == loadGeneration && requestedIndex >= 0 && requestedIndex < qaFiles.Count)
                    ShowQaToast(requestedIndex, Path.GetFileName(qaFiles[requestedIndex]), "Load failed");
#endif
            }
        }

        private void ActivateSource(Vrm10AnimationInstance candidate, AvatarGestureIntent intent)
        {
            StopImmediately(); source = candidate; sourceIntent = intent;
            animationComponent = candidate.GetComponent<Animation>(); sourceAnimationState = null;
        }

        private void ReplaceSource(Vrm10AnimationInstance candidateSource, Animation candidateAnimation, AvatarGestureIntent intent = AvatarGestureIntent.Wave)
        {
            Vrm10AnimationInstance previous = intent == AvatarGestureIntent.Nod ? nodSource : waveSource;
            if (intent == AvatarGestureIntent.Nod) nodSource = candidateSource; else waveSource = candidateSource;
            sourceIntent = intent;
            StopImmediately();
            source = candidateSource;
            animationComponent = candidateAnimation;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogQaPoseMappings();
#endif
            if (previous != null) Destroy(previous.gameObject);
        }

        private void CaptureBaseline()
        {
            capturedBaseline.Clear();
            hasCapturedBaseline = false;
            if (target == null) return;

            var seen = new HashSet<Transform>();
            foreach (HumanBodyBones bone in Enum.GetValues(typeof(HumanBodyBones)))
            {
                if (bone == HumanBodyBones.LastBone) continue;
                Transform boneTransform = GetOriginalHumanoidBone(target, bone);
                if (boneTransform != null && seen.Add(boneTransform)) capturedBaseline.Add(new CapturedBonePose(boneTransform));
            }
            hasCapturedBaseline = capturedBaseline.Count > 0;
        }

        private void RestoreBaseline()
        {
            if (!hasCapturedBaseline) return;
            foreach (CapturedBonePose pose in capturedBaseline)
            {
                if (pose.Transform == null) continue;
                pose.Transform.localPosition = pose.LocalPosition;
                pose.Transform.localRotation = pose.LocalRotation;
            }
            capturedBaseline.Clear();
            hasCapturedBaseline = false;
            Debug.Log("[AvatarVrma] restored captured Humanoid baseline.");
        }

        private void CaptureCurrentPose(List<CapturedBonePose> destination)
        {
            destination.Clear();
            foreach (CapturedBonePose baseline in capturedBaseline)
                if (baseline.Transform != null) destination.Add(new CapturedBonePose(baseline.Transform));
        }

        private void BlendBaselineToCurrent(float weight)
        {
            foreach (CapturedBonePose baseline in capturedBaseline)
            {
                if (baseline.Transform == null) continue;
                Vector3 vrmaPosition = baseline.Transform.localPosition;
                Quaternion vrmaRotation = baseline.Transform.localRotation;
                baseline.Transform.localPosition = Vector3.Lerp(baseline.LocalPosition, vrmaPosition, weight);
                baseline.Transform.localRotation = Quaternion.Slerp(baseline.LocalRotation, vrmaRotation, weight);
            }
        }

        private void BlendExitToBaseline(float weight)
        {
            int count = Math.Min(exitPose.Count, capturedBaseline.Count);
            for (int i = 0; i < count; i++)
            {
                CapturedBonePose from = exitPose[i];
                CapturedBonePose to = capturedBaseline[i];
                if (from.Transform == null || from.Transform != to.Transform) continue;
                from.Transform.localPosition = Vector3.Lerp(from.LocalPosition, to.LocalPosition, weight);
                from.Transform.localRotation = Quaternion.Slerp(from.LocalRotation, to.LocalRotation, weight);
            }
        }

        private void BeginSourcePlayback()
        {
            if (sourceAnimationState == null) return;
            sourceAnimationState.time = 0f;
            sourceAnimationState.speed = 1f;
            sourceAnimationState.wrapMode = WrapMode.ClampForever;
            animationComponent.Play(sourceAnimationState.name);
            startedAt = Time.unscaledTime;
            playbackState = PlaybackState.Playing;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogHips("source playback begins at t=0");
#endif
        }

        private bool HasReachedOneShotEnd()
        {
            float duration = Duration;
            if (duration <= 0f) return true;
            return (sourceAnimationState != null && sourceAnimationState.time >= duration - .0001f) ||
                Time.unscaledTime >= startedAt + duration;
        }

        private void FreezeSourceAtCurrentTime()
        {
            if (sourceAnimationState == null) return;
            FreezeSourceAt(sourceAnimationState.time);
        }

        private void FreezeSourceAt(float time)
        {
            if (sourceAnimationState == null || animationComponent == null) return;
            sourceAnimationState.time = ClampOneShotTime(time, Duration);
            sourceAnimationState.speed = 0f;
            sourceAnimationState.wrapMode = WrapMode.ClampForever;
            animationComponent.Sample();
        }

        private bool TryCreateHipsInPlaceCalibration(out HipsInPlaceCalibration calibration)
        {
            calibration = default;
            if (source == null || target == null || target.Runtime == null || target.Runtime.ControlRig == null) return false;

            var sourcePose = source.ControlRig.Item1;
            var sourceTPose = source.ControlRig.Item2.GetWorldTransform(HumanBodyBones.Hips);
            Transform targetHips = GetOriginalHumanoidBone(target, HumanBodyBones.Hips);
            Transform targetControlHips = target.Runtime.ControlRig.GetBoneTransform(HumanBodyBones.Hips);
            Transform targetControlRigRoot = targetControlHips != null ? targetControlHips.parent : null;
            var targetTPose = ((ITPoseProvider)target.Runtime.ControlRig).GetWorldTransform(HumanBodyBones.Hips);
            if (!sourceTPose.HasValue || !targetTPose.HasValue || targetHips == null || targetControlRigRoot == null) return false;

            float sourceReferenceHeight = sourceTPose.Value.Translation.y;
            if (Mathf.Abs(sourceReferenceHeight) < .0001f) return false;
            float scale = targetTPose.Value.Translation.y / sourceReferenceHeight;
            if (Mathf.Abs(scale) < .0001f) return false;

            // Vrm10Retarget writes raw hips in the ControlRig-root space. The
            // captured AIFren presentation hips must therefore be represented
            // in that same space, rather than in a skeleton-local transform.
            Vector3 targetBaseline = targetControlRigRoot.InverseTransformPoint(targetHips.position);
            calibration = new HipsInPlaceCalibration(
                sourcePose.GetRawHipsPosition(),
                sourceTPose.Value.Translation,
                targetBaseline,
                targetTPose.Value.Translation,
                scale,
                targetControlRigRoot);
            return true;
        }

        public static Vector3 CalculateInPlaceSourceHips(
            Vector3 currentSourceHips,
            Vector3 sourceReferenceHips,
            Vector3 targetBaselineOffsetInSourceSpace)
        {
            return sourceReferenceHips + targetBaselineOffsetInSourceSpace +
                (currentSourceHips - sourceReferenceHips);
        }

        public static float ClampOneShotTime(float time, float duration)
        {
            return duration <= 0f ? 0f : Mathf.Clamp(time, 0f, duration);
        }

        private static float BlendProgress(float startedAt, float duration)
        {
            if (duration <= 0f) return 1f;
            return Mathf.SmoothStep(0f, 1f, Mathf.Clamp01((Time.unscaledTime - startedAt) / duration));
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void OnEnable() => AvatarQaVisibility.Changed += RefreshQaVisibility;
        private void OnDisable() => AvatarQaVisibility.Changed -= RefreshQaVisibility;

        private void ShowQaToast(int index, string fileName, string state)
        {
            EnsureQaToast();
            if (qaToast == null) return;
            qaToast.text = "VRMA " + (index + 1) + "/" + qaFiles.Count + "\n" + fileName + "\n" + state;
            qaToastRoot.SetActive(AvatarQaVisibility.Visible);
            qaToastUntil = Time.unscaledTime + 3.5f;
        }

        private void RefreshQaVisibility()
        {
            if (qaToastRoot != null) qaToastRoot.SetActive(AvatarQaVisibility.Visible);
        }

        private void EnsureQaToast()
        {
            if (qaToast != null) return;
            TextMeshProUGUI sample = FindObjectsOfType<TextMeshProUGUI>(true).FirstOrDefault();
            if (sample == null || sample.canvas == null) return;

            qaToastRoot = new GameObject("Development VRMA QA Toast", typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            qaToastRoot.transform.SetParent(sample.canvas.transform, false);
            Image backing = qaToastRoot.GetComponent<Image>();
            backing.color = new Color(.025f, .035f, .065f, .84f);
            backing.raycastTarget = false;
            RectTransform backingRect = qaToastRoot.GetComponent<RectTransform>();
            backingRect.anchorMin = new Vector2(.5f, .82f);
            backingRect.anchorMax = new Vector2(.5f, .82f);
            backingRect.pivot = new Vector2(.5f, .5f);
            backingRect.sizeDelta = new Vector2(440f, 88f);

            GameObject textObject = new GameObject("Text", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            textObject.transform.SetParent(qaToastRoot.transform, false);
            qaToast = textObject.GetComponent<TextMeshProUGUI>();
            qaToast.font = sample.font;
            qaToast.fontSize = 16f;
            qaToast.color = new Color(.88f, .92f, 1f, .96f);
            qaToast.alignment = TextAlignmentOptions.Center;
            qaToast.raycastTarget = false;
            qaToast.enableWordWrapping = false;
            Outline outline = textObject.AddComponent<Outline>();
            outline.effectColor = new Color(0f, 0f, 0f, .8f);
            outline.effectDistance = new Vector2(1f, -1f);
            RectTransform rect = qaToast.rectTransform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(12f, 8f);
            rect.offsetMax = new Vector2(-12f, -8f);
            qaToastRoot.SetActive(false);
        }

        private void LogTargetArmMappings()
        {
            if (target == null) return;
            HumanBodyBones[] armBones =
            {
                HumanBodyBones.LeftShoulder, HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand,
                HumanBodyBones.RightShoulder, HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand,
            };
            var seen = new HashSet<Transform>();
            bool valid = true;
            string[] details = new string[armBones.Length];
            for (int i = 0; i < armBones.Length; i++)
            {
                Transform bone = GetTargetHumanoidBone(armBones[i]);
                valid &= bone != null && seen.Add(bone);
                details[i] = armBones[i] + "=" + (bone != null ? bone.name : "missing");
            }
            Debug.Log("[AvatarVrma QA Arms] target mapping " + (valid ? "valid" : "INVALID") + "; " + string.Join(", ", details) + ".");
        }

        private void LogQaPoseMappings()
        {
            string[] values = QaPoseBones.Select(bone => bone + " source=" +
                (GetSourceHumanoidBone(bone) != null ? "yes" : "no") + " target=" +
                (GetTargetHumanoidBone(bone) != null ? "yes" : "no")).ToArray();
            Debug.Log("[AvatarVrma QA Pose] source/target mappings; " + string.Join("; ", values) + ".");
        }

        private Transform GetTargetHumanoidBone(HumanBodyBones bone)
        {
            return GetOriginalHumanoidBone(target, bone);
        }

        private Transform GetSourceHumanoidBone(HumanBodyBones bone)
        {
            return GetOriginalHumanoidBone(source, bone);
        }

        private static Transform GetOriginalHumanoidBone(Component owner, HumanBodyBones bone)
        {
            Component humanoid = owner != null ? owner.GetComponent("UniHumanoid.Humanoid") : null;
            MethodInfo getBoneTransform = humanoid != null
                ? humanoid.GetType().GetMethod("GetBoneTransform", new[] { typeof(HumanBodyBones) })
                : null;
            return getBoneTransform != null ? getBoneTransform.Invoke(humanoid, new object[] { bone }) as Transform : null;
        }

        private void LogHipsBeforePlayback()
        {
            if (target == null || target.Runtime == null || target.Runtime.ControlRig == null) return;
            Transform targetHips = GetTargetHumanoidBone(HumanBodyBones.Hips);
            Transform targetControlHips = target.Runtime.ControlRig.GetBoneTransform(HumanBodyBones.Hips);
            Transform controlRigRoot = targetControlHips != null ? targetControlHips.parent : null;
            var targetReference = ((ITPoseProvider)target.Runtime.ControlRig).GetWorldTransform(HumanBodyBones.Hips);
            Vector3 targetOriginal = targetHips != null && controlRigRoot != null
                ? controlRigRoot.InverseTransformPoint(targetHips.position) : Vector3.zero;
            Vector3 targetControl = targetControlHips != null && controlRigRoot != null
                ? controlRigRoot.InverseTransformPoint(targetControlHips.position) : Vector3.zero;
            Debug.Log("[AvatarVrma QA Hips] before Animation.Play; targetOriginal(controlRig)=" +
                (targetHips != null ? targetOriginal.ToString("F3") : "missing") +
                "; targetControl(controlRig)=" + (targetControlHips != null ? targetControl.ToString("F3") : "missing") +
                "; targetReference(controlRig)=" + (targetReference.HasValue ? targetReference.Value.Translation.ToString("F3") : "missing") +
                "; targetRootWorld=" + target.transform.position.ToString("F3") + ".");
        }

        private IEnumerator LogHipsTimeline()
        {
            yield return new WaitForEndOfFrame();
            if (IsActive) LogHips("first target frame");
            yield return new WaitForSecondsRealtime(.12f);
            if (IsActive) LogHips("during entry");
            yield return new WaitForSecondsRealtime(.63f);
            if (IsActive) LogHips("during playback");
        }

        private void LogHips(string phase)
        {
            if (target == null || source == null) return;
            Vector3 sourceRaw = source.ControlRig.Item1.GetRawHipsPosition();
            var sourceReference = source.ControlRig.Item2.GetWorldTransform(HumanBodyBones.Hips);
            Transform targetHips = GetTargetHumanoidBone(HumanBodyBones.Hips);
            Transform targetControlHips = target.Runtime != null && target.Runtime.ControlRig != null
                ? target.Runtime.ControlRig.GetBoneTransform(HumanBodyBones.Hips) : null;
            Transform controlRigRoot = hipsCalibration.TargetControlRigRoot;
            Vector3 sourceRelative = sourceRaw - hipsCalibration.SourceReference;
            Vector3 sourceFirstDelta = hipsCalibration.SourceInitial - hipsCalibration.SourceReference;
            Vector3 expectedTarget = hipsCalibration.TargetBaseline + sourceRelative * hipsCalibration.Scale;
            Vector3 targetOriginal = targetHips != null && controlRigRoot != null
                ? controlRigRoot.InverseTransformPoint(targetHips.position) : Vector3.zero;
            Vector3 targetControl = targetControlHips != null && controlRigRoot != null
                ? controlRigRoot.InverseTransformPoint(targetControlHips.position) : Vector3.zero;
            Debug.Log("[AvatarVrma QA Hips] " + phase + "; sourceRaw=" + sourceRaw.ToString("F3") +
                "; lifecycle=" + playbackState + "; duration=" + Duration.ToString("F3") +
                "; animationTime=" + (sourceAnimationState != null ? sourceAnimationState.time.ToString("F3") : "missing") +
                "; animationSpeed=" + (sourceAnimationState != null ? sourceAnimationState.speed.ToString("F2") : "missing") +
                "; wrap=" + (sourceAnimationState != null ? sourceAnimationState.wrapMode.ToString() : "missing") +
                "; sourceReference=" + (sourceReference.HasValue ? sourceReference.Value.Translation.ToString("F3") : "missing") +
                "; sourceInitial=" + hipsCalibration.SourceInitial.ToString("F3") + "; sourceFirstDelta=" + sourceFirstDelta.ToString("F3") +
                "; sourceRelative(reference)=" + sourceRelative.ToString("F3") +
                "; targetBaseline(controlRig)=" + hipsCalibration.TargetBaseline.ToString("F3") +
                "; targetReference(controlRig)=" + hipsCalibration.TargetReference.ToString("F3") +
                "; expectedTarget(controlRig)=" + expectedTarget.ToString("F3") +
                "; targetOriginal(controlRig)=" + (targetHips != null ? targetOriginal.ToString("F3") : "missing") +
                "; targetControl(controlRig)=" + (targetControlHips != null ? targetControl.ToString("F3") : "missing") +
                "; targetRootWorld=" + target.transform.position.ToString("F3") + ".");
        }
#endif

        private Dictionary<HumanBodyBones, Quaternion> CaptureStationaryChannels()
        {
            // The already initialized normalized ControlRig owns the relaxed
            // pose. Original head/neck trials leave every other channel there.
            var result = new Dictionary<HumanBodyBones, Quaternion>();
            foreach (HumanBodyBones bone in Enum.GetValues(typeof(HumanBodyBones)))
            {
                if (bone == HumanBodyBones.LastBone) continue;
                Transform transform = target.Runtime.ControlRig.GetBoneTransform(bone);
                if (transform != null) result[bone] = transform.localRotation;
            }
            return result;
        }

        private sealed class BodyOnlyVrma : IVrm10Animation
        {
            private readonly Vrm10AnimationInstance source;
            private readonly (INormalizedPoseProvider, ITPoseProvider) controlRig;
            public BodyOnlyVrma(Vrm10AnimationInstance source, HipsInPlaceCalibration hipsCalibration, Dictionary<HumanBodyBones, Quaternion> stationary)
            {
                this.source = source;
                controlRig = (new InPlaceHipsPoseProvider(source.ControlRig.Item1, hipsCalibration, stationary), source.ControlRig.Item2);
            }
            public (INormalizedPoseProvider, ITPoseProvider) ControlRig => controlRig;
            public IReadOnlyDictionary<ExpressionKey, Func<float>> ExpressionMap { get; } = new Dictionary<ExpressionKey, Func<float>>();
            public LookAtInput? LookAt => null;
            public void ShowBoxMan(bool enable) => source.ShowBoxMan(enable);
            public void SetBoxManMaterial(Material material) => source.SetBoxManMaterial(material);
            public void Dispose() { }
        }

        private sealed class InPlaceHipsPoseProvider : INormalizedPoseProvider
        {
            private readonly INormalizedPoseProvider source;
            private readonly HipsInPlaceCalibration hipsCalibration;
            private readonly Dictionary<HumanBodyBones, Quaternion> stationary;

            public InPlaceHipsPoseProvider(INormalizedPoseProvider source, HipsInPlaceCalibration hipsCalibration, Dictionary<HumanBodyBones, Quaternion> stationary)
            {
                this.source = source;
                this.hipsCalibration = hipsCalibration;
                this.stationary = stationary;
            }

            public Vector3 GetRawHipsPosition() => CalculateInPlaceSourceHips(
                source.GetRawHipsPosition(),
                hipsCalibration.SourceReference,
                hipsCalibration.SourceBaselineOffset);
            public Quaternion GetNormalizedLocalRotation(HumanBodyBones bone, HumanBodyBones parentBone)
            {
                Quaternion motion = source.GetNormalizedLocalRotation(bone, parentBone);
                if (stationary == null || !stationary.TryGetValue(bone, out Quaternion baseline)) return motion;
                return bone == HumanBodyBones.Head || bone == HumanBodyBones.Neck ? baseline * motion : baseline;
            }
        }
    }
}
