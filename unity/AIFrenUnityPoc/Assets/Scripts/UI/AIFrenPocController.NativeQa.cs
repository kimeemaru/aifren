#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    public sealed partial class AIFrenPocController
    {
        private StreamWriter qaTrace;
        private int qaTerminal, qaSnapshots, qaCaptures, qaFrameRows, qaRejected, qaTimeouts;
        private string qaStep = "startup";
        private float qaNextFrameCapture;
        private bool qaSequence, qaFadeSequence, qaMotionSequence;
        private float qaNextExpressionSample;
        private int qaExpressionRows;
        private bool qaCaptureEnabled = true;
        private int qaFadeCaptures, qaLastReadbackFrame = -10;
        private int qaLastPage = -1, qaLastWord = -1;
        private string qaTtsState = "";
        private bool qaLastTurnFailed;
        private StreamWriter qaAvatarTrace;
        private Quaternion[] qaMotionBaseline;
        private float qaMaxMotion;
        private bool qaVrmaPlayed;
        private NativeQaPointer qaPointer;

        private NativeQaPointer QaPointer
        {
            get
            {
                if (qaPointer == null)
                {
                    qaPointer = UnityEngine.EventSystems.EventSystem.current.gameObject.AddComponent<NativeQaPointer>();
                    qaPointer.enabled = false;
                }
                return qaPointer;
            }
        }

        private void ObserveNativeQaMessage(ServerMessage message)
        {
            if (!NativeQaSession.Active) return;
            if (message.type == "snapshot") qaSnapshots++;
            BackendEvent evt = message.@event;
            if (evt == null) return;
            ObserveCharacterNativeQaEvent(evt);
            if (evt.type == "turn_started") { qaTtsState = ""; qaLastTurnFailed = false; }
            if (evt.type == "tts_state" && evt.data != null) qaTtsState = evt.data.state;
            if (evt.type == "turn_cancelled" || evt.type == "error") qaLastTurnFailed = true;
            if (evt.type == "assistant_response" || evt.type == "turn_cancelled" || evt.type == "error") qaTerminal++;
            QaMark("event_" + evt.type);
        }

        private void QaMark(string code)
        {
            if (qaTrace == null || qaFrameRows++ > 100000) return;
            qaTrace.WriteLine(string.Join(",", Time.realtimeSinceStartup.ToString("F4", CultureInfo.InvariantCulture),
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                Time.frameCount, Time.unscaledDeltaTime.ToString("F4", CultureInfo.InvariantCulture),
                qaStep, code, subtitlePlaybackId, qaLastPage, qaLastWord,
                hiddenSubtitlePresenter != null ? hiddenSubtitlePresenter.State.ToString() : "none",
                hiddenSubtitlePresenter != null ? hiddenSubtitlePresenter.PresentedOnPage : 0,
                hiddenDialogueText != null && hiddenDialogueText.enabled && hiddenDialogueText.gameObject.activeInHierarchy ? 1 : 0,
                hiddenDialogueCanvasGroup != null ? hiddenDialogueCanvasGroup.alpha.ToString("F3", CultureInfo.InvariantCulture) : "0",
                QaWordOpacity(qaLastWord).ToString("F3", CultureInfo.InvariantCulture),
                QaWordOpacity(qaLastWord - 1).ToString("F3", CultureInfo.InvariantCulture),
                Time.frameCount - qaLastReadbackFrame <= 1 ? 1 : 0));
        }

        private float QaWordOpacity(int global)
        {
            if (hiddenSubtitleRenderTarget == null || qaLastPage < 0 || qaLastPage >= subtitlePageWordRanges.Count) return 0f;
            return hiddenSubtitleRenderTarget.RenderedWordOpacity(global - subtitlePageWordRanges[qaLastPage].FirstWordIndex);
        }

        private IEnumerator RunNativeQa()
        {
            using (qaTrace = new StreamWriter(Path.Combine(NativeQaSession.Output, "frames.csv")))
            using (qaAvatarTrace = new StreamWriter(Path.Combine(NativeQaSession.Output, "avatar-frames.jsonl")))
            {
                qaTrace.WriteLine("time,utc_ms,frame,dt,step,event,playback,page,global_word,state,tmp_words,tmp_active,alpha,word_alpha,previous_word_alpha,readback");
                float deadline = Time.realtimeSinceStartup + 40;
                while (!QaStartupPresentationReady() && Time.realtimeSinceStartup < deadline) yield return null;
                if (!QaStartupPresentationReady()) { QaMark("startup_timeout"); qaTrace.Flush(); Application.Quit(4); yield break; }
                // The host window manager caps a 1080-high decorated window
                // to its work area. Borderless QA uses the existing display
                // mode at native resolution; it never changes OS resolution.
                Screen.SetResolution(NativeQaSession.Current.width, NativeQaSession.Current.height,
                    FullScreenMode.FullScreenWindow);
                yield return new WaitForSecondsRealtime(.5f);
                yield return new WaitForEndOfFrame();
                DisplayInfo actualDisplay = Screen.mainWindowDisplayInfo;
                File.WriteAllText(Path.Combine(NativeQaSession.Output, "display.json"), JsonUtility.ToJson(new QaDisplay {
                    name = actualDisplay.name, width = actualDisplay.width, height = actualDisplay.height,
                    position = Screen.mainWindowPosition, windowWidth = Screen.width, windowHeight = Screen.height }));
                if (actualDisplay.width != NativeQaSession.Current.display.width ||
                    actualDisplay.height != NativeQaSession.Current.display.height ||
                    Screen.width != NativeQaSession.Current.width || Screen.height != NativeQaSession.Current.height)
                { Application.Quit(7); yield break; }
                if (!NativeQaSession.Current.componentOnly && !QaProbeAvatar("preflight"))
                {
                    CaptureNativeQa("avatar-preflight-failed");
                    QaMark("avatar_preflight_failed"); qaTrace.Flush();
                    Application.Quit(6); yield break;
                }
                File.WriteAllText(Path.Combine(NativeQaSession.Output, "subtitle-style.json"),
                    JsonUtility.ToJson(new QaSubtitleStyle { font = hiddenDialogueText.font.faceInfo.familyName,
                        size = SubtitleStyle.FontSize, minimumSize = SubtitleStyle.MinimumFontSize,
                        wordFade = SubtitleStyle.WordFadeSeconds, pageDwell = SubtitleStyle.PageDwellSeconds,
                        outline = hiddenSubtitleMaterial.GetFloat(ShaderUtilities.ID_OutlineWidth),
                        shadowAlpha = hiddenSubtitleMaterial.GetColor(ShaderUtilities.ID_UnderlayColor).a }));
                if (hiddenSubtitlePresenter != null)
                {
                    hiddenSubtitlePresenter.WordPresented += word => { qaLastWord = word; QaMark("word_shown"); };
                    hiddenSubtitlePresenter.PageActivated += page => { qaLastPage = page; QaMark("page_activated"); };
                }
                float runDeadline = Time.realtimeSinceStartup + 1200;
                foreach (NativeQaSession.Step step in NativeQaSession.Current.steps)
                {
                    qaStep = SafeQaName(step.name);
                    QaMark("step_begin");
                    bool failed = false;
                    int terminalBefore = qaTerminal;
                    try { ApplyNativeQaStep(step); }
                    catch { failed = true; qaRejected++; QaMark("step_rejected"); }
                    if (failed) continue;
                    float until = Time.realtimeSinceStartup + Mathf.Clamp(step.seconds, .15f, 180f);
                    while (Time.realtimeSinceStartup < until && Time.realtimeSinceStartup < runDeadline)
                    {
                        QaMark("frame");
                        QaSampleExpression();
                        if (qaVrmaPlayed) QaSampleMotion();
                        if (qaSequence && Time.realtimeSinceStartup >= qaNextFrameCapture &&
                            (!qaFadeSequence || (qaFadeCaptures < 36 && hiddenSubtitlePresenter != null &&
                             hiddenSubtitlePresenter.HasFadingWord && hiddenSubtitlePresenter.PresentedOnPage > 0)))
                        {
                            // Stills are sampled; the structural trace above
                            // records every frame and exact word/page events.
                            qaNextFrameCapture = Time.realtimeSinceStartup + (qaFadeSequence ? .025f : qaMotionSequence ? .08f : .75f);
                            if (qaFadeSequence) qaFadeCaptures++;
                            yield return new WaitForEndOfFrame();
                            CaptureNativeQa(qaStep + "-sequence");
                        }
                        if (step.action == "submit" && qaTerminal > terminalBefore && QaTurnPresentationFinished()) break;
                        if (step.action == "wait_synthesis" && (qaTtsState == "starting" || qaTtsState == "synthesizing")) break;
                        if (step.action == "wait_playback" && qaTtsState == "playback_started") break;
                        if (step.action == "character_wait_ready" && QaCharacterReady(step.value)) break;
                        yield return null;
                    }
                    if ((step.action == "submit" && (qaTerminal == terminalBefore || !QaTurnPresentationFinished())) ||
                        (step.action == "wait_synthesis" && qaTtsState != "starting" && qaTtsState != "synthesizing") ||
                        (step.action == "wait_playback" && qaTtsState != "playback_started") ||
                        (step.action == "character_wait_ready" && !QaCharacterReady(step.value)))
                    { qaTimeouts++; QaMark("step_timeout"); }
                    yield return new WaitForEndOfFrame();
                    CaptureNativeQa(qaStep);
                    if (!NativeQaSession.Current.componentOnly && !QaProbeAvatar(qaStep))
                    { qaRejected++; QaMark("avatar_gate_failed"); }
                    if (step.action == "vrma_play")
                    {
                        var vrma = avatarLoader.GetComponent<AvatarVrmaGesturePlayer>();
                        float residual = QaMotionDelta();
                        bool complete = vrma != null && !vrma.IsActive && qaMaxMotion > 5f && residual < 2f;
                        qaAvatarTrace.WriteLine("{\"stage\":\"motion_result\",\"played\":" + (complete ? "true" : "false") +
                            ",\"max_degrees\":" + qaMaxMotion.ToString("F3", CultureInfo.InvariantCulture) +
                            ",\"residual_degrees\":" + residual.ToString("F3", CultureInfo.InvariantCulture) + "}");
                        qaAvatarTrace.Flush(); qaVrmaPlayed = false;
                        if (!complete) { qaRejected++; QaMark("vrma_motion_failed"); }
                    }
                    QaMark("step_end"); qaTrace.Flush();
                    if (Time.realtimeSinceStartup >= runDeadline) { qaTimeouts++; QaMark("run_timeout"); break; }
                }
                QaMark("complete"); qaTrace.Flush();
                File.WriteAllText(Path.Combine(NativeQaSession.Output, "complete.json"),
                    "{\"width\":" + Screen.width + ",\"height\":" + Screen.height +
                    ",\"captures\":" + qaCaptures + ",\"terminal_events\":" + qaTerminal +
                    ",\"snapshots\":" + qaSnapshots + ",\"rejected_steps\":" + qaRejected +
                    ",\"timed_out_steps\":" + qaTimeouts + "}");
                yield return new WaitForSecondsRealtime(.5f);
            }
            qaTrace = null;
            Application.Quit(qaRejected == 0 && qaTimeouts == 0 ? 0 : 5);
        }

        private static string SafeQaName(string value) => new string((value ?? "step").Take(64)
            .Select(c => char.IsLetterOrDigit(c) || c == '-' || c == '_' ? c : '_').ToArray());

        private bool QaTurnPresentationFinished() =>
            (qaLastTurnFailed || qaTtsState == "stopped" || qaTtsState == "failed" || qaTtsState == "not_started") &&
            !subtitleSpeechActive && (hiddenSubtitlePresenter == null || !hiddenSubtitlePresenter.IsActive);

        private void QaSampleExpression()
        {
            if (avatarLoader == null || avatarLoader.ActiveAvatar == null || qaAvatarTrace == null) return;
            if (qaExpressionRows >= 12000 || Time.realtimeSinceStartup < qaNextExpressionSample) return;
            qaNextExpressionSample = Time.realtimeSinceStartup + .05f; qaExpressionRows++;
            var expression = avatarLoader.GetComponent<AvatarExpressionController>();
            var resolver = avatarLoader.GetComponent<AvatarPresentationResolver>();
            var animator = avatarLoader.ActiveAvatar.GetComponentInChildren<Animator>();
            var head = animator != null ? animator.GetBoneTransform(HumanBodyBones.Head) : null;
            var neck = animator != null ? animator.GetBoneTransform(HumanBodyBones.Neck) : null;
            var hips = animator != null ? animator.GetBoneTransform(HumanBodyBones.Hips) : null;
            var leftArm = animator != null ? animator.GetBoneTransform(HumanBodyBones.LeftUpperArm) : null;
            var rightArm = animator != null ? animator.GetBoneTransform(HumanBodyBones.RightUpperArm) : null;
            var vrma = avatarLoader.GetComponent<AvatarVrmaGesturePlayer>();
            qaAvatarTrace.WriteLine(JsonUtility.ToJson(new QaExpressionFrame {
                stage = "expression_frame", step = qaStep, frame = Time.frameCount,
                seconds = Time.realtimeSinceStartup,
                emotion = expression?.ActiveExpression?.Id ?? "none", intensity = expression?.ActiveIntensity ?? 0,
                faceOrigin = resolver?.LastFaceOrigin ?? "no_change", faceRequest = resolver?.LastFaceRequest ?? "none",
                faceApplied = resolver != null && resolver.LastFaceApplied,
                gesture = avatarAnimation != null ? avatarAnimation.ActiveGesture.ToString() : "none",
                vrma = vrma != null && vrma.IsActive, motionSource = vrma?.QaSelectionLabel ?? "none", head = head != null ? head.localRotation : Quaternion.identity,
                neck = neck != null ? neck.localRotation : Quaternion.identity,
                hips = hips != null ? hips.position : Vector3.zero,
                root = avatarLoader.ActiveAvatar.transform.position,
                leftArm = leftArm != null ? leftArm.localRotation : Quaternion.identity,
                rightArm = rightArm != null ? rightArm.localRotation : Quaternion.identity }));
        }
        [Serializable] private sealed class QaExpressionFrame
        { public string stage, step, emotion, gesture, motionSource, faceOrigin, faceRequest; public int frame; public float seconds, intensity; public bool vrma, faceApplied; public Quaternion head, neck, leftArm, rightArm; public Vector3 hips, root; }

        private bool QaProbeAvatar(string stage)
        {
            if (activeCharacterId == "no-character")
            {
                bool verified = QaVerifiedEmptyCharacterState();
                qaAvatarTrace?.WriteLine(JsonUtility.ToJson(new QaEmptyCharacterVisual {
                    stage = SafeQaName(stage), character = activeCharacterId, absence_kind = "empty_character_library",
                    verified = verified, storage_unavailable = characterStorageUnavailable,
                    composer_disabled = messageInput != null && sendButton != null && !messageInput.interactable && !sendButton.interactable,
                    outgoing_avatar_visible = QaOutgoingAvatarVisible() }));
                qaAvatarTrace?.Flush();
                return verified;
            }
            var result = AvatarVisualProbe.Inspect(avatarLoader != null ? avatarLoader.ActiveAvatar : null,
                avatarLoader != null ? avatarLoader.PresentationCamera : null);
            qaAvatarTrace?.WriteLine("{\"stage\":\"" + SafeQaName(stage) + "\",\"character\":\"" + activeCharacterId +
                "\",\"geometry\":" + JsonUtility.ToJson(result) + "}");
            qaAvatarTrace?.Flush();
            return result.ready;
        }

        [Serializable] private sealed class QaSubtitleStyle
        { public string font; public float size, minimumSize, wordFade, pageDwell, outline, shadowAlpha; }

        [Serializable] private sealed class QaDisplay
        {
            public string name;
            public int width, height, windowWidth, windowHeight;
            public Vector2Int position;
        }

        private Quaternion[] QaArmPose()
        {
            var animator = avatarLoader.ActiveAvatar.GetComponentInChildren<Animator>();
            return new[] { HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm,
                HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm }
                .Select(bone => animator.GetBoneTransform(bone).localRotation).ToArray();
        }

        private float QaMotionDelta()
        {
            Quaternion[] pose = QaArmPose();
            return pose.Select((rotation, index) => Quaternion.Angle(rotation, qaMotionBaseline[index])).Max();
        }

        private void QaSampleMotion()
        {
            float delta = QaMotionDelta(); qaMaxMotion = Mathf.Max(qaMaxMotion, delta);
            var vrma = avatarLoader.GetComponent<AvatarVrmaGesturePlayer>();
            qaAvatarTrace.WriteLine("{\"stage\":\"motion_frame\",\"frame\":" + Time.frameCount +
                ",\"seconds\":" + Time.realtimeSinceStartup.ToString("F4", CultureInfo.InvariantCulture) +
                ",\"active\":" + (vrma.IsActive ? "true" : "false") +
                ",\"degrees\":" + delta.ToString("F3", CultureInfo.InvariantCulture) + "}");
        }

        private void CaptureNativeQa(string name)
        {
            if (!qaCaptureEnabled) return;
            qaLastReadbackFrame = Time.frameCount;
            if (qaCaptures >= 420) return;
            // Full player framebuffer, including the real overlay canvas and direct avatar.
            var frame = new Texture2D(Screen.width, Screen.height, TextureFormat.RGB24, false);
            try
            {
                frame.ReadPixels(new Rect(0, 0, Screen.width, Screen.height), 0, 0);
                frame.Apply();
                File.WriteAllBytes(Path.Combine(NativeQaSession.Output,
                    (++qaCaptures).ToString("D4") + "-" + name + ".png"), frame.EncodeToPNG());
            }
            finally { Destroy(frame); }
        }

        private void QaClosePanels()
        {
            CloseSettingsPanel(); CloseHistoryPanel(); CloseConsolePanel();
            foreach (GameObject panel in new[] { modelLibraryPanel, backgroundLibraryPanel, memoryViewerPanel })
                if (panel != null) panel.SetActive(false);
        }

        private void QaSeedManagementAssets()
        {
            // Synthetic library fixtures, never a substitute character/avatar.
            // They are owned by the separate QA product and are not packaged.
            string image = Path.Combine(NativeQaSession.Output, "qa-background.png");
            var pixels = new Texture2D(16, 16, TextureFormat.RGB24, false);
            try
            {
                pixels.SetPixels(Enumerable.Repeat(new Color(.25f, .3f, .5f), 256).ToArray());
                pixels.Apply(); File.WriteAllBytes(image, pixels.EncodeToPNG());
            }
            finally { Destroy(pixels); }
            if (!managedAssetLibrary.TryImport(image, ManagedAssetLibrary.BackgroundKind, out _, out _))
                throw new InvalidOperationException();
            string model = Path.Combine(NativeQaSession.Output, "qa-library-model.vrm");
            byte[] json = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"},\"extensions\":{\"VRM\":{}}}");
            int length = json.Length; Array.Resize(ref json, (length + 3) & ~3);
            for (int i = length; i < json.Length; i++) json[i] = 32;
            using (var writer = new BinaryWriter(File.Create(model)))
            {
                writer.Write(0x46546C67u); writer.Write(2u); writer.Write((uint)(28 + json.Length));
                writer.Write((uint)json.Length); writer.Write(0x4E4F534Au); writer.Write(json);
                writer.Write(0u); writer.Write(0x004E4942u);
            }
            if (!managedAssetLibrary.TryImport(model, ManagedAssetLibrary.ModelKind, out _, out _))
                throw new InvalidOperationException();
        }

        private void ApplyNativeQaStep(NativeQaSession.Step step)
        {
            if (ApplyCharacterNativeQaStep(step)) return;
            switch (step.action)
            {
                case "wait": break;
                case "resize_narrow": Screen.SetResolution(760, 1400, FullScreenMode.Windowed); break;
                case "resize_restore": Screen.SetResolution(NativeQaSession.Current.width, NativeQaSession.Current.height, FullScreenMode.FullScreenWindow); break;
                case "viewer_rows_fit":
                    if (memoryPageButtons.Count == 0) throw new InvalidOperationException();
                    Canvas.ForceUpdateCanvases();
                    foreach (Button row in memoryPageButtons)
                    {
                        TMP_Text label = row.GetComponentInChildren<TMP_Text>();
                        if (label.fontSize != 16 || label.GetPreferredValues(label.text, label.rectTransform.rect.width, 0).y > row.GetComponent<RectTransform>().rect.height + 1)
                            throw new InvalidOperationException();
                    }
                    break;
                case "original_nod":
                    var original = avatarLoader.GetComponent<AvatarVrmaGesturePlayer>();
                    if (original.OriginalNodTrialSelected != (step.value == "on"))
                    {
                        Button trial = settingsTabContent["Advanced"].GetComponentsInChildren<Button>()
                            .Single(button => button.GetComponentInChildren<TMP_Text>()?.text.StartsWith("Nod:") == true);
                        QaPointer.Target(trial.GetComponent<RectTransform>(), true);
                    }
                    break;
                case "original_ready":
                    if (!avatarLoader.GetComponent<AvatarVrmaGesturePlayer>().OriginalNodTrialReady) throw new InvalidOperationException(); break;
                case "nod_play":
                    if (!avatarAnimation.PlayGesture(AvatarGestureIntent.Nod)) throw new InvalidOperationException(); break;
                case "scene_hover": QaPointer.Target(sceneDrawerTab.GetComponent<RectTransform>()); break;
                case "scene_enter": QaPointer.Target(sceneOverlayPanel.GetComponent<RectTransform>()); break;
                case "scene_leave": QaPointer.Move(new Vector2(-100, -100)); break;
                case "scene_pin": QaPointer.Target(sceneDrawerTab.GetComponent<RectTransform>(), true); break;
                case "scene_focus": UnityEngine.EventSystems.EventSystem.current.SetSelectedGameObject(sceneDrawerTab.gameObject); break;
                case "scene_key":
                    if (step.value == "submit") HandlePresentationReturn(false);
                    QaPointer.SubmitSelected(step.value == "cancel"); break;
                case "scene_state":
                    bool expected = step.value == "expanded" ? sceneDrawer.Expanded :
                        step.value == "hidden" ? !sceneDrawerTab.gameObject.activeInHierarchy && !sceneOverlayPanel.activeInHierarchy :
                        step.value == "collapsed" && sceneDrawerTab.gameObject.activeInHierarchy && !sceneOverlayPanel.activeInHierarchy;
                    if (!expected) throw new InvalidOperationException(); break;
                case "scene_scroll": sceneOverlayScroll.verticalNormalizedPosition = step.value == "bottom" ? 0 : 1; break;
                case "avatar_probe": if (!QaProbeAvatar(qaStep)) throw new InvalidOperationException(); break;
                case "vrma_play":
                    var vrma = avatarLoader.GetComponent<AvatarVrmaGesturePlayer>();
                    qaMotionBaseline = QaArmPose(); qaMaxMotion = 0;
                    if (vrma == null || vrma.Duration <= 0 || !vrma.TryPlay(AvatarGestureIntent.Wave))
                        throw new InvalidOperationException();
                    qaVrmaPlayed = true;
                    qaAvatarTrace.WriteLine("{\"stage\":\"vrma_start\",\"clip\":\"" + SafeQaName(vrma.QaSelectionLabel) +
                        "\",\"duration\":" + vrma.Duration.ToString("F3", CultureInfo.InvariantCulture) + "}");
                    break;
                case "wait_synthesis": break;
                case "wait_playback": break;
                case "assets_seed": QaSeedManagementAssets(); break;
                case "sequence":
                    qaSequence = step.value == "on" || step.value == "fade" || step.value == "motion";
                    qaMotionSequence = step.value == "motion";
                    qaFadeSequence = step.value == "fade"; qaFadeCaptures = 0; break;
                case "captures": qaCaptureEnabled = step.value != "off"; break;
                case "avatar_cues":
                    if (step.value == "on" || step.value == "off") avatarCuesToggle.isOn = step.value == "on";
                    else if (step.value == "save") avatarCuesSave.onClick.Invoke();
                    else if (step.value == "cancel") CancelAvatarCuesDraft();
                    else if (step.value == "preview") PreviewAvatarSmile();
                    else if (step.value == "reset") ResetAvatarExpression();
                    else throw new InvalidOperationException();
                    break;
                case "companion_preferences":
                    if (step.value == "natural" || step.value == "roleplay") SetConversationStyleDraft(step.value);
                    else if (step.value == "speech_on" || step.value == "speech_off") responsiveSpeechToggle.isOn = step.value == "speech_on";
                    else if (step.value == "face_on" || step.value == "face_off") automaticExpressionToggle.isOn = step.value == "face_on";
                    else if (step.value == "save_style") conversationStyleSave.onClick.Invoke();
                    else if (step.value == "save_speech") responsiveSpeechSave.onClick.Invoke();
                    else if (step.value == "save_face") automaticExpressionSave.onClick.Invoke();
                    else if (step.value == "cancel") CancelCompanionPreferenceDraft();
                    else throw new InvalidOperationException();
                    break;
                case "subtitle_color":
                    // Drive the actual controls, in the isolated QA product only.
                    if (step.value == "save") subtitleColorSave.onClick.Invoke();
                    else if (step.value == "cancel") CancelSubtitleColor();
                    else if (step.value == "reset") SetSubtitleColorDraft("white");
                    else subtitleColorInput.text = step.value;
                    break;
                case "subtitle_background":
                    if (step.value != "light" && step.value != "dark") throw new InvalidOperationException();
                    avatarViewerBackgroundState.Set(AvatarViewPortrait, step.value == "light" ?
                        AvatarViewerBackground.LightNeutral : AvatarViewerBackground.NeutralGrey, false);
                    ApplyAvatarViewerBackground(); break;
                case "main":
                    QaClosePanels(); temporarilyRevealed = false; edgeRevealActive = false;
                    interfaceHidden = false; RequestInput(false); RefreshPresentationVisibility(); break;
                case "hidden": QaClosePanels(); interfaceHidden = true; inputRequested = false; showDialogueWhenHidden = true; RefreshPresentationVisibility(); break;
                case "peek": interfaceHidden = false; temporarilyRevealed = true; edgeRevealActive = true; RefreshPresentationVisibility(); break;
                case "hide_peek": interfaceHidden = true; temporarilyRevealed = false; edgeRevealActive = false; RefreshPresentationVisibility(); break;
                case "show":
                    if (interfaceHidden || temporarilyRevealed) hideUiButton.onClick.Invoke(); break;
                case "settings":
                    QaClosePanels(); interfaceHidden = false; RefreshPresentationVisibility();
                    ToggleSettingsPanel(); SelectSettingsTab(step.value); break;
                case "scroll":
                    foreach (ScrollRect scroll in settingsPanel.GetComponentsInChildren<ScrollRect>())
                        scroll.verticalNormalizedPosition = step.value == "bottom" ? 0 : 1;
                    break;
                case "history": QaClosePanels(); ToggleHistoryPanel(); break;
                case "view_drop":
                    if (!NativeQaSession.Active || (step.value != "history" && step.value != "memory")) throw new InvalidOperationException();
                    client.DropNextViewResponseForTest = step.value;
                    goto case "view_refresh";
                case "view_refresh":
                    if (step.value == "history") historyRefreshButton.onClick.Invoke();
                    else if (step.value == "memory") memoryViewerRefreshButton.onClick.Invoke();
                    else throw new InvalidOperationException();
                    break;
                case "view_assert":
                    string[] viewExpectation = step.value.Split(':');
                    ViewRequestState view = viewExpectation[0] == "history" ? historyViewRequest : memoryViewerState.PageRequest;
                    int count = viewExpectation[0] == "history" ? messages.Count : memoryViewerState.Page?.items?.Length ?? 0;
                    bool statusMatches = viewExpectation.Length >= 2 && view.Status.ToString() == viewExpectation[1];
                    bool countMatches = viewExpectation.Length < 3 || (viewExpectation[2] == "nonempty" ? count > 0 : count == 0);
                    MarkView("qa_assert_" + (statusMatches && countMatches ? "passed" : "failed"), null, count);
                    if (!statusMatches || !countMatches) throw new InvalidOperationException();
                    break;
                case "view_draft_assert":
                    if (memoryViewerContentInput.text != step.value) throw new InvalidOperationException();
                    break;
                case "view_dump": developmentFlightRecorder?.ManualDump(); break;
                case "history_back": NavigateHistoryBack(); break;
                case "history_previous": ChangeHistoryPage(-1); break;
                case "console": QaClosePanels(); ToggleConsolePanel(); break;
                case "models": OpenModelLibrary(); break;
                case "backgrounds": OpenBackgroundLibrary(); break;
                case "avatar_view": EnterAvatarViewEditor(); break;
                case "avatar_cancel": CancelAvatarViewEditor(); break;
                case "model_picker": OpenLocalModelPicker(); break;
                case "model_picker_cancel":
                    localModelPickerPanel.GetComponentsInChildren<Button>().First(x =>
                        x.GetComponentInChildren<TMP_Text>()?.text == "Cancel").onClick.Invoke(); break;
                case "model_select":
                    SelectOnlyModelForDeletion(managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind).First().id); break;
                case "background_select":
                    SelectOnlyBackgroundForDeletion(managedAssetLibrary.Assets(ManagedAssetLibrary.BackgroundKind).First().id); break;
                case "asset_rename": OpenAssetRenameDialog(step.value); break;
                case "asset_rename_error": assetRenameInput.text = ""; SaveAssetRename(); break;
                case "asset_rename_cancel": CloseAssetRenameDialog(); break;
                case "model_delete_dialog": OpenModelDeleteConfirmation(); break;
                case "background_delete_dialog": OpenBackgroundDeleteConfirmation(); break;
                case "asset_delete_cancel":
                    if (modelDeleteConfirmPanel != null) modelDeleteConfirmPanel.SetActive(false);
                    if (backgroundDeleteConfirmPanel != null) backgroundDeleteConfirmPanel.SetActive(false); break;
                case "display_confirm": ApplyDisplaySettings(currentDisplaySettings.Clone(), true); break;
                case "display_revert": RevertDisplaySettings(); break;
                case "character_switch":
                    CharacterSummary selected = ResolveQaCharacter(availableCharacters, step.value);
                    RequestCharacterSwitch(selected.character_id); break;
                case "viewer": OpenMemoryViewer(); break;
                case "viewer_lane":
                    for (int i = 0; i < 4 && memoryViewerState.Lane != step.value; i++) memoryViewerState.CycleLane();
                    RequestMemoryViewerPage(); break;
                case "viewer_detail":
                    if (memoryViewerState.Page?.items?.Length > 0)
                    {
                        MemoryViewItem item = step.value == "editable"
                            ? memoryViewerState.Page.items.FirstOrDefault(x => x.editable) : memoryViewerState.Page.items[0];
                        if (item == null) throw new InvalidOperationException();
                        SelectMemoryViewerItem(item);
                    }
                    else throw new InvalidOperationException();
                    break;
                case "viewer_search":
                    memoryViewerState.Query = step.value; memoryViewerState.InvalidatePage(); RequestMemoryViewerPage(); break;
                case "viewer_correct":
                    if (memoryViewerState.Selected == null || !memoryViewerState.Selected.editable) throw new InvalidOperationException();
                    memoryViewerContentInput.text = step.value;
                    QaPointer.Target(memoryViewerSaveButton.GetComponent<RectTransform>(), true); break;
                case "viewer_status": QaPointer.Target(memoryViewerStatusButton.GetComponent<RectTransform>(), true); break;
                case "viewer_scope": QaPointer.Target(memoryViewerScopeButton.GetComponent<RectTransform>(), true); break;
                case "viewer_retire": QaPointer.Target(memoryViewerRetireButton.GetComponent<RectTransform>(), true); break;
                case "viewer_scroll":
                    foreach (ScrollRect scroll in memoryViewerPanel.GetComponentsInChildren<ScrollRect>())
                        scroll.verticalNormalizedPosition = step.value == "bottom" ? 0 : 1;
                    break;
                case "viewer_draft": memoryViewerContentInput.text = step.value; break;
                case "viewer_back":
                    QaPointer.Target(memoryViewerPanel.GetComponentsInChildren<Button>().Single(button =>
                        button.GetComponentInChildren<TMP_Text>()?.text == "Back to Advanced").GetComponent<RectTransform>(), true);
                    break;
                case "scene_remove":
                    bool removed = false;
                    foreach (Transform row in sceneOverlayContent)
                    {
                        TMP_Text label = row.GetComponentInChildren<TMP_Text>();
                        Button button = row.GetComponentInChildren<Button>();
                        if (label != null && button != null && button.interactable &&
                            label.text.IndexOf(step.value, StringComparison.OrdinalIgnoreCase) >= 0)
                        { QaPointer.Target(button.GetComponent<RectTransform>(), true); removed = true; break; }
                    }
                    if (!removed) throw new InvalidOperationException();
                    break;
                case "viewer_next": if (memoryViewerState.NextPage()) RequestMemoryViewerPage(); break;
                case "submit": qaTtsState = ""; qaLastTurnFailed = false; messageInput.text = step.value; SubmitCurrentText(); break;
                case "send": qaTtsState = ""; qaLastTurnFailed = false; _ = client.SubmitTextAsync(step.value); break;
                case "stop_speech": _ = client.StopTtsAsync(); break;
                case "ptt_press": _ = client.SetPushToTalkPressedAsync(true); break;
                case "ptt_release": _ = client.SetPushToTalkPressedAsync(false); break;
                case "disconnect": _ = client.DisconnectAsync(); break;
                case "reconnect": Reconnect(); break;
                default: throw new InvalidOperationException();
            }
        }
    }
}
#endif
