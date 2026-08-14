using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using Unity.VectorGraphics;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// The Unity presentation client. It only renders snapshots and events;
    /// the Python backend remains the owner of all conversation and TTS work.
    /// </summary>
    public sealed class AIFrenPocController : MonoBehaviour
    {
        private const string BackendUri = "ws://127.0.0.1:8765";
        private const string RevealSpeedPreference = "AIFren.DialogueRevealSpeed";
        private const string InstantTextPreference = "AIFren.InstantDialogueText";
        private const string DisplaySettingsPreference = "AIFren.PresentationDisplaySettings.v1";
        private const string PushToTalkBindingPreference = "AIFren.PushToTalkBinding";
        private const string PttAutoSendPreference = "AIFren.PttAutoSend";
        private const string AvatarRenderScalePreference = "AIFren.AvatarRenderScale";
        private const string GraphicsQualityPreference = "AIFren.GraphicsQuality";
        private const string ShowDialogueWhenHiddenPreference = "AIFren.ShowDialogueWhenHidden";
        private static readonly Vector2 DefaultReferenceResolution = new Vector2(1440f, 900f);
        private const float DialogueMinimumHeight = 150f;
        private const float DialogueMaximumHeight = 360f;
        private const float DialogueChromeHeight = 66f;
        private const float DialogueHorizontalPadding = 14f;
        private const float DialogueVerticalPadding = 10f;
        // Stable VN-style dialogue cards: long replies scroll inside rather
        // than continuously pushing the companion composition around.
        private const float DialogueLandscapeHeight = 252f;
        private const float DialoguePortraitHeight = 288f;

        private static readonly Color Ink = new Color(0.95f, 0.94f, 0.99f, 1f);
        private static readonly Color Panel = new Color(0.07f, 0.07f, 0.13f, 0.88f);
        private static readonly Color Accent = new Color(0.83f, 0.55f, 0.91f, 1f);
        private static readonly Color UserAccent = new Color(0.42f, 0.72f, 0.92f, 1f);

        private readonly List<ConversationMessage> messages = new List<ConversationMessage>();
        private readonly WordReveal wordReveal = new WordReveal();

        private AIFrenWebSocketClient client;
        private AvatarLoader avatarLoader;
        private AvatarPresentationFramingState avatarFraming;
        private CompanionPresentationConfiguration presentation;
        private TMP_FontAsset font;
        private TMP_Text characterNameLabel;
        private TMP_Text dialogueSpeakerLabel;
        private TMP_Text dialogueTextLabel;
        private TMP_Text pttLabel;
        private SVGImage pttIndicator;
        private RectTransform dialogueCardRect;
        private RectTransform dialogueViewportRect;
        private RectTransform inputCardRect;
        private ScrollRect dialogueScroll;
        private Scrollbar dialogueScrollbar;
        private TMP_Text statusLabel;
        private TMP_Text statusDetailLabel;
        private TMP_Text volumeLabel;
        private TMP_Text revealSpeedLabel;
        private TMP_InputField messageInput;
        private RectTransform messageInputRect;
        private RectTransform sendButtonRect;
        private Button sendButton;
        private GameObject historyPanel;
        private GameObject settingsPanel;
        private Transform historyContent;
        private ScrollRect historyScroll;
        private Slider volumeSlider;
        private bool ttsVolumeDirty;
        private float pendingTtsVolume;
        private float nextTtsVolumeSendAt;
        private Slider revealSlider;
        private Toggle instantTextToggle;
        private Toggle hiddenDialogueToggle;
        private Toggle sfxMuteToggle;
        private Slider sfxVolumeSlider;
        private Toggle bgmMuteToggle;
        private Slider bgmVolumeSlider;
        private RawImage avatarSurface;
        private AspectRatioFitter avatarAspectFitter;
        private Coroutine avatarPresentationInitialization;
        private Image statusDot;
        private GameObject topBar;
        private GameObject dialogueCard;
        private GameObject inputCard;
        private GameObject modalScrim;
        private GameObject startupPanel;
        private RawImage backgroundImage;
        private Image backgroundTint;
        private PresentationThemeDefinition theme;
        private PresentationAudio presentationAudio;
        private AvatarAnimationController avatarAnimation;
        private CanvasScaler canvasScaler;
        private RectTransform avatarFrameRect;
        private AvatarFramingInputSurface avatarFramingInputSurface;
        private GameObject avatarFramingModePanel;
        private GameObject avatarCompositionGrid;
        private Slider avatarSizeSlider;
        private Slider avatarHorizontalSlider;
        private Slider avatarVerticalSlider;
        private TMP_Text avatarSizeValue;
        private TMP_Text avatarHorizontalValue;
        private TMP_Text avatarVerticalValue;
        private bool avatarFramingModeActive;
        private bool suppressAvatarFramingCallbacks;
        private bool avatarFramingSessionPortrait;
        private AvatarPresentationFramingValues avatarFramingSessionSnapshot;
        private PresentationDisplaySettings currentDisplaySettings;
        private PresentationDisplaySettings pendingDisplaySettings;
        private PresentationDisplaySettings revertDisplaySettings;
        private List<DisplayInfo> displayLayout = new List<DisplayInfo>();
        private List<Vector2Int> resolutionOptions = new List<Vector2Int>();
        private TMP_Text displayModeValue;
        private TMP_Text monitorValue;
        private TMP_Text resolutionValue;
        private TMP_Text orientationValue;
        private TMP_Text uiScaleValue;
        private TMP_Text vSyncValue;
        private TMP_Text frameLimitValue;
        private TMP_Text antiAliasingValue;
        private TMP_Text graphicsQualityValue;
        private TMP_Text avatarRenderScaleValue;
        private TMP_Text geminiProviderStatus;
        private TMP_Text geminiModelValue;
        private TMP_Text ttsProviderValue;
        private TMP_Text ttsVoiceValue;
        private TMP_Text ttsDeviceValue;
        private TMP_InputField geminiApiKeyInput;
        private bool showGeminiApiKey;
        private TMP_Text pttBindValue;
        private TMP_Text pttRebindHint;
        private TMP_Text globalPttStatus;
        private KeyCode pushToTalkKey;
        private bool rebindingPushToTalk;
        private bool unityPttPressed;
        private bool backendGlobalPtt;
        private bool pttAutoSend;
        private TMP_Text transcriptionModeValue;
        private Slider uiScaleSlider;
        private GameObject displayConfirmPanel;
        private GameObject consolePanel;
        private Transform consoleContent;
        private ScrollRect consoleScroll;
        private TMP_Text consoleText;
        private Button consoleButton;
        private Button consoleCopyButton;
        private bool consoleUnlocked;
        private string consoleUnlockBuffer = string.Empty;
        private readonly List<string> consoleLines = new List<string>();
        private TMP_Text displayConfirmLabel;
        private float displayConfirmDeadline;
        private bool displayConfirmActive;
        private bool startupDisplayFinalizationPending;
        private string characterName = "AIFren";
        private string visibleState = "Disconnected";
        private string detail = "Start backend_host.py to connect.";
        private bool submitInFlight;
        private bool instantText;
        private float revealWordsPerSecond;
        private string pendingAssistantContent;
        private bool pendingAssistantReveal;
        private bool pendingSpeechReady;
        private float pendingSpeechDuration;
        private bool interfaceHidden;
        private bool inputRequested;
        private float inputVisibility;
        private float inputVisibilityTarget;
        private int lastScreenWidth;
        private int lastScreenHeight;
        private float thinkingElapsed;
        private string dialogueLayoutContent = "I’m here when you’re ready to talk.";
        private Button hideUiButton;
        private Button historyButton;
        private Button settingsButton;
        private Button closeButton;
        private bool edgeRevealActive;
        private bool temporarilyRevealed;
        private bool dialogueAutoFollow = true;
        private float edgeRevealGraceUntil;
        private Coroutine visibilityTransition;
        private readonly Dictionary<string, GameObject> settingsPages = new Dictionary<string, GameObject>();
        private readonly Dictionary<string, Transform> settingsTabContent = new Dictionary<string, Transform>();
        private readonly Dictionary<string, Button> settingsTabButtons = new Dictionary<string, Button>();
        private string activeSettingsTab = "Display";
        private static readonly HashSet<string> LoggedIconResources = new HashSet<string>();

        private PresentationGraphicsQuality graphicsQuality;
        private float avatarRenderScale = 1.5f;
        private bool showDialogueWhenHidden;
        private TMP_Text hiddenDialogueText;
        private RectTransform hiddenDialogueViewport;
        private ScrollRect hiddenDialogueScroll;
        private Scrollbar hiddenDialogueScrollbar;

        private const float TtsVolumeSendIntervalSeconds = .12f;

        private const float EdgeRevealThresholdPixels = 18f;
        private const float EdgeRevealGraceSeconds = 1.4f;
        private const float DialogueFontMinimum = 18f;
        private const float DialogueFontLandscapeMaximum = 29f;
        private const float DialogueFontPortraitMaximum = 25f;
        private const float SettingsOuterMargin = .04f;
        private const float SettingsLabelColumnEnd = .48f;
        private const float SettingsControlColumnStart = .50f;
        private const float SettingsTabColumnEnd = .25f;
        private const float SettingsContentColumnStart = .275f;
        private const float StandardControlHeight = 40f;
        private const float StandardGap = 10f;
        // Top-row buttons retain their established hit targets; only the
        // contained SVG grows so icon-only controls read clearly at distance.
        private const float IconButtonSize = 28f;
        private const float ButtonHorizontalPadding = 12f;
        private const float IconTextGap = 8f;

        private const float InputSlideSpeed = 7.5f;
        private const float InputHeight = 92f;
        private const float HiddenInputOffset = -116f;

        private enum PresentationGraphicsQuality { Low, Medium, High, Ultra }

        public void ConfigureAvatarLoader(AvatarLoader loader)
        {
            avatarLoader = loader;
            avatarLoader.AvatarLoaded += HandleAvatarLoaded;
            avatarLoader.AvatarLoadFailed += HandleAvatarLoadFailed;

            if (avatarSurface != null)
            {
                avatarLoader.SetPreviewSurface(avatarSurface);
            }
            avatarLoader.SetPresentationRenderScale(avatarRenderScale);
        }

        private async void Start()
        {
            if (Environment.GetCommandLineArgs().Contains("-aifren-reset-console-unlock"))
            {
                PlayerPrefs.DeleteKey("AIFren.ConsoleUnlocked");
                PlayerPrefs.Save();
            }
            if (Environment.GetCommandLineArgs().Contains("-aifren-reset-ui"))
            {
                // A recovery launch resets display and presentation geometry,
                // including presentation-only avatar framing.
                // It never affects conversation, memories, audio, or API data.
                PlayerPrefs.DeleteKey(DisplaySettingsPreference);
                PlayerPrefs.DeleteKey(AvatarRenderScalePreference);
                PlayerPrefs.DeleteKey(GraphicsQualityPreference);
                PlayerPrefs.DeleteKey(ShowDialogueWhenHiddenPreference);
                AvatarPresentationFramingState.DeleteAllPersisted();
                PlayerPrefs.Save();
            }
            presentation = CompanionPresentationConfiguration.Load();
            if (!presentation.IsValid(out string configurationError))
            {
                Debug.LogWarning(configurationError);
                presentation = new CompanionPresentationConfiguration();
            }

            revealWordsPerSecond = PlayerPrefs.GetFloat(
                RevealSpeedPreference,
                presentation.defaultRevealWordsPerSecond
            );
            instantText = PlayerPrefs.GetInt(InstantTextPreference, 0) == 1;
            pttAutoSend = PlayerPrefs.GetInt(PttAutoSendPreference, 0) == 1;
            theme = PresentationThemes.Load();
            currentDisplaySettings = LoadDisplaySettings();
            pendingDisplaySettings = currentDisplaySettings.Clone();
            graphicsQuality = (PresentationGraphicsQuality)Mathf.Clamp(
                PlayerPrefs.GetInt(GraphicsQualityPreference, (int)PresentationGraphicsQuality.High),
                (int)PresentationGraphicsQuality.Low, (int)PresentationGraphicsQuality.Ultra);
            avatarRenderScale = Mathf.Clamp(PlayerPrefs.GetFloat(
                AvatarRenderScalePreference, DefaultAvatarRenderScale(graphicsQuality)), 1f, 2f);
            showDialogueWhenHidden = PlayerPrefs.GetInt(ShowDialogueWhenHiddenPreference, 0) == 1;
            avatarFraming = AvatarPresentationFramingState.Load(AvatarConfiguration.Load());
            pushToTalkKey = PresentationPttBinding.Load(PlayerPrefs.GetString(
                PushToTalkBindingPreference, PresentationPttBinding.DefaultKey.ToString()));
            wordReveal.WordsPerSecond = revealWordsPerSecond;
            presentationAudio = gameObject.AddComponent<PresentationAudio>();
            presentationAudio.Initialize();
            // Build controls after persisted presentation-audio state has been
            // applied, so their initial visual values match their sources.
            BuildInterface();
            ApplyTheme();
            ApplyPresentationGraphics();
            // Startup must move to the persisted monitor even when its saved
            // resolution/mode happens to match the launch monitor. This uses
            // the same authoritative Apply path as an interactive change.
            ApplyDisplaySettings(currentDisplaySettings, false, true);

            if (avatarLoader != null)
            {
                avatarLoader.SetPreviewSurface(avatarSurface);
                avatarLoader.SetPresentationRenderScale(avatarRenderScale);
            }

            client = new AIFrenWebSocketClient();
            await ConnectAsync();
        }

        private void Update()
        {
            if (Screen.width != lastScreenWidth || Screen.height != lastScreenHeight)
            {
                lastScreenWidth = Screen.width;
                lastScreenHeight = Screen.height;
                UpdateDialogueLayout(false);
                UpdateCompositionLayout();
                UpdateBackgroundCover();
            }

            UpdateDisplayConfirmation();
            UpdateHiddenInterfaceReveal();
            UpdateUnityPushToTalk();

            HandlePresentationInput();
            UpdateInputPresentation();

            if (client != null)
            {
                while (client.TryDequeue(out ServerMessage message))
                {
                    HandleServerMessage(message);
                }

                if (client.State == ConnectionState.Disconnected && visibleState != "Disconnected")
                {
                    ApplyStatus("disconnected", "Backend is unavailable or disconnected.");
                    backendGlobalPtt = false;
                    UpdatePttIndicator("ready");
                }
                else if (client.State == ConnectionState.Error)
                {
                    ApplyStatus("error", client.LastError);
                    submitInFlight = false;
                    backendGlobalPtt = false;
                    UpdatePttIndicator("ready");
                    RefreshInputAvailability();
                }
            }

            if (wordReveal.Advance(Time.unscaledDeltaTime))
            {
                dialogueTextLabel.text = wordReveal.VisibleText;
                SyncHiddenDialogueText();
                if (dialogueAutoFollow && RefreshDialogueScrollableContent())
                    FollowScrollIfNearBottom(dialogueScroll);
            }

            if (ttsVolumeDirty && Time.unscaledTime >= nextTtsVolumeSendAt)
            {
                SendPendingTtsVolume();
            }

            if (visibleState == "Thinking" && dialogueTextLabel != null && !pendingAssistantReveal)
            {
                thinkingElapsed += Time.unscaledDeltaTime;
                dialogueTextLabel.text = "Thinking" + new string('.', 1 + (int)(thinkingElapsed * 2f) % 3);
                SyncHiddenDialogueText();
            }

        }

        private void HandlePresentationInput()
        {
            HandleConsoleUnlockSequence();
            if (displayConfirmActive)
            {
                if (Input.GetKeyDown(KeyCode.Escape))
                {
                    RevertDisplaySettings();
                }
                return;
            }

            if (rebindingPushToTalk)
            {
                CapturePushToTalkBinding();
                return;
            }

            if (Input.GetKeyDown(KeyCode.Escape))
            {
                if (historyPanel != null && historyPanel.activeSelf)
                {
                    CloseHistoryPanel();
                    return;
                }
                if (consolePanel != null && consolePanel.activeSelf)
                {
                    CloseConsolePanel();
                    return;
                }
                if (settingsPanel != null && settingsPanel.activeSelf)
                {
                    CloseSettingsPanel();
                    return;
                }
                if (inputRequested)
                {
                    DismissInput();
                }
                return;
            }

            if (Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter))
            {
                bool overlayOpen = (historyPanel != null && historyPanel.activeSelf)
                    || (consolePanel != null && consolePanel.activeSelf)
                    || (settingsPanel != null && settingsPanel.activeSelf);
                bool hasText = messageInput != null && !string.IsNullOrWhiteSpace(messageInput.text);

                // Settings and Log are modal interaction surfaces. Return is
                // deliberately not a global conversation shortcut while one
                // of them is open.
                if (overlayOpen)
                {
                    return;
                }

                // A focused field submits through TMP's onSubmit callback. Do
                // not also treat the same Return as a global "open input" key.
                if (PresentationInputPolicy.ShouldDismissEmptyInput(inputRequested, hasText))
                {
                    DismissInput();
                    return;
                }

                if (PresentationInputPolicy.CanOpenInput(false, inputRequested))
                {
                    RequestInput(true);
                }
            }

            if (!interfaceHidden && messageInput != null && messageInput.isFocused)
            {
                inputRequested = true;
                inputVisibilityTarget = 1f;
            }
        }

        private void HandleConsoleUnlockSequence()
        {
            if (messageInput != null && messageInput.isFocused) return;
            string input = Input.inputString;
            if (string.IsNullOrEmpty(input)) return;
            foreach (char character in input)
            {
                if (character != '8')
                {
                    consoleUnlockBuffer = string.Empty;
                    continue;
                }
                // Retain the complete eight-character unlock sequence and
                // discard only characters older than that window.
                consoleUnlockBuffer += character;
                if (consoleUnlockBuffer.Length > 8)
                {
                    consoleUnlockBuffer = consoleUnlockBuffer.Substring(consoleUnlockBuffer.Length - 8);
                }
                if (consoleUnlockBuffer == "88888888")
                {
                    consoleUnlocked = true;
                    PlayerPrefs.SetInt("AIFren.ConsoleUnlocked", 1);
                    PlayerPrefs.Save();
                    RefreshDeveloperControlVisibility();
                    consoleUnlockBuffer = string.Empty;
                }
            }
        }

        private void UpdateHiddenInterfaceReveal()
        {
            if (interfaceHidden)
            {
                float mouseY = Input.mousePosition.y;
                if (mouseY <= EdgeRevealThresholdPixels || mouseY >= Screen.height - EdgeRevealThresholdPixels)
                {
                    interfaceHidden = false;
                    edgeRevealActive = true;
                    temporarilyRevealed = true;
                    edgeRevealGraceUntil = Time.unscaledTime + EdgeRevealGraceSeconds;
                    RefreshPresentationVisibility();
                }
                return;
            }

            if (!edgeRevealActive || inputRequested || (historyPanel != null && historyPanel.activeSelf) ||
                (settingsPanel != null && settingsPanel.activeSelf)) return;
            float mouseYAfterReveal = Input.mousePosition.y;
            bool atEdge = mouseYAfterReveal <= EdgeRevealThresholdPixels || mouseYAfterReveal >= Screen.height - EdgeRevealThresholdPixels;
            if (atEdge) edgeRevealGraceUntil = Time.unscaledTime + EdgeRevealGraceSeconds;
            if (!atEdge && Time.unscaledTime >= edgeRevealGraceUntil)
            {
                interfaceHidden = true;
                edgeRevealActive = false;
                temporarilyRevealed = false;
                RefreshPresentationVisibility();
            }
        }

        private void RequestInput(bool focus)
        {
            bool wasHidden = interfaceHidden;
            interfaceHidden = false;
            inputRequested = true;
            inputVisibilityTarget = 1f;
            // Avoid restarting the foreground CanvasGroup transition when
            // Enter opens a normally visible input field.
            if (wasHidden || (topBar != null && !topBar.activeSelf))
                RefreshPresentationVisibility();
            if (focus && messageInput != null && messageInput.interactable)
            {
                messageInput.ActivateInputField();
            }
        }

        private void DismissInput()
        {
            inputRequested = false;
            inputVisibilityTarget = 0f;
            if (messageInput != null)
            {
                messageInput.DeactivateInputField();
            }
        }

        private void UpdateInputPresentation()
        {
            // The coordinated hide/reveal coroutine owns foreground positions
            // while it is sliding them. Do not overwrite its anchored offsets.
            if (interfaceHidden || visibilityTransition != null)
            {
                return;
            }

            if (Mathf.Abs(inputVisibility - inputVisibilityTarget) < .001f)
            {
                return;
            }

            inputVisibility = Mathf.MoveTowards(
                inputVisibility,
                inputVisibilityTarget,
                Time.unscaledDeltaTime * InputSlideSpeed
            );

            if (inputCardRect != null)
            {
                inputCardRect.anchoredPosition = new Vector2(
                    0f,
                    Mathf.Lerp(HiddenInputOffset, 18f, inputVisibility)
                );
            }

            if (dialogueCardRect != null)
            {
                float baseOffset = 28f;
                float inputClearance = InputHeight + 24f;
                dialogueCardRect.anchoredPosition = new Vector2(
                    0f,
                    baseOffset + inputVisibility * inputClearance
                );
            }

            // The avatar composition is deliberately fixed while the input
            // animates.  Only foreground UI moves during a conversation.
        }

        private void RefreshPresentationVisibility()
        {
            bool show = !interfaceHidden || inputRequested;
            if (visibilityTransition != null) StopCoroutine(visibilityTransition);
            visibilityTransition = StartCoroutine(TransitionUiVisibility(show));
            SetTopControlLabel(hideUiButton, interfaceHidden || temporarilyRevealed ? "Show" : "Hide");
        }

        private IEnumerator TransitionUiVisibility(bool show)
        {
            // The hidden overlay owns dialogue only while the ordinary UI is
            // hidden. Disable it before the normal card begins its entrance.
            if (show && hiddenDialogueViewport != null)
                hiddenDialogueViewport.gameObject.SetActive(false);

            GameObject[] elements = { topBar, dialogueCard, inputCard };
            foreach (GameObject element in elements) if (element != null) element.SetActive(true);

            RectTransform topRect = topBar != null ? topBar.GetComponent<RectTransform>() : null;
            RectTransform dialogueRect = dialogueCard != null ? dialogueCard.GetComponent<RectTransform>() : null;
            RectTransform inputRect = inputCard != null ? inputCard.GetComponent<RectTransform>() : null;
            Vector2 topResting = Vector2.zero;
            Vector2 inputResting = new Vector2(0f, Mathf.Lerp(HiddenInputOffset, 18f, inputVisibility));
            Vector2 dialogueResting = new Vector2(0f, 28f + inputVisibility * (InputHeight + 24f));
            Vector2 topHidden = topResting + new Vector2(0f, (topRect != null ? topRect.rect.height : 72f) + 36f);
            Vector2 dialogueHidden = dialogueResting - new Vector2(0f, (dialogueRect != null ? dialogueRect.rect.height : DialogueMaximumHeight) + 40f);
            Vector2 inputHidden = inputResting - new Vector2(0f, (inputRect != null ? inputRect.rect.height : InputHeight) + 40f);
            CanvasGroup existing = topBar != null ? topBar.GetComponent<CanvasGroup>() : null;
            float from = existing != null ? existing.alpha : (show ? 0f : 1f);
            float to = show ? 1f : 0f;
            Vector2 topFrom = topRect != null ? topRect.anchoredPosition : topResting;
            Vector2 dialogueFrom = dialogueRect != null ? dialogueRect.anchoredPosition : dialogueResting;
            Vector2 inputFrom = inputRect != null ? inputRect.anchoredPosition : inputResting;
            Vector2 topTo = show ? topResting : topHidden;
            Vector2 dialogueTo = show ? dialogueResting : dialogueHidden;
            Vector2 inputTo = show ? inputResting : inputHidden;
            const float duration = .24f;
            for (float elapsed = 0f; elapsed < duration; elapsed += Time.unscaledDeltaTime)
            {
                float eased = Mathf.SmoothStep(0f, 1f, elapsed / duration);
                float alpha = Mathf.Lerp(from, to, eased);
                foreach (GameObject element in elements) SetPresentationAlpha(element, alpha, show);
                if (topRect != null) topRect.anchoredPosition = Vector2.Lerp(topFrom, topTo, eased);
                if (dialogueRect != null) dialogueRect.anchoredPosition = Vector2.Lerp(dialogueFrom, dialogueTo, eased);
                if (inputRect != null) inputRect.anchoredPosition = Vector2.Lerp(inputFrom, inputTo, eased);
                yield return null;
            }
            foreach (GameObject element in elements)
            {
                SetPresentationAlpha(element, to, show);
                if (!show && element != null) element.SetActive(false);
            }
            if (topRect != null) topRect.anchoredPosition = topTo;
            if (dialogueRect != null) dialogueRect.anchoredPosition = dialogueTo;
            if (inputRect != null) inputRect.anchoredPosition = inputTo;
            visibilityTransition = null;
            SyncHiddenDialogueText();
        }

        private static void SetPresentationAlpha(GameObject element, float alpha, bool interactable)
        {
            if (element == null) return;
            CanvasGroup group = element.GetComponent<CanvasGroup>() ?? element.AddComponent<CanvasGroup>();
            group.alpha = alpha;
            group.interactable = interactable;
            group.blocksRaycasts = interactable;
        }

        private async Task ConnectAsync()
        {
            if (client == null)
            {
                return;
            }

            ApplyStatus("connecting", BackendUri);
            await client.ConnectAsync(BackendUri);

            if (client.State == ConnectionState.Error)
            {
                ApplyStatus("error", client.LastError);
            }
        }

        private async void Reconnect()
        {
            if (client == null || client.State == ConnectionState.Connecting)
            {
                return;
            }
            ApplyStatus("connecting", "Reconnecting to local backend...");
            RefreshInputAvailability();
            await ConnectAsync();
            if (client.State == ConnectionState.Connected)
            {
                // The following snapshot drives Ready plus the live Models
                // values. Keep the button visible only for this attempt.
                ApplyStatus("connecting", "Connected. Loading snapshot...");
            }
        }

        private void HandleServerMessage(ServerMessage message)
        {
            if (message == null)
            {
                return;
            }

            if (message.type == "snapshot")
            {
                ApplySnapshot(message.data);
                return;
            }

            if (message.type == "command_error")
            {
                ApplyStatus("error", message.error != null ? message.error.message : "Backend command error.");
                submitInFlight = false;
                RefreshInputAvailability();
                return;
            }

            if (message.type != "event" || message.@event == null)
            {
                return;
            }

            BackendEvent backendEvent = message.@event;
            BackendEventData data = backendEvent.data;

            if (backendEvent.type == "status" && data != null)
            {
                ApplyStatus(data.state, data.message);
            }
            else if (backendEvent.type == "turn_started")
            {
                ApplyStatus("thinking", "Thinking...");
            }
            else if (backendEvent.type == "conversation_message" && data != null)
            {
                // Canonical conversation_message is the only source for Log
                // history. It is emitted after each persisted message and is
                // intentionally independent of visibility/reveal state.
                AddMessage(data.role, data.content, DateTimeOffset.Now.ToString("o"), false, false);
            }
            else if (backendEvent.type == "assistant_response" && data != null)
            {
                // This is presentation-only. Its later canonical
                // conversation_message event appends history exactly once.
                pendingAssistantContent = data.content;
                avatarAnimation?.PlayAttentiveReaction();
                pendingAssistantReveal = true;
                TryBeginPendingAssistantReveal();
            }
            else if (backendEvent.type == "tts_state" && data != null)
            {
                if (data.state == "playback_started")
                {
                    pendingSpeechReady = true;
                    pendingSpeechDuration = data.duration_seconds;
                    avatarAnimation?.BeginSpeech(data.duration_seconds, data.lip_sync_envelope);
                    TryBeginPendingAssistantReveal();
                    ApplyStatus("speaking", data.message);
                }
                else if (data.state == "failed" || data.state == "not_started" || data.state == "stopped")
                {
                    avatarAnimation?.StopSpeech();
                    pendingSpeechReady = true;
                    pendingSpeechDuration = 0f;
                    TryBeginPendingAssistantReveal();
                    ApplyStatus("ready", data.message);
                }
                else
                {
                    ApplyStatus(data.state == "speaking" ? "speaking" : "ready", data.message);
                }
            }
            else if (backendEvent.type == "voice_state" && data != null)
            {
                backendGlobalPtt = data.global_listener;
                RefreshGlobalPttStatus();
                UpdatePttIndicator(data.state);
            }
            else if (backendEvent.type == "voice_transcription" && data != null)
            {
                if (!pttAutoSend && !string.IsNullOrWhiteSpace(data.content))
                {
                    messageInput.text = data.content;
                    RequestInput(true);
                    ApplyStatus("ready", "Review transcription before sending.");
                }
            }
            else if (backendEvent.type == "console_log" && data != null)
            {
                PopulateConsole(data.lines);
                Debug.Log("AIFren console diagnostics received: " + (data.lines == null ? 0 : data.lines.Length) + " safe entries.");
            }
            else if (backendEvent.type == "error" && data != null)
            {
                if (string.Equals(data.source, "voice", StringComparison.OrdinalIgnoreCase))
                {
                    // A transcription/microphone error is not evidence that
                    // the independently operating global hook is unavailable.
                    RefreshGlobalPttStatus();
                    UpdatePttIndicator("ready");
                }
                ApplyStatus("error", data.message);
                submitInFlight = false;
                RefreshInputAvailability();
            }
        }

        private void ApplySnapshot(SnapshotData snapshot)
        {
            if (snapshot == null)
            {
                ApplyStatus("error", "Backend returned an invalid snapshot.");
                return;
            }

            if (snapshot.transport_version < 2)
            {
                ApplyStatus("error", "An older backend is listening on port 8765. Stop it, then launch the current AIFren backend.");
                return;
            }

            if (snapshot.character != null && !string.IsNullOrWhiteSpace(snapshot.character.name))
            {
                characterName = snapshot.character.name;
            }

            // A snapshot is authoritative at connection/reconnection time.
            // Replacing this list never depends on UI visibility.
            messages.Clear();
            if (snapshot.conversation != null)
            {
                messages.AddRange(snapshot.conversation);
            }

            if (characterNameLabel != null) characterNameLabel.text = characterName;
            RebuildHistory();

            ConversationMessage latestAssistant = null;
            for (int index = messages.Count - 1; index >= 0; index--)
            {
                if (messages[index].role == "assistant")
                {
                    latestAssistant = messages[index];
                    break;
                }
            }

            if (latestAssistant != null)
            {
                ShowAssistantDialogue(latestAssistant.content, true);
            }
            else
            {
                dialogueTextLabel.text = "I’m here when you’re ready to talk.";
                SyncHiddenDialogueText();
            }

            if (snapshot.tts != null)
            {
                volumeSlider.SetValueWithoutNotify(snapshot.tts.volume);
                UpdateVolumeLabel(snapshot.tts.volume);
                RefreshTtsModelUi(snapshot.tts);
            }

            RefreshGeminiModelUi(snapshot.models != null ? snapshot.models.gemini : null);
            UpdatePttIndicator(snapshot.voice != null ? snapshot.voice.state : "ready");
            Debug.Log(
                "AIFren snapshot received: Gemini=" +
                (snapshot.models != null && snapshot.models.gemini != null ? snapshot.models.gemini.model : "missing") +
                ", source=" +
                (snapshot.models != null && snapshot.models.gemini != null ? snapshot.models.gemini.source : "missing") +
                ", TTS=" + (snapshot.tts != null ? snapshot.tts.provider : "missing") +
                ", voice=" + (snapshot.tts != null ? snapshot.tts.voice : "missing") +
                ", device=" + (snapshot.tts != null ? snapshot.tts.device : "missing") + "."
            );

            _ = client.SetPushToTalkTranscriptionModeAsync(pttAutoSend);
            backendGlobalPtt = snapshot.voice != null && snapshot.voice.global_listener;
            _ = client.SetPushToTalkBindingAsync(PresentationPttBinding.Save(pushToTalkKey));
            RefreshGlobalPttStatus(true);
            if (startupPanel != null) startupPanel.SetActive(false);

            ApplyStatus(
                snapshot.status != null ? snapshot.status.state : "ready",
                snapshot.status != null ? snapshot.status.message : "Ready"
            );
        }

        private void AddMessage(string role, string content, string timestamp, bool animateAssistant, bool presentAssistant = true)
        {
            if (string.IsNullOrWhiteSpace(content))
            {
                return;
            }

            messages.Add(new ConversationMessage
            {
                role = role,
                content = content,
                timestamp = timestamp
            });
            RebuildHistory();

            if (role == "assistant" && presentAssistant)
            {
                ShowAssistantDialogue(content, !animateAssistant);
            }
        }

        private void TryBeginPendingAssistantReveal()
        {
            if (!pendingAssistantReveal || !pendingSpeechReady || string.IsNullOrWhiteSpace(pendingAssistantContent))
            {
                return;
            }

            ShowAssistantDialogue(pendingAssistantContent, false, pendingSpeechDuration);
            pendingAssistantContent = null;
            pendingAssistantReveal = false;
            pendingSpeechReady = false;
            pendingSpeechDuration = 0f;
        }

        private void ShowAssistantDialogue(string content, bool revealImmediately, float spokenDurationSeconds = 0f)
        {
            dialogueLayoutContent = content ?? string.Empty;
            UpdateDialogueLayout(true);
            wordReveal.WordsPerSecond = revealWordsPerSecond;
            wordReveal.Begin(content, revealImmediately || instantText);
            if (!revealImmediately && !instantText)
            {
                wordReveal.WordsPerSecond = WordReveal.WordsPerSecondForDuration(
                    wordReveal.WordCount,
                    spokenDurationSeconds,
                    revealWordsPerSecond
                );
            }
            dialogueTextLabel.text = wordReveal.VisibleText;
            SyncHiddenDialogueText();
            if (hiddenDialogueScroll != null)
            {
                Canvas.ForceUpdateCanvases();
                hiddenDialogueScroll.verticalNormalizedPosition = 1f;
            }
            dialogueAutoFollow = true;
            RefreshDialogueScrollableContent();
        }

        private void SkipCurrentReveal()
        {
            if (!wordReveal.IsComplete)
            {
                wordReveal.RevealAll();
                dialogueTextLabel.text = wordReveal.VisibleText;
                SyncHiddenDialogueText();
            }
        }

        private void UpdateDialogueLayout(bool scrollToTop)
        {
            if (dialogueCardRect == null || dialogueViewportRect == null || dialogueTextLabel == null)
            {
                return;
            }

            float width = Mathf.Max(1f, dialogueViewportRect.rect.width - 2f * DialogueHorizontalPadding);
            string measuredContent = string.IsNullOrWhiteSpace(dialogueLayoutContent)
                ? dialogueTextLabel.text : dialogueLayoutContent;
            float textHeight = dialogueTextLabel.GetPreferredValues(measuredContent, width, 0f).y;
            bool portrait = currentDisplaySettings != null && PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode, Screen.width, Screen.height);
            float stableHeight = portrait ? DialoguePortraitHeight : DialogueLandscapeHeight;
            dialogueCardRect.sizeDelta = new Vector2(0f, stableHeight);

            Canvas.ForceUpdateCanvases();
            float viewportHeight = Mathf.Max(1f, dialogueViewportRect.rect.height);
            RectTransform textRect = dialogueTextLabel.rectTransform;
            textRect.sizeDelta = new Vector2(-2f * DialogueHorizontalPadding, Mathf.Max(viewportHeight, textHeight + 2f * DialogueVerticalPadding));
            UpdateDialogueScrollbarVisibility();

            if (scrollToTop && dialogueScroll != null)
            {
                Canvas.ForceUpdateCanvases();
                dialogueScroll.verticalNormalizedPosition = 1f;
            }
        }

        private bool RefreshDialogueScrollableContent()
        {
            if (dialogueViewportRect == null || dialogueTextLabel == null) return false;
            float width = Mathf.Max(1f, dialogueViewportRect.rect.width - 2f * DialogueHorizontalPadding);
            float preferredHeight = dialogueTextLabel.GetPreferredValues(dialogueTextLabel.text, width, 0f).y;
            float targetHeight = Mathf.Max(dialogueViewportRect.rect.height, preferredHeight + 2f * DialogueVerticalPadding);
            Vector2 current = dialogueTextLabel.rectTransform.sizeDelta;
            if (Mathf.Abs(current.y - targetHeight) < .5f) return false;
            dialogueTextLabel.rectTransform.sizeDelta = new Vector2(current.x, targetHeight);
            Canvas.ForceUpdateCanvases();
            UpdateDialogueScrollbarVisibility();
            return true;
        }

        private void UpdateDialogueScrollbarVisibility()
        {
            if (dialogueScrollbar == null || dialogueViewportRect == null || dialogueTextLabel == null) return;
            bool needsScroll = dialogueTextLabel.rectTransform.rect.height > dialogueViewportRect.rect.height + 1f;
            if (dialogueScrollbar.gameObject.activeSelf != needsScroll)
                dialogueScrollbar.gameObject.SetActive(needsScroll);
        }

        private static bool IsNearBottom(ScrollRect scroll)
        {
            return scroll != null && scroll.verticalNormalizedPosition <= .035f;
        }

        private static void FollowScrollIfNearBottom(ScrollRect scroll)
        {
            if (scroll == null) return;
            Canvas.ForceUpdateCanvases();
            scroll.verticalNormalizedPosition = 0f;
        }

        private static Scrollbar AddThinScrollbar(Transform parent, ScrollRect scroll, float left = .955f, float right = .98f)
        {
            GameObject track = new GameObject("Scrollbar", typeof(RectTransform), typeof(Image), typeof(Scrollbar));
            track.transform.SetParent(parent, false);
            Image trackImage = track.GetComponent<Image>();
            trackImage.color = new Color(.55f, .38f, .78f, .18f);
            Scrollbar bar = track.GetComponent<Scrollbar>();
            bar.direction = Scrollbar.Direction.BottomToTop;
            GameObject handle = CreateScrollbarHandle(track.transform);
            bar.targetGraphic = handle.GetComponent<Image>();
            bar.handleRect = handle.GetComponent<RectTransform>();
            Stretch(track.GetComponent<RectTransform>(), new Vector2(left, .06f), new Vector2(right, .94f), Vector2.zero, Vector2.zero);
            scroll.verticalScrollbar = bar;
            // Reserve the track. Expanding/shrinking the viewport during TMP
            // word reveal invalidates content measurements and made the
            // dialogue scrollbar disappear after a long reply.
            scroll.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.Permanent;
            return bar;
        }

        private static GameObject CreateScrollbarHandle(Transform parent)
        {
            GameObject handle = new GameObject("Handle", typeof(RectTransform), typeof(Image));
            handle.transform.SetParent(parent, false);
            Image image = handle.GetComponent<Image>();
            image.color = new Color(.80f, .57f, .96f, .72f);
            Stretch(handle.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            return handle;
        }

        private void ApplyStatus(string state, string message)
        {
            detail = string.IsNullOrWhiteSpace(message) ? string.Empty : message;

            switch (state)
            {
                case "thinking":
                case "connecting":
                    visibleState = state == "connecting" ? "Connecting" : "Thinking";
                    statusDot.color = theme.statusThinking;
                    break;
                case "speaking":
                    visibleState = "Speaking";
                    statusDot.color = theme.statusSpeaking;
                    break;
                case "error":
                    visibleState = "Error";
                    statusDot.color = theme.statusError;
                    break;
                case "disconnected":
                    visibleState = "Disconnected";
                    statusDot.color = theme.secondaryText;
                    break;
                default:
                    visibleState = "Ready";
                    statusDot.color = theme.statusReady;
                    break;
            }

            statusLabel.text = visibleState;
            statusDetailLabel.text = detail;
            if (visibleState == "Ready")
            {
                submitInFlight = false;
            }

            if (visibleState == "Thinking")
            {
                ShowThinkingDialogue();
            }

            RefreshInputAvailability();

        }

        private async void SubmitCurrentText()
        {
            string text = messageInput != null ? messageInput.text.Trim() : string.Empty;
            if (submitInFlight || string.IsNullOrEmpty(text) || client == null || client.State != ConnectionState.Connected)
            {
                return;
            }

            submitInFlight = true;
            messageInput.text = string.Empty;
            DismissInput();
            RefreshInputAvailability();
            await client.SubmitTextAsync(text);

            if (client.State == ConnectionState.Error)
            {
                ApplyStatus("error", client.LastError);
                submitInFlight = false;
                RefreshInputAvailability();
            }
        }

        private void HandleInputSubmit(string _)
        {
            // TMP invokes this for Return in a single-line input field.  It is
            // preferred to polling Update(), which can miss Return after TMP
            // consumes the key and clears focus. submitInFlight prevents a
            // duplicate if a platform invokes submit more than once.
            SubmitCurrentText();
        }

        private void HandleInputDeselect(string _)
        {
            SetMessageInputFocused(false);
            if (!submitInFlight && messageInput != null && string.IsNullOrWhiteSpace(messageInput.text))
            {
                DismissInput();
            }
        }

        private void SetMessageInputFocused(bool focused)
        {
            if (messageInput == null) return;
            Image surface = messageInput.GetComponent<Image>();
            Outline outline = messageInput.GetComponent<Outline>();
            if (surface != null)
                surface.color = focused ? theme.controlHover : theme.surface;
            if (outline != null)
                outline.effectColor = focused
                    ? new Color(theme.accent.r, theme.accent.g, theme.accent.b, .9f)
                    : new Color(theme.outline.r, theme.outline.g, theme.outline.b, .48f);
        }

        private void ShowThinkingDialogue()
        {
            if (dialogueTextLabel == null || pendingAssistantReveal)
            {
                return;
            }

            thinkingElapsed = 0f;
            wordReveal.Begin("Thinking.", true);
            dialogueTextLabel.text = wordReveal.VisibleText;
            SyncHiddenDialogueText();
            // Thinking is presentation state, not a new dialogue measurement.
            // Preserve the current card geometry until the actual reply arrives.
        }

        private void UpdatePttIndicator(string state)
        {
            if (pttLabel == null)
            {
                return;
            }

            bool listening = string.Equals(state, "recording", StringComparison.OrdinalIgnoreCase)
                || string.Equals(state, "listening", StringComparison.OrdinalIgnoreCase);
            bool processing = string.Equals(state, "released", StringComparison.OrdinalIgnoreCase)
                || string.Equals(state, "transcribing", StringComparison.OrdinalIgnoreCase);
            pttLabel.text = listening ? "Listening" : (processing ? "Transcribing" : pushToTalkKey.ToString());
            pttLabel.color = listening ? theme.accentPink : theme.secondaryText;
            pttLabel.alignment = TextAlignmentOptions.MidlineRight;
            if (pttIndicator != null)
            {
                pttIndicator.color = listening ? theme.accentPink :
                    (processing ? theme.accent : theme.secondaryText);
            }
        }

        private void ToggleHistoryPanel()
        {
            if (historyPanel == null)
            {
                return;
            }

            bool show = !historyPanel.activeSelf;
            historyPanel.SetActive(show);
            if (modalScrim != null) modalScrim.SetActive(show);
            if (show)
            {
                modalScrim.transform.SetAsLastSibling();
                historyPanel.transform.SetAsLastSibling();
                RebuildHistory();
                if (historyScroll != null) historyScroll.verticalNormalizedPosition = 0f;
            }
        }

        private void CloseHistoryPanel()
        {
            if (historyPanel != null)
            {
                historyPanel.SetActive(false);
            }
            if (modalScrim != null) modalScrim.SetActive(false);
        }

        private void ToggleConsolePanel()
        {
            if (consolePanel == null) return;
            bool show = !consolePanel.activeSelf;
            consolePanel.SetActive(show);
            if (modalScrim != null) modalScrim.SetActive(show);
            if (show)
            {
                modalScrim.transform.SetAsLastSibling();
                consolePanel.transform.SetAsLastSibling();
                PopulateConsole(consoleLines.ToArray());
                _ = client?.RequestConsoleLogAsync();
            }
        }

        private void CloseConsolePanel()
        {
            if (consolePanel != null) consolePanel.SetActive(false);
            if (modalScrim != null) modalScrim.SetActive(false);
            SetTopControlLabel(consoleCopyButton, "Copy All");
            RefreshDeveloperControlVisibility();
        }

        private void ToggleSettingsPanel()
        {
            bool show = !settingsPanel.activeSelf;
            settingsPanel.SetActive(show);
            if (modalScrim != null) modalScrim.SetActive(show);
            if (show)
            {
                pendingDisplaySettings = currentDisplaySettings.Clone();
                RefreshDisplaySettingsUi();
                ConfigureSettingsPanelForCurrentOrientation();
                Debug.Log("AIFren Settings requested a fresh backend snapshot for live model status.");
                _ = client?.RequestSnapshotAsync();
                modalScrim.transform.SetAsLastSibling();
                settingsPanel.transform.SetAsLastSibling();
            }
        }

        private void ConfigureSettingsPanelForCurrentOrientation()
        {
            if (settingsPanel == null || currentDisplaySettings == null) return;
            bool portrait = PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode, Screen.width, Screen.height);
            RectTransform panel = settingsPanel.GetComponent<RectTransform>();
            if (portrait)
            {
                Stretch(panel, new Vector2(.04f, .075f), new Vector2(.96f, .925f), Vector2.zero, Vector2.zero);
                foreach (KeyValuePair<string, Button> tab in settingsTabButtons)
                {
                    RectTransform rect = tab.Value.GetComponent<RectTransform>();
                    rect.anchorMin = new Vector2(.045f, rect.anchorMin.y); rect.anchorMax = new Vector2(.275f, rect.anchorMax.y);
                }
                foreach (KeyValuePair<string, GameObject> page in settingsPages)
                {
                    RectTransform rect = page.Value.GetComponent<RectTransform>();
                    rect.anchorMin = new Vector2(.295f, .06f); rect.anchorMax = new Vector2(.955f, .89f);
                }
            }
            else
            {
                Stretch(panel, new Vector2(.12f, .07f), new Vector2(.88f, .91f), Vector2.zero, Vector2.zero);
                foreach (KeyValuePair<string, Button> tab in settingsTabButtons)
                {
                    RectTransform rect = tab.Value.GetComponent<RectTransform>();
                    rect.anchorMin = new Vector2(.05f, rect.anchorMin.y); rect.anchorMax = new Vector2(SettingsTabColumnEnd, rect.anchorMax.y);
                }
                foreach (KeyValuePair<string, GameObject> page in settingsPages)
                {
                    RectTransform rect = page.Value.GetComponent<RectTransform>();
                    rect.anchorMin = new Vector2(SettingsContentColumnStart, .06f); rect.anchorMax = new Vector2(.95f, .89f);
                }
            }
        }

        private void CloseSettingsPanel()
        {
            if (settingsPanel != null) settingsPanel.SetActive(false);
            if (modalScrim != null) modalScrim.SetActive(false);
        }

        private void ToggleTheme()
        {
            theme = theme.mode == PresentationThemeMode.Light ? PresentationThemes.Dark : PresentationThemes.Light;
            PresentationThemes.Save(theme.mode);
            ApplyTheme();
        }

        private void SetPttAutoSend(bool value)
        {
            pttAutoSend = value;
            PlayerPrefs.SetInt(PttAutoSendPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            if (transcriptionModeValue != null) transcriptionModeValue.text = value ? "Send automatically" : "Review before sending";
            if (client != null && client.State == ConnectionState.Connected)
                _ = client.SetPushToTalkTranscriptionModeAsync(value);
        }

        private void ApplyTheme()
        {
            if (theme == null) theme = PresentationThemes.Dark;
            if (backgroundImage != null)
            {
                Sprite customBackground = Resources.Load<Sprite>(presentation.backgroundResourcePath);
                if (customBackground != null)
                {
                    // A user-provided ignored local background remains the
                    // visual source; the theme only adjusts its overlay.
                    backgroundImage.texture = customBackground.texture;
                    backgroundImage.color = Color.white;
                }
                else
                {
                    backgroundImage.enabled = true;
                    backgroundImage.texture = Resources.Load<Texture2D>("Presentation/Backgrounds/" +
                        (theme.mode == PresentationThemeMode.Light ? "bedroom_day" : "bedroom_night"));
                    backgroundImage.color = Color.white;
                }
            }
            UpdateBackgroundCover();
            if (backgroundTint != null) backgroundTint.color = theme.backgroundTint;

            foreach (Image image in FindObjectsOfType<Image>(true))
            {
                string name = image.gameObject.name;
                if (name.Contains("Background") || name == "Status Dot") continue;
                if (name == "Modal Scrim") image.color = new Color(0f, 0f, 0f, .74f);
                else if (name == "Dialogue Card")
                    image.color = new Color(theme.surface.r, theme.surface.g, theme.surface.b,
                        theme.mode == PresentationThemeMode.Light ? .72f : .64f);
                else if (name == "Fill") image.color = theme.sliderFill;
                else if (name == "Handle" || name == "Checkmark") image.color = theme.accent;
                else if (name == "Background") image.color = theme.sliderTrack;
                else if (name.Contains("Button")) image.color = theme.control;
                else if (name.Contains("Viewport")) image.color = new Color(
                    theme.surfaceStrong.r, theme.surfaceStrong.g, theme.surfaceStrong.b,
                    theme.mode == PresentationThemeMode.Light ? .78f : .46f);
                else image.color = theme.surface;
                Button button = image.GetComponent<Button>();
                if (button != null)
                {
                    ColorBlock colors = button.colors;
                    colors.normalColor = Color.white;
                    colors.highlightedColor = theme.controlHover;
                    colors.pressedColor = theme.controlPressed;
                    colors.disabledColor = theme.disabledControl;
                    button.colors = colors;
                }
            }
            foreach (TextMeshProUGUI text in FindObjectsOfType<TextMeshProUGUI>(true))
            {
                bool isButtonLabel = text.GetComponentInParent<Button>() != null;
                text.color = isButtonLabel ? theme.text : theme.text;
                if (text.text == text.text.ToUpperInvariant() && text.text.Length > 2)
                    text.color = theme.sectionHeader;
            }
            foreach (SVGImage icon in FindObjectsOfType<SVGImage>(true))
            {
                icon.color = theme.text;
            }
            if (dialogueTextLabel != null) dialogueTextLabel.color = theme.text;
            if (pttLabel != null) UpdatePttIndicator("ready");
            ApplyStatus(visibleState.ToLowerInvariant(), detail);
        }

        private void UpdateBackgroundCover()
        {
            if (backgroundImage == null || backgroundImage.texture == null) return;
            Rect rect = backgroundImage.rectTransform.rect;
            if (rect.width <= 0f || rect.height <= 0f) return;
            float sourceAspect = backgroundImage.texture.width / (float)backgroundImage.texture.height;
            float viewportAspect = rect.width / rect.height;
            if (sourceAspect > viewportAspect)
            {
                float width = viewportAspect / sourceAspect;
                backgroundImage.uvRect = new Rect((1f - width) * .5f, 0f, width, 1f);
            }
            else
            {
                float height = sourceAspect / viewportAspect;
                backgroundImage.uvRect = new Rect(0f, (1f - height) * .5f, 1f, height);
            }
        }

        private void BeginPushToTalkRebind()
        {
            rebindingPushToTalk = true;
            pttRebindHint.text = "Press a key or mouse button... Escape cancels.";
        }

        private void CapturePushToTalkBinding()
        {
            if (Input.GetKeyDown(KeyCode.Escape))
            {
                rebindingPushToTalk = false;
                pttRebindHint.text = "Rebinding cancelled.";
                return;
            }

            foreach (KeyCode key in Enum.GetValues(typeof(KeyCode)))
            {
                if (PresentationPttBinding.IsValid(key) && Input.GetKeyDown(key))
                {
                    pushToTalkKey = key;
                    PlayerPrefs.SetString(PushToTalkBindingPreference, PresentationPttBinding.Save(key));
                    PlayerPrefs.Save();
                    _ = client?.SetPushToTalkBindingAsync(PresentationPttBinding.Save(pushToTalkKey));
                    rebindingPushToTalk = false;
                    pttRebindHint.text = "Bound to " + key + ".";
                    RefreshDisplaySettingsUi();
                    return;
                }
            }
        }

        private void UpdateUnityPushToTalk()
        {
            // Focused Unity input is always the primary path. The optional
            // OS-wide listener is layered on top in the backend; PushToTalk's
            // lock de-duplicates the matching global/local press or release.
            // Do not disable the reliable focused path merely because the
            // optional listener happens to be active.
            if (rebindingPushToTalk || settingsPanel == null || settingsPanel.activeSelf || client == null ||
                client.State != ConnectionState.Connected)
            {
                if (unityPttPressed)
                {
                    unityPttPressed = false;
                    UpdatePttIndicator("ready");
                }
                return;
            }

            if (!unityPttPressed && Input.GetKeyDown(pushToTalkKey))
            {
                unityPttPressed = true;
                presentationAudio?.PlayInterrupt();
                _ = client.SetPushToTalkPressedAsync(true);
            }
            else if (unityPttPressed && Input.GetKeyUp(pushToTalkKey))
            {
                unityPttPressed = false;
                _ = client.SetPushToTalkPressedAsync(false);
            }
        }

        private async void StopSpeech()
        {
            avatarAnimation?.StopSpeech();
            if (client != null && client.State == ConnectionState.Connected)
            {
                await client.StopTtsAsync();
            }
        }

        private void SetVolume(float volume)
        {
            UpdateVolumeLabel(volume);
            pendingTtsVolume = Mathf.Clamp01(volume);
            ttsVolumeDirty = true;
            if (Time.unscaledTime >= nextTtsVolumeSendAt) SendPendingTtsVolume();
        }

        private void ResetTtsVolumeToDefault()
        {
            const float defaultVolume = 1f;
            if (volumeSlider != null) volumeSlider.SetValueWithoutNotify(defaultVolume);
            UpdateVolumeLabel(defaultVolume);
            pendingTtsVolume = defaultVolume;
            ttsVolumeDirty = true;
            nextTtsVolumeSendAt = 0f;
            SendPendingTtsVolume();
        }

        private void RefreshGlobalPttStatus(bool connecting = false)
        {
            if (globalPttStatus == null) return;
            globalPttStatus.text = backendGlobalPtt
                ? "Global PTT: Active"
                : connecting ? "Global PTT: Starting" : "Global PTT: Unavailable";
        }

        private async void SendPendingTtsVolume()
        {
            if (!ttsVolumeDirty || client == null || client.State != ConnectionState.Connected) return;
            float volume = pendingTtsVolume;
            ttsVolumeDirty = false;
            nextTtsVolumeSendAt = Time.unscaledTime + TtsVolumeSendIntervalSeconds;
            await client.SetTtsVolumeAsync(volume);
        }

        private void FlushTtsVolume()
        {
            nextTtsVolumeSendAt = 0f;
            SendPendingTtsVolume();
        }

        private void SetRevealSpeed(float value)
        {
            revealWordsPerSecond = value;
            wordReveal.WordsPerSecond = value;
            PlayerPrefs.SetFloat(RevealSpeedPreference, value);
            PlayerPrefs.Save();
            revealSpeedLabel.text = $"{value:0.0} words / sec";
        }

        private void SetInstantText(bool value)
        {
            instantText = value;
            PlayerPrefs.SetInt(InstantTextPreference, value ? 1 : 0);
            PlayerPrefs.Save();
        }

        private void UpdateVolumeLabel(float value)
        {
            volumeLabel.text = $"{Mathf.RoundToInt(value * 100f)}%";
        }

        private void RefreshInputAvailability()
        {
            bool connected = client != null && client.State == ConnectionState.Connected;
            messageInput.interactable = connected && !submitInFlight;
            sendButton.interactable = connected && !submitInFlight;
        }

        private void HandleAvatarLoaded(GameObject avatar)
        {
            avatarAnimation = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarAnimationController>()
                : null;
            if (avatarSurface != null)
            {
                avatarSurface.gameObject.SetActive(true);
                // Keep the first visible avatar frame until its actual
                // RenderTexture and crop aspect are both finalized.
                avatarSurface.color = new Color(1f, 1f, 1f, 0f);
            }
            if (avatarPresentationInitialization != null)
            {
                StopCoroutine(avatarPresentationInitialization);
            }
            avatarPresentationInitialization = StartCoroutine(FinalizeAvatarPresentationAfterLayout());
        }

        private IEnumerator FinalizeAvatarPresentationAfterLayout()
        {
            // AvatarLoader creates its target from the RawImage's final canvas
            // dimensions.  Startup previously read the crop aspect before that
            // target existed (using the 1x1 fallback), whereas Reset ran after
            // target allocation.  Complete this deterministic layout/target
            // sequence once before exposing the avatar; this is not a timing
            // retry and it leaves Reset on the same canonical path.
            // The initial persisted display move completes through a bounded
            // native-window finalization coroutine. Do not reveal the avatar
            // using the launch monitor's pre-move geometry.
            while (startupDisplayFinalizationPending)
            {
                yield return null;
            }
            Canvas.ForceUpdateCanvases();
            yield return new WaitForEndOfFrame();
            Canvas.ForceUpdateCanvases();
            UpdateCompositionLayout();
            Canvas.ForceUpdateCanvases();
            // UpdateCompositionLayout is the single final path: it applies the
            // layout, refreshes the RT from its final dimensions, then resolves
            // the canonical framing state before this surface becomes visible.
            Canvas.ForceUpdateCanvases();

            if (avatarSurface != null)
            {
                avatarSurface.color = Color.white;
            }
            avatarPresentationInitialization = null;
        }

        private void HandleAvatarLoadFailed(string error)
        {
            if (avatarSurface != null)
            {
                avatarSurface.gameObject.SetActive(false);
            }

            ApplyStatus("error", error);
        }

        private void RebuildHistory()
        {
            if (historyContent == null)
            {
                return;
            }

            bool followLatest = IsNearBottom(historyScroll);
            for (int index = historyContent.childCount - 1; index >= 0; index--)
            {
                Destroy(historyContent.GetChild(index).gameObject);
            }

            Canvas.ForceUpdateCanvases();
            float y = -8f;
            string activeDate = null;
            bool hasUndatedEntries = false;
            for (int index = 0; index < messages.Count; index++)
            {
                ConversationMessage message = messages[index];
                string dateHeading = FormatHistoryDate(message.timestamp);
                if (dateHeading != activeDate)
                {
                    activeDate = dateHeading;
                    if (!string.IsNullOrEmpty(activeDate))
                    {
                        TMP_Text heading = CreateText(historyContent, "<b>" + activeDate + "</b>  ─────────", 15f, theme.sectionHeader, TextAlignmentOptions.MidlineLeft);
                        RectTransform headingRect = heading.rectTransform;
                        headingRect.anchorMin = new Vector2(0f, 1f);
                        headingRect.anchorMax = new Vector2(1f, 1f);
                        headingRect.pivot = new Vector2(.5f, 1f);
                        headingRect.anchoredPosition = new Vector2(0f, y);
                        headingRect.sizeDelta = new Vector2(-28f, 28f);
                        y -= 34f;
                    }
                    else if (!hasUndatedEntries)
                    {
                        hasUndatedEntries = true;
                        TMP_Text heading = CreateText(historyContent, "Older history — date unavailable", 15f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
                        RectTransform headingRect = heading.rectTransform;
                        headingRect.anchorMin = new Vector2(0f, 1f);
                        headingRect.anchorMax = new Vector2(1f, 1f);
                        headingRect.pivot = new Vector2(.5f, 1f);
                        headingRect.anchoredPosition = new Vector2(0f, y);
                        headingRect.sizeDelta = new Vector2(-20f, 24f);
                        y -= 30f;
                    }
                }
                bool isUser = message.role == "user";
                TMP_Text bubble = CreateText(
                    historyContent,
                    isUser ? "You" : characterName,
                    19f,
                    theme.text,
                    TextAlignmentOptions.TopLeft
                );
                RectTransform bubbleRect = bubble.rectTransform;
                bubbleRect.anchorMin = new Vector2(0f, 1f);
                bubbleRect.anchorMax = new Vector2(1f, 1f);
                bubbleRect.pivot = new Vector2(0.5f, 1f);
                bubbleRect.anchoredPosition = new Vector2(0f, y);
                bubbleRect.sizeDelta = new Vector2(-28f, 28f);
                string timestamp = FormatHistoryTimestamp(message.timestamp);
                string speakerColor = "#" + ColorUtility.ToHtmlStringRGB(isUser ? theme.userText : theme.sectionHeader);
                string timestampColor = "#" + ColorUtility.ToHtmlStringRGB(theme.mutedText);
                bubble.text = string.IsNullOrEmpty(timestamp)
                    ? $"<b><color={speakerColor}>{(isUser ? "You" : characterName)}</color></b>\n{message.content}"
                    : $"<b><color={speakerColor}>{(isUser ? "You" : characterName)}</color></b>  <size=65%><color={timestampColor}>{timestamp}</color></size>\n{message.content}";
                float height = Mathf.Max(54f, bubble.preferredHeight + 14f);
                bubbleRect.sizeDelta = new Vector2(-28f, height);
                y -= height + 12f;
            }

            RectTransform contentRect = historyContent as RectTransform;
            contentRect.sizeDelta = new Vector2(0f, Mathf.Max(20f, -y));
            Canvas.ForceUpdateCanvases();
            if (followLatest) historyScroll.verticalNormalizedPosition = 0f;
        }

        private static string FormatHistoryTimestamp(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return string.Empty;
            return PresentationHistoryTime.TryGetLocalTime(value, out DateTime timestamp)
                ? timestamp.ToString("h:mm tt")
                : string.Empty;
        }

        private static string FormatHistoryDate(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return string.Empty;
            return PresentationHistoryTime.TryGetLocalTime(value, out DateTime timestamp)
                ? timestamp.ToString("dddd, MMMM d, yyyy")
                : string.Empty;
        }

        private void BuildInterface()
        {
            EnsureEventSystem();
            font = Resources.Load<TMP_FontAsset>("Fonts & Materials/LiberationSans SDF") ?? TMP_Settings.defaultFontAsset;

            GameObject canvasObject = new GameObject("AIFren Companion Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            Canvas canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 20;
            canvasScaler = canvasObject.GetComponent<CanvasScaler>();
            canvasScaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            canvasScaler.referenceResolution = DefaultReferenceResolution;
            canvasScaler.matchWidthOrHeight = 0.5f;
            DontDestroyOnLoad(canvasObject);

            RectTransform root = canvasObject.GetComponent<RectTransform>();
            RawImage background = CreateRawImage(root, "Background");
            backgroundImage = background;
            Stretch(background.rectTransform, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            Sprite customBackground = Resources.Load<Sprite>(presentation.backgroundResourcePath);
            if (customBackground != null)
            {
                background.texture = customBackground.texture;
            }
            else
            {
                background.texture = CreateGradientTexture(presentation.backgroundTopColor, presentation.backgroundBottomColor);
            }
            Image tint = CreateImage(root, "Background Tint", Color.clear);
            Stretch(tint.rectTransform, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            tint.raycastTarget = false;
            backgroundTint = tint;

            RawImage avatarFrame = CreateRawImage(root, "Avatar Presentation");
            Stretch(avatarFrame.rectTransform, new Vector2(0.10f, 0.15f), new Vector2(0.90f, 0.94f), Vector2.zero, Vector2.zero);
            avatarFrame.color = new Color(1f, 1f, 1f, 0f);
            avatarSurface = avatarFrame;
            avatarFrameRect = avatarFrame.rectTransform;
            avatarAspectFitter = avatarFrame.gameObject.AddComponent<AspectRatioFitter>();
            avatarAspectFitter.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
            avatarFramingInputSurface = avatarFrame.gameObject.AddComponent<AvatarFramingInputSurface>();
            avatarFramingInputSurface.Dragged += HandleAvatarFramingDrag;
            avatarFramingInputSurface.Scrolled += HandleAvatarFramingScroll;

            // This is only an invisible layout parent.  Each control below is
            // its own floating surface; there is intentionally no header bar.
            topBar = new GameObject("Floating Companion Controls", typeof(RectTransform));
            topBar.transform.SetParent(root, false);
            Stretch(topBar.GetComponent<RectTransform>(), new Vector2(0.025f, 0.91f), new Vector2(0.975f, 0.98f), Vector2.zero, Vector2.zero);
            // Character identity is rendered by the dialogue/history surfaces.
            // A zero-sized TMP child here was the stray glyph fragment that
            // moved with the Hide control during the top-control transition.
            characterNameLabel = null;
            hideUiButton = CreateButton(topBar.transform, "Hide", Panel);
            // The generic Outline sampled the custom rounded sprite one pixel
            // beyond this larger text button's lower-left corner, leaving the
            // persistent stray mark below Hide. The sliced surface remains.
            Outline hideOutline = hideUiButton.GetComponent<Outline>();
            if (hideOutline != null) hideOutline.enabled = false;
            Stretch(hideUiButton.GetComponent<RectTransform>(), new Vector2(0f, 0.14f), new Vector2(0.10f, 0.88f), Vector2.zero, Vector2.zero);
            hideUiButton.onClick.AddListener(() =>
            {
                // A temporary top-edge reveal is already visible. "Show"
                // pins it instead of toggling it straight back to hidden.
                interfaceHidden = temporarilyRevealed ? false : !interfaceHidden;
                inputRequested = false;
                inputVisibilityTarget = 0f;
                edgeRevealActive = false;
                temporarilyRevealed = false;
                RefreshPresentationVisibility();
            });
            pttIndicator = CreatePttIndicator(topBar.transform);
            Stretch(pttIndicator.rectTransform, new Vector2(0.115f, .22f), new Vector2(.145f, .78f), Vector2.zero, Vector2.zero);
            pttLabel = CreateText(topBar.transform, KeyCode.F8.ToString(), 14f, new Color(.72f, .72f, .82f, 1f), TextAlignmentOptions.MidlineLeft);
            Stretch(pttLabel.rectTransform, new Vector2(0.148f, 0.24f), new Vector2(0.25f, 0.76f), Vector2.zero, Vector2.zero);
            statusDot = CreateImage(topBar.transform, "Status Dot", Color.white);
            Stretch(statusDot.rectTransform, new Vector2(0.66f, 0.38f), new Vector2(0.672f, 0.62f), Vector2.zero, Vector2.zero);
            statusLabel = CreateText(topBar.transform, "Disconnected", 16f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(statusLabel.rectTransform, new Vector2(0.678f, 0.25f), new Vector2(0.76f, 0.78f), Vector2.zero, Vector2.zero);
            statusDetailLabel = CreateText(topBar.transform, detail, 13f, new Color(0.74f, 0.73f, 0.84f, 1f), TextAlignmentOptions.MidlineLeft);
            Stretch(statusDetailLabel.rectTransform, new Vector2(0.76f, 0.18f), new Vector2(0.83f, 0.82f), Vector2.zero, Vector2.zero);
            statusDetailLabel.gameObject.SetActive(false);
            statusDot.gameObject.SetActive(false);
            statusLabel.gameObject.SetActive(false);
            historyButton = CreateButton(topBar.transform, "Log", Panel);
            Stretch(historyButton.GetComponent<RectTransform>(), new Vector2(0.855f, 0.14f), new Vector2(0.90f, 0.88f), Vector2.zero, Vector2.zero);
            historyButton.onClick.AddListener(ToggleHistoryPanel);
            consoleUnlocked = PlayerPrefs.GetInt("AIFren.ConsoleUnlocked", 0) == 1;
            consoleButton = CreateButton(topBar.transform, "Console", Panel);
            consoleButton.onClick.AddListener(ToggleConsolePanel);
            RefreshDeveloperControlVisibility();
            settingsButton = CreateButton(topBar.transform, "Settings", Panel);
            Stretch(settingsButton.GetComponent<RectTransform>(), new Vector2(0.905f, 0.14f), new Vector2(0.955f, 0.88f), Vector2.zero, Vector2.zero);
            settingsButton.onClick.AddListener(ToggleSettingsPanel);
            closeButton = CreateButton(topBar.transform, "Close", Panel);
            Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(0.96f, 0.14f), Vector2.one, Vector2.zero, Vector2.zero);
            closeButton.onClick.AddListener(Application.Quit);

            dialogueCard = CreatePanel(root, "Dialogue Card", Panel);
            dialogueCardRect = dialogueCard.GetComponent<RectTransform>();
            dialogueCardRect.anchorMin = new Vector2(0.055f, 0f);
            dialogueCardRect.anchorMax = new Vector2(0.945f, 0f);
            dialogueCardRect.pivot = new Vector2(0.5f, 0f);
            dialogueCardRect.anchoredPosition = new Vector2(0f, 28f);
            dialogueCardRect.sizeDelta = new Vector2(0f, DialogueMinimumHeight);
            Button revealButton = dialogueCard.AddComponent<Button>();
            revealButton.transition = Selectable.Transition.None;
            revealButton.onClick.AddListener(SkipCurrentReveal);
            dialogueSpeakerLabel = CreateText(dialogueCard.transform, string.Empty, 1f, Color.clear, TextAlignmentOptions.MidlineLeft);
            Stretch(dialogueSpeakerLabel.rectTransform, Vector2.zero, Vector2.zero, Vector2.zero, Vector2.zero);
            dialogueTextLabel = CreateText(dialogueCard.transform, "I’m here when you’re ready to talk.", 28f, Ink, TextAlignmentOptions.TopLeft);
            dialogueTextLabel.enableWordWrapping = true;
            dialogueTextLabel.enableAutoSizing = false;
            dialogueTextLabel.lineSpacing = -5f;
            dialogueTextLabel.paragraphSpacing = -7f;
            dialogueTextLabel.fontSizeMin = DialogueFontMinimum;
            dialogueTextLabel.fontSizeMax = DialogueFontLandscapeMaximum;
            dialogueTextLabel.overflowMode = TextOverflowModes.Overflow;
            dialogueTextLabel.margin = new Vector4(DialogueHorizontalPadding, DialogueVerticalPadding, DialogueHorizontalPadding, DialogueVerticalPadding);
            GameObject dialogueViewport = new GameObject("Dialogue Viewport", typeof(RectTransform));
            dialogueViewport.transform.SetParent(dialogueCard.transform, false);
            dialogueViewportRect = dialogueViewport.GetComponent<RectTransform>();
            Stretch(dialogueViewportRect, new Vector2(0.04f, 0.19f), new Vector2(0.96f, 0.91f), Vector2.zero, Vector2.zero);
            dialogueViewport.AddComponent<RectMask2D>();
            dialogueTextLabel.transform.SetParent(dialogueViewport.transform, false);
            dialogueTextLabel.rectTransform.anchorMin = new Vector2(0f, 1f);
            dialogueTextLabel.rectTransform.anchorMax = new Vector2(1f, 1f);
            dialogueTextLabel.rectTransform.pivot = new Vector2(0.5f, 1f);
            dialogueTextLabel.rectTransform.anchoredPosition = Vector2.zero;
            dialogueTextLabel.rectTransform.sizeDelta = new Vector2(-2f * DialogueHorizontalPadding, DialogueMinimumHeight - DialogueChromeHeight);
            dialogueScroll = dialogueCard.AddComponent<ScrollRect>();
            dialogueScroll.viewport = dialogueViewportRect;
            dialogueScroll.content = dialogueTextLabel.rectTransform;
            dialogueScroll.horizontal = false;
            dialogueScroll.vertical = true;
            dialogueScroll.movementType = ScrollRect.MovementType.Clamped;
            dialogueScroll.scrollSensitivity = 32f;
            dialogueScroll.onValueChanged.AddListener(_ =>
            {
                // ScrollRect emits value changes while TMP/content geometry is
                // refreshed. Only direct scroll input should pause/resume
                // automatic following of assistant text.
                if (Input.GetMouseButton(0) || Mathf.Abs(Input.mouseScrollDelta.y) > .001f)
                {
                    dialogueAutoFollow = IsNearBottom(dialogueScroll);
                }
            });
            dialogueScrollbar = AddThinScrollbar(dialogueCard.transform, dialogueScroll, .976f, .984f);
            pttIndicator.transform.SetParent(dialogueCard.transform, false);
            pttLabel.transform.SetParent(dialogueCard.transform, false);
            PlacePttPresentation();

            inputCard = CreatePanel(root, "Message Input", new Color(0.04f, 0.04f, 0.08f, 0.92f));
            inputCardRect = inputCard.GetComponent<RectTransform>();
            inputCardRect.anchorMin = new Vector2(0.055f, 0f);
            inputCardRect.anchorMax = new Vector2(0.945f, 0f);
            inputCardRect.pivot = new Vector2(0.5f, 0f);
            inputCardRect.sizeDelta = new Vector2(0f, InputHeight);
            inputCardRect.anchoredPosition = new Vector2(0f, HiddenInputOffset);
            messageInput = CreateInputField(inputCard.transform);
            messageInputRect = messageInput.GetComponent<RectTransform>();
            messageInput.onSubmit.AddListener(HandleInputSubmit);
            messageInput.onSelect.AddListener(_ => SetMessageInputFocused(true));
            messageInput.onDeselect.AddListener(HandleInputDeselect);
            Stretch(messageInputRect, new Vector2(0.025f, 0.18f), new Vector2(0.84f, 0.82f), Vector2.zero, Vector2.zero);
            sendButton = CreateButton(inputCard.transform, "Send", new Color(0.48f, 0.28f, 0.63f, 1f));
            sendButtonRect = sendButton.GetComponent<RectTransform>();
            Stretch(sendButtonRect, new Vector2(0.855f, 0.18f), new Vector2(0.975f, 0.82f), Vector2.zero, Vector2.zero);
            sendButton.onClick.AddListener(SubmitCurrentText);

            GameObject hiddenDialogueViewportObject = new GameObject("Hidden Dialogue Viewport", typeof(RectTransform), typeof(RectMask2D));
            hiddenDialogueViewportObject.transform.SetParent(root, false);
            hiddenDialogueViewport = hiddenDialogueViewportObject.GetComponent<RectTransform>();
            Stretch(hiddenDialogueViewport, new Vector2(.10f, .08f), new Vector2(.90f, .28f), Vector2.zero, Vector2.zero);
            hiddenDialogueText = CreateText(hiddenDialogueViewport, string.Empty, 25f, Ink, TextAlignmentOptions.TopLeft);
            hiddenDialogueText.enableWordWrapping = true;
            hiddenDialogueText.lineSpacing = -4f;
            hiddenDialogueText.paragraphSpacing = -6f;
            hiddenDialogueText.margin = new Vector4(12f, 10f, 12f, 10f);
            hiddenDialogueText.overflowMode = TextOverflowModes.Masking;
            hiddenDialogueText.rectTransform.anchorMin = new Vector2(0f, 1f);
            hiddenDialogueText.rectTransform.anchorMax = new Vector2(1f, 1f);
            hiddenDialogueText.rectTransform.pivot = new Vector2(.5f, 1f);
            hiddenDialogueText.rectTransform.anchoredPosition = Vector2.zero;
            hiddenDialogueText.rectTransform.sizeDelta = new Vector2(-24f, 1f);
            Outline hiddenDialogueOutline = hiddenDialogueText.gameObject.AddComponent<Outline>();
            hiddenDialogueOutline.effectColor = new Color(0f, 0f, 0f, .72f);
            hiddenDialogueOutline.effectDistance = new Vector2(1.2f, -1.2f);
            hiddenDialogueScroll = hiddenDialogueViewportObject.AddComponent<ScrollRect>();
            hiddenDialogueScroll.viewport = hiddenDialogueViewport;
            hiddenDialogueScroll.content = hiddenDialogueText.rectTransform;
            hiddenDialogueScroll.horizontal = false;
            hiddenDialogueScroll.vertical = true;
            hiddenDialogueScroll.movementType = ScrollRect.MovementType.Clamped;
            hiddenDialogueScroll.scrollSensitivity = 24f;
            hiddenDialogueScrollbar = AddThinScrollbar(hiddenDialogueViewportObject.transform, hiddenDialogueScroll, .978f, .987f);
            hiddenDialogueScrollbar.gameObject.SetActive(false);
            hiddenDialogueViewportObject.SetActive(false);

            modalScrim = CreatePanel(root, "Modal Scrim", new Color(0f, 0f, 0f, 0.70f));
            Stretch(modalScrim.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            modalScrim.SetActive(false);
            historyPanel = CreateHistoryPanel(root);
            consolePanel = CreateConsolePanel(root);
            settingsPanel = CreateSettingsPanel(root);
            displayConfirmPanel = CreateDisplayConfirmationPanel(root);
            avatarFramingModePanel = CreateAvatarFramingMode(root);
            startupPanel = CreateStartupPanel(root);
            historyPanel.SetActive(false);
            consolePanel.SetActive(false);
            settingsPanel.SetActive(false);
            displayConfirmPanel.SetActive(false);
            avatarFramingModePanel.SetActive(false);
            ApplyStatus("disconnected", detail);
            RefreshInputAvailability();
            // CanvasScaler changes are deferred until the canvas rebuild. Force
            // the baseline rebuild before reading landscape RectTransforms.
            Canvas.ForceUpdateCanvases();
            UpdateCompositionLayout();
            Canvas.ForceUpdateCanvases();
            UpdateDialogueLayout(true);
        }

        private GameObject CreateHistoryPanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Conversation History", new Color(0.055f, 0.05f, 0.11f, 0.96f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(0.12f, 0.16f), new Vector2(0.88f, 0.84f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "Conversation history", 22f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(0.06f, 0.89f), new Vector2(0.72f, 0.98f), Vector2.zero, Vector2.zero);
            Button closeButton = CreateButton(panel.transform, "Close", new Color(0.22f, 0.19f, 0.31f, 1f));
            Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(0.76f, 0.89f), new Vector2(0.94f, 0.98f), Vector2.zero, Vector2.zero);
            closeButton.onClick.AddListener(CloseHistoryPanel);

            GameObject viewport = CreatePanel(panel.transform, "Viewport", new Color(0f, 0f, 0f, 0.18f));
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(0.045f, 0.05f), new Vector2(0.955f, 0.875f), Vector2.zero, Vector2.zero);
            GameObject textViewport = new GameObject("History Text Viewport", typeof(RectTransform), typeof(RectMask2D));
            textViewport.transform.SetParent(viewport.transform, false);
            Stretch(textViewport.GetComponent<RectTransform>(), Vector2.zero, new Vector2(.955f, 1f), Vector2.zero, Vector2.zero);
            GameObject content = new GameObject("History Content", typeof(RectTransform));
            content.transform.SetParent(textViewport.transform, false);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 1f);
            contentRect.anchorMax = new Vector2(1f, 1f);
            contentRect.pivot = new Vector2(0.5f, 1f);
            contentRect.anchoredPosition = Vector2.zero;
            contentRect.sizeDelta = new Vector2(0f, 20f);
            historyContent = content.transform;

            // Keep the scrolling hit area inside the clipped viewport so it
            // cannot compete with header buttons or modal chrome.
            historyScroll = viewport.AddComponent<ScrollRect>();
            historyScroll.viewport = textViewport.GetComponent<RectTransform>();
            historyScroll.content = contentRect;
            historyScroll.horizontal = false;
            historyScroll.vertical = true;
            historyScroll.movementType = ScrollRect.MovementType.Clamped;
            historyScroll.scrollSensitivity = 35f;
            AddThinScrollbar(viewport.transform, historyScroll, .965f, .978f);
            return panel;
        }

        private GameObject CreateConsolePanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Backend Console", new Color(.035f, .03f, .07f, .98f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(.12f, .14f), new Vector2(.88f, .86f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "Backend console", 22f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.06f, .89f), new Vector2(.58f, .98f), Vector2.zero, Vector2.zero);
            Button clear = CreateButton(panel.transform, "Clear", Panel);
            Stretch(clear.GetComponent<RectTransform>(), new Vector2(.60f, .89f), new Vector2(.76f, .98f), Vector2.zero, Vector2.zero);
            clear.onClick.AddListener(() => PopulateConsole(new string[0]));
            consoleCopyButton = CreateButton(panel.transform, "Copy All", Panel);
            Stretch(consoleCopyButton.GetComponent<RectTransform>(), new Vector2(.45f, .89f), new Vector2(.59f, .98f), Vector2.zero, Vector2.zero);
            consoleCopyButton.onClick.AddListener(() =>
            {
                GUIUtility.systemCopyBuffer = string.Join("\n", consoleLines);
                SetTopControlLabel(consoleCopyButton, "Copied");
            });
            Button close = CreateButton(panel.transform, "Close", Panel);
            Stretch(close.GetComponent<RectTransform>(), new Vector2(.78f, .89f), new Vector2(.94f, .98f), Vector2.zero, Vector2.zero);
            close.onClick.AddListener(CloseConsolePanel);
            GameObject viewport = CreatePanel(panel.transform, "Console Viewport", new Color(0f, 0f, 0f, .28f));
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(.045f, .05f), new Vector2(.955f, .865f), Vector2.zero, Vector2.zero);
            GameObject textViewport = new GameObject("Console Text Viewport", typeof(RectTransform), typeof(RectMask2D));
            textViewport.transform.SetParent(viewport.transform, false);
            Stretch(textViewport.GetComponent<RectTransform>(), Vector2.zero, new Vector2(.955f, 1f), Vector2.zero, Vector2.zero);
            GameObject content = new GameObject("Console Content", typeof(RectTransform)); content.transform.SetParent(textViewport.transform, false);
            RectTransform contentRect = content.GetComponent<RectTransform>(); contentRect.anchorMin = new Vector2(0f, 1f); contentRect.anchorMax = new Vector2(1f, 1f); contentRect.pivot = new Vector2(.5f, 1f); contentRect.sizeDelta = new Vector2(0f, 24f);
            consoleContent = content.transform;
            consoleText = CreateText(consoleContent, string.Empty, 14f, theme.secondaryText, TextAlignmentOptions.TopLeft);
            consoleText.enableWordWrapping = true;
            consoleText.rectTransform.anchorMin = new Vector2(0f, 1f);
            consoleText.rectTransform.anchorMax = new Vector2(1f, 1f);
            consoleText.rectTransform.pivot = new Vector2(.5f, 1f);
            consoleText.rectTransform.anchoredPosition = Vector2.zero;
            consoleText.rectTransform.sizeDelta = new Vector2(-20f, 24f);
            consoleScroll = viewport.AddComponent<ScrollRect>(); consoleScroll.viewport = textViewport.GetComponent<RectTransform>(); consoleScroll.content = contentRect; consoleScroll.horizontal = false; consoleScroll.vertical = true; consoleScroll.movementType = ScrollRect.MovementType.Clamped; consoleScroll.scrollSensitivity = 35f;
            AddThinScrollbar(viewport.transform, consoleScroll, .965f, .978f);
            return panel;
        }

        private void PopulateConsole(string[] lines)
        {
            bool followLatest = IsNearBottom(consoleScroll);
            consoleLines.Clear();
            if (lines != null) consoleLines.AddRange(lines.Where(line => !string.IsNullOrWhiteSpace(line)));
            if (consoleContent == null || consoleText == null) return;
            consoleText.text = consoleLines.Count > 0
                ? string.Join("\n", consoleLines)
                : "No backend diagnostics have been received yet.";
            // A modal can be populated in the same frame it is made active.
            // Resolve its viewport before asking TMP for a preferred size so
            // the first console paint never measures at width zero.
            Canvas.ForceUpdateCanvases();
            float width = Mathf.Max(1f, consoleContent.GetComponent<RectTransform>().rect.width - 20f);
            float height = Mathf.Max(24f, consoleText.GetPreferredValues(consoleText.text, width, 0f).y + 16f);
            consoleText.rectTransform.sizeDelta = new Vector2(-20f, height);
            consoleContent.GetComponent<RectTransform>().sizeDelta = new Vector2(0f, height);
            Canvas.ForceUpdateCanvases();
            if (followLatest && consoleScroll != null) consoleScroll.verticalNormalizedPosition = 0f;
        }

        private GameObject CreateSettingsPanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Settings", new Color(0.055f, 0.05f, 0.11f, 0.97f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(0.12f, 0.07f), new Vector2(0.88f, 0.91f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "Settings", 27f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(0.055f, 0.91f), new Vector2(0.65f, 0.985f), Vector2.zero, Vector2.zero);
            Button closeButton = CreateButton(panel.transform, "Close", new Color(0.22f, 0.19f, 0.31f, 1f));
            Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(0.79f, 0.915f), new Vector2(0.945f, 0.98f), Vector2.zero, Vector2.zero);
            closeButton.onClick.AddListener(CloseSettingsPanel);
            string[] tabs = { "Display", "Models", "Audio", "Dialogue", "Controls", "Appearance", "Advanced" };
            for (int index = 0; index < tabs.Length; index++)
            {
                string tab = tabs[index];
                Button tabButton = CreateButton(panel.transform, tab, Panel);
                PlaceTop(tabButton.GetComponent<RectTransform>(), -128f - index * 48f, StandardControlHeight, .05f, SettingsTabColumnEnd);
                tabButton.onClick.AddListener(() => SelectSettingsTab(tab));
                settingsTabButtons[tab] = tabButton;
                settingsPages[tab] = CreateSettingsTabPage(panel.transform, tab);
            }
            Button globalResetDefaults = CreateButton(panel.transform, "Reset to Defaults", Panel);
            Stretch(globalResetDefaults.GetComponent<RectTransform>(), new Vector2(.64f, .012f), new Vector2(.945f, .055f), Vector2.zero, Vector2.zero);
            globalResetDefaults.onClick.AddListener(ResetPresentationDefaults);

            Transform display = settingsTabContent["Display"];
            float y = -18f;
            AddSettingsHeading(display, "DISPLAY", ref y);
            displayModeValue = AddSettingsChoice(display, "Display mode", ref y, CycleDisplayMode);
            monitorValue = AddSettingsChoice(display, "Monitor", ref y, CycleMonitor);
            resolutionValue = AddSettingsChoice(display, "Resolution", ref y, CycleResolution);
            orientationValue = AddSettingsChoice(display, "Layout / orientation", ref y, CycleOrientation);
            uiScaleValue = AddSettingsValue(display, "UI scale", ref y);
            uiScaleSlider = CreateSlider(display, PresentationDisplaySettingsPolicy.MinimumUiScale, PresentationDisplaySettingsPolicy.MaximumUiScale, pendingDisplaySettings.uiScale);
            PlaceTop(uiScaleSlider.GetComponent<RectTransform>(), y, 30f); uiScaleSlider.onValueChanged.AddListener(SetPendingUiScale); y -= 46f;
            vSyncValue = AddSettingsChoice(display, "VSync", ref y, ToggleVSync);
            frameLimitValue = AddSettingsChoice(display, "Frame limit", ref y, CycleFrameLimit);
            y -= 16f; // Separate the fixed staged-display actions from the scrolling fields above.
            Button applyButton = CreateButton(display, "Apply display settings", Accent);
            PlaceTop(applyButton.GetComponent<RectTransform>(), y, 44f, SettingsOuterMargin, .48f);
            applyButton.onClick.AddListener(BeginApplyDisplaySettings);
            Button cancelDisplayButton = CreateButton(display, "Cancel", Panel);
            PlaceTop(cancelDisplayButton.GetComponent<RectTransform>(), y, 44f, .52f, 1f - SettingsOuterMargin);
            cancelDisplayButton.onClick.AddListener(CancelPendingDisplaySettings);

            Transform models = settingsTabContent["Models"]; y = -18f;
            AddSettingsHeading(models, "GEMINI", ref y);
            geminiProviderStatus = AddSettingsValue(models, "Provider", ref y);
            geminiModelValue = AddSettingsValue(models, "Current model", ref y);
            TMP_Text keyLabel = CreateText(models, "Gemini API key", 18f, Ink, TextAlignmentOptions.MidlineLeft); PlaceTop(keyLabel.rectTransform, y, 34f, SettingsOuterMargin, SettingsLabelColumnEnd);
            geminiApiKeyInput = CreateInputField(models); geminiApiKeyInput.contentType = TMP_InputField.ContentType.Password; geminiApiKeyInput.characterLimit = 512; PlaceTop(geminiApiKeyInput.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, .77f);
            if (geminiApiKeyInput.placeholder is TMP_Text keyPlaceholder)
            {
                keyPlaceholder.text = "Enter Gemini API key";
                keyPlaceholder.enableWordWrapping = false;
                keyPlaceholder.enableAutoSizing = true;
                keyPlaceholder.fontSizeMin = 13f;
                keyPlaceholder.fontSizeMax = 17f;
            }
            if (geminiApiKeyInput.textComponent != null)
            {
                geminiApiKeyInput.textComponent.enableWordWrapping = false;
                geminiApiKeyInput.textComponent.enableAutoSizing = true;
                geminiApiKeyInput.textComponent.fontSizeMin = 13f;
                geminiApiKeyInput.textComponent.fontSizeMax = 17f;
            }
            Button showKeyButton = CreateButton(models, "Show", Panel); PlaceTop(showKeyButton.GetComponent<RectTransform>(), y, 38f, .78f, .87f); showKeyButton.onClick.AddListener(() => ToggleGeminiKeyVisibility(showKeyButton));
            Button saveKeyButton = CreateButton(models, "Save", Accent); PlaceTop(saveKeyButton.GetComponent<RectTransform>(), y, 38f, .88f, .96f); saveKeyButton.onClick.AddListener(SaveGeminiApiKey); y -= 52f;
            Button clearKeyButton = CreateButton(models, "Clear saved API key", Panel); PlaceTop(clearKeyButton.GetComponent<RectTransform>(), y, StandardControlHeight); clearKeyButton.onClick.AddListener(ClearGeminiApiKey);
            y -= 62f;
            AddSettingsHeading(models, "TEXT TO SPEECH", ref y);
            ttsProviderValue = AddSettingsValue(models, "Provider", ref y);
            ttsVoiceValue = AddSettingsValue(models, "Voice", ref y);
            ttsDeviceValue = AddSettingsValue(models, "Device", ref y);

            Transform audio = settingsTabContent["Audio"]; y = -18f;
            AddSettingsHeading(audio, "SPEECH", ref y); volumeLabel = AddSettingsValue(audio, "TTS volume", ref y); volumeSlider = CreateSlider(audio, 0f, 1f, 1f); PlaceTop(volumeSlider.GetComponent<RectTransform>(), y, 30f); volumeSlider.onValueChanged.AddListener(SetVolume); AddPointerUpHandler(volumeSlider.gameObject, FlushTtsVolume); y -= 46f;
            Button stopSpeechButton = CreateButton(audio, "Stop speaking", Panel); PlaceTop(stopSpeechButton.GetComponent<RectTransform>(), y, StandardControlHeight); stopSpeechButton.onClick.AddListener(StopSpeech); y -= 58f;
            AddSettingsHeading(audio, "PRESENTATION AUDIO", ref y); sfxMuteToggle = CreateToggle(audio, "Mute UI SFX", presentationAudio == null || presentationAudio.SfxMuted); PlaceTop(sfxMuteToggle.GetComponent<RectTransform>(), y, 34f); sfxMuteToggle.onValueChanged.AddListener(value => presentationAudio?.SetSfxMuted(value)); y -= 42f;
            sfxVolumeSlider = CreateSlider(audio, 0f, 1f, presentationAudio == null ? .45f : presentationAudio.SfxVolume); PlaceTop(sfxVolumeSlider.GetComponent<RectTransform>(), y, 30f); sfxVolumeSlider.onValueChanged.AddListener(value => presentationAudio?.SetSfxVolume(value)); y -= 46f;
            bgmMuteToggle = CreateToggle(audio, "Mute background music", presentationAudio == null || presentationAudio.BgmMuted); PlaceTop(bgmMuteToggle.GetComponent<RectTransform>(), y, 34f); bgmMuteToggle.onValueChanged.AddListener(value => presentationAudio?.SetBgmMuted(value)); y -= 42f;
            bgmVolumeSlider = CreateSlider(audio, 0f, .35f, presentationAudio == null ? .14f : presentationAudio.BgmVolume); PlaceTop(bgmVolumeSlider.GetComponent<RectTransform>(), y, 30f); bgmVolumeSlider.onValueChanged.AddListener(value => presentationAudio?.SetBgmVolume(value));

            Transform dialogue = settingsTabContent["Dialogue"]; y = -18f;
            AddSettingsHeading(dialogue, "DIALOGUE", ref y); revealSpeedLabel = AddSettingsValue(dialogue, "Reveal speed", ref y); revealSlider = CreateSlider(dialogue, 2f, 16f, revealWordsPerSecond); PlaceTop(revealSlider.GetComponent<RectTransform>(), y, 30f); revealSlider.onValueChanged.AddListener(SetRevealSpeed); y -= 46f;
            instantTextToggle = CreateToggle(dialogue, "Instant assistant text", instantText); PlaceTop(instantTextToggle.GetComponent<RectTransform>(), y, 34f); instantTextToggle.onValueChanged.AddListener(SetInstantText);

            Transform controls = settingsTabContent["Controls"]; y = -18f;
            AddSettingsHeading(controls, "PUSH-TO-TALK", ref y); pttBindValue = AddSettingsValue(controls, "Push-to-Talk", ref y); Button rebindButton = CreateButton(controls, "Rebind", Panel); PlaceTop(rebindButton.GetComponent<RectTransform>(), y, StandardControlHeight); rebindButton.onClick.AddListener(BeginPushToTalkRebind); y -= 46f;
            pttRebindHint = CreateText(controls, string.Empty, 15f, new Color(0.72f, 0.72f, 0.82f, 1f), TextAlignmentOptions.MidlineLeft); PlaceTop(pttRebindHint.rectTransform, y, 32f); y -= 42f; transcriptionModeValue = AddSettingsChoice(controls, "Transcription", ref y, () => SetPttAutoSend(!pttAutoSend));
            globalPttStatus = AddSettingsValue(controls, "Global PTT", ref y);
            globalPttStatus.text = "Global PTT: Starting";

            Transform appearance = settingsTabContent["Appearance"]; y = -18f;
            AddSettingsHeading(appearance, "APPEARANCE", ref y); Button themeButton = CreateButton(appearance, "Light / Dark", Panel); PlaceTop(themeButton.GetComponent<RectTransform>(), y, StandardControlHeight); themeButton.onClick.AddListener(ToggleTheme); y -= 58f;
            hiddenDialogueToggle = CreateToggle(appearance, "Show dialogue text when UI is hidden", showDialogueWhenHidden);
            PlaceTop(hiddenDialogueToggle.GetComponent<RectTransform>(), y, 34f);
            hiddenDialogueToggle.onValueChanged.AddListener(SetShowDialogueWhenHidden);
            y -= 46f;
            AddSettingsHeading(appearance, "AVATAR PRESENTATION", ref y);
            Button adjustAvatarFraming = CreateButton(appearance, "Adjust Avatar Framing", Accent);
            PlaceTop(adjustAvatarFraming.GetComponent<RectTransform>(), y, StandardControlHeight);
            adjustAvatarFraming.onClick.AddListener(EnterAvatarFramingMode);

            Transform advanced = settingsTabContent["Advanced"]; y = -18f;
            AddSettingsHeading(advanced, "GRAPHICS", ref y);
            graphicsQualityValue = AddSettingsChoice(advanced, "Quality preset", ref y, CycleGraphicsQuality);
            avatarRenderScaleValue = AddSettingsChoice(advanced, "Avatar render scale", ref y, CycleAvatarRenderScale);
            antiAliasingValue = AddSettingsChoice(advanced, "Anti-aliasing", ref y, CycleAntiAliasing);
            SetPttAutoSend(pttAutoSend);
            SetRevealSpeed(revealWordsPerSecond);
            RefreshDisplaySettingsUi();
            SelectSettingsTab(activeSettingsTab);
            return panel;
        }

        private GameObject CreateAvatarFramingMode(Transform root)
        {
            avatarCompositionGrid = new GameObject("Avatar Composition Grid", typeof(RectTransform));
            avatarCompositionGrid.transform.SetParent(root, false);
            RectTransform gridRect = avatarCompositionGrid.GetComponent<RectTransform>();
            Stretch(gridRect, new Vector2(.04f, .05f), new Vector2(.96f, .95f), Vector2.zero, Vector2.zero);
            for (int index = 1; index <= 2; index++)
            {
                Image vertical = CreateImage(avatarCompositionGrid.transform, "Vertical Grid " + index, new Color(1f, 1f, 1f, .16f));
                vertical.raycastTarget = false;
                Stretch(vertical.rectTransform, new Vector2(index / 3f, 0f), new Vector2(index / 3f, 1f), new Vector2(-.5f, 0f), new Vector2(.5f, 0f));
                Image horizontal = CreateImage(avatarCompositionGrid.transform, "Horizontal Grid " + index, new Color(1f, 1f, 1f, .16f));
                horizontal.raycastTarget = false;
                Stretch(horizontal.rectTransform, new Vector2(0f, index / 3f), new Vector2(1f, index / 3f), new Vector2(0f, -.5f), new Vector2(0f, .5f));
            }
            Image centerVertical = CreateImage(avatarCompositionGrid.transform, "Center Vertical", new Color(1f, 1f, 1f, .34f));
            centerVertical.raycastTarget = false;
            Stretch(centerVertical.rectTransform, new Vector2(.5f, .43f), new Vector2(.5f, .57f), new Vector2(-.8f, 0f), new Vector2(.8f, 0f));
            Image centerHorizontal = CreateImage(avatarCompositionGrid.transform, "Center Horizontal", new Color(1f, 1f, 1f, .34f));
            centerHorizontal.raycastTarget = false;
            Stretch(centerHorizontal.rectTransform, new Vector2(.43f, .5f), new Vector2(.57f, .5f), new Vector2(0f, -.8f), new Vector2(0f, .8f));
            avatarCompositionGrid.SetActive(false);

            GameObject panel = CreatePanel(root, "Avatar Framing Controls", new Color(.07f, .06f, .13f, .94f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(.16f, .025f), new Vector2(.84f, .18f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "Adjust Avatar Framing", 20f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.035f, .67f), new Vector2(.60f, .96f), Vector2.zero, Vector2.zero);
            TMP_Text hint = CreateText(panel.transform, "Drag the avatar to position it. Use the mouse wheel to change size.", 14f, theme.secondaryText, TextAlignmentOptions.MidlineLeft);
            Stretch(hint.rectTransform, new Vector2(.035f, .42f), new Vector2(.64f, .68f), Vector2.zero, Vector2.zero);

            Button save = CreateButton(panel.transform, "Save", Accent);
            Stretch(save.GetComponent<RectTransform>(), new Vector2(.69f, .58f), new Vector2(.79f, .91f), Vector2.zero, Vector2.zero);
            save.onClick.AddListener(SaveAvatarFramingSession);
            Button cancel = CreateButton(panel.transform, "Cancel", Panel);
            Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.80f, .58f), new Vector2(.90f, .91f), Vector2.zero, Vector2.zero);
            cancel.onClick.AddListener(CancelAvatarFramingSession);
            Button reset = CreateButton(panel.transform, "Reset", Panel);
            Stretch(reset.GetComponent<RectTransform>(), new Vector2(.91f, .58f), new Vector2(.98f, .91f), Vector2.zero, Vector2.zero);
            reset.onClick.AddListener(ResetAvatarFramingSession);

            avatarSizeValue = CreateFramingControl(panel.transform, "Avatar Size", new Vector2(.035f, .08f), new Vector2(.32f, .38f), AvatarPresentationFramingField.Zoom, out avatarSizeSlider);
            avatarHorizontalValue = CreateFramingControl(panel.transform, "Horizontal", new Vector2(.35f, .08f), new Vector2(.63f, .38f), AvatarPresentationFramingField.HorizontalPan, out avatarHorizontalSlider);
            avatarVerticalValue = CreateFramingControl(panel.transform, "Vertical", new Vector2(.66f, .08f), new Vector2(.98f, .38f), AvatarPresentationFramingField.VerticalPan, out avatarVerticalSlider);
            return panel;
        }

        private TMP_Text CreateFramingControl(Transform parent, string label, Vector2 anchorMin, Vector2 anchorMax,
            AvatarPresentationFramingField field, out Slider slider)
        {
            GameObject row = new GameObject(label + " Framing Control", typeof(RectTransform));
            row.transform.SetParent(parent, false);
            Stretch(row.GetComponent<RectTransform>(), anchorMin, anchorMax, Vector2.zero, Vector2.zero);
            TMP_Text name = CreateText(row.transform, label, 14f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(name.rectTransform, new Vector2(0f, .55f), new Vector2(.60f, 1f), Vector2.zero, Vector2.zero);
            TMP_Text value = CreateText(row.transform, "0.00", 14f, theme.secondaryText, TextAlignmentOptions.MidlineRight);
            Stretch(value.rectTransform, new Vector2(.60f, .55f), Vector2.one, Vector2.zero, Vector2.zero);
            AvatarConfiguration configuration = AvatarConfiguration.Load();
            float minimum = field == AvatarPresentationFramingField.Zoom
                ? AvatarUiFraming.MinimumZoom(configuration.portraitUiCrop)
                : -1f;
            float maximum = field == AvatarPresentationFramingField.Zoom ? AvatarUiFraming.MaximumZoom : 1f;
            slider = CreateSlider(row.transform, minimum, maximum, minimum);
            Stretch(slider.GetComponent<RectTransform>(), new Vector2(0f, 0f), new Vector2(1f, .52f), Vector2.zero, Vector2.zero);
            slider.onValueChanged.AddListener(valueChanged => SetAvatarFramingValue(field, valueChanged));
            return value;
        }

        private void EnterAvatarFramingMode()
        {
            if (avatarFramingModeActive || avatarFraming == null || currentDisplaySettings == null) return;
            avatarFramingSessionPortrait = PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode, Screen.width, Screen.height);
            avatarFramingSessionSnapshot = avatarFraming.GetValues(avatarFramingSessionPortrait);
            avatarFramingModeActive = true;
            AvatarConfiguration configuration = AvatarConfiguration.Load();
            AvatarCrop activeCrop = avatarFramingSessionPortrait ? configuration.portraitUiCrop : configuration.landscapeUiCrop;
            suppressAvatarFramingCallbacks = true;
            if (avatarSizeSlider != null) avatarSizeSlider.minValue = AvatarUiFraming.MinimumZoom(activeCrop);
            suppressAvatarFramingCallbacks = false;
            CloseSettingsPanel();
            if (topBar != null) topBar.SetActive(false);
            if (dialogueCard != null) dialogueCard.SetActive(false);
            if (inputCard != null) inputCard.SetActive(false);
            if (avatarCompositionGrid != null) avatarCompositionGrid.SetActive(true);
            if (avatarFramingModePanel != null)
            {
                bool portrait = avatarFramingSessionPortrait;
                Stretch(avatarFramingModePanel.GetComponent<RectTransform>(),
                    portrait ? new Vector2(.035f, .025f) : new Vector2(.16f, .025f),
                    portrait ? new Vector2(.965f, .22f) : new Vector2(.84f, .18f),
                    Vector2.zero, Vector2.zero);
                avatarFramingModePanel.SetActive(true);
                avatarFramingModePanel.transform.SetAsLastSibling();
            }
            SyncAvatarFramingControls(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
        }

        private void SaveAvatarFramingSession()
        {
            if (!avatarFramingModeActive) return;
            avatarFraming.Commit(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
            ExitAvatarFramingMode();
        }

        private void CancelAvatarFramingSession()
        {
            if (!avatarFramingModeActive) return;
            avatarFraming.SetValues(avatarFramingSessionPortrait, avatarFramingSessionSnapshot, false);
            SyncAvatarFramingControls(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
            ExitAvatarFramingMode();
        }

        private void ResetAvatarFramingSession()
        {
            if (!avatarFramingModeActive) return;
            avatarFraming.Reset(avatarFramingSessionPortrait, false);
            SyncAvatarFramingControls(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
        }

        private void ExitAvatarFramingMode()
        {
            avatarFramingModeActive = false;
            if (avatarCompositionGrid != null) avatarCompositionGrid.SetActive(false);
            if (avatarFramingModePanel != null) avatarFramingModePanel.SetActive(false);
            RefreshPresentationVisibility();
        }

        private void SetAvatarFramingValue(AvatarPresentationFramingField field, float value)
        {
            if (suppressAvatarFramingCallbacks || !avatarFramingModeActive || avatarFraming == null) return;
            avatarFraming.SetValue(avatarFramingSessionPortrait, field, value, false);
            SyncAvatarFramingControls(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
        }

        private void HandleAvatarFramingDrag(Vector2 pointerDelta)
        {
            if (!avatarFramingModeActive || avatarFrameRect == null) return;
            AvatarPresentationFramingValues values = avatarFraming.GetValues(avatarFramingSessionPortrait);
            AvatarConfiguration configuration = AvatarConfiguration.Load();
            AvatarCrop crop = avatarFramingSessionPortrait ? configuration.portraitUiCrop : configuration.landscapeUiCrop;
            // Move the sampled crop opposite to pointer motion so the visible
            // avatar follows the cursor like a positioned subject. Screen size
            // is stable during drag; never normalize against a fitted RawImage
            // whose geometry is allowed to change with presentation crop.
            if (AvatarUiFraming.HasPanRange(crop, values.zoom, true))
            {
                values.panX -= pointerDelta.x / Mathf.Max(1f, Screen.width) * 1.5f;
            }
            if (AvatarUiFraming.HasPanRange(crop, values.zoom, false))
            {
                values.panY -= pointerDelta.y / Mathf.Max(1f, Screen.height) * 1.5f;
            }
            avatarFraming.SetValues(avatarFramingSessionPortrait, values, false);
            SyncAvatarFramingControls(avatarFramingSessionPortrait);
            ApplyCanonicalAvatarPresentation(avatarFramingSessionPortrait, false);
        }

        private void HandleAvatarFramingScroll(float scrollDelta)
        {
            if (!avatarFramingModeActive || avatarFraming == null) return;
            float zoom = avatarFraming.GetValue(avatarFramingSessionPortrait, AvatarPresentationFramingField.Zoom) + scrollDelta * .08f;
            SetAvatarFramingValue(AvatarPresentationFramingField.Zoom, zoom);
        }

        private GameObject CreateSettingsTabPage(Transform parent, string name)
        {
            GameObject viewport = CreatePanel(parent, name + " Settings Page", theme.surfaceMuted);
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(SettingsContentColumnStart, .06f), new Vector2(.95f, .84f), Vector2.zero, Vector2.zero);
            viewport.AddComponent<RectMask2D>();
            GameObject content = new GameObject(name + " Settings Content", typeof(RectTransform)); content.transform.SetParent(viewport.transform, false);
            RectTransform contentRect = content.GetComponent<RectTransform>(); contentRect.anchorMin = new Vector2(0f, 1f); contentRect.anchorMax = new Vector2(1f, 1f); contentRect.pivot = new Vector2(.5f, 1f); contentRect.sizeDelta = new Vector2(0f, 900f);
            ScrollRect scroll = viewport.AddComponent<ScrollRect>(); scroll.viewport = viewport.GetComponent<RectTransform>(); scroll.content = contentRect; scroll.horizontal = false; scroll.vertical = true; scroll.movementType = ScrollRect.MovementType.Clamped; scroll.scrollSensitivity = 32f;
            settingsTabContent[name] = content.transform;
            return viewport;
        }

        private void ApplyPersistedPresentationState()
        {
            // All persistent values have been loaded and normalized before
            // this runs. Apply the live layout before any user interaction,
            // then refresh visual controls without notifying their listeners.
            if (currentDisplaySettings != null)
            {
                currentDisplaySettings = PresentationDisplaySettingsPolicy.NormalizeForScreen(
                    currentDisplaySettings, Screen.width, Screen.height);
                pendingDisplaySettings = currentDisplaySettings.Clone();
            }
            UpdateCompositionLayout();
            UpdateDialogueLayout(false);
            RefreshDisplaySettingsUi();
        }

        private void SelectSettingsTab(string tab)
        {
            if (!settingsPages.ContainsKey(tab)) return;
            activeSettingsTab = tab;
            foreach (KeyValuePair<string, GameObject> page in settingsPages) page.Value.SetActive(page.Key == tab);
            foreach (KeyValuePair<string, Button> item in settingsTabButtons)
            {
                Image image = item.Value.GetComponent<Image>();
                if (image != null) image.color = item.Key == tab ? theme.accent : theme.control;
                TMP_Text label = item.Value.GetComponentInChildren<TMP_Text>();
                if (label != null) label.color = item.Key == tab ? Color.white : theme.text;
            }
        }

        private void RefreshGeminiModelUi(GeminiModelSnapshot gemini)
        {
            if (geminiProviderStatus == null || geminiModelValue == null) return;
            if (gemini == null)
            {
                geminiProviderStatus.text = "Unavailable";
                geminiModelValue.text = "Unknown";
                return;
            }
            string source = string.IsNullOrWhiteSpace(gemini.source) ? "unknown source" : gemini.source.Replace("_", " ");
            geminiProviderStatus.text = gemini.configured ? "Configured · " + source : "Missing API key";
            geminiModelValue.text = string.IsNullOrWhiteSpace(gemini.model) ? "Unknown" : gemini.model;
        }

        private void RefreshTtsModelUi(TtsSnapshot tts)
        {
            if (ttsProviderValue == null || tts == null) return;
            ttsProviderValue.text = string.IsNullOrWhiteSpace(tts.provider) ? "Unavailable" : tts.provider;
            ttsVoiceValue.text = string.IsNullOrWhiteSpace(tts.voice) ? "Default" : tts.voice;
            ttsDeviceValue.text = string.IsNullOrWhiteSpace(tts.device) ? "Automatic" : tts.device;
        }

        private void ToggleGeminiKeyVisibility(Button button)
        {
            showGeminiApiKey = !showGeminiApiKey;
            geminiApiKeyInput.contentType = showGeminiApiKey
                ? TMP_InputField.ContentType.Standard
                : TMP_InputField.ContentType.Password;
            geminiApiKeyInput.ForceLabelUpdate();
            SetTopControlLabel(button, showGeminiApiKey ? "Hide" : "Show");
        }

        private void SaveGeminiApiKey()
        {
            if (client == null || geminiApiKeyInput == null) return;
            _ = client.SetGeminiApiKeyAsync(geminiApiKeyInput.text);
            geminiApiKeyInput.text = string.Empty;
            ApplyStatus("ready", "Saving Gemini configuration...");
        }

        private void ClearGeminiApiKey()
        {
            if (client == null) return;
            _ = client.SetGeminiApiKeyAsync(string.Empty);
            ApplyStatus("ready", "Clearing saved Gemini configuration...");
        }

        private void AddSettingsHeading(Transform parent, string heading, ref float y)
        {
            TMP_Text text = CreateText(parent, heading, 16f, Accent, TextAlignmentOptions.MidlineLeft);
            PlaceTop(text.rectTransform, y, 30f);
            y -= 38f;
        }

        private TMP_Text AddSettingsValue(Transform parent, string label, ref float y)
        {
            TMP_Text labelText = CreateText(parent, label, 18f, Ink, TextAlignmentOptions.MidlineLeft);
            PlaceTop(labelText.rectTransform, y, 34f, SettingsOuterMargin, .54f);
            TMP_Text value = CreateText(parent, string.Empty, 17f, Ink, TextAlignmentOptions.MidlineRight);
            PlaceTop(value.rectTransform, y, 34f, .56f, 1f - SettingsOuterMargin);
            y -= 40f;
            return value;
        }

        private void SyncAvatarFramingControls(bool portrait)
        {
            if (avatarFraming == null || !avatarFramingModeActive || portrait != avatarFramingSessionPortrait) return;
            AvatarPresentationFramingValues values = avatarFraming.GetValues(portrait);
            suppressAvatarFramingCallbacks = true;
            try
            {
                if (avatarSizeSlider != null) avatarSizeSlider.SetValueWithoutNotify(values.zoom);
                if (avatarHorizontalSlider != null) avatarHorizontalSlider.SetValueWithoutNotify(values.panX);
                if (avatarVerticalSlider != null) avatarVerticalSlider.SetValueWithoutNotify(values.panY);
                if (avatarSizeValue != null) avatarSizeValue.text = values.zoom.ToString("0.00");
                if (avatarHorizontalValue != null) avatarHorizontalValue.text = values.panX.ToString("0.00");
                if (avatarVerticalValue != null) avatarVerticalValue.text = values.panY.ToString("0.00");
            }
            finally
            {
                suppressAvatarFramingCallbacks = false;
            }
        }

        private TMP_Text AddSettingsChoice(Transform parent, string label, ref float y, Action onClick)
        {
            TMP_Text labelText = CreateText(parent, label, 18f, Ink, TextAlignmentOptions.MidlineLeft);
            PlaceTop(labelText.rectTransform, y, 38f, SettingsOuterMargin, SettingsLabelColumnEnd);
            Button button = CreateButton(parent, string.Empty, new Color(0.20f, 0.18f, 0.30f, 1f));
            PlaceTop(button.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, 1f - SettingsOuterMargin);
            TMP_Text value = button.GetComponentInChildren<TMP_Text>();
            button.onClick.AddListener(() => onClick());
            y -= 46f;
            return value;
        }

        private static void PlaceTop(RectTransform transform, float y, float height, float minX = 0.04f, float maxX = 0.96f)
        {
            transform.anchorMin = new Vector2(minX, 1f);
            transform.anchorMax = new Vector2(maxX, 1f);
            transform.pivot = new Vector2(0.5f, 1f);
            transform.anchoredPosition = new Vector2(0f, y);
            transform.sizeDelta = new Vector2(0f, height);
        }

        private PresentationDisplaySettings LoadDisplaySettings()
        {
            PresentationDisplaySettings defaults = CaptureRuntimeDisplaySettings();
            if (!PlayerPrefs.HasKey(DisplaySettingsPreference))
            {
                return defaults;
            }

            try
            {
                PresentationDisplaySettings saved = JsonUtility.FromJson<PresentationDisplaySettings>(PlayerPrefs.GetString(DisplaySettingsPreference));
                if (saved == null)
                {
                    return defaults;
                }
                return PresentationDisplaySettingsPolicy.Normalize(saved);
            }
            catch (ArgumentException)
            {
                return defaults;
            }
        }

        private PresentationDisplaySettings CaptureRuntimeDisplaySettings()
        {
            int displayIndex = 0;
            try
            {
                List<DisplayInfo> layout = new List<DisplayInfo>();
                Screen.GetDisplayLayout(layout);
                DisplayInfo currentDisplay = Screen.mainWindowDisplayInfo;
                int matchedIndex = layout.FindIndex(display =>
                    display.width == currentDisplay.width &&
                    display.height == currentDisplay.height &&
                    string.Equals(display.name, currentDisplay.name, StringComparison.Ordinal));
                if (matchedIndex >= 0)
                {
                    displayIndex = matchedIndex;
                }
            }
            catch (Exception)
            {
                // Editor/test contexts do not expose the standalone main-window display.
            }

            return PresentationDisplaySettingsPolicy.Normalize(new PresentationDisplaySettings
            {
                displayIndex = displayIndex,
                width = Mathf.Max(640, Screen.width),
                height = Mathf.Max(480, Screen.height),
                displayMode = PresentationDisplaySettingsPolicy.FromUnityMode(Screen.fullScreenMode),
                uiScale = 1f,
                vSync = QualitySettings.vSyncCount > 0,
                frameLimit = Application.targetFrameRate == 120 ? 120 : Application.targetFrameRate < 0 ? -1 : 60,
                antiAliasing = QualitySettings.antiAliasing
            });
        }

        private void RefreshDisplayLayout()
        {
            displayLayout.Clear();
            try
            {
                Screen.GetDisplayLayout(displayLayout);
            }
            catch (UnityException)
            {
                // The API is unavailable in some editor/test contexts. The
                // standalone player supplies the real layout at runtime.
            }
            if (displayLayout.Count == 0)
            {
                displayLayout.Add(default(DisplayInfo));
            }
            pendingDisplaySettings.displayIndex = Mathf.Clamp(pendingDisplaySettings.displayIndex, 0, displayLayout.Count - 1);
            resolutionOptions = PresentationDisplaySettingsPolicy.DistinctResolutions(Screen.resolutions, Screen.width, Screen.height);
        }

        private void RefreshDisplaySettingsUi()
        {
            if (pendingDisplaySettings == null)
            {
                return;
            }

            RefreshDisplayLayout();
            displayModeValue.text = DisplayModeLabel(pendingDisplaySettings.displayMode);
            DisplayInfo display = displayLayout[pendingDisplaySettings.displayIndex];
            string displayContext = display.width > 0
                ? $"Display {pendingDisplaySettings.displayIndex + 1} ({display.width} x {display.height})"
                : $"Display {pendingDisplaySettings.displayIndex + 1}";
            monitorValue.text = displayContext;
            resolutionValue.text = $"{pendingDisplaySettings.width} x {pendingDisplaySettings.height}";
            orientationValue.text = pendingDisplaySettings.layoutMode.ToString();
            uiScaleValue.text = $"{pendingDisplaySettings.uiScale:0.00}x";
            vSyncValue.text = pendingDisplaySettings.vSync ? "On" : "Off";
            frameLimitValue.text = pendingDisplaySettings.frameLimit < 0 ? "Unlimited" : pendingDisplaySettings.frameLimit.ToString();
            antiAliasingValue.text = pendingDisplaySettings.antiAliasing == 0 ? "Off" : pendingDisplaySettings.antiAliasing + "x MSAA";
            uiScaleSlider.SetValueWithoutNotify(pendingDisplaySettings.uiScale);
            if (graphicsQualityValue != null) graphicsQualityValue.text = graphicsQuality.ToString();
            if (avatarRenderScaleValue != null) avatarRenderScaleValue.text = avatarRenderScale.ToString("0.0") + "x";
            if (pttBindValue != null)
            {
                pttBindValue.text = pushToTalkKey.ToString();
            }
        }

        private static string DisplayModeLabel(PresentationDisplayMode mode)
        {
            return mode == PresentationDisplayMode.Windowed ? "Windowed" :
                mode == PresentationDisplayMode.Fullscreen ? "Fullscreen" : "Borderless Fullscreen";
        }

        private void CycleDisplayMode()
        {
            pendingDisplaySettings.displayMode = (PresentationDisplayMode)(((int)pendingDisplaySettings.displayMode + 1) % 3);
            RefreshDisplaySettingsUi();
        }

        private void CycleMonitor()
        {
            RefreshDisplayLayout();
            pendingDisplaySettings.displayIndex = (pendingDisplaySettings.displayIndex + 1) % displayLayout.Count;
            RefreshDisplaySettingsUi();
        }

        private void CycleResolution()
        {
            RefreshDisplayLayout();
            int current = resolutionOptions.FindIndex(resolution => resolution.x == pendingDisplaySettings.width && resolution.y == pendingDisplaySettings.height);
            Vector2Int next = resolutionOptions[(Mathf.Max(-1, current) + 1) % resolutionOptions.Count];
            pendingDisplaySettings.width = next.x;
            pendingDisplaySettings.height = next.y;
            RefreshDisplaySettingsUi();
        }

        private void CycleOrientation()
        {
            pendingDisplaySettings.layoutMode = (PresentationLayoutMode)(((int)pendingDisplaySettings.layoutMode + 1) % 3);
            RefreshDisplaySettingsUi();
        }

        private void SetPendingUiScale(float value)
        {
            pendingDisplaySettings.uiScale = value;
            RefreshDisplaySettingsUi();
        }

        private void ToggleVSync()
        {
            pendingDisplaySettings.vSync = !pendingDisplaySettings.vSync;
            RefreshDisplaySettingsUi();
        }

        private void CycleFrameLimit()
        {
            int index = Array.IndexOf(PresentationDisplaySettingsPolicy.FrameLimits, pendingDisplaySettings.frameLimit);
            pendingDisplaySettings.frameLimit = PresentationDisplaySettingsPolicy.FrameLimits[(Mathf.Max(-1, index) + 1) % PresentationDisplaySettingsPolicy.FrameLimits.Length];
            RefreshDisplaySettingsUi();
        }

        private void CycleAntiAliasing()
        {
            int index = Array.IndexOf(PresentationDisplaySettingsPolicy.AntiAliasingOptions, pendingDisplaySettings.antiAliasing);
            pendingDisplaySettings.antiAliasing = PresentationDisplaySettingsPolicy.AntiAliasingOptions[(Mathf.Max(-1, index) + 1) % PresentationDisplaySettingsPolicy.AntiAliasingOptions.Length];
            // AA is not a staged window-mode change. Apply it to both the
            // global quality state and the avatar RenderTexture immediately.
            currentDisplaySettings.antiAliasing = pendingDisplaySettings.antiAliasing;
            QualitySettings.antiAliasing = pendingDisplaySettings.antiAliasing;
            avatarLoader?.SetAntiAliasing(pendingDisplaySettings.antiAliasing);
            SaveDisplaySettings(currentDisplaySettings);
            RefreshDisplaySettingsUi();
        }

        private static float DefaultAvatarRenderScale(PresentationGraphicsQuality quality)
        {
            switch (quality)
            {
                case PresentationGraphicsQuality.Low: return 1f;
                case PresentationGraphicsQuality.Medium: return 1f;
                case PresentationGraphicsQuality.Ultra: return 2f;
                default: return 1.5f;
            }
        }

        private static int DefaultAntiAliasing(PresentationGraphicsQuality quality)
        {
            switch (quality)
            {
                case PresentationGraphicsQuality.Low: return 0;
                case PresentationGraphicsQuality.Medium: return 2;
                case PresentationGraphicsQuality.Ultra: return 8;
                default: return 4;
            }
        }

        private void ApplyPresentationGraphics()
        {
            avatarLoader?.SetPresentationRenderScale(avatarRenderScale);
        }

        private void CycleGraphicsQuality()
        {
            graphicsQuality = (PresentationGraphicsQuality)(((int)graphicsQuality + 1) % 4);
            avatarRenderScale = DefaultAvatarRenderScale(graphicsQuality);
            pendingDisplaySettings.antiAliasing = DefaultAntiAliasing(graphicsQuality);
            currentDisplaySettings.antiAliasing = pendingDisplaySettings.antiAliasing;
            QualitySettings.antiAliasing = pendingDisplaySettings.antiAliasing;
            avatarLoader?.SetAntiAliasing(pendingDisplaySettings.antiAliasing);
            ApplyPresentationGraphics();
            PlayerPrefs.SetInt(GraphicsQualityPreference, (int)graphicsQuality);
            PlayerPrefs.SetFloat(AvatarRenderScalePreference, avatarRenderScale);
            PlayerPrefs.Save();
            SaveDisplaySettings(currentDisplaySettings);
            RefreshDisplaySettingsUi();
        }

        private void CycleAvatarRenderScale()
        {
            avatarRenderScale = avatarRenderScale < 1.25f ? 1.5f : avatarRenderScale < 1.75f ? 2f : 1f;
            ApplyPresentationGraphics();
            PlayerPrefs.SetFloat(AvatarRenderScalePreference, avatarRenderScale);
            PlayerPrefs.Save();
            RefreshDisplaySettingsUi();
        }

        private void SetShowDialogueWhenHidden(bool value)
        {
            showDialogueWhenHidden = value;
            PlayerPrefs.SetInt(ShowDialogueWhenHiddenPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            SyncHiddenDialogueText();
        }

        private void SyncHiddenDialogueText()
        {
            if (hiddenDialogueText == null || hiddenDialogueViewport == null) return;
            hiddenDialogueText.text = dialogueTextLabel != null ? dialogueTextLabel.text : string.Empty;
            float width = Mathf.Max(1f, hiddenDialogueViewport.rect.width - 24f);
            float preferredHeight = hiddenDialogueText.GetPreferredValues(hiddenDialogueText.text, width, 0f).y + 20f;
            float viewportHeight = Mathf.Max(1f, hiddenDialogueViewport.rect.height);
            hiddenDialogueText.rectTransform.sizeDelta = new Vector2(-24f, Mathf.Max(viewportHeight, preferredHeight));
            bool overflow = preferredHeight > viewportHeight + 1f;
            if (hiddenDialogueScrollbar != null) hiddenDialogueScrollbar.gameObject.SetActive(overflow && wordReveal.IsComplete);
            // Transition ownership is exclusive: the hidden copy appears only
            // after the normal UI has completely left, and disappears before
            // it returns. This prevents two dialogue copies during a reveal.
            bool show = visibilityTransition == null && interfaceHidden && showDialogueWhenHidden &&
                !string.IsNullOrWhiteSpace(hiddenDialogueText.text);
            hiddenDialogueViewport.gameObject.SetActive(show);
        }

        private void ResetPresentationDefaults()
        {
            // Global safe-settings reset. It deliberately excludes data,
            // secrets, assets, and avatar framing; none of those are settings.
            graphicsQuality = PresentationGraphicsQuality.High;
            avatarRenderScale = DefaultAvatarRenderScale(graphicsQuality);
            showDialogueWhenHidden = false;
            theme = PresentationThemes.Dark;
            PresentationThemes.Save(theme.mode);
            revealWordsPerSecond = presentation.defaultRevealWordsPerSecond;
            instantText = false;
            pttAutoSend = false;
            pushToTalkKey = PresentationPttBinding.DefaultKey;
            PlayerPrefs.DeleteKey(RevealSpeedPreference);
            PlayerPrefs.DeleteKey(InstantTextPreference);
            PlayerPrefs.DeleteKey(PushToTalkBindingPreference);
            PlayerPrefs.DeleteKey(PttAutoSendPreference);
            currentDisplaySettings.uiScale = 1f;
            currentDisplaySettings.antiAliasing = DefaultAntiAliasing(graphicsQuality);
            // Display defaults are staged only. Do not move the active window
            // or change its monitor/resolution until the user presses Apply.
            PresentationDisplaySettings stagedDisplayDefaults = CaptureRuntimeDisplaySettings();
            stagedDisplayDefaults.uiScale = 1f;
            stagedDisplayDefaults.antiAliasing = DefaultAntiAliasing(graphicsQuality);
            QualitySettings.antiAliasing = currentDisplaySettings.antiAliasing;
            avatarLoader?.SetAntiAliasing(currentDisplaySettings.antiAliasing);
            ApplyPresentationGraphics();
            PlayerPrefs.SetInt(GraphicsQualityPreference, (int)graphicsQuality);
            PlayerPrefs.SetFloat(AvatarRenderScalePreference, avatarRenderScale);
            PlayerPrefs.SetInt(ShowDialogueWhenHiddenPreference, 0);
            PlayerPrefs.Save();
            presentationAudio?.ResetToDefaults();
            ResetTtsVolumeToDefault();
            SetRevealSpeed(revealWordsPerSecond);
            SetPttAutoSend(false);
            _ = client?.SetPushToTalkBindingAsync(PresentationPttBinding.Save(pushToTalkKey));
            ApplyDisplaySettings(currentDisplaySettings, false);
            pendingDisplaySettings = stagedDisplayDefaults;
            ApplyTheme();
            RefreshOrdinarySettingsControls();
            RefreshDisplaySettingsUi();
            SyncHiddenDialogueText();
        }

        private void RefreshOrdinarySettingsControls()
        {
            if (revealSlider != null) revealSlider.SetValueWithoutNotify(revealWordsPerSecond);
            if (instantTextToggle != null) instantTextToggle.SetIsOnWithoutNotify(instantText);
            if (hiddenDialogueToggle != null) hiddenDialogueToggle.SetIsOnWithoutNotify(showDialogueWhenHidden);
            if (sfxMuteToggle != null) sfxMuteToggle.SetIsOnWithoutNotify(presentationAudio != null && presentationAudio.SfxMuted);
            if (sfxVolumeSlider != null) sfxVolumeSlider.SetValueWithoutNotify(presentationAudio != null ? presentationAudio.SfxVolume : .45f);
            if (bgmMuteToggle != null) bgmMuteToggle.SetIsOnWithoutNotify(presentationAudio != null && presentationAudio.BgmMuted);
            if (bgmVolumeSlider != null) bgmVolumeSlider.SetValueWithoutNotify(presentationAudio != null ? presentationAudio.BgmVolume : .14f);
        }

        private void BeginApplyDisplaySettings()
        {
            pendingDisplaySettings = PresentationDisplaySettingsPolicy.Normalize(pendingDisplaySettings);
            revertDisplaySettings = currentDisplaySettings.Clone();
            ApplyDisplaySettings(pendingDisplaySettings, true);
        }

        private void CancelPendingDisplaySettings()
        {
            if (currentDisplaySettings == null) return;
            pendingDisplaySettings = currentDisplaySettings.Clone();
            RefreshDisplaySettingsUi();
        }

        private void ApplyDisplaySettings(PresentationDisplaySettings settings, bool requestConfirmation, bool forceStartupDisplayMove = false)
        {
            PresentationDisplaySettings normalized = PresentationDisplaySettingsPolicy.NormalizeForScreen(settings, Screen.width, Screen.height);
            QualitySettings.vSyncCount = normalized.vSync ? 1 : 0;
            Application.targetFrameRate = normalized.vSync ? -1 : normalized.frameLimit;
            QualitySettings.antiAliasing = normalized.antiAliasing;
            avatarLoader?.SetAntiAliasing(normalized.antiAliasing);
            if (canvasScaler != null)
            {
                // Derive from the immutable baseline every time. Never use
                // the current reference resolution or RectTransform geometry
                // as the next scale input; that is what permits cumulative
                // drift after repeated Settings applies.
                canvasScaler.transform.localScale = Vector3.one;
                canvasScaler.referenceResolution = DefaultReferenceResolution / normalized.uiScale;
            }
            Canvas.ForceUpdateCanvases();

            // A pure UI-scale or quality change must not issue another Windows
            // mode transition. Repeated SetResolution calls were the
            // landscape-only source of modal/top-control drift.
            bool requiresWindowChange = normalized.width != Screen.width ||
                normalized.height != Screen.height ||
                PresentationDisplaySettingsPolicy.ToUnityMode(normalized.displayMode) != Screen.fullScreenMode;
            bool requiresDisplayMove = forceStartupDisplayMove || currentDisplaySettings == null ||
                normalized.displayIndex != currentDisplaySettings.displayIndex;
            if (requiresWindowChange)
            {
                Screen.SetResolution(normalized.width, normalized.height, PresentationDisplaySettingsPolicy.ToUnityMode(normalized.displayMode));
            }
            currentDisplaySettings = normalized.Clone();
            pendingDisplaySettings = normalized.Clone();
            if (requiresWindowChange || requiresDisplayMove)
            {
                if (forceStartupDisplayMove) startupDisplayFinalizationPending = true;
                StartCoroutine(MoveMainWindowAfterResolution(normalized.displayIndex, forceStartupDisplayMove));
            }
            else
            {
                FinalizeDisplayGeometry();
            }
            if (requestConfirmation)
            {
                displayConfirmActive = true;
                displayConfirmDeadline = Time.unscaledTime + 12f;
                displayConfirmPanel.SetActive(true);
                displayConfirmPanel.transform.SetAsLastSibling();
            }
            else
            {
                SaveDisplaySettings(normalized);
            }
            RefreshDisplaySettingsUi();
        }

        private IEnumerator MoveMainWindowAfterResolution(int requestedDisplayIndex, bool startupMove)
        {
            // Screen.SetResolution completes at the end of its current frame.
            // Move only after that transition so a mode/resolution change does
            // not require a second, unexplained user adjustment.
            yield return null;
            RefreshDisplayLayout();
            if (requestedDisplayIndex >= 0 && requestedDisplayIndex < displayLayout.Count &&
                displayLayout[requestedDisplayIndex].width > 0)
            {
                DisplayInfo targetDisplay = displayLayout[requestedDisplayIndex];
                Screen.MoveMainWindowTo(targetDisplay, Vector2Int.zero);
            }
            // Native window movement and Screen geometry settle asynchronously.
            // This is deliberately bounded: it waits only for the transition
            // this Apply call initiated, then uses one final canonical layout.
            yield return null;
            Canvas.ForceUpdateCanvases();
            FinalizeDisplayGeometry();
            if (startupMove) startupDisplayFinalizationPending = false;
        }

        private void FinalizeDisplayGeometry()
        {
            Canvas.ForceUpdateCanvases();
            SynchronizeAppliedDisplaySettings();
            UpdateDialogueLayout(false);
            UpdateCompositionLayout();
            UpdateBackgroundCover();
            Canvas.ForceUpdateCanvases();
            RefreshDisplaySettingsUi();
        }

        private void SynchronizeAppliedDisplaySettings()
        {
            if (currentDisplaySettings == null) return;
            PresentationDisplaySettings runtime = CaptureRuntimeDisplaySettings();
            currentDisplaySettings.displayMode = runtime.displayMode;
            currentDisplaySettings.width = runtime.width;
            currentDisplaySettings.height = runtime.height;
            currentDisplaySettings.vSync = runtime.vSync;
            currentDisplaySettings.frameLimit = runtime.frameLimit;
            currentDisplaySettings.antiAliasing = runtime.antiAliasing;
            pendingDisplaySettings = currentDisplaySettings.Clone();
        }

        private void UpdateDisplayConfirmation()
        {
            if (!displayConfirmActive)
            {
                return;
            }

            float remaining = Mathf.Max(0f, displayConfirmDeadline - Time.unscaledTime);
            displayConfirmLabel.text = $"Keep these display settings?\nReverting in {Mathf.CeilToInt(remaining)} seconds.";
            if (remaining <= 0f)
            {
                RevertDisplaySettings();
            }
        }

        private void KeepDisplaySettings()
        {
            displayConfirmActive = false;
            displayConfirmPanel.SetActive(false);
            SaveDisplaySettings(currentDisplaySettings);
        }

        private void RevertDisplaySettings()
        {
            if (revertDisplaySettings != null)
            {
                ApplyDisplaySettings(revertDisplaySettings, false);
            }
            displayConfirmActive = false;
            displayConfirmPanel.SetActive(false);
        }

        private static void SaveDisplaySettings(PresentationDisplaySettings settings)
        {
            PlayerPrefs.SetString(DisplaySettingsPreference, JsonUtility.ToJson(settings));
            PlayerPrefs.Save();
        }

        private GameObject CreateDisplayConfirmationPanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Display Confirmation", new Color(0.08f, 0.06f, 0.13f, 0.98f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(0.29f, 0.38f), new Vector2(0.71f, 0.62f), Vector2.zero, Vector2.zero);
            displayConfirmLabel = CreateText(panel.transform, string.Empty, 20f, Ink, TextAlignmentOptions.Center);
            Stretch(displayConfirmLabel.rectTransform, new Vector2(0.08f, 0.44f), new Vector2(0.92f, 0.90f), Vector2.zero, Vector2.zero);
            Button keep = CreateButton(panel.transform, "Keep changes", new Color(0.36f, 0.25f, 0.54f, 1f));
            Stretch(keep.GetComponent<RectTransform>(), new Vector2(0.08f, 0.10f), new Vector2(0.47f, 0.33f), Vector2.zero, Vector2.zero);
            keep.onClick.AddListener(KeepDisplaySettings);
            Button revert = CreateButton(panel.transform, "Revert", new Color(0.24f, 0.20f, 0.34f, 1f));
            Stretch(revert.GetComponent<RectTransform>(), new Vector2(0.53f, 0.10f), new Vector2(0.92f, 0.33f), Vector2.zero, Vector2.zero);
            revert.onClick.AddListener(RevertDisplaySettings);
            return panel;
        }

        private GameObject CreateStartupPanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Startup Loading", new Color(0.06f, 0.03f, 0.12f, .92f));
            Stretch(panel.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            RawImage logo = CreateRawImage(panel.transform, "AIFren Logo");
            logo.texture = Resources.Load<Texture2D>("Presentation/Branding/logo");
            Stretch(logo.rectTransform, new Vector2(.39f, .52f), new Vector2(.61f, .74f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "AIFren", 38f, Ink, TextAlignmentOptions.Center);
            Stretch(title.rectTransform, new Vector2(.25f, .38f), new Vector2(.75f, .52f), Vector2.zero, Vector2.zero);
            TMP_Text loading = CreateText(panel.transform, "Preparing your companion...", 18f, Ink, TextAlignmentOptions.Center);
            Stretch(loading.rectTransform, new Vector2(.2f, .29f), new Vector2(.8f, .38f), Vector2.zero, Vector2.zero);
            return panel;
        }

        private void UpdateCompositionLayout()
        {
            if (currentDisplaySettings == null || avatarFrameRect == null)
            {
                return;
            }

            bool portrait = PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode,
                Screen.width,
                Screen.height
            );
            if (avatarFramingModeActive && portrait != avatarFramingSessionPortrait)
            {
                // A display/orientation transition never converts one layout's
                // unsaved adjustment into the other. Restore the old transient
                // tuple, then begin a fresh snapshot for the selected layout.
                avatarFraming.SetValues(avatarFramingSessionPortrait, avatarFramingSessionSnapshot, false);
                avatarFramingSessionPortrait = portrait;
                avatarFramingSessionSnapshot = avatarFraming.GetValues(portrait);
                AvatarConfiguration sessionConfiguration = AvatarConfiguration.Load();
                AvatarCrop sessionCrop = portrait ? sessionConfiguration.portraitUiCrop : sessionConfiguration.landscapeUiCrop;
                suppressAvatarFramingCallbacks = true;
                if (avatarSizeSlider != null) avatarSizeSlider.minValue = AvatarUiFraming.MinimumZoom(sessionCrop);
                suppressAvatarFramingCallbacks = false;
            }
            if (portrait)
            {
                Stretch(avatarFrameRect, new Vector2(0.025f, .22f), new Vector2(0.975f, 0.89f), Vector2.zero, Vector2.zero);
                dialogueCardRect.anchorMin = new Vector2(0.035f, 0f);
                dialogueCardRect.anchorMax = new Vector2(0.965f, 0f);
                inputCardRect.anchorMin = new Vector2(0.035f, 0f);
                inputCardRect.anchorMax = new Vector2(0.965f, 0f);
                ConfigureInputForOrientation(true);
                ConfigureTopControlsForPortrait(true);
                dialogueTextLabel.fontSizeMax = DialogueFontPortraitMaximum;
            }
            else
            {
                Stretch(avatarFrameRect, new Vector2(0.075f, .19f), new Vector2(0.925f, 0.93f), Vector2.zero, Vector2.zero);
                dialogueCardRect.anchorMin = new Vector2(0.055f, 0f);
                dialogueCardRect.anchorMax = new Vector2(0.945f, 0f);
                inputCardRect.anchorMin = new Vector2(0.055f, 0f);
                inputCardRect.anchorMax = new Vector2(0.945f, 0f);
                ConfigureInputForOrientation(false);
                ConfigureTopControlsForPortrait(false);
                dialogueTextLabel.fontSizeMax = DialogueFontLandscapeMaximum;
            }

            if (avatarLoader != null)
            {
                avatarLoader.SetPresentationOrientation(portrait);
                avatarLoader.SetPresentationViewportPixels(StableAvatarPresentationPixels(portrait));
                avatarLoader.SetPreviewSurface(avatarSurface);
            }
            ApplyCanonicalAvatarPresentation(portrait, true);
            PlacePttPresentation();
        }

        private static Vector2 StableAvatarPresentationPixels(bool portrait)
        {
            // This is the outer composition viewport, not the fitted RawImage.
            // It intentionally stays independent of user UV crop/zoom so the
            // full-body RenderTexture and preview-camera aspect never chase a
            // close-up presentation adjustment.
            return new Vector2(
                Screen.width * (portrait ? .95f : .85f),
                Screen.height * (portrait ? .67f : .74f)
            );
        }

        private void ConfigureInputForOrientation(bool portrait)
        {
            if (messageInputRect == null || sendButtonRect == null) return;
            // Portrait needs a slightly wider Send hit target and a taller
            // usable text viewport; both remain above the bottom safe margin.
            float textRight = portrait ? .79f : .84f;
            float sendLeft = portrait ? .805f : .855f;
            Stretch(messageInputRect, new Vector2(.025f, portrait ? .14f : .18f), new Vector2(textRight, portrait ? .86f : .82f), Vector2.zero, Vector2.zero);
            Stretch(sendButtonRect, new Vector2(sendLeft, portrait ? .14f : .18f), new Vector2(.975f, portrait ? .86f : .82f), Vector2.zero, Vector2.zero);
        }

        private void ApplyCanonicalAvatarPresentation(bool portrait, bool syncControls)
        {
            if (avatarSurface == null)
            {
                return;
            }

            AvatarConfiguration configuration = AvatarConfiguration.Load();
            AvatarCrop crop = portrait ? configuration.portraitUiCrop : configuration.landscapeUiCrop;
            // The camera continues to render the complete, padded avatar. This
            // uvRect is presentation-only and is intentionally independent of
            // dialogue/input visibility or other transient UI geometry.
            if (crop != null && crop.IsValid())
            {
                if (avatarFraming == null)
                {
                    avatarFraming = AvatarPresentationFramingState.Load(configuration);
                }
                Rect resolvedCrop = avatarFraming.Resolve(portrait);
                avatarSurface.uvRect = resolvedCrop;
                // The crop can expand from the authored close composition to
                // the full RT. Match its actual pixel aspect so zoom-out never
                // stretches the avatar just to fill the presentation viewport.
                if (avatarAspectFitter != null)
                {
                    Texture texture = avatarSurface.texture;
                    avatarAspectFitter.aspectRatio = AvatarUiFraming.DisplayAspect(
                        resolvedCrop,
                        texture != null ? texture.width : 1,
                        texture != null ? texture.height : 1
                    );
                    avatarAspectFitter.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
                }
                if (syncControls)
                {
                    SyncAvatarFramingControls(portrait);
                }
            }
            else
            {
                avatarSurface.uvRect = new Rect(0f, 0f, 1f, 1f);
                if (avatarAspectFitter != null) avatarAspectFitter.aspectRatio = 1f;
            }
        }

        private void PlacePttPresentation()
        {
            if (pttIndicator == null || pttLabel == null) return;
            bool portrait = currentDisplaySettings != null && PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode, Screen.width, Screen.height);
            float iconSize = portrait ? 34f : 30f;
            float verticalGap = portrait ? 18f : 15f;
            const float labelGap = 6f;
            pttIndicator.rectTransform.anchorMin = Vector2.one;
            pttIndicator.rectTransform.anchorMax = Vector2.one;
            pttIndicator.rectTransform.pivot = new Vector2(1f, 0f);
            pttIndicator.rectTransform.anchoredPosition = new Vector2(-26f, verticalGap);
            pttIndicator.rectTransform.sizeDelta = Vector2.one * iconSize;
            pttLabel.rectTransform.anchorMin = Vector2.one;
            pttLabel.rectTransform.anchorMax = Vector2.one;
            pttLabel.rectTransform.pivot = new Vector2(1f, 0f);
            pttLabel.rectTransform.anchoredPosition = new Vector2(-26f - iconSize - labelGap, verticalGap);
            pttLabel.rectTransform.sizeDelta = new Vector2(portrait ? 125f : 112f, iconSize);
        }

        private void ConfigureTopControlsForPortrait(bool portrait)
        {
            if (hideUiButton == null || historyButton == null || settingsButton == null || closeButton == null) return;
            if (topBar != null)
            {
                float iconSide = portrait ? 78f : 58f;
                float hideWidth = portrait ? 142f : 116f;
                float outerMargin = portrait ? 14f : 18f;
                float gap = portrait ? 10f : 8f;
                float usableWidth = Mathf.Max(1f, topBar.GetComponent<RectTransform>().rect.width);
                PlaceFloatingTopControl(hideUiButton, outerMargin + hideWidth * .5f, hideWidth, iconSide);
                float closeCenter = usableWidth - outerMargin - iconSide * .5f;
                PlaceFloatingTopControl(closeButton, closeCenter, iconSide, iconSide);
                PlaceFloatingTopControl(settingsButton, closeCenter - (iconSide + gap), iconSide, iconSide);
                float historyCenter = closeCenter - 2f * (iconSide + gap);
                PlaceFloatingTopControl(historyButton, historyCenter, iconSide, iconSide);
                if (consoleButton != null && consoleUnlocked)
                {
                    float consoleWidth = portrait ? 108f : 88f;
                    float consoleCenter = historyCenter - iconSide * .5f - gap - consoleWidth * .5f;
                    PlaceFloatingTopControl(consoleButton, consoleCenter, consoleWidth, iconSide);
                    SetTopControlLabel(consoleButton, "Console");
                }
                SetTopControlLabel(historyButton, string.Empty);
                SetTopControlLabel(settingsButton, string.Empty);
                SetTopControlLabel(closeButton, "X");
                return;
            }
            if (portrait)
            {
                Stretch(hideUiButton.GetComponent<RectTransform>(), new Vector2(.00f, .14f), new Vector2(.14f, .88f), Vector2.zero, Vector2.zero);
                Stretch(historyButton.GetComponent<RectTransform>(), new Vector2(.76f, .14f), new Vector2(.835f, .88f), Vector2.zero, Vector2.zero);
                Stretch(settingsButton.GetComponent<RectTransform>(), new Vector2(.8475f, .14f), new Vector2(.9225f, .88f), Vector2.zero, Vector2.zero);
                Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(.935f, .14f), Vector2.one, Vector2.zero, Vector2.zero);
                SetTopControlLabel(historyButton, string.Empty);
                SetTopControlLabel(settingsButton, string.Empty);
                SetTopControlLabel(closeButton, "X");
            }
                /* Previous encoded portrait label retained below only to keep this focused repair minimal.
                Stretch(hideUiButton.GetComponent<RectTransform>(), new Vector2(.00f, .14f), new Vector2(.16f, .88f), Vector2.zero, Vector2.zero);
                Stretch(historyButton.GetComponent<RectTransform>(), new Vector2(.73f, .14f), new Vector2(.81f, .88f), Vector2.zero, Vector2.zero);
                Stretch(settingsButton.GetComponent<RectTransform>(), new Vector2(.825f, .14f), new Vector2(.905f, .88f), Vector2.zero, Vector2.zero);
                Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(.92f, .14f), Vector2.one, Vector2.zero, Vector2.zero);
                SetTopControlLabel(historyButton, string.Empty);
                SetTopControlLabel(settingsButton, string.Empty);
                SetTopControlLabel(closeButton, "×");
            }
                */
            else
            {
                Stretch(hideUiButton.GetComponent<RectTransform>(), new Vector2(0f, .14f), new Vector2(.10f, .88f), Vector2.zero, Vector2.zero);
                Stretch(historyButton.GetComponent<RectTransform>(), new Vector2(.855f, .14f), new Vector2(.90f, .88f), Vector2.zero, Vector2.zero);
                Stretch(settingsButton.GetComponent<RectTransform>(), new Vector2(.905f, .14f), new Vector2(.955f, .88f), Vector2.zero, Vector2.zero);
                Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(.96f, .14f), Vector2.one, Vector2.zero, Vector2.zero);
                SetTopControlLabel(historyButton, "Log");
                SetTopControlLabel(settingsButton, "Settings");
                SetTopControlLabel(closeButton, "Close");
            }
        }

        private static void PlaceFloatingTopControl(Button button, float centerX, float width, float height)
        {
            RectTransform rect = button.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(0f, .5f);
            rect.anchorMax = new Vector2(0f, .5f);
            rect.pivot = new Vector2(.5f, .5f);
            rect.anchoredPosition = new Vector2(centerX, 0f);
            rect.sizeDelta = new Vector2(width, height);
        }

        private void RefreshDeveloperControlVisibility()
        {
            if (consoleButton == null) return;
            // The unlock code controls exactly one object. It never toggles
            // reconnect, character, or placeholder controls.
            consoleButton.gameObject.SetActive(consoleUnlocked);
            if (topBar != null)
            {
                bool portrait = currentDisplaySettings != null && PresentationDisplaySettingsPolicy.IsPortrait(
                    currentDisplaySettings.layoutMode, Screen.width, Screen.height);
                ConfigureTopControlsForPortrait(portrait);
            }
        }

        private static void SetTopControlLabel(Button button, string value)
        {
            TMP_Text label = button.GetComponentInChildren<TMP_Text>();
            if (label != null) label.text = value;
            SVGImage icon = button.GetComponentInChildren<SVGImage>(true);
            if (icon == null) return;
            bool iconOnly = string.IsNullOrEmpty(value);
            icon.gameObject.SetActive(true);
            RectTransform iconRect = icon.rectTransform;
            iconRect.anchorMin = new Vector2(iconOnly ? .5f : 0f, .5f);
            iconRect.anchorMax = iconRect.anchorMin;
            iconRect.pivot = new Vector2(iconOnly ? .5f : 0f, .5f);
            iconRect.sizeDelta = Vector2.one * IconButtonSize;
            iconRect.anchoredPosition = new Vector2(iconOnly ? 0f : ButtonHorizontalPadding, 0f);
            if (label != null)
            {
                label.gameObject.SetActive(!iconOnly);
                if (!iconOnly)
                {
                    label.rectTransform.offsetMin = new Vector2(IconButtonSize + ButtonHorizontalPadding + IconTextGap, 3f);
                    label.rectTransform.offsetMax = new Vector2(-ButtonHorizontalPadding, -3f);
                }
            }
        }

        private static void AddPointerUpHandler(GameObject target, UnityEngine.Events.UnityAction callback)
        {
            EventTrigger trigger = target.GetComponent<EventTrigger>() ?? target.AddComponent<EventTrigger>();
            if (trigger.triggers == null) trigger.triggers = new List<EventTrigger.Entry>();
            EventTrigger.Entry entry = new EventTrigger.Entry { eventID = EventTriggerType.PointerUp };
            entry.callback.AddListener(_ => callback());
            trigger.triggers.Add(entry);
        }

        private TMP_InputField CreateInputField(Transform parent)
        {
            GameObject field = CreatePanel(parent, "Input Field", new Color(0.12f, 0.12f, 0.20f, 1f));
            TMP_InputField inputField = field.AddComponent<TMP_InputField>();
            inputField.lineType = TMP_InputField.LineType.SingleLine;
            inputField.characterLimit = 4000;

            TMP_Text placeholder = CreateText(field.transform, "Say something…", 22f, new Color(0.60f, 0.59f, 0.68f, 1f), TextAlignmentOptions.MidlineLeft);
            Stretch(placeholder.rectTransform, new Vector2(0.03f, 0.08f), new Vector2(0.97f, 0.92f), Vector2.zero, Vector2.zero);
            TMP_Text text = CreateText(field.transform, string.Empty, 22f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(text.rectTransform, new Vector2(0.03f, 0.08f), new Vector2(0.97f, 0.92f), Vector2.zero, Vector2.zero);
            inputField.textViewport = field.GetComponent<RectTransform>();
            inputField.textComponent = text as TextMeshProUGUI;
            inputField.placeholder = placeholder as TextMeshProUGUI;
            return inputField;
        }

        private Slider CreateSlider(Transform parent, float min, float max, float value)
        {
            GameObject sliderObject = new GameObject("Slider", typeof(RectTransform), typeof(Slider));
            sliderObject.transform.SetParent(parent, false);
            Slider slider = sliderObject.GetComponent<Slider>();
            slider.minValue = min;
            slider.maxValue = max;
            slider.value = value;
            slider.wholeNumbers = false;

            Image background = CreateImage(sliderObject.transform, "Background", new Color(0.19f, 0.18f, 0.28f, 1f));
            Stretch(background.rectTransform, new Vector2(0f, 0.30f), Vector2.one * 1f, new Vector2(0f, -4f), new Vector2(0f, 4f));
            GameObject fillArea = new GameObject("Fill Area", typeof(RectTransform));
            fillArea.transform.SetParent(sliderObject.transform, false);
            Stretch(fillArea.GetComponent<RectTransform>(), new Vector2(0f, 0.30f), Vector2.one * 1f, new Vector2(8f, -4f), new Vector2(-8f, 4f));
            Image fill = CreateImage(fillArea.transform, "Fill", Accent);
            Stretch(fill.rectTransform, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            GameObject handleArea = new GameObject("Handle Slide Area", typeof(RectTransform));
            handleArea.transform.SetParent(sliderObject.transform, false);
            Stretch(handleArea.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, new Vector2(8f, 0f), new Vector2(-8f, 0f));
            Image handle = CreateImage(handleArea.transform, "Handle", Ink);
            handle.rectTransform.sizeDelta = new Vector2(16f, 22f);
            slider.fillRect = fill.rectTransform;
            slider.handleRect = handle.rectTransform;
            slider.targetGraphic = handle;
            return slider;
        }

        private Toggle CreateToggle(Transform parent, string label, bool value)
        {
            GameObject toggleObject = new GameObject("Toggle", typeof(RectTransform), typeof(Toggle));
            toggleObject.transform.SetParent(parent, false);
            Toggle toggle = toggleObject.GetComponent<Toggle>();
            Image background = CreateImage(toggleObject.transform, "Background", new Color(0.20f, 0.18f, 0.30f, 1f));
            Stretch(background.rectTransform, new Vector2(0f, 0.1f), new Vector2(0.07f, 0.9f), Vector2.zero, Vector2.zero);
            Image checkmark = CreateImage(background.transform, "Checkmark", Accent);
            Stretch(checkmark.rectTransform, new Vector2(0.22f, 0.22f), new Vector2(0.78f, 0.78f), Vector2.zero, Vector2.zero);
            TMP_Text text = CreateText(toggleObject.transform, label, 17f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(text.rectTransform, new Vector2(0.10f, 0f), Vector2.one, Vector2.zero, Vector2.zero);
            toggle.targetGraphic = background;
            toggle.graphic = checkmark;
            toggle.isOn = value;
            return toggle;
        }

        private Button CreateButton(Transform parent, string label, Color color)
        {
            GameObject buttonObject = CreatePanel(parent, label + " Button", theme != null ? theme.surfaceStrong : color);
            Button button = buttonObject.AddComponent<Button>();
            ColorBlock colors = button.colors;
            colors.normalColor = Color.white;
            colors.highlightedColor = new Color(1f, 1f, 1f, 0.88f);
            colors.pressedColor = new Color(1f, 1f, 1f, 0.68f);
            button.colors = colors;
            TMP_Text buttonText = CreateText(buttonObject.transform, label, 18f, theme != null ? theme.text : Ink, TextAlignmentOptions.Center);
            buttonText.enableAutoSizing = true;
            buttonText.fontSizeMin = 11f;
            buttonText.fontSizeMax = 18f;
            buttonText.enableWordWrapping = false;
            Stretch(buttonText.rectTransform, Vector2.zero, Vector2.one, new Vector2(5f, 3f), new Vector2(-5f, -3f));
            AddButtonIcon(buttonObject.transform, label, buttonText.rectTransform);
            button.onClick.AddListener(() => presentationAudio?.PlayTap());
            return button;
        }

        private void AddButtonIcon(Transform parent, string label, RectTransform buttonText)
        {
            string iconName = label == "Settings" ? "settings-knobs" :
                label == "Log" ? "archive-register" :
                label == "Hide" ? "expand" :
                label == "Stop speaking" ? "speaker-off" :
                label == "Reconnect" ? "confirmed" :
                label == "Rebind" ? "microphone" : null;
            if (iconName == null) return;

            Sprite iconSprite = Resources.Load<Sprite>("Presentation/Icons/" + iconName);
            if (iconSprite == null)
            {
                Debug.LogWarning("AIFren UI icon could not be loaded as a Sprite: Presentation/Icons/" + iconName +
                    ". Verify the Vector Graphics importer and SVG asset import settings.");
                return;
            }
            if (Debug.isDebugBuild && LoggedIconResources.Add(iconName))
                Debug.Log("AIFren UI icon loaded: Presentation/Icons/" + iconName + " (" + iconSprite.name + ")");
            GameObject iconObject = new GameObject("Icon " + iconName, typeof(RectTransform), typeof(SVGImage));
            iconObject.transform.SetParent(parent, false);
            SVGImage icon = iconObject.GetComponent<SVGImage>();
            icon.sprite = iconSprite;
            icon.preserveAspect = true;
            icon.color = theme.text;
            icon.raycastTarget = false;
            RectTransform iconRect = icon.rectTransform;
            iconRect.anchorMin = new Vector2(string.IsNullOrEmpty(label) ? .5f : 0f, .5f);
            iconRect.anchorMax = iconRect.anchorMin;
            iconRect.pivot = new Vector2(string.IsNullOrEmpty(label) ? .5f : 0f, .5f);
            iconRect.sizeDelta = Vector2.one * IconButtonSize;
            iconRect.anchoredPosition = new Vector2(string.IsNullOrEmpty(label) ? 0f : ButtonHorizontalPadding, 0f);
            if (string.IsNullOrEmpty(label))
            {
                buttonText.gameObject.SetActive(false);
            }
            else
            {
                buttonText.offsetMin = new Vector2(IconButtonSize + ButtonHorizontalPadding + IconTextGap, 3f);
                buttonText.offsetMax = new Vector2(-ButtonHorizontalPadding, -3f);
            }
        }

        private TMP_Text CreateText(Transform parent, string text, float fontSize, Color color, TextAlignmentOptions alignment)
        {
            GameObject textObject = new GameObject("Text", typeof(RectTransform), typeof(TextMeshProUGUI));
            textObject.transform.SetParent(parent, false);
            TextMeshProUGUI label = textObject.GetComponent<TextMeshProUGUI>();
            label.font = font;
            label.text = text;
            label.fontSize = fontSize;
            label.color = color;
            label.alignment = alignment;
            label.enableWordWrapping = true;
            label.raycastTarget = false;
            return label;
        }

        private Image CreateImage(Transform parent, string name, Color color)
        {
            GameObject imageObject = new GameObject(name, typeof(RectTransform), typeof(Image));
            imageObject.transform.SetParent(parent, false);
            Image image = imageObject.GetComponent<Image>();
            image.color = color;
            image.sprite = CreateRoundedSprite();
            image.type = Image.Type.Sliced;
            return image;
        }

        private RawImage CreateRawImage(Transform parent, string name)
        {
            GameObject imageObject = new GameObject(name, typeof(RectTransform), typeof(RawImage));
            imageObject.transform.SetParent(parent, false);
            return imageObject.GetComponent<RawImage>();
        }

        private SVGImage CreatePttIndicator(Transform parent)
        {
            Sprite sprite = Resources.Load<Sprite>("Presentation/Icons/microphone");
            if (sprite == null)
            {
                Debug.LogWarning("AIFren PTT icon could not be loaded: Presentation/Icons/microphone");
                return new GameObject("PTT Indicator", typeof(RectTransform), typeof(SVGImage)).GetComponent<SVGImage>();
            }

            GameObject iconObject = new GameObject("PTT Indicator", typeof(RectTransform), typeof(SVGImage));
            iconObject.transform.SetParent(parent, false);
            SVGImage icon = iconObject.GetComponent<SVGImage>();
            icon.sprite = sprite;
            icon.preserveAspect = true;
            icon.raycastTarget = false;
            return icon;
        }

        private GameObject CreatePanel(Transform parent, string name, Color color)
        {
            GameObject panel = new GameObject(name, typeof(RectTransform), typeof(Image));
            panel.transform.SetParent(parent, false);
            Image image = panel.GetComponent<Image>();
            image.color = color;
            image.sprite = CreateRoundedSprite();
            image.type = Image.Type.Sliced;
            Outline outline = panel.AddComponent<Outline>();
            outline.effectColor = theme != null ? new Color(theme.outline.r, theme.outline.g, theme.outline.b, .48f) : new Color(.6f, .4f, .9f, .4f);
            outline.effectDistance = new Vector2(1f, -1f);
            return panel;
        }

        private static Sprite roundedSprite;
        private static Sprite CreateRoundedSprite()
        {
            if (roundedSprite != null) return roundedSprite;
            const int size = 48;
            const float radius = 12f;
            Texture2D texture = new Texture2D(size, size, TextureFormat.RGBA32, false) { name = "AIFren Rounded UI Surface" };
            for (int y = 0; y < size; y++) for (int x = 0; x < size; x++)
            {
                float dx = Mathf.Max(radius - x, x - (size - radius - 1), 0f);
                float dy = Mathf.Max(radius - y, y - (size - radius - 1), 0f);
                float distance = Mathf.Sqrt(dx * dx + dy * dy);
                float alpha = Mathf.Clamp01(radius - distance + 1f);
                texture.SetPixel(x, y, new Color(1f, 1f, 1f, alpha));
            }
            texture.Apply();
            roundedSprite = Sprite.Create(texture, new Rect(0, 0, size, size), new Vector2(.5f, .5f), 100f, 0, SpriteMeshType.FullRect, new Vector4(radius, radius, radius, radius));
            return roundedSprite;
        }

        private static void Stretch(RectTransform transform, Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax)
        {
            transform.anchorMin = anchorMin;
            transform.anchorMax = anchorMax;
            transform.offsetMin = offsetMin;
            transform.offsetMax = offsetMax;
        }

        private static void EnsureEventSystem()
        {
            if (FindObjectOfType<EventSystem>() != null)
            {
                return;
            }

            GameObject eventSystem = new GameObject("EventSystem", typeof(EventSystem), typeof(StandaloneInputModule));
            DontDestroyOnLoad(eventSystem);
        }

        private static Texture2D CreateGradientTexture(Color top, Color bottom)
        {
            Texture2D texture = new Texture2D(2, 64, TextureFormat.RGBA32, false)
            {
                name = "AIFren Neutral Background",
                wrapMode = TextureWrapMode.Clamp
            };
            for (int y = 0; y < texture.height; y++)
            {
                Color color = Color.Lerp(bottom, top, y / (float)(texture.height - 1));
                texture.SetPixel(0, y, color);
                texture.SetPixel(1, y, color);
            }
            texture.Apply();
            return texture;
        }

        private void OnApplicationQuit()
        {
            if (client != null)
            {
                client.Dispose();
            }
        }
    }

    public static class AIFrenPocBootstrap
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void CreateController()
        {
            if (UnityEngine.Object.FindObjectOfType<AIFrenPocController>() != null)
            {
                return;
            }

            GameObject host = new GameObject("AIFren Companion Client");
            UnityEngine.Object.DontDestroyOnLoad(host);
            AIFrenPocController controller = host.AddComponent<AIFrenPocController>();
            AvatarLoader avatarLoader = host.AddComponent<AvatarLoader>();
            controller.ConfigureAvatarLoader(avatarLoader);
        }
    }
}
