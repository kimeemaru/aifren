using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
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
        private enum HiddenSubtitlePageState
        {
            ShowingPage,
            FadingOut,
            PreparingNextPage,
            FadingIn
        }

        private const string BackendUri = "ws://127.0.0.1:8765";
        private const string RevealSpeedPreference = "AIFren.DialogueRevealSpeed";
        private const string InstantTextPreference = "AIFren.InstantDialogueText";
        private const string DisplaySettingsPreference = "AIFren.PresentationDisplaySettings.v1";
        private const string PushToTalkBindingPreference = "AIFren.PushToTalkBinding";
        private const string PttAutoSendPreference = "AIFren.PttAutoSend";
        private const string AvatarRenderScalePreference = "AIFren.AvatarRenderScale";
        private const string GraphicsQualityPreference = "AIFren.GraphicsQuality";
        private const string ShowDialogueWhenHiddenPreference = "AIFren.ShowDialogueWhenHidden";
        private const string AlwaysOnTopPreference = "AIFren.AlwaysOnTop";
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
        private readonly WordReveal hiddenSubtitleReveal = new WordReveal();

        private AIFrenWebSocketClient client;
        private AvatarLoader avatarLoader;
        private AvatarPresentationState avatarPresentationState;
        private AvatarViewerBackgroundState avatarViewerBackgroundState;
        private ManagedAssetLibrary managedAssetLibrary;
        private bool useDirectAvatarPresentation = true;
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
        private TMP_Text backendWarningLabel;
        private Button backendReconnectButton;
        private TMP_Text avatarModelValue;
        private TMP_Text volumeLabel;
        private TMP_Text revealSpeedLabel;
        private TMP_InputField messageInput;
        private RectTransform messageInputRect;
        private RectTransform sendButtonRect;
        private Button sendButton;
        private GameObject historyPanel;
        private GameObject settingsPanel;
        private GameObject backgroundLibraryPanel;
        private GameObject modelLibraryPanel;
        private Transform modelLibraryTiles;
        private Transform backgroundLibraryTiles;
        private readonly HashSet<string> selectedModelAssets = new HashSet<string>();
        private readonly HashSet<string> selectedBackgroundAssets = new HashSet<string>();
        private readonly HashSet<string> thumbnailGenerationInFlight = new HashSet<string>();
        // Model loading changes live Unity objects asynchronously. Queue the
        // most recent request and let only that request commit UI/state.
        private ManagedAssetRecord pendingModelApply;
        private bool pendingModelApplyRemoveOnFailure;
        private bool modelApplyInProgress;
        private string modelApplyInFlightId;
        private int modelApplyGeneration;
        private Button deleteModelAssetsButton;
        private Button deleteBackgroundAssetsButton;
        private GameObject modelDeleteConfirmPanel;
        private GameObject backgroundDeleteConfirmPanel;
        private Transform historyContent;
        private ScrollRect historyScroll;
        private Slider volumeSlider;
        private bool ttsVolumeDirty;
        private float pendingTtsVolume;
        private float nextTtsVolumeSendAt;
        private Slider revealSlider;
        private Toggle instantTextToggle;
        private Toggle hiddenDialogueToggle;
        private Toggle alwaysOnTopToggle;
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
        private AvatarPresentationInputSurface avatarPresentationInput;
        private GameObject avatarViewPanel;
        private GameObject avatarViewGrid;
        private Slider avatarViewXSlider;
        private Slider avatarViewYSlider;
        private Slider avatarViewScaleSlider;
        private TMP_InputField avatarViewXInput;
        private TMP_InputField avatarViewYInput;
        private TMP_InputField avatarViewScaleInput;
        private bool avatarViewEditing;
        private bool suppressAvatarViewCallbacks;
        private AvatarPresentationValues avatarViewPortraitSnapshot;
        private AvatarPresentationValues avatarViewLandscapeSnapshot;
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
        private TMP_Text avatarViewerBackgroundValue;
        private Texture2D portraitCustomBackground;
        private Texture2D landscapeCustomBackground;
        private TMP_Text geminiProviderStatus;
        private TMP_Text geminiModelValue;
        private TMP_Text ttsProviderValue;
        private TMP_Text ttsVoiceValue;
        private TMP_Text ttsDeviceValue;
        private TMP_InputField geminiApiKeyInput;
        private bool showGeminiApiKey;
        private bool alwaysOnTop;
        private TMP_Text pttBindValue;
        private TMP_Text pttRebindHint;
        private TMP_Text globalPttStatus;
        private KeyCode pushToTalkKey;
        private bool rebindingPushToTalk;
        private bool unityPttPressed;
        private bool restoreMessageInputAfterPtt;
        private bool? lastMessageInputEnabled;
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
        private string avatarQaUnlockBuffer = string.Empty;
        private readonly List<string> consoleLines = new List<string>();
        private TMP_Text displayConfirmLabel;
        private float displayConfirmDeadline;
        private bool displayConfirmActive;
        private bool startupDisplayFinalizationPending;
        private string characterName = "AIFren";
        private string visibleState = "Disconnected";
        private ConnectionState lastObservedConnectionState = ConnectionState.Disconnected;
        private string detail = "Start backend_host.py to connect.";
        private bool submitInFlight;
        private bool backendReconnectInProgress;
        private bool instantText;
        private float revealWordsPerSecond;
        // A fixed, non-accumulating subtitle lead keeps the caption close to
        // speech without distorting provider/fallback word spacing.
        private const float HiddenSubtitleLeadSeconds = .10f;
        private const float HiddenSubtitlePageFadeOutSeconds = .09f;
        private const float HiddenSubtitlePageFadeInSeconds = .12f;
        private string pendingAssistantContent;
        private bool pendingAssistantReveal;
        private bool pendingSpeechReady;
        private float pendingSpeechDuration;
        private bool interfaceHidden;
        private Vector2 lastLoggedAvatarContainerSize;
        private bool lastLoggedAvatarContainerUiHidden;
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
        private bool hiddenSubtitleTemporarilySuppressed;
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
        private readonly List<TMP_Text> hiddenSubtitleBackings = new List<TMP_Text>();
        private readonly List<Material> hiddenSubtitleBackingMaterials = new List<Material>();
        private RectTransform hiddenDialogueViewport;
        private ScrollRect hiddenDialogueScroll;
        private Scrollbar hiddenDialogueScrollbar;
        private CanvasGroup hiddenDialogueCanvasGroup;
        private Material hiddenSubtitleMaterial;
        private HiddenSubtitlePresenter hiddenSubtitlePresenter;
        private string currentAssistantPresentationText = string.Empty;
        private bool subtitleSpeechActive;
        private readonly List<string> subtitlePages = new List<string>();
        private int subtitlePageIndex;
        private int subtitleGeneration;
        private int subtitlePlaybackGeneration = -1;
        private float subtitleSpeechDuration;
        private bool subtitleAwaitingPlayback;
        private Coroutine subtitlePresentationCoroutine;
        private bool subtitlePlaybackStartedSignal;
        private bool subtitlePlaybackStoppedSignal;
        private readonly List<float> subtitleWordSchedule = new List<float>();
        private readonly List<SubtitlePageWordRange> subtitlePageWordRanges = new List<SubtitlePageWordRange>();
        private float subtitlePresentationStartedAt;
        private float subtitlePlaybackStartedAt;
        private bool subtitleTimingUsesPlaybackClock;
        private int subtitlePlaybackId;
        private float subtitleResponseReceivedAt;
        private bool subtitleFirstWordLogged;
        private HiddenSubtitlePageState hiddenSubtitlePageState;

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
            avatarLoader.SetDirectPresentation(useDirectAvatarPresentation);

            if (avatarSurface != null)
            {
                avatarLoader.SetPreviewSurface(avatarSurface);
            }
            avatarLoader.SetPresentationRenderScale(avatarRenderScale);
        }

        private void Awake()
        {
            // Keep Linux presentation/animation updates running when another
            // window has focus without changing Windows player behavior. PTT
            // still releases on focus loss below as an input safety boundary;
            // it does not pause the companion presentation.
            if (Application.platform == RuntimePlatform.LinuxPlayer)
            {
                Application.runInBackground = true;
                Debug.Log("[AIFren Runtime] Background execution enabled=" + Application.runInBackground + ".");
            }
        }

        private async void Start()
        {
            string[] commandLine = Environment.GetCommandLineArgs();
            useDirectAvatarPresentation = !commandLine.Contains("-aifren-avatar-rt") || commandLine.Contains("-aifren-avatar-direct");
            avatarLoader?.SetDirectPresentation(useDirectAvatarPresentation);
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
            alwaysOnTop = PlayerPrefs.GetInt(AlwaysOnTopPreference, 0) == 1;
            avatarPresentationState = AvatarPresentationState.Load(AvatarConfiguration.Load());
            avatarViewerBackgroundState = AvatarViewerBackgroundState.Load();
            managedAssetLibrary = ManagedAssetLibrary.Load();
            List<ManagedAssetRecord> removedInvalidModels = managedAssetLibrary.RemoveInvalidModelRecords();
            if (removedInvalidModels.Count > 0)
            {
                string configuredModel = PlayerPrefs.GetString(AvatarLoader.CustomModelPathPreference, string.Empty);
                if (removedInvalidModels.Exists(record => record.path == configuredModel))
                    PlayerPrefs.DeleteKey(AvatarLoader.CustomModelPathPreference);
                PlayerPrefs.Save();
                Debug.LogWarning("Removed " + removedInvalidModels.Count + " invalid managed avatar model(s); using the bundled avatar if one was selected.");
            }
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
            ApplyAvatarPresentationMode();
            if (alwaysOnTop)
            {
                StartCoroutine(ApplyAlwaysOnTopAfterWindowCreation());
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
            UpdateSubtitlePaging();

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

                UpdateBackendDisconnectWarning();
            }

            if (wordReveal.Advance(Time.unscaledDeltaTime))
            {
                dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, !wordReveal.IsComplete);
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
            HandleHiddenDeveloperSequences();
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

                if (messageInput != null && messageInput.isFocused)
                {
                    bool shift = Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift);
                    if (!shift && hasText)
                    {
                        SubmitCurrentText();
                        return;
                    }
                    // Shift+Enter remains a multiline input-field newline.
                    if (shift) return;
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

        private void HandleHiddenDeveloperSequences()
        {
            if (messageInput != null && messageInput.isFocused) return;
            string input = Input.inputString;
            if (string.IsNullOrEmpty(input)) return;
            foreach (char character in input)
            {
                AdvanceHiddenSequence(ref consoleUnlockBuffer, character, '8', 8, () =>
                {
                    consoleUnlocked = true;
                    PlayerPrefs.SetInt("AIFren.ConsoleUnlocked", 1);
                    PlayerPrefs.Save();
                    RefreshDeveloperControlVisibility();
                    ToggleConsolePanel();
                });
                AdvanceHiddenSequence(ref avatarQaUnlockBuffer, character, '7', 7, ToggleAvatarQaPanel);
            }
        }

        private static void AdvanceHiddenSequence(ref string buffer, char input, char expected, int length, Action matched)
        {
            if (input != expected) { buffer = string.Empty; return; }
            buffer += input;
            if (buffer.Length > length) buffer = buffer.Substring(buffer.Length - length);
            if (buffer.Length != length) return;
            buffer = string.Empty;
            matched?.Invoke();
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
            // Hiding the UI changes only foreground overlay visibility. In
            // particular, do not re-enter the avatar layout/RT path here:
            // doing so reapplied the presentation transform while Canvas UI
            // elements were being enabled or disabled, which made saved
            // framing appear to shift on Hide -> Show.
            if (visibilityTransition != null) StopCoroutine(visibilityTransition);
            visibilityTransition = StartCoroutine(TransitionUiVisibility(show));
            SetTopControlLabel(hideUiButton, interfaceHidden || temporarilyRevealed ? "Show" : "Hide");
        }

        private IEnumerator TransitionUiVisibility(bool show)
        {
            // The hidden overlay owns dialogue only while the ordinary UI is
            // hidden. Disable it before the normal card begins its entrance.
            // A top-edge peek is not a committed Show action. It suppresses
            // the floating subtitle visually, but never invalidates its
            // response generation or stops its sole presentation coroutine.
            if (show && !temporarilyRevealed && hiddenDialogueViewport != null)
            {
                subtitleGeneration++;
                if (subtitlePresentationCoroutine != null) StopCoroutine(subtitlePresentationCoroutine);
                subtitlePresentationCoroutine = null;
                HideHiddenSubtitleImmediately();
            }
            else if (show && temporarilyRevealed)
            {
                SuppressHiddenSubtitleForTemporaryReveal();
            }

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
            // Restore only after the normal dialogue has fully left the
            // screen, so temporary edge reveal can never overlap both text
            // presentations. The running subtitle coroutine kept its page,
            // schedule, and reveal position while its root was inactive.
            if (!show) RestoreHiddenSubtitleAfterTemporaryReveal();
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
            if (client == null || backendReconnectInProgress || client.State == ConnectionState.Connecting)
            {
                return;
            }
            backendReconnectInProgress = true;
            RefreshBackendReconnectControl();
            Debug.Log("[AIFren Transport] Reconnect: attempting existing backend.");
            SetBackendDisconnectWarning("Reconnecting...");
            ApplyStatus("connecting", "Reconnecting to local backend...");
            RefreshInputAvailability();
            await ConnectAsync();
            if (client.State == ConnectionState.Connected)
            {
                // The following snapshot drives Ready plus the live Models
                // values. Keep the control disabled until that health check.
                Debug.Log("[AIFren Transport] Reconnect: connected; waiting for healthy snapshot.");
                ApplyStatus("connecting", "Connected. Loading snapshot...");
                return;
            }

            if (Application.platform == RuntimePlatform.LinuxPlayer)
            {
                Debug.Log("[AIFren Transport] Reconnect: no backend connection; ensuring repository-owned backend.");
                SetBackendDisconnectWarning("Reconnecting... starting repository backend.");
                LinuxBackendRecovery.Result lifecycle = await LinuxBackendRecovery.EnsureAsync();
                if (lifecycle.Succeeded)
                {
                    Debug.Log("[AIFren Transport] Reconnect: " + lifecycle.Detail);
                    await ConnectAsync();
                    if (client.State == ConnectionState.Connected)
                    {
                        Debug.Log("[AIFren Transport] Reconnect: connected; waiting for healthy snapshot.");
                        ApplyStatus("connecting", "Connected. Loading snapshot...");
                        return;
                    }
                }
                else
                {
                    Debug.LogWarning("[AIFren Transport] Reconnect: backend recovery failed: " + lifecycle.Detail);
                    FinishBackendReconnectFailure(lifecycle.Detail);
                    return;
                }
            }

            FinishBackendReconnectFailure(client.LastError);
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
                subtitleResponseReceivedAt = Time.unscaledTime;
                Debug.Log("[AIFren Timing] Unity assistant response received t=" + subtitleResponseReceivedAt.ToString("F3"));
                Debug.Log("[AIFren Subtitle] assistant response received hidden=" + interfaceHidden + " enabled=" + showDialogueWhenHidden);
                BeginSubtitleResponse(data.content);
                List<string> emotes = DialoguePresentationParser.EmoteTexts(data.content);
                if (AvatarGestureMapper.TryFirstSupported(emotes, out AvatarGestureIntent gesture, out string matchedEmote))
                {
                    Debug.Log("[AvatarGesture] mapped emote=\"" + matchedEmote + "\" -> " + gesture);
                    avatarAnimation?.PlayGesture(gesture);
                }
                else avatarAnimation?.PlayAttentiveReaction();
                pendingAssistantReveal = true;
                TryBeginPendingAssistantReveal();
            }
            else if (backendEvent.type == "tts_state" && data != null)
            {
                if (data.state == "playback_started")
                {
                    Debug.Log("[AIFren Subtitle] playback_started generation=" + subtitleGeneration);
                    pendingSpeechReady = true;
                    pendingSpeechDuration = data.duration_seconds;
                    subtitleSpeechDuration = data.duration_seconds;
                    avatarAnimation?.BeginSpeech(data.duration_seconds, data.lip_sync_envelope);
                    subtitleSpeechActive = true;
                    subtitlePlaybackStartedSignal = true;
                    subtitleAwaitingPlayback = false;
                    subtitlePlaybackGeneration = subtitleGeneration;
                    subtitlePlaybackId = data.playback_id;
                    subtitlePlaybackStartedAt = Time.unscaledTime;
                    Debug.Log("[AIFren Timing] Unity playback_started; response-to-playback=" +
                        (subtitlePlaybackStartedAt - subtitleResponseReceivedAt).ToString("F3") + "s; id=" + subtitlePlaybackId);
                    ConfigureSubtitleTimingPlan(subtitleSpeechDuration, true, data.word_start_seconds);
                    hiddenSubtitlePresenter?.OnPlaybackStarted(subtitleGeneration, data.playback_id,
                        new List<float>(subtitleWordSchedule), Time.unscaledTime);
                    TryBeginPendingAssistantReveal();
                    SyncHiddenDialogueText();
                    ApplyStatus("speaking", data.message);
                }
                else if (data.state == "failed" || data.state == "not_started" || data.state == "stopped")
                {
                    avatarAnimation?.StopSpeech();
                    pendingSpeechReady = true;
                    pendingSpeechDuration = 0f;
                    TryBeginPendingAssistantReveal();
                    if (data.state == "stopped" && subtitlePlaybackGeneration != subtitleGeneration)
                    {
                        // A stop that arrived before this response ever began
                        // playback belongs to an older response.
                        return;
                    }
                    if (data.state == "stopped" && data.playback_id > 0 && subtitlePlaybackId > 0 &&
                        data.playback_id != subtitlePlaybackId)
                    {
                        // A delayed completion from an older local playback
                        // must not end a newer subtitle response.
                        return;
                    }
                    subtitleSpeechActive = false;
                    subtitlePlaybackStoppedSignal = data.state == "stopped";
                    subtitleAwaitingPlayback = false;
                    if (data.state == "stopped") hiddenSubtitlePresenter?.OnPlaybackStopped(data.playback_id, Time.unscaledTime);
                    // Do not RevealAll or rewrite page ownership here: a late
                    // stop must show only the current page's already-due
                    // words, then let the sole presentation coroutine exit.
                    // Preserve the final readable page briefly after actual
                    // playback; failed/disabled TTS uses the text-duration
                    // fallback scheduled when the response arrived.
                    SyncHiddenDialogueText();
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
                Debug.Log("[AIFren PTT] Backend voice state=" + data.state +
                    ", globalListener=" + backendGlobalPtt + ".");
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
            backendReconnectInProgress = false;
            ClearBackendDisconnectWarning();

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
            currentAssistantPresentationText = content ?? string.Empty;
            dialogueLayoutContent = DialoguePresentationParser.FormatVisible(currentAssistantPresentationText);
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
            dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, !wordReveal.IsComplete);
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
                dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, !wordReveal.IsComplete);
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

        private void UpdateBackendDisconnectWarning()
        {
            if (client == null || client.State == lastObservedConnectionState) return;
            lastObservedConnectionState = client.State;
            if (client.State == ConnectionState.Disconnected || client.State == ConnectionState.Error)
            {
                string reason = !string.IsNullOrWhiteSpace(client.LastDisconnectReason)
                    ? client.LastDisconnectReason
                    : client.LastError;
                SetBackendDisconnectWarning(reason);
            }
        }

        private void SetBackendDisconnectWarning(string reason)
        {
            string warning = "Warning: backend disconnected";
            string compactReason = string.Empty;
            if (!string.IsNullOrWhiteSpace(reason))
            {
                const int maximumReasonLength = 120;
                compactReason = reason.Trim();
                if (compactReason.Length > maximumReasonLength)
                    compactReason = compactReason.Substring(0, maximumReasonLength - 1) + "…";
                warning += " — " + compactReason;
            }
            Debug.LogWarning("[AIFren Transport] " + warning);
            if (backendWarningLabel != null)
            {
                // Keep the fixed alert line large. A close reason is useful,
                // but it must never shrink or overlap the primary warning.
                backendWarningLabel.text = string.IsNullOrEmpty(compactReason)
                    ? "Warning: backend disconnected"
                    : "Warning: backend disconnected\n<size=65%>" + compactReason + "</size>";
                // The warning must be independent of the Hide-able top bar
                // and remain above ordinary companion controls.
                backendWarningLabel.color = new Color(1f, .20f, .24f, 1f);
                backendWarningLabel.transform.SetAsLastSibling();
                backendWarningLabel.gameObject.SetActive(true);
            }
            RefreshBackendReconnectControl();
        }

        private void ClearBackendDisconnectWarning()
        {
            if (backendWarningLabel != null) backendWarningLabel.gameObject.SetActive(false);
            if (backendReconnectButton != null) backendReconnectButton.gameObject.SetActive(false);
        }

        private void FinishBackendReconnectFailure(string reason)
        {
            backendReconnectInProgress = false;
            string detail = string.IsNullOrWhiteSpace(reason) ? "Reconnect failed." : "Reconnect failed: " + reason;
            Debug.LogWarning("[AIFren Transport] Reconnect: " + detail);
            SetBackendDisconnectWarning(detail);
            RefreshBackendReconnectControl();
        }

        private void RefreshBackendReconnectControl()
        {
            if (backendReconnectButton == null) return;
            bool warningVisible = backendWarningLabel != null && backendWarningLabel.gameObject.activeSelf;
            backendReconnectButton.gameObject.SetActive(warningVisible);
            backendReconnectButton.interactable = !backendReconnectInProgress;
            TMP_Text label = backendReconnectButton.GetComponentInChildren<TMP_Text>();
            if (label != null) label.text = backendReconnectInProgress ? "Reconnecting..." : "Reconnect";
            if (warningVisible) backendReconnectButton.transform.SetAsLastSibling();
        }

        private async void SubmitCurrentText()
        {
            string text = messageInput != null ? messageInput.text.Trim() : string.Empty;
            if (submitInFlight || string.IsNullOrEmpty(text) || client == null || client.State != ConnectionState.Connected)
            {
                return;
            }

            submitInFlight = true;
            Debug.Log("[AIFren Timing] Unity user submit accepted t=" + Time.unscaledTime.ToString("F3"));
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
            dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, !wordReveal.IsComplete);
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

        private void ToggleAvatarQaPanel()
        {
            if (avatarViewPanel == null || avatarViewGrid == null) return;
            bool show = !avatarViewPanel.activeSelf;
            avatarViewPanel.SetActive(show);
            avatarViewGrid.SetActive(show);
            if (show) avatarViewPanel.transform.SetAsLastSibling();
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
            selectedModelAssets.Clear();
            if (modelLibraryPanel != null) modelLibraryPanel.SetActive(false);
            selectedBackgroundAssets.Clear();
            if (backgroundLibraryPanel != null) backgroundLibraryPanel.SetActive(false);
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
            if (!useDirectAvatarPresentation && backgroundImage != null)
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
            if (!useDirectAvatarPresentation) UpdateBackgroundCover();
            if (backgroundTint != null)
            {
                backgroundTint.enabled = !useDirectAvatarPresentation;
                if (!useDirectAvatarPresentation) backgroundTint.color = theme.backgroundTint;
            }

            foreach (Image image in FindObjectsOfType<Image>(true))
            {
                string name = image.gameObject.name;
                if (name.Contains("Background") || name == "Status Dot" || name == "Avatar Presentation Container") continue;
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
                if (text == hiddenDialogueText || hiddenSubtitleBackings.Contains(text)) continue;
                if (text == backendWarningLabel)
                {
                    text.color = new Color(1f, .20f, .24f, 1f);
                    continue;
                }
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
            EnsureHiddenSubtitlePresentation();
            if (pttLabel != null) UpdatePttIndicator("ready");
            ApplyStatus(visibleState.ToLowerInvariant(), detail);
            ApplyAvatarViewerBackground();
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

        private void ApplyAvatarPresentationMode()
        {
            if (backgroundImage != null) backgroundImage.enabled = !useDirectAvatarPresentation;
            if (backgroundTint != null) backgroundTint.enabled = !useDirectAvatarPresentation;
            if (avatarSurface != null) avatarSurface.gameObject.SetActive(!useDirectAvatarPresentation);
            avatarLoader?.SetDirectPresentation(useDirectAvatarPresentation);
            ApplyAvatarViewerBackground();
        }

        private void ApplyAvatarViewerBackground()
        {
            if (!useDirectAvatarPresentation || avatarLoader == null) return;
            AvatarViewerBackground background = CurrentAvatarViewerBackground;
            Texture2D image = background == AvatarViewerBackground.CustomImage
                ? LoadCustomBackground(AvatarViewPortrait)
                : Resources.Load<Texture2D>("Presentation/Backgrounds/bedroom_day");
            avatarLoader.SetDirectBackground(background, image);
        }

        private Texture2D LoadCustomBackground(bool portrait)
        {
            string path = avatarViewerBackgroundState != null ? avatarViewerBackgroundState.GetCustomPath(portrait) : string.Empty;
            Texture2D cached = portrait ? portraitCustomBackground : landscapeCustomBackground;
            if (cached != null && cached.name == path) return cached;
            try
            {
                if (string.IsNullOrWhiteSpace(path) || !System.IO.File.Exists(path)) throw new System.IO.FileNotFoundException();
                byte[] bytes = System.IO.File.ReadAllBytes(path);
                Texture2D texture = new Texture2D(2, 2, TextureFormat.RGBA32, false) { name = path };
                if (!ImageConversion.LoadImage(texture, bytes, false)) throw new InvalidOperationException("Unsupported image data.");
                if (portrait) portraitCustomBackground = texture; else landscapeCustomBackground = texture;
                return texture;
            }
            catch (Exception)
            {
                AvatarViewerBackground fallback = portrait ? AvatarViewerBackground.LightNeutral : AvatarViewerBackground.Bedroom;
                avatarViewerBackgroundState.Set(portrait, fallback, true);
                Debug.LogWarning("Custom viewer background is unavailable; using " + AvatarViewerBackgroundState.Label(fallback) + ".");
                return fallback == AvatarViewerBackground.Bedroom
                    ? Resources.Load<Texture2D>("Presentation/Backgrounds/bedroom_day") : null;
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
                ReleaseUnityPushToTalk();
                return;
            }

            // Background execution keeps Unity updating, but this polling path
            // remains intentionally window-focused. When unfocused, the
            // backend's OS-level listener owns the configured global binding;
            // it avoids a duplicate frontend WebSocket press and works while
            // another application has focus.
            if (!unityPttPressed && PresentationPttInputPolicy.ShouldStart(
                Application.isFocused, Input.GetKeyDown(pushToTalkKey)))
            {
                unityPttPressed = true;
                restoreMessageInputAfterPtt = messageInput != null && messageInput.isFocused;
                Debug.Log("[AIFren PTT] Focused press detected; inputFocused=" + restoreMessageInputAfterPtt + ".");
                presentationAudio?.PlayInterrupt();
                _ = client.SetPushToTalkPressedAsync(true);
            }
            else if (PresentationPttInputPolicy.ShouldRelease(
                unityPttPressed, Application.isFocused, Input.GetKey(pushToTalkKey)))
            {
                ReleaseUnityPushToTalk();
            }
        }

        private void ReleaseUnityPushToTalk()
        {
            if (!unityPttPressed)
            {
                return;
            }

            unityPttPressed = false;
            Debug.Log("[AIFren PTT] Releasing focused press; client=" +
                (client != null ? client.State.ToString() : "missing") + ".");
            if (client != null && client.State == ConnectionState.Connected)
            {
                _ = client.SetPushToTalkPressedAsync(false);
            }
            UpdatePttIndicator("ready");

            if (restoreMessageInputAfterPtt)
            {
                restoreMessageInputAfterPtt = false;
                if (Application.isFocused && settingsPanel != null && !settingsPanel.activeSelf &&
                    messageInput != null && messageInput.interactable)
                {
                    RequestInput(true);
                }
            }
        }

        private void OnApplicationFocus(bool hasFocus)
        {
            Debug.Log("[AIFren Input] Application focus=" + hasFocus + ", pttPressed=" + unityPttPressed + ".");
            if (!hasFocus)
            {
                ReleaseUnityPushToTalk();
            }
        }

        private void OnApplicationPause(bool paused)
        {
            Debug.Log("[AIFren Input] Application paused=" + paused + ", pttPressed=" + unityPttPressed + ".");
            if (paused)
            {
                ReleaseUnityPushToTalk();
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
            bool enabled = connected && !submitInFlight;
            messageInput.interactable = enabled;
            sendButton.interactable = enabled;
            if (lastMessageInputEnabled != enabled)
            {
                lastMessageInputEnabled = enabled;
                Debug.Log("[AIFren Input] Message input enabled=" + enabled +
                    ", connection=" + (client != null ? client.State.ToString() : "missing") +
                    ", submitInFlight=" + submitInFlight + ".");
            }
        }

        private void HandleAvatarLoaded(GameObject avatar)
        {
            avatarAnimation = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarAnimationController>()
                : null;
            if (!useDirectAvatarPresentation && avatarSurface != null)
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

            if (!useDirectAvatarPresentation && avatarSurface != null)
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

            // The container clips a transformed full-avatar texture. It is the
            // composition boundary; the preview camera always keeps the whole
            // avatar in its padded frustum.
            GameObject avatarContainer = new GameObject("Avatar Presentation Container", typeof(RectTransform), typeof(Image), typeof(RectMask2D));
            avatarContainer.transform.SetParent(root, false);
            avatarFrameRect = avatarContainer.GetComponent<RectTransform>();
            Image avatarInputGraphic = avatarContainer.GetComponent<Image>();
            avatarInputGraphic.color = Color.clear;
            avatarPresentationInput = avatarContainer.AddComponent<AvatarPresentationInputSurface>();
            avatarPresentationInput.Dragged += HandleAvatarViewDrag;
            avatarPresentationInput.Scrolled += HandleAvatarViewScroll;
            Stretch(avatarFrameRect, new Vector2(0.10f, 0.15f), new Vector2(0.90f, 0.94f), Vector2.zero, Vector2.zero);
            RawImage avatarFrame = CreateRawImage(avatarContainer.transform, "Avatar Presentation");
            Stretch(avatarFrame.rectTransform, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            avatarFrame.color = new Color(1f, 1f, 1f, 0f);
            avatarFrame.raycastTarget = false;
            avatarSurface = avatarFrame;
            avatarAspectFitter = avatarFrame.gameObject.AddComponent<AspectRatioFitter>();
            avatarAspectFitter.aspectMode = AspectRatioFitter.AspectMode.FitInParent;

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
            // Transport health is a root-level overlay, rather than a child
            // of the Hide-able control bar. It occupies the top-centre space
            // between the left Hide control and right-side Console controls
            // without participating in avatar or foreground layout.
            backendWarningLabel = CreateText(root, string.Empty, 18f, new Color(1f, .20f, .24f, 1f), TextAlignmentOptions.Midline);
            backendWarningLabel.gameObject.name = "Backend Disconnect Warning";
            Stretch(backendWarningLabel.rectTransform, new Vector2(.18f, .855f), new Vector2(.82f, .915f), Vector2.zero, Vector2.zero);
            backendWarningLabel.enableWordWrapping = true;
            backendWarningLabel.enableAutoSizing = true;
            backendWarningLabel.fontSizeMin = 14f;
            backendWarningLabel.fontSizeMax = 20f;
            backendWarningLabel.overflowMode = TextOverflowModes.Overflow;
            backendWarningLabel.raycastTarget = false;
            backendWarningLabel.gameObject.SetActive(false);
            backendReconnectButton = CreateButton(root, "Reconnect", Panel);
            backendReconnectButton.gameObject.name = "Backend Reconnect";
            Stretch(backendReconnectButton.GetComponent<RectTransform>(), new Vector2(.42f, .815f), new Vector2(.58f, .85f), Vector2.zero, Vector2.zero);
            backendReconnectButton.onClick.AddListener(Reconnect);
            backendReconnectButton.gameObject.SetActive(false);
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
            dialogueTextLabel.fontStyle = FontStyles.Bold;
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
            messageInput = CreateInputField(inputCard.transform, multiline: true);
            messageInputRect = messageInput.GetComponent<RectTransform>();
            messageInput.onSubmit.AddListener(HandleInputSubmit);
            messageInput.onSelect.AddListener(_ => SetMessageInputFocused(true));
            messageInput.onDeselect.AddListener(HandleInputDeselect);
            Stretch(messageInputRect, new Vector2(0.025f, 0.18f), new Vector2(0.84f, 0.82f), Vector2.zero, Vector2.zero);
            sendButton = CreateButton(inputCard.transform, "Send", new Color(0.48f, 0.28f, 0.63f, 1f));
            sendButtonRect = sendButton.GetComponent<RectTransform>();
            Stretch(sendButtonRect, new Vector2(0.855f, 0.18f), new Vector2(0.975f, 0.82f), Vector2.zero, Vector2.zero);
            sendButton.onClick.AddListener(SubmitCurrentText);

            GameObject hiddenDialogueViewportObject = new GameObject("Hidden Dialogue Subtitle", typeof(RectTransform), typeof(CanvasGroup));
            hiddenDialogueViewportObject.transform.SetParent(root, false);
            hiddenDialogueViewport = hiddenDialogueViewportObject.GetComponent<RectTransform>();
            LayoutHiddenSubtitleRegion();
            hiddenDialogueCanvasGroup = hiddenDialogueViewportObject.GetComponent<CanvasGroup>();
            hiddenDialogueCanvasGroup.alpha = 0f; hiddenDialogueCanvasGroup.interactable = false; hiddenDialogueCanvasGroup.blocksRaycasts = false;
            // The reserved region is deliberately top-aligned: revealing a
            // wrapped line then grows downward instead of recentering all of
            // the already-visible lines upward.
            hiddenDialogueText = CreateText(hiddenDialogueViewport, string.Empty, 35f, new Color(.98f,.62f,.78f,1f), TextAlignmentOptions.Top);
            hiddenDialogueText.enableWordWrapping = true;
            hiddenDialogueText.fontStyle = FontStyles.Bold;
            hiddenDialogueText.lineSpacing = -5f;
            hiddenDialogueText.paragraphSpacing = -3f;
            hiddenDialogueText.margin = new Vector4(18f, 12f, 18f, 12f);
            hiddenDialogueText.overflowMode = TextOverflowModes.Masking;
            Stretch(hiddenDialogueText.rectTransform, Vector2.zero, Vector2.one, new Vector2(18f, 10f), new Vector2(-18f, -10f));
            // Deterministic fansub-style edge: four tiny black text copies
            // behind the pink front glyphs. They share the same CanvasGroup,
            // reveal text, sizing and layout, so no TMP material outline is
            // relied upon for visible contrast.
            // Keep the backing copies tightly and symmetrically around the
            // foreground glyphs. A larger one-pixel offset read as duplicate
            // lettering rather than a clean subtitle edge at player scale.
            foreach (Vector2 offset in new[] { new Vector2(-.45f, 0f), new Vector2(.45f, 0f), new Vector2(0f, -.45f), new Vector2(0f, .45f) })
            {
                TMP_Text backing = CreateText(hiddenDialogueViewport, string.Empty, 35f, Color.black, TextAlignmentOptions.Top);
                backing.fontStyle = FontStyles.Bold; backing.enableWordWrapping = true; backing.raycastTarget = false;
                backing.lineSpacing = -5f; backing.paragraphSpacing = -3f; backing.margin = new Vector4(18f, 12f, 18f, 12f);
                Stretch(backing.rectTransform, Vector2.zero, Vector2.one, new Vector2(18f, 10f), new Vector2(-18f, -10f));
                backing.rectTransform.anchoredPosition = offset;
                backing.transform.SetAsFirstSibling();
                Material backingMaterial = new Material(backing.fontSharedMaterial) { name = "AIFren Hidden Subtitle Black Backing" };
                backing.fontMaterial = backingMaterial;
                if (backingMaterial.HasProperty(ShaderUtilities.ID_FaceColor)) backingMaterial.SetColor(ShaderUtilities.ID_FaceColor, Color.black);
                backing.color = Color.black;
                hiddenSubtitleBackings.Add(backing);
                hiddenSubtitleBackingMaterials.Add(backingMaterial);
            }
            Shadow hiddenDialogueShadow = hiddenDialogueText.gameObject.AddComponent<Shadow>();
            hiddenDialogueShadow.effectColor = new Color(0f, 0f, 0f, .72f);
            hiddenDialogueShadow.effectDistance = new Vector2(1.25f, -1.25f);
            EnsureHiddenSubtitlePresentation();
            hiddenDialogueViewportObject.SetActive(false);
            hiddenSubtitlePresenter = new HiddenSubtitlePresenter(
                new TmpHiddenSubtitleRenderTarget(hiddenDialogueViewportObject, hiddenDialogueCanvasGroup,
                    hiddenDialogueViewport, hiddenDialogueText, hiddenSubtitleBackings));

            modalScrim = CreatePanel(root, "Modal Scrim", new Color(0f, 0f, 0f, 0.70f));
            Stretch(modalScrim.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            modalScrim.SetActive(false);
            historyPanel = CreateHistoryPanel(root);
            consolePanel = CreateConsolePanel(root);
            settingsPanel = CreateSettingsPanel(root);
            displayConfirmPanel = CreateDisplayConfirmationPanel(root);
            avatarViewGrid = CreateAvatarViewGrid(root);
            avatarViewPanel = CreateAvatarViewPanel(root);
            startupPanel = CreateStartupPanel(root);
            historyPanel.SetActive(false);
            consolePanel.SetActive(false);
            settingsPanel.SetActive(false);
            displayConfirmPanel.SetActive(false);
            avatarViewGrid.SetActive(false);
            avatarViewPanel.SetActive(false);
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

        private GameObject CreateAvatarViewGrid(Transform root)
        {
            GameObject grid = new GameObject("Avatar View Grid", typeof(RectTransform));
            grid.transform.SetParent(root, false);
            Stretch(grid.GetComponent<RectTransform>(), Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            for (int index = 1; index <= 2; index++)
            {
                Image vertical = CreateImage(grid.transform, "Avatar View Vertical Grid", new Color(1f, 1f, 1f, .14f));
                vertical.raycastTarget = false;
                Stretch(vertical.rectTransform, new Vector2(index / 3f, 0f), new Vector2(index / 3f, 1f), new Vector2(-.5f, 0f), new Vector2(.5f, 0f));
                Image horizontal = CreateImage(grid.transform, "Avatar View Horizontal Grid", new Color(1f, 1f, 1f, .14f));
                horizontal.raycastTarget = false;
                Stretch(horizontal.rectTransform, new Vector2(0f, index / 3f), new Vector2(1f, index / 3f), new Vector2(0f, -.5f), new Vector2(0f, .5f));
            }
            Image crossV = CreateImage(grid.transform, "Avatar View Crosshair", new Color(1f, .75f, 1f, .5f)); crossV.raycastTarget = false;
            Stretch(crossV.rectTransform, new Vector2(.5f, 0f), new Vector2(.5f, 1f), new Vector2(-.75f, 0f), new Vector2(.75f, 0f));
            Image crossH = CreateImage(grid.transform, "Avatar View Crosshair", new Color(1f, .75f, 1f, .5f)); crossH.raycastTarget = false;
            Stretch(crossH.rectTransform, new Vector2(0f, .5f), new Vector2(1f, .5f), new Vector2(0f, -.75f), new Vector2(0f, .75f));
            return grid;
        }

        private GameObject CreateAvatarViewPanel(Transform root)
        {
            GameObject panel = CreatePanel(root, "Avatar View", new Color(.055f, .05f, .11f, .95f));
            Stretch(panel.GetComponent<RectTransform>(), new Vector2(.025f, .16f), new Vector2(.30f, .53f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(panel.transform, "Avatar View", 21f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.06f, .84f), new Vector2(.70f, .97f), Vector2.zero, Vector2.zero);
            TMP_Text hint = CreateText(panel.transform, "Drag to position · wheel to zoom", 12f, theme.secondaryText, TextAlignmentOptions.MidlineLeft);
            Stretch(hint.rectTransform, new Vector2(.06f, .74f), new Vector2(.94f, .84f), Vector2.zero, Vector2.zero);
            CreateAvatarViewControl(panel.transform, "X", .52f, -AvatarPresentationTransform.MaximumTranslation, AvatarPresentationTransform.MaximumTranslation, out avatarViewXSlider, out avatarViewXInput);
            CreateAvatarViewControl(panel.transform, "Y", .34f, -AvatarPresentationTransform.MaximumTranslation, AvatarPresentationTransform.MaximumTranslation, out avatarViewYSlider, out avatarViewYInput);
            CreateAvatarViewControl(panel.transform, "Scale", .16f, 1f, AvatarPresentationTransform.MaximumScale, out avatarViewScaleSlider, out avatarViewScaleInput);
            Button save = CreateButton(panel.transform, "Save", Accent); Stretch(save.GetComponent<RectTransform>(), new Vector2(.06f, .03f), new Vector2(.29f, .13f), Vector2.zero, Vector2.zero); save.onClick.AddListener(SaveAvatarViewEditor);
            Button cancel = CreateButton(panel.transform, "Cancel", Panel); Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.32f, .03f), new Vector2(.58f, .13f), Vector2.zero, Vector2.zero); cancel.onClick.AddListener(CancelAvatarViewEditor);
            Button reset = CreateButton(panel.transform, "Reset", Panel); Stretch(reset.GetComponent<RectTransform>(), new Vector2(.61f, .03f), new Vector2(.94f, .13f), Vector2.zero, Vector2.zero); reset.onClick.AddListener(ResetAvatarViewEditor);
            avatarViewXSlider.onValueChanged.AddListener(value => SetAvatarViewValue(0, value));
            avatarViewYSlider.onValueChanged.AddListener(value => SetAvatarViewValue(1, value));
            avatarViewScaleSlider.onValueChanged.AddListener(value => SetAvatarViewValue(2, value));
            avatarViewXInput.onEndEdit.AddListener(value => SetAvatarViewNumeric(0, value));
            avatarViewYInput.onEndEdit.AddListener(value => SetAvatarViewNumeric(1, value));
            avatarViewScaleInput.onEndEdit.AddListener(value => SetAvatarViewNumeric(2, value));
            return panel;
        }

        private void CreateAvatarViewControl(Transform parent, string label, float top, float minimum, float maximum, out Slider slider, out TMP_InputField input)
        {
            TMP_Text text = CreateText(parent, label, 14f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(text.rectTransform, new Vector2(.06f, top + .07f), new Vector2(.18f, top + .15f), Vector2.zero, Vector2.zero);
            slider = CreateSlider(parent, minimum, maximum, minimum);
            Stretch(slider.GetComponent<RectTransform>(), new Vector2(.18f, top + .05f), new Vector2(.69f, top + .14f), Vector2.zero, Vector2.zero);
            input = CreateInputField(parent); input.characterLimit = 8;
            Stretch(input.GetComponent<RectTransform>(), new Vector2(.72f, top + .03f), new Vector2(.94f, top + .16f), Vector2.zero, Vector2.zero);
        }

        private bool AvatarViewPortrait => currentDisplaySettings != null && PresentationDisplaySettingsPolicy.IsPortrait(currentDisplaySettings.layoutMode, Screen.width, Screen.height);

        private void EnterAvatarViewEditor()
        {
            if (avatarViewEditing) return;
            avatarViewPortraitSnapshot = avatarPresentationState.GetValues(true);
            avatarViewLandscapeSnapshot = avatarPresentationState.GetValues(false);
            avatarViewEditing = true;
            CloseSettingsPanel();
            avatarViewGrid.SetActive(true); avatarViewPanel.SetActive(true); avatarViewPanel.transform.SetAsLastSibling();
            SyncAvatarViewControls();
        }

        private void SaveAvatarViewEditor()
        {
            avatarPresentationState.Commit(AvatarViewPortrait);
            avatarPresentationState.SetValues(!AvatarViewPortrait, !AvatarViewPortrait ? avatarViewPortraitSnapshot : avatarViewLandscapeSnapshot, false);
            ExitAvatarViewEditor();
        }

        private void CancelAvatarViewEditor()
        {
            avatarPresentationState.SetValues(true, avatarViewPortraitSnapshot, false);
            avatarPresentationState.SetValues(false, avatarViewLandscapeSnapshot, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait);
            ExitAvatarViewEditor();
        }

        private void ResetAvatarViewEditor()
        {
            avatarPresentationState.Reset(AvatarViewPortrait, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait); SyncAvatarViewControls();
        }

        private void ExitAvatarViewEditor()
        {
            avatarViewEditing = false; avatarViewGrid.SetActive(false); avatarViewPanel.SetActive(false);
        }

        private void SetAvatarViewValue(int field, float value)
        {
            if (suppressAvatarViewCallbacks || !avatarViewEditing) return;
            AvatarPresentationValues values = avatarPresentationState.GetValues(AvatarViewPortrait);
            if (field == 0) values.x = value; else if (field == 1) values.y = value; else values.scale = value;
            avatarPresentationState.SetValues(AvatarViewPortrait, values, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait); SyncAvatarViewControls();
        }

        private void SetAvatarViewNumeric(int field, string text)
        {
            if (!float.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out float value)) { SyncAvatarViewControls(); return; }
            SetAvatarViewValue(field, value);
        }

        private void HandleAvatarViewDrag(Vector2 delta)
        {
            if (!avatarViewEditing) return;
            AvatarPresentationValues values = avatarPresentationState.GetValues(AvatarViewPortrait);
            values.x += delta.x / Mathf.Max(1f, Screen.width);
            values.y += delta.y / Mathf.Max(1f, Screen.height);
            avatarPresentationState.SetValues(AvatarViewPortrait, values, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait); SyncAvatarViewControls();
        }

        private void HandleAvatarViewScroll(float delta)
        {
            if (!avatarViewEditing) return;
            SetAvatarViewValue(2, avatarPresentationState.GetValues(AvatarViewPortrait).scale + delta * .08f);
        }

        private void SyncAvatarViewControls()
        {
            if (!avatarViewEditing) return;
            AvatarPresentationValues values = avatarPresentationState.GetValues(AvatarViewPortrait);
            suppressAvatarViewCallbacks = true;
            avatarViewXSlider.SetValueWithoutNotify(values.x); avatarViewYSlider.SetValueWithoutNotify(values.y); avatarViewScaleSlider.SetValueWithoutNotify(values.scale);
            avatarViewXInput.SetTextWithoutNotify(values.x.ToString("0.00", CultureInfo.InvariantCulture));
            avatarViewYInput.SetTextWithoutNotify(values.y.ToString("0.00", CultureInfo.InvariantCulture));
            avatarViewScaleInput.SetTextWithoutNotify(values.scale.ToString("0.00", CultureInfo.InvariantCulture));
            suppressAvatarViewCallbacks = false;
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
            alwaysOnTopToggle = CreateToggle(display, "Always on top (Linux)", alwaysOnTop);
            PlaceTop(alwaysOnTopToggle.GetComponent<RectTransform>(), y, 34f);
            alwaysOnTopToggle.onValueChanged.AddListener(SetAlwaysOnTop);
            y -= 42f;
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
            AddSettingsHeading(appearance, "THEME", ref y); Button themeButton = CreateButton(appearance, "Theme: Light / Dark", Panel); PlaceTop(themeButton.GetComponent<RectTransform>(), y, StandardControlHeight); themeButton.onClick.AddListener(ToggleTheme); y -= 58f;
            AddSettingsHeading(appearance, "AVATAR MODEL", ref y);
            avatarModelValue = AddSettingsValue(appearance, "Current model", ref y);
            Button changeModelButton = CreateButton(appearance, "Change Model…", Panel); PlaceTop(changeModelButton.GetComponent<RectTransform>(), y, StandardControlHeight, .55f, .74f); changeModelButton.onClick.AddListener(OpenModelLibrary);
            Button resetModelButton = CreateButton(appearance, "Reset to Default", Panel); PlaceTop(resetModelButton.GetComponent<RectTransform>(), y, StandardControlHeight, .76f, .95f); resetModelButton.onClick.AddListener(() => ResetAvatarModel()); y -= 52f;
            AddSettingsHeading(appearance, "AVATAR VIEW", ref y);
            Button avatarViewButton = CreateButton(appearance, "Edit Avatar View", Panel);
            PlaceTop(avatarViewButton.GetComponent<RectTransform>(), y, StandardControlHeight);
            avatarViewButton.onClick.AddListener(EnterAvatarViewEditor);
            y -= 54f;
            AddSettingsHeading(appearance, "VIEWER BACKGROUND", ref y);
            avatarViewerBackgroundValue = AddSettingsChoice(appearance, "Current background", ref y, CycleAvatarViewerBackground);
            Button customBackgroundButton = CreateButton(appearance, "Change Background", Panel); PlaceTop(customBackgroundButton.GetComponent<RectTransform>(), y, StandardControlHeight); customBackgroundButton.onClick.AddListener(OpenBackgroundLibrary); y -= 56f;
            AddSettingsHeading(appearance, "DIALOGUE / UI", ref y);
            hiddenDialogueToggle = CreateToggle(appearance, "Show dialogue text when UI is hidden", showDialogueWhenHidden);
            PlaceTop(hiddenDialogueToggle.GetComponent<RectTransform>(), y, 34f);
            hiddenDialogueToggle.onValueChanged.AddListener(SetShowDialogueWhenHidden);
            y -= 46f;
            CreateBackgroundLibraryPanel(panel.transform);
            CreateModelLibraryPanel(panel.transform);
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
            if (alwaysOnTopToggle != null) alwaysOnTopToggle.SetIsOnWithoutNotify(alwaysOnTop);
            antiAliasingValue.text = pendingDisplaySettings.antiAliasing == 0 ? "Off" : pendingDisplaySettings.antiAliasing + "x MSAA";
            uiScaleSlider.SetValueWithoutNotify(pendingDisplaySettings.uiScale);
            if (graphicsQualityValue != null) graphicsQualityValue.text = graphicsQuality.ToString();
            if (avatarRenderScaleValue != null) avatarRenderScaleValue.text = avatarRenderScale.ToString("0.0") + "x";
            if (avatarViewerBackgroundValue != null)
                avatarViewerBackgroundValue.text = FriendlyBackgroundName();
            if (avatarModelValue != null)
                avatarModelValue.text = FriendlyModelName();
            if (pttBindValue != null)
            {
                pttBindValue.text = pushToTalkKey.ToString();
            }
        }

        private string FriendlyModelName()
        {
            if (avatarLoader == null || string.IsNullOrWhiteSpace(avatarLoader.ActiveModelPath) || avatarLoader.ActiveModelPath == "Bundled model")
                return "Bundled avatar";
            ManagedAssetRecord record = managedAssetLibrary?.Assets(ManagedAssetLibrary.ModelKind)
                .Find(asset => asset.path == avatarLoader.ActiveModelPath);
            return record != null && !string.IsNullOrWhiteSpace(record.displayName)
                ? record.displayName : "Imported avatar";
        }

        private string FriendlyBackgroundName()
        {
            if (CurrentAvatarViewerBackground != AvatarViewerBackground.CustomImage)
                return AvatarViewerBackgroundState.Label(CurrentAvatarViewerBackground);
            string path = avatarViewerBackgroundState != null ? avatarViewerBackgroundState.GetCustomPath(AvatarViewPortrait) : string.Empty;
            ManagedAssetRecord record = managedAssetLibrary?.Assets(ManagedAssetLibrary.BackgroundKind)
                .Find(asset => asset.path == path);
            if (record == null) return "Custom image";
            List<ManagedAssetRecord> backgrounds = managedAssetLibrary.Assets(ManagedAssetLibrary.BackgroundKind);
            backgrounds.Sort(CompareManagedAssetNames);
            Dictionary<string, int> counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (ManagedAssetRecord candidate in backgrounds)
            {
                string visibleName = DisambiguatedManagedName(candidate, counts, "Imported background");
                if (candidate.id == record.id) return visibleName;
            }
            return "Imported background";
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
            DisplayInfo selectedDisplay = displayLayout[pendingDisplaySettings.displayIndex];
            Vector2Int selectedResolution = PresentationDisplaySettingsPolicy.ResolutionForSelectedDisplay(
                selectedDisplay.width,
                selectedDisplay.height,
                Screen.width,
                Screen.height
            );
            pendingDisplaySettings.width = selectedResolution.x;
            pendingDisplaySettings.height = selectedResolution.y;
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

        private AvatarViewerBackground CurrentAvatarViewerBackground => avatarViewerBackgroundState != null
            ? avatarViewerBackgroundState.Get(AvatarViewPortrait)
            : AvatarViewPortrait ? AvatarViewerBackground.LightNeutral : AvatarViewerBackground.Bedroom;

        private void CycleAvatarViewerBackground()
        {
            AvatarViewerBackground next = (AvatarViewerBackground)(((int)CurrentAvatarViewerBackground + 1) % 4);
            avatarViewerBackgroundState.Set(AvatarViewPortrait, next, true);
            ApplyAvatarViewerBackground();
            RefreshDisplaySettingsUi();
        }

        // Both asset libraries use this same scrollable flexible grid.  Tiles
        // participate only in the layout group, so imports/deletions cannot
        // retain stale hand-authored positions or overlap a later row.
        private Transform CreateLibraryTileGrid(Transform parent, string name)
        {
            GameObject viewport = new GameObject(name + " Viewport", typeof(RectTransform), typeof(RectMask2D), typeof(ScrollRect));
            viewport.transform.SetParent(parent, false);
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(.05f, .08f), new Vector2(.95f, .82f), Vector2.zero, Vector2.zero);

            GameObject content = new GameObject(name, typeof(RectTransform), typeof(GridLayoutGroup), typeof(ContentSizeFitter));
            content.transform.SetParent(viewport.transform, false);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 1f); contentRect.anchorMax = new Vector2(1f, 1f); contentRect.pivot = new Vector2(.5f, 1f); contentRect.sizeDelta = Vector2.zero;
            GridLayoutGroup grid = content.GetComponent<GridLayoutGroup>();
            grid.cellSize = new Vector2(164f, 142f); grid.spacing = new Vector2(12f, 12f);
            grid.padding = new RectOffset(10, 10, 10, 10); grid.childAlignment = TextAnchor.UpperLeft;
            grid.constraint = GridLayoutGroup.Constraint.Flexible;
            ContentSizeFitter fitter = content.GetComponent<ContentSizeFitter>();
            fitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained; fitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

            ScrollRect scroll = viewport.GetComponent<ScrollRect>();
            scroll.viewport = viewport.GetComponent<RectTransform>(); scroll.content = contentRect;
            scroll.horizontal = false; scroll.vertical = true; scroll.movementType = ScrollRect.MovementType.Clamped; scroll.scrollSensitivity = 34f;
            AddThinScrollbar(viewport.transform, scroll, .97f, .99f);
            return content.transform;
        }

        private static float ClearLibraryTiles(Transform tiles)
        {
            ScrollRect scroll = tiles.GetComponentInParent<ScrollRect>();
            float position = scroll != null ? scroll.verticalNormalizedPosition : 1f;
            // Reparenting while using Transform's foreach enumerator skips
            // children, leaving old cards visible on the next rebuild.
            for (int index = tiles.childCount - 1; index >= 0; index--)
            {
                Transform child = tiles.GetChild(index);
                child.gameObject.SetActive(false);
                child.SetParent(null);
                if (Application.isPlaying)
                {
                    Destroy(child.gameObject);
                }
                else
                {
                    DestroyImmediate(child.gameObject);
                }
            }
            return position;
        }

        private static void RestoreLibraryScroll(Transform tiles, float position)
        {
            Canvas.ForceUpdateCanvases();
            LayoutRebuilder.ForceRebuildLayoutImmediate(tiles as RectTransform);
            ScrollRect scroll = tiles.GetComponentInParent<ScrollRect>();
            if (scroll != null) scroll.verticalNormalizedPosition = position;
        }

        private void CreateModelLibraryPanel(Transform parent)
        {
            modelLibraryPanel = CreatePanel(parent, "Avatar Model Library", new Color(.08f,.06f,.13f,.98f));
            Stretch(modelLibraryPanel.GetComponent<RectTransform>(), new Vector2(.12f,.14f),new Vector2(.88f,.86f),Vector2.zero,Vector2.zero);
            TMP_Text title=CreateText(modelLibraryPanel.transform,"Avatar Model",24f,Ink,TextAlignmentOptions.MidlineLeft); Stretch(title.rectTransform,new Vector2(.06f,.87f),new Vector2(.34f,.96f),Vector2.zero,Vector2.zero);
            Button import=CreateButton(modelLibraryPanel.transform,"Import",Panel); Stretch(import.GetComponent<RectTransform>(),new Vector2(.36f,.87f),new Vector2(.52f,.96f),Vector2.zero,Vector2.zero); import.onClick.AddListener(ChangeAvatarModel);
            Button back=CreateButton(modelLibraryPanel.transform,"Back",Panel); Stretch(back.GetComponent<RectTransform>(),new Vector2(.76f,.87f),new Vector2(.94f,.96f),Vector2.zero,Vector2.zero); back.onClick.AddListener(()=>{selectedModelAssets.Clear();modelLibraryPanel.SetActive(false);});
            deleteModelAssetsButton=CreateButton(modelLibraryPanel.transform,"Delete Selected",new Color(.42f,.16f,.22f,1f)); Stretch(deleteModelAssetsButton.GetComponent<RectTransform>(),new Vector2(.54f,.87f),new Vector2(.74f,.96f),Vector2.zero,Vector2.zero); deleteModelAssetsButton.onClick.AddListener(OpenModelDeleteConfirmation);
            modelLibraryTiles=CreateLibraryTileGrid(modelLibraryPanel.transform,"Model Library Tiles");
            deleteModelAssetsButton.transform.SetAsLastSibling();
            LogDeleteHeaderState("model", deleteModelAssetsButton, selectedModelAssets.Count, "created");
            BuildModelLibraryTiles(); modelLibraryPanel.SetActive(false);
        }

        private void BuildModelLibraryTiles()
        {
            float scrollPosition=ClearLibraryTiles(modelLibraryTiles);
            Button bundled=CreateButton(modelLibraryTiles,"Bundled avatar",Panel); AddModelTilePreview(bundled, null); bundled.onClick.AddListener(()=>{selectedModelAssets.Clear(); ResetAvatarModel(); RefreshModelLibrarySelection();});
            List<ManagedAssetRecord> records = managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind);
            records.Sort((left, right) => { int name = string.Compare(left.displayName, right.displayName, StringComparison.OrdinalIgnoreCase); return name != 0 ? name : string.CompareOrdinal(left.id, right.id); });
            Dictionary<string, int> nameCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach(ManagedAssetRecord asset in records) { ManagedAssetRecord selected=asset; string baseName=string.IsNullOrWhiteSpace(asset.displayName)?"Imported model":asset.displayName; nameCounts.TryGetValue(baseName,out int occurrence); occurrence++; nameCounts[baseName]=occurrence; string visibleName=occurrence==1?baseName:baseName+" ("+occurrence+")"; Button tile=CreateButton(modelLibraryTiles,visibleName,Panel); AddModelTilePreview(tile, asset.thumbnailPath); tile.gameObject.name="Managed Model "+asset.id; tile.onClick.AddListener(()=>{if(Input.GetKey(KeyCode.LeftControl)||Input.GetKey(KeyCode.RightControl)){ToggleModelDeletionSelection(selected.id);return;}SelectOnlyModelForDeletion(selected.id);RequestManagedAvatarModel(selected);}); if(VrmThumbnailGenerator.NeedsGeneration(managedAssetLibrary.ThumbnailPath(asset.id))) _=EnsureModelThumbnailAsync(asset,modelApplyGeneration); }
            RestoreLibraryScroll(modelLibraryTiles,scrollPosition); RefreshModelLibrarySelection();
        }

        private void OpenModelLibrary(){ selectedModelAssets.Clear(); BuildModelLibraryTiles(); modelLibraryPanel.SetActive(true); modelLibraryPanel.transform.SetAsLastSibling(); LogDeleteHeaderState("model",deleteModelAssetsButton,selectedModelAssets.Count,"opened"); }
        private void AddModelTilePreview(Button tile, string thumbnailPath)
        {
            RawImage preview = CreateRawImage(tile.transform, "Model Preview");
            preview.raycastTarget = false;
            Stretch(preview.rectTransform, new Vector2(.08f,.30f), new Vector2(.92f,.92f), Vector2.zero, Vector2.zero);
            preview.color = new Color(.33f,.28f,.43f,1f);
            PositionLibraryTileLabel(tile);
            try { if (!string.IsNullOrWhiteSpace(thumbnailPath) && System.IO.File.Exists(thumbnailPath)) { Texture2D texture=new Texture2D(2,2); if(ImageConversion.LoadImage(texture,System.IO.File.ReadAllBytes(thumbnailPath),false)) { preview.texture=texture; preview.color=Color.white; } } } catch { }
        }

        private static void PositionLibraryTileLabel(Button tile)
        {
            TMP_Text label = tile.GetComponentInChildren<TMP_Text>();
            if (label != null) { label.raycastTarget = false; label.enableWordWrapping = false; label.overflowMode = TextOverflowModes.Ellipsis; label.enableAutoSizing = true; label.fontSizeMin = 12f; label.fontSizeMax = 17f; Stretch(label.rectTransform, new Vector2(.07f,.06f), new Vector2(.93f,.25f), Vector2.zero, Vector2.zero); }
        }
        private void ToggleModelDeletionSelection(string assetId)
        {
            if (!selectedModelAssets.Add(assetId)) selectedModelAssets.Remove(assetId);
            Debug.Log("[AIFren Asset Library] model Ctrl-click selection id=" + assetId + " count=" + selectedModelAssets.Count);
            RefreshModelLibrarySelection();
        }
        private void SelectOnlyModelForDeletion(string assetId)
        {
            selectedModelAssets.Clear();
            selectedModelAssets.Add(assetId);
            Debug.Log("[AIFren Asset Library] model regular-click selection id=" + assetId + " count=1");
            RefreshModelLibrarySelection();
        }
        private void RequestManagedAvatarModel(ManagedAssetRecord asset, bool removeOnFailure = false)
        {
            if (asset == null || string.IsNullOrWhiteSpace(asset.id)) return;
            if (!modelApplyInProgress && avatarLoader != null && avatarLoader.ActiveModelPath == asset.path)
            {
                // Repeatedly clicking the already active card is deliberately
                // idempotent: keep its delete-selection state, but do not load.
                RefreshModelLibrarySelection();
                return;
            }
            if (modelApplyInProgress &&
                (modelApplyInFlightId == asset.id || (pendingModelApply != null && pendingModelApply.id == asset.id)))
                return;

            pendingModelApply = asset;
            pendingModelApplyRemoveOnFailure = removeOnFailure;
            modelApplyGeneration++;
            if (!modelApplyInProgress) _ = ProcessManagedAvatarModelRequestsAsync();
        }

        private async Task ProcessManagedAvatarModelRequestsAsync()
        {
            modelApplyInProgress = true;
            try
            {
                while (pendingModelApply != null)
                {
                    ManagedAssetRecord asset = pendingModelApply;
                    bool removeOnFailure = pendingModelApplyRemoveOnFailure;
                    int request = modelApplyGeneration;
                    pendingModelApply = null;
                    pendingModelApplyRemoveOnFailure = false;
                    modelApplyInFlightId = asset.id;
                    ApplyStatus("connecting", "Loading visual avatar model...");
                    bool loaded = avatarLoader != null && await avatarLoader.LoadAvatarFromPathAsync(asset.path);

                    // A later click supersedes every UI/state side effect from
                    // this completion. The loop will then load only the latest
                    // pending asset, rather than racing parallel avatar swaps.
                    if (request != modelApplyGeneration) continue;
                    if (!loaded)
                    {
                        if (removeOnFailure) managedAssetLibrary.Delete(ManagedAssetLibrary.ModelKind, new[] { asset.id });
                        ApplyStatus("error", avatarLoader != null ? avatarLoader.LastError : "Avatar loader is unavailable.");
                        if (modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy) BuildModelLibraryTiles();
                        continue;
                    }
                    PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, asset.path);
                    managedAssetLibrary.SetDisplayName(asset.id, avatarLoader.LastLoadedModelName);
                    PlayerPrefs.Save();
                    ApplyStatus("ready", "Visual avatar model loaded.");
                    RefreshModelLibrarySelection();
                    RefreshDisplaySettingsUi();
                    _ = EnsureModelThumbnailAsync(asset, request);
                }
            }
            finally
            {
                modelApplyInFlightId = null;
                modelApplyInProgress = false;
                RefreshModelLibrarySelection();
            }
        }
        private void RefreshModelLibrarySelection(){ if(modelLibraryPanel==null)return; string active=avatarLoader!=null?avatarLoader.ActiveModelPath:string.Empty; foreach(Button tile in modelLibraryPanel.GetComponentsInChildren<Button>(true)){bool imported=tile.gameObject.name.StartsWith("Managed Model ");bool bundled=tile.gameObject.name=="Bundled avatar";if(!imported&&!bundled)continue;bool selected=imported&&selectedModelAssets.Contains(tile.gameObject.name.Substring("Managed Model ".Length));bool on=imported?active==managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind).Find(x=>tile.gameObject.name=="Managed Model "+x.id)?.path:bundled&&(string.IsNullOrEmpty(active)||active=="Bundled model");SetLibraryTileVisual(tile,on,selected);} UpdateDeleteSelectedHeader(deleteModelAssetsButton,selectedModelAssets.Count); LogDeleteHeaderState("model",deleteModelAssetsButton,selectedModelAssets.Count,"refresh"); }

        private void OpenModelDeleteConfirmation()
        {
            if(selectedModelAssets.Count==0)return;
            if(modelDeleteConfirmPanel==null)
            {
                modelDeleteConfirmPanel=CreatePanel(modelLibraryPanel.transform,"Delete Model Confirmation",new Color(.08f,.06f,.13f,.99f)); Stretch(modelDeleteConfirmPanel.GetComponent<RectTransform>(),new Vector2(.24f,.34f),new Vector2(.76f,.66f),Vector2.zero,Vector2.zero);
                TMP_Text text=CreateText(modelDeleteConfirmPanel.transform,string.Empty,20f,Ink,TextAlignmentOptions.Center);text.gameObject.name="Message";Stretch(text.rectTransform,new Vector2(.08f,.42f),new Vector2(.92f,.88f),Vector2.zero,Vector2.zero);
                Button cancel=CreateButton(modelDeleteConfirmPanel.transform,"Cancel",Panel);Stretch(cancel.GetComponent<RectTransform>(),new Vector2(.08f,.12f),new Vector2(.46f,.32f),Vector2.zero,Vector2.zero);cancel.onClick.AddListener(()=>modelDeleteConfirmPanel.SetActive(false));
                Button confirm=CreateButton(modelDeleteConfirmPanel.transform,"Delete",new Color(.42f,.16f,.22f,1f));Stretch(confirm.GetComponent<RectTransform>(),new Vector2(.54f,.12f),new Vector2(.92f,.32f),Vector2.zero,Vector2.zero);confirm.onClick.AddListener(DeleteSelectedModels);
            }
            modelDeleteConfirmPanel.transform.Find("Message").GetComponent<TMP_Text>().text="Delete "+selectedModelAssets.Count+" imported model"+(selectedModelAssets.Count==1?"?":"s?"); modelDeleteConfirmPanel.SetActive(true);modelDeleteConfirmPanel.transform.SetAsLastSibling();
        }

        private void DeleteSelectedModels()
        {
            string activePath=avatarLoader!=null?avatarLoader.ActiveModelPath:string.Empty;
            bool activeWasDeleted=false;
            foreach(ManagedAssetRecord asset in managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind)) if(selectedModelAssets.Contains(asset.id)&&asset.path==activePath){activeWasDeleted=true;break;}
            if(activeWasDeleted && !ResetAvatarModel()) return;
            managedAssetLibrary.Delete(ManagedAssetLibrary.ModelKind, selectedModelAssets); selectedModelAssets.Clear();
            if(modelDeleteConfirmPanel!=null)modelDeleteConfirmPanel.SetActive(false);
            BuildModelLibraryTiles(); RefreshDisplaySettingsUi();
        }

        private async void ChangeAvatarModel()
        {
            string path = await LinuxNativeFilePicker.PickAsync("Choose VRM avatar", "VRM models | *.vrm");
            if (string.IsNullOrWhiteSpace(path)) return;
            if (!string.Equals(System.IO.Path.GetExtension(path), ".vrm", StringComparison.OrdinalIgnoreCase))
            {
                ApplyStatus("error", "Choose a .vrm avatar model.");
                return;
            }
            LinuxNativeFilePicker.Remember(path);
            if (!managedAssetLibrary.TryImport(path, ManagedAssetLibrary.ModelKind, out ManagedAssetRecord asset, out string importError))
            {
                ApplyStatus("error", "Could not import VRM: " + importError); return;
            }
            SelectOnlyModelForDeletion(asset.id);
            if (modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy) BuildModelLibraryTiles();
            RequestManagedAvatarModel(asset, true);
        }

        private async Task EnsureModelThumbnailAsync(ManagedAssetRecord asset, int request)
        {
            if (asset == null || string.IsNullOrWhiteSpace(asset.path)) return;
            if (!thumbnailGenerationInFlight.Add(asset.id)) return;
            string thumbnailPath = managedAssetLibrary.ThumbnailPath(asset.id);
            try
            {
                if (await VrmThumbnailGenerator.TryGenerateAsync(asset.path, thumbnailPath))
                {
                    managedAssetLibrary.SetThumbnailPath(asset.id, thumbnailPath);
                    // A thumbnail completion may rebuild a visible card, but must
                    // not repaint state after a newer model request won.
                    if (request == modelApplyGeneration && modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy)
                        BuildModelLibraryTiles();
                }
            }
            finally { thumbnailGenerationInFlight.Remove(asset.id); }
        }

        private bool ResetAvatarModel()
        {
            if (avatarLoader == null || !avatarLoader.LoadConfiguredAvatar())
            {
                ApplyStatus("error", avatarLoader != null ? avatarLoader.LastError : "Avatar loader is unavailable.");
                return false;
            }
            AvatarLoader.ClearCustomModelPathPreference();
            ApplyStatus("ready", "Bundled visual avatar restored.");
            RefreshDisplaySettingsUi();
            return true;
        }

        private void CreateBackgroundLibraryPanel(Transform parent)
        {
            backgroundLibraryPanel = CreatePanel(parent, "Background Library", new Color(.08f, .06f, .13f, .98f));
            Stretch(backgroundLibraryPanel.GetComponent<RectTransform>(), new Vector2(.12f, .14f), new Vector2(.88f, .86f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(backgroundLibraryPanel.transform, "Viewer Background", 24f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.06f, .87f), new Vector2(.34f, .96f), Vector2.zero, Vector2.zero);
            Button import = CreateButton(backgroundLibraryPanel.transform, "Import", Panel);
            Stretch(import.GetComponent<RectTransform>(), new Vector2(.36f, .87f), new Vector2(.52f, .96f), Vector2.zero, Vector2.zero);
            import.onClick.AddListener(ChangeCustomBackground);
            Button back = CreateButton(backgroundLibraryPanel.transform, "Back", Panel);
            Stretch(back.GetComponent<RectTransform>(), new Vector2(.76f, .87f), new Vector2(.94f, .96f), Vector2.zero, Vector2.zero);
            back.onClick.AddListener(() => { selectedBackgroundAssets.Clear(); backgroundLibraryPanel.SetActive(false); });
            deleteBackgroundAssetsButton = CreateButton(backgroundLibraryPanel.transform, "Delete Selected", new Color(.42f,.16f,.22f,1f));
            Stretch(deleteBackgroundAssetsButton.GetComponent<RectTransform>(), new Vector2(.54f,.87f), new Vector2(.74f,.96f), Vector2.zero, Vector2.zero);
            deleteBackgroundAssetsButton.onClick.AddListener(OpenBackgroundDeleteConfirmation);
            AvatarViewerBackground[] builtIns = { AvatarViewerBackground.LightNeutral, AvatarViewerBackground.NeutralGrey, AvatarViewerBackground.Bedroom };
            backgroundLibraryTiles = CreateLibraryTileGrid(backgroundLibraryPanel.transform, "Background Library Tiles");
            // The delete action is a header action, never part of the clipped
            // ScrollRect content.  Keep it above the viewport in hierarchy
            // order as well as in its authored header rect.
            deleteBackgroundAssetsButton.transform.SetAsLastSibling();
            LogDeleteHeaderState("background", deleteBackgroundAssetsButton, selectedBackgroundAssets.Count, "created");
            BuildBackgroundLibraryTiles(builtIns);
            backgroundLibraryPanel.SetActive(false);
        }

        private void BuildBackgroundLibraryTiles(AvatarViewerBackground[] builtIns)
        {
            float scrollPosition = ClearLibraryTiles(backgroundLibraryTiles);
            for (int i = 0; i < builtIns.Length; i++)
            {
                AvatarViewerBackground value = builtIns[i];
                Button tile = CreateButton(backgroundLibraryTiles, AvatarViewerBackgroundState.Label(value), Panel);
                AddBackgroundTilePreview(tile, value);
                tile.onClick.AddListener(() =>
                {
                    selectedBackgroundAssets.Clear();
                    bool alreadyActive = CurrentAvatarViewerBackground == value;
                    if (!alreadyActive)
                    {
                        avatarViewerBackgroundState.Set(AvatarViewPortrait, value, true);
                        ApplyAvatarViewerBackground();
                        RefreshDisplaySettingsUi();
                    }
                    RefreshBackgroundLibrarySelection();
                });
            }
            List<ManagedAssetRecord> backgrounds = managedAssetLibrary.Assets(ManagedAssetLibrary.BackgroundKind);
            backgrounds.Sort(CompareManagedAssetNames);
            Dictionary<string, int> backgroundNameCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (ManagedAssetRecord asset in backgrounds)
            {
                ManagedAssetRecord selected = asset;
                string visibleName = DisambiguatedManagedName(asset, backgroundNameCounts, "Imported background");
                Button tile = CreateButton(backgroundLibraryTiles, visibleName, Panel);
                tile.gameObject.name = "Managed Background " + asset.id;
                RawImage thumb = CreateRawImage(tile.transform, "Thumbnail");
                thumb.raycastTarget = false;
                Stretch(thumb.rectTransform, new Vector2(.08f, .30f), new Vector2(.92f, .92f), Vector2.zero, Vector2.zero);
                PositionLibraryTileLabel(tile);
                try { byte[] bytes = System.IO.File.ReadAllBytes(selected.path); Texture2D image = new Texture2D(2,2); if (ImageConversion.LoadImage(image, bytes, false)) { thumb.texture = image; AspectRatioFitter fit = thumb.gameObject.AddComponent<AspectRatioFitter>(); fit.aspectMode = AspectRatioFitter.AspectMode.FitInParent; fit.aspectRatio = image.width / (float)image.height; } else thumb.color = new Color(.25f,.22f,.32f,1f); }
                catch { thumb.color = new Color(.25f,.22f,.32f,1f); }
                tile.onClick.AddListener(() =>
                {
                    if (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                    {
                        ToggleBackgroundDeletionSelection(selected.id);
                        return;
                    }
                    SelectOnlyBackgroundForDeletion(selected.id);
                    bool alreadyActive = CurrentAvatarViewerBackground == AvatarViewerBackground.CustomImage &&
                        avatarViewerBackgroundState.GetCustomPath(AvatarViewPortrait) == selected.path;
                    if (!alreadyActive)
                    {
                        avatarViewerBackgroundState.SetCustomPath(AvatarViewPortrait, selected.path, true);
                        avatarViewerBackgroundState.Set(AvatarViewPortrait, AvatarViewerBackground.CustomImage, true);
                        if (AvatarViewPortrait) portraitCustomBackground = null; else landscapeCustomBackground = null;
                        ApplyAvatarViewerBackground();
                        RefreshDisplaySettingsUi();
                    }
                    RefreshBackgroundLibrarySelection();
                });
            }
            RestoreLibraryScroll(backgroundLibraryTiles, scrollPosition); RefreshBackgroundLibrarySelection();
        }

        private void AddBackgroundTilePreview(Button tile, AvatarViewerBackground background)
        {
            Image swatch = CreateImage(tile.transform, "Background Preview", background == AvatarViewerBackground.LightNeutral
                ? new Color(.93f,.93f,.90f,1f) : background == AvatarViewerBackground.NeutralGrey
                ? new Color(.40f,.40f,.43f,1f) : new Color(.22f,.18f,.22f,1f));
            swatch.raycastTarget = false;
            Stretch(swatch.rectTransform, new Vector2(.08f,.30f), new Vector2(.92f,.92f), Vector2.zero, Vector2.zero);
            if (background == AvatarViewerBackground.Bedroom)
            {
                Texture2D bedroom = Resources.Load<Texture2D>("Presentation/Backgrounds/bedroom_day");
                if (bedroom != null)
                {
                    RawImage image = CreateRawImage(swatch.transform, "Bedroom Preview");
                    image.texture = bedroom; image.raycastTarget = false;
                    Stretch(image.rectTransform, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
                }
            }
            PositionLibraryTileLabel(tile);
        }

        private static int CompareManagedAssetNames(ManagedAssetRecord left, ManagedAssetRecord right)
        {
            int name = string.Compare(left.displayName, right.displayName, StringComparison.OrdinalIgnoreCase);
            return name != 0 ? name : string.CompareOrdinal(left.id, right.id);
        }

        private static string DisambiguatedManagedName(ManagedAssetRecord asset, Dictionary<string, int> counts, string fallback)
        {
            string name = string.IsNullOrWhiteSpace(asset.displayName) ? fallback : asset.displayName;
            counts.TryGetValue(name, out int occurrence);
            occurrence++;
            counts[name] = occurrence;
            return occurrence == 1 ? name : name + " (" + occurrence + ")";
        }

        private void OpenBackgroundLibrary()
        {
            RefreshBackgroundLibrarySelection();
            BuildBackgroundLibraryTiles(new[] { AvatarViewerBackground.LightNeutral, AvatarViewerBackground.NeutralGrey, AvatarViewerBackground.Bedroom });
            backgroundLibraryPanel.SetActive(true);
            backgroundLibraryPanel.transform.SetAsLastSibling();
            LogDeleteHeaderState("background", deleteBackgroundAssetsButton, selectedBackgroundAssets.Count, "opened");
        }

        private void ToggleBackgroundDeletionSelection(string assetId)
        {
            if (!selectedBackgroundAssets.Add(assetId)) selectedBackgroundAssets.Remove(assetId);
            Debug.Log("[AIFren Asset Library] background Ctrl-click selection id=" + assetId + " count=" + selectedBackgroundAssets.Count);
            RefreshBackgroundLibrarySelection();
        }
        private void SelectOnlyBackgroundForDeletion(string assetId)
        {
            selectedBackgroundAssets.Clear();
            selectedBackgroundAssets.Add(assetId);
            Debug.Log("[AIFren Asset Library] background regular-click selection id=" + assetId + " count=1");
            RefreshBackgroundLibrarySelection();
        }

        private void RefreshBackgroundLibrarySelection()
        {
            if (backgroundLibraryPanel == null) return;
            foreach (Button tile in backgroundLibraryPanel.GetComponentsInChildren<Button>(true))
            {
                TMP_Text label = tile.GetComponentInChildren<TMP_Text>();
                if (label == null || label.text == "Back" || label.text == "Import" || label.text.StartsWith("Delete Selected")) continue;
                bool selected = tile.gameObject.name.StartsWith("Managed Background ") &&
                    selectedBackgroundAssets.Contains(tile.gameObject.name.Substring("Managed Background ".Length));
                ManagedAssetRecord asset = tile.gameObject.name.StartsWith("Managed Background ")
                    ? managedAssetLibrary.Assets(ManagedAssetLibrary.BackgroundKind).Find(x => tile.gameObject.name == "Managed Background " + x.id)
                    : null;
                bool active = asset != null
                    ? CurrentAvatarViewerBackground == AvatarViewerBackground.CustomImage &&
                      avatarViewerBackgroundState.GetCustomPath(AvatarViewPortrait) == asset.path
                    : label.text == AvatarViewerBackgroundState.Label(CurrentAvatarViewerBackground);
                SetLibraryTileVisual(tile, active, selected);
            }
            UpdateDeleteSelectedHeader(deleteBackgroundAssetsButton, selectedBackgroundAssets.Count);
            LogDeleteHeaderState("background", deleteBackgroundAssetsButton, selectedBackgroundAssets.Count, "refresh");
        }

        private static void SetLibraryTileVisual(Button tile, bool active, bool deleteSelected)
        {
            Image image = tile.GetComponent<Image>();
            if (image != null) image.color = active ? new Color(.14f,.11f,.21f,.97f) : Panel;
            Outline outline = tile.GetComponent<Outline>();
            if (outline == null) return;
            outline.effectDistance = (active || deleteSelected) ? new Vector2(2f, -2f) : new Vector2(1f, -1f);
            // Purple identifies the applied asset; teal identifies the pending
            // deletion set. Combined state retains the teal selection border
            // over a subtly purple card surface.
            outline.effectColor = deleteSelected ? new Color(.20f,.72f,.76f,.95f) : active
                ? new Color(.68f,.43f,.96f,.95f) : new Color(.42f,.32f,.60f,.48f);
        }

        // The deletion slot remains in the non-scrolling header even at zero
        // selection. Keeping it disabled makes its lifecycle and placement
        // independent from grid rebuilds and async thumbnail refreshes.
        private static void UpdateDeleteSelectedHeader(Button button, int count)
        {
            if (button == null) return;
            button.gameObject.SetActive(true);
            TMP_Text caption = button.GetComponentInChildren<TMP_Text>(true);
            if (caption != null)
            {
                caption.raycastTarget = false;
                caption.text = "Delete Selected (" + count + ")";
            }
            button.interactable = count > 0;
        }

        private static void LogDeleteHeaderState(string library, Button button, int count, string phase)
        {
            if (button == null)
            {
                Debug.LogWarning("[AIFren Asset Library] " + library + " delete header " + phase + ": button was not created.");
                return;
            }
            RectTransform rect = button.GetComponent<RectTransform>();
            Debug.Log("[AIFren Asset Library] " + library + " delete header " + phase +
                " parent=" + (button.transform.parent != null ? button.transform.parent.name : "<none>") +
                " activeSelf=" + button.gameObject.activeSelf +
                " activeInHierarchy=" + button.gameObject.activeInHierarchy +
                " anchoredPosition=" + rect.anchoredPosition +
                " sizeDelta=" + rect.sizeDelta +
                " sibling=" + button.transform.GetSiblingIndex() +
                " selectedCount=" + count +
                " interactable=" + button.interactable);
        }

        private void OpenBackgroundDeleteConfirmation()
        {
            if (selectedBackgroundAssets.Count == 0) return;
            if (backgroundDeleteConfirmPanel == null)
            {
                backgroundDeleteConfirmPanel = CreatePanel(backgroundLibraryPanel.transform, "Delete Background Confirmation", new Color(.08f,.06f,.13f,.99f));
                Stretch(backgroundDeleteConfirmPanel.GetComponent<RectTransform>(), new Vector2(.24f,.34f), new Vector2(.76f,.66f), Vector2.zero, Vector2.zero);
                TMP_Text text = CreateText(backgroundDeleteConfirmPanel.transform, string.Empty, 20f, Ink, TextAlignmentOptions.Center); text.gameObject.name="Message";
                Stretch(text.rectTransform, new Vector2(.08f,.42f), new Vector2(.92f,.88f), Vector2.zero, Vector2.zero);
                Button cancel=CreateButton(backgroundDeleteConfirmPanel.transform,"Cancel",Panel); Stretch(cancel.GetComponent<RectTransform>(),new Vector2(.08f,.12f),new Vector2(.46f,.32f),Vector2.zero,Vector2.zero); cancel.onClick.AddListener(()=>backgroundDeleteConfirmPanel.SetActive(false));
                Button confirm=CreateButton(backgroundDeleteConfirmPanel.transform,"Delete",new Color(.42f,.16f,.22f,1f)); Stretch(confirm.GetComponent<RectTransform>(),new Vector2(.54f,.12f),new Vector2(.92f,.32f),Vector2.zero,Vector2.zero); confirm.onClick.AddListener(DeleteSelectedBackgrounds);
            }
            backgroundDeleteConfirmPanel.transform.Find("Message").GetComponent<TMP_Text>().text = "Delete " + selectedBackgroundAssets.Count + " imported background" + (selectedBackgroundAssets.Count == 1 ? "?" : "s?");
            backgroundDeleteConfirmPanel.SetActive(true); backgroundDeleteConfirmPanel.transform.SetAsLastSibling();
        }

        private void DeleteSelectedBackgrounds()
        {
            var deletedPaths = new HashSet<string>();
            foreach (ManagedAssetRecord asset in managedAssetLibrary.Records(ManagedAssetLibrary.BackgroundKind))
                if (selectedBackgroundAssets.Contains(asset.id)) deletedPaths.Add(asset.path);
            avatarViewerBackgroundState.RepairDeletedCustomPaths(deletedPaths, true);
            managedAssetLibrary.Delete(ManagedAssetLibrary.BackgroundKind, selectedBackgroundAssets); selectedBackgroundAssets.Clear();
            if (backgroundDeleteConfirmPanel != null) backgroundDeleteConfirmPanel.SetActive(false);
            portraitCustomBackground = null; landscapeCustomBackground = null;
            ApplyAvatarViewerBackground(); BuildBackgroundLibraryTiles(new[] { AvatarViewerBackground.LightNeutral, AvatarViewerBackground.NeutralGrey, AvatarViewerBackground.Bedroom }); RefreshDisplaySettingsUi();
        }

        private async void ChangeCustomBackground()
        {
            string path = await LinuxNativeFilePicker.PickAsync("Choose viewer background", "Images | *.png *.jpg *.jpeg");
            if (string.IsNullOrWhiteSpace(path)) return;
            LinuxNativeFilePicker.Remember(path);
            string extension = System.IO.Path.GetExtension(path);
            if (!string.Equals(extension, ".png", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(extension, ".jpg", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(extension, ".jpeg", StringComparison.OrdinalIgnoreCase))
            {
                ApplyStatus("error", "Choose a PNG or JPEG viewer background.");
                return;
            }
            if (!managedAssetLibrary.TryImport(path, ManagedAssetLibrary.BackgroundKind, out ManagedAssetRecord asset, out string importError))
            {
                ApplyStatus("error", "Could not import background: " + importError);
                return;
            }
            avatarViewerBackgroundState.SetCustomPath(AvatarViewPortrait, asset.path, true);
            avatarViewerBackgroundState.Set(AvatarViewPortrait, AvatarViewerBackground.CustomImage, true);
            if (AvatarViewPortrait) portraitCustomBackground = null; else landscapeCustomBackground = null;
            ApplyAvatarViewerBackground();
            BuildBackgroundLibraryTiles(new[] { AvatarViewerBackground.LightNeutral, AvatarViewerBackground.NeutralGrey, AvatarViewerBackground.Bedroom });
            RefreshDisplaySettingsUi();
        }

        private void SetShowDialogueWhenHidden(bool value)
        {
            showDialogueWhenHidden = value;
            PlayerPrefs.SetInt(ShowDialogueWhenHiddenPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            if (!value) HideHiddenSubtitleImmediately();
            SyncHiddenDialogueText();
        }

        private void SetAlwaysOnTop(bool value)
        {
            alwaysOnTop = value;
            PlayerPrefs.SetInt(AlwaysOnTopPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            ApplyAlwaysOnTop();
        }

        private IEnumerator ApplyAlwaysOnTopAfterWindowCreation()
        {
            // The standalone window is not necessarily registered with the
            // X11 window manager during Start or a native display transition.
            yield return null;
            yield return new WaitForEndOfFrame();
            ApplyAlwaysOnTop();
        }

        private void ApplyAlwaysOnTop()
        {
            if (!LinuxWindowAlwaysOnTop.TrySet(alwaysOnTop, out string detail))
            {
                Debug.LogWarning("[AIFren Window] " + detail);
                return;
            }

            Debug.Log("[AIFren Window] " + detail);
        }

        private void SyncHiddenDialogueText(bool prepareWhileInactive = false)
        {
            // HiddenSubtitlePresenter is the sole production owner of this
            // visual tree. Retain the legacy method only until its callers are
            // removed from unrelated normal-dialogue refresh paths.
            if (hiddenSubtitlePresenter != null) return;
            if (hiddenDialogueText == null || hiddenDialogueViewport == null) return;
            // This is an independent spoken-text subtitle, never a copy of
            // the visible dialogue card. Emotes remain visible in the card but
            // are deliberately omitted here.
            string page = subtitlePages.Count == 0 ? string.Empty :
                subtitlePages[Mathf.Clamp(subtitlePageIndex, 0, subtitlePages.Count - 1)];
            // Keep the complete escaped page as ordinary TMP text. Word
            // visibility is applied to its mesh below, never encoded in the
            // string, so control tags cannot leak or change layout.
            string fullPage = DialoguePresentationParser.FormatSubtitleText(page);
            if (hiddenDialogueText.text != fullPage) hiddenDialogueText.text = fullPage;
            float width = Mathf.Max(1f, hiddenDialogueViewport.rect.width - 36f);
            float viewportHeight = Mathf.Max(1f, hiddenDialogueViewport.rect.height - 20f);
            const float defaultSize = 35f;
            const float minimumSize = 23f;
            float size = defaultSize;
            float preferredHeight = 0f;
            for (; size >= minimumSize; size -= 1f)
            {
                hiddenDialogueText.fontSize = size;
                preferredHeight = hiddenDialogueText.GetPreferredValues(fullPage, width, 0f).y;
                if (preferredHeight <= viewportHeight) break;
            }
            hiddenDialogueText.fontSize = Mathf.Max(minimumSize, size);
            foreach (TMP_Text backing in hiddenSubtitleBackings)
            {
                if (backing == null) continue;
                backing.text = hiddenDialogueText.text;
                backing.fontSize = hiddenDialogueText.fontSize;
                backing.fontStyle = hiddenDialogueText.fontStyle;
                backing.alignment = hiddenDialogueText.alignment;
                backing.color = Color.black;
            }
            // Normal dialogue updates also call this method. Do not rebuild
            // five inactive subtitle meshes for every ordinary word reveal.
            // The root is alpha-zero before activation and receives a mesh
            // update on the first subtitle fade frame.
            if (!prepareWhileInactive && !hiddenDialogueViewport.gameObject.activeInHierarchy)
            {
                if (hiddenDialogueScrollbar != null) hiddenDialogueScrollbar.gameObject.SetActive(false);
                return;
            }
            ApplyHiddenSubtitleWordVisibility(hiddenDialogueText);
            foreach (TMP_Text backing in hiddenSubtitleBackings)
                if (backing != null) ApplyHiddenSubtitleWordVisibility(backing);
            if (hiddenDialogueScrollbar != null) hiddenDialogueScrollbar.gameObject.SetActive(false);
        }

        private void ApplyHiddenSubtitleWordVisibility(TMP_Text text)
        {
            text.ForceMeshUpdate();
            TMP_TextInfo info = text.textInfo;
            int wordIndex = -1;
            bool inWord = false;
            int revealed = hiddenSubtitleReveal.RevealedTokenCount;
            byte newestAlpha = (byte)Mathf.RoundToInt(Mathf.Clamp01(hiddenSubtitleReveal.LatestTokenAlpha) * 255f);
            for (int index = 0; index < info.characterCount; index++)
            {
                TMP_CharacterInfo character = info.characterInfo[index];
                bool whitespace = char.IsWhiteSpace(character.character);
                if (whitespace) inWord = false;
                else if (!inWord) { wordIndex++; inWord = true; }

                byte alpha = wordIndex < revealed - 1 ? (byte)255 :
                    wordIndex == revealed - 1 ? newestAlpha : (byte)0;
                if (!character.isVisible || character.materialReferenceIndex < 0) continue;
                Color32[] colors = info.meshInfo[character.materialReferenceIndex].colors32;
                int vertex = character.vertexIndex;
                for (int offset = 0; offset < 4; offset++)
                {
                    Color32 color = colors[vertex + offset];
                    color.a = alpha;
                    colors[vertex + offset] = color;
                }
            }
            text.UpdateVertexData(TMP_VertexDataUpdateFlags.Colors32);
        }

        private void EnsureHiddenSubtitlePresentation()
        {
            if (hiddenDialogueText == null) return;
            Color pink = new Color(.98f, .62f, .78f, 1f);
            hiddenDialogueText.color = pink;
            hiddenDialogueText.fontStyle = FontStyles.Bold;
            if (hiddenSubtitleMaterial == null)
            {
                // Clone the exact active font material, not a loosely related
                // preset asset, so the visible TMP uses compatible SDF shader
                // properties without altering any shared UI material.
                hiddenSubtitleMaterial = new Material(hiddenDialogueText.fontSharedMaterial) { name = "AIFren Hidden Subtitle Material" };
                hiddenDialogueText.fontMaterial = hiddenSubtitleMaterial;
            }
            if (hiddenSubtitleMaterial.HasProperty(ShaderUtilities.ID_FaceColor))
                hiddenSubtitleMaterial.SetColor(ShaderUtilities.ID_FaceColor, Color.white);
            if (hiddenSubtitleMaterial.HasProperty(ShaderUtilities.ID_OutlineColor))
                hiddenSubtitleMaterial.SetColor(ShaderUtilities.ID_OutlineColor, Color.black);
            if (hiddenSubtitleMaterial.HasProperty(ShaderUtilities.ID_OutlineWidth))
                hiddenSubtitleMaterial.SetFloat(ShaderUtilities.ID_OutlineWidth, .09f);
            if (hiddenSubtitleMaterial.HasProperty(ShaderUtilities.ID_OutlineSoftness))
                hiddenSubtitleMaterial.SetFloat(ShaderUtilities.ID_OutlineSoftness, 0f);
            foreach (Material backingMaterial in hiddenSubtitleBackingMaterials)
                if (backingMaterial != null && backingMaterial.HasProperty(ShaderUtilities.ID_FaceColor))
                    backingMaterial.SetColor(ShaderUtilities.ID_FaceColor, Color.black);
        }

        private void LogHiddenSubtitleState(string phase)
        {
            if (!Debug.isDebugBuild || hiddenDialogueText == null || hiddenDialogueCanvasGroup == null) return;
            Material material = hiddenDialogueText.fontMaterial;
            string properties = material != null && material.HasProperty(ShaderUtilities.ID_OutlineWidth)
                ? " face=" + material.GetColor(ShaderUtilities.ID_FaceColor) + " outline=" + material.GetColor(ShaderUtilities.ID_OutlineColor) +
                  " width=" + material.GetFloat(ShaderUtilities.ID_OutlineWidth) + " softness=" + material.GetFloat(ShaderUtilities.ID_OutlineSoftness)
                : " material-properties-unavailable";
            Debug.Log("[AIFren Subtitle] " + phase + " object=" + hiddenDialogueText.gameObject.name +
                " tmp=" + hiddenDialogueText.GetInstanceID() + " parent=" + (hiddenDialogueText.transform.parent != null ? hiddenDialogueText.transform.parent.name : "<none>") +
                " activeSelf=" + hiddenDialogueText.gameObject.activeSelf + " active=" + hiddenDialogueText.gameObject.activeInHierarchy +
                " canvasAlpha=" + hiddenDialogueCanvasGroup.alpha + " textColor=" + hiddenDialogueText.color +
                " anchors=" + hiddenDialogueText.rectTransform.anchorMin + ".." + hiddenDialogueText.rectTransform.anchorMax +
                " position=" + hiddenDialogueText.rectTransform.anchoredPosition + " size=" + hiddenDialogueText.rectTransform.rect.size +
                " font=" + (hiddenDialogueText.font != null ? hiddenDialogueText.font.name : "<none>") +
                " material=" + (material != null ? material.name : "<none>") + " shader=" + (material != null ? material.shader.name : "<none>") + properties);
            if (hiddenSubtitleBackings.Count > 0 && hiddenSubtitleBackings[0] != null)
            {
                Material backing = hiddenSubtitleBackings[0].fontMaterial;
                Debug.Log("[AIFren Subtitle] backing color=" + hiddenSubtitleBackings[0].color +
                    " material=" + (backing != null ? backing.name : "<none>") +
                    " face=" + (backing != null && backing.HasProperty(ShaderUtilities.ID_FaceColor) ? backing.GetColor(ShaderUtilities.ID_FaceColor).ToString() : "<none>"));
            }
        }

        private void BeginSubtitleResponse(string rawResponse)
        {
            subtitleGeneration++;
            if (subtitlePresentationCoroutine != null) StopCoroutine(subtitlePresentationCoroutine);
            HideHiddenSubtitleImmediately();
            string spokenSubtitleText = DialoguePresentationParser.SpokenText(rawResponse);
            subtitlePages.Clear();
            subtitlePages.AddRange(SubtitlePagination.Split(DialoguePresentationParser.SubtitleSourceText(rawResponse)));
            subtitlePageWordRanges.Clear();
            subtitlePageWordRanges.AddRange(SubtitleTimingPlan.BuildPageWordRanges(subtitlePages));
            if (!SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                spokenSubtitleText, subtitlePages, subtitlePageWordRanges, DialoguePresentationParser.SpokenText, out string ownershipError))
            {
                Debug.LogError("[AIFren Subtitle] invalid page ownership; refusing hidden subtitle: " + ownershipError);
                return;
            }
            LogSubtitlePageOwnership(spokenSubtitleText);
            subtitlePageIndex = 0;
            hiddenSubtitleReveal.Begin(string.Empty, true);
            subtitleSpeechActive = false;
            subtitlePlaybackGeneration = -1;
            subtitleAwaitingPlayback = true;
            subtitlePlaybackStartedSignal = false;
            subtitlePlaybackStoppedSignal = false;
            subtitlePlaybackId = 0;
            subtitleSpeechDuration = 0f;
            subtitleTimingUsesPlaybackClock = false;
            subtitlePresentationStartedAt = 0f;
            subtitlePlaybackStartedAt = 0f;
            ConfigureSubtitleTimingPlan(0f, false, null);
            subtitleFirstWordLogged = false;
            currentAssistantPresentationText = rawResponse ?? string.Empty;
            int generation = subtitleGeneration;
            Debug.Log("[AIFren Subtitle] prepared generation=" + generation + " pages=" + subtitlePages.Count + " enabled=" + showDialogueWhenHidden + " hidden=" + interfaceHidden);
            hiddenSubtitlePresenter?.Begin(new SubtitleSession(
                new List<string>(subtitlePages), new List<SubtitlePageWordRange>(subtitlePageWordRanges),
                new List<float>(subtitleWordSchedule), generation, Time.unscaledTime));
        }

        private void LogSubtitlePageOwnership(string spokenText)
        {
            if (!Debug.isDebugBuild) return;
            List<string> allWords = SubtitleTimingPlan.TokenizeWords(spokenText);
            for (int pageIndex = 0; pageIndex < subtitlePages.Count; pageIndex++)
            {
                SubtitlePageWordRange range = subtitlePageWordRanges[pageIndex];
                int count = range.LastWordIndex - range.FirstWordIndex + 1;
                List<string> owned = allWords.GetRange(range.FirstWordIndex, count);
                Debug.Log("[AIFren Subtitle] page=" + pageIndex + " firstGlobalWord=" + range.FirstWordIndex +
                    " lastGlobalWord=" + range.LastWordIndex + " wordCount=" + count +
                    " text=\"" + subtitlePages[pageIndex] + "\" words=[" + string.Join(" | ", owned) + "]");
            }
        }

        private void UpdateSubtitlePaging()
        {
            hiddenSubtitlePresenter?.Tick(Time.unscaledTime, interfaceHidden, showDialogueWhenHidden);
        }

        private IEnumerator RunHiddenSubtitle(int generation)
        {
            Debug.Log("[AIFren Subtitle] coroutine started generation=" + generation + " waiting playback_started");
            // Let ordinary TTS claim the presentation first; otherwise use
            // the same deterministic fallback lifecycle after a short wait.
            float waitUntil = Time.unscaledTime + .9f;
            while (generation == subtitleGeneration && !subtitlePlaybackStartedSignal && Time.unscaledTime < waitUntil)
                yield return null;
            if (generation != subtitleGeneration || subtitlePages.Count == 0) { Debug.LogWarning("[AIFren Subtitle] coroutine aborted before start generation=" + generation); yield break; }

            if (!subtitlePlaybackStartedSignal) Debug.Log("[AIFren Subtitle] fallback timeout generation=" + generation);

            subtitleAwaitingPlayback = false;
            // Do not abandon a prepared response if an edge reveal or UI
            // transition briefly makes the interface visible. It becomes
            // eligible as soon as the user hides the normal UI again.
            while (generation == subtitleGeneration && (!interfaceHidden || !showDialogueWhenHidden)) yield return null;
            if (generation != subtitleGeneration) yield break;
            subtitlePresentationStartedAt = Time.unscaledTime;
            // Do not begin the root fade against an empty mesh. This waits
            // only for the first scheduled word (with the fixed lead), never
            // invents an early reveal or pauses the playback clock.
            while (generation == subtitleGeneration && !subtitlePlaybackStoppedSignal &&
                subtitleWordSchedule.Count > 0 && GetSubtitleDueWordCount() == 0)
                yield return null;
            if (generation != subtitleGeneration || subtitlePlaybackStoppedSignal) yield break;
            hiddenSubtitlePageState = HiddenSubtitlePageState.PreparingNextPage;
            PrepareSubtitlePageForFadeIn(0, true);
            CommitSubtitlePageForFadeIn();
            EnsureHiddenSubtitlePresentation();
            Debug.Log("[AIFren Subtitle] root activated generation=" + generation + " alpha=" + hiddenDialogueCanvasGroup.alpha);
            if (!subtitleFirstWordLogged)
            {
                subtitleFirstWordLogged = true;
                Debug.Log("[AIFren Timing] first hidden subtitle word prepared; response-to-first=" +
                    (Time.unscaledTime - subtitleResponseReceivedAt).ToString("F3") + "s");
            }
            yield return null;
            yield return FadeSubtitle(generation, 0f, 1f, .32f, true);
            if (generation != subtitleGeneration) yield break;
            hiddenSubtitlePageState = HiddenSubtitlePageState.ShowingPage;

            for (int page = 0; page < subtitlePages.Count; page++)
            {
                subtitlePageIndex = page;
                if (page > 0)
                {
                    // A short shared-CanvasGroup transition keeps completed
                    // pages from snapping into the next page. No transition
                    // can run until the current WordReveal has completed.
                    hiddenSubtitlePageState = HiddenSubtitlePageState.FadingOut;
                    yield return FadeSubtitle(generation, hiddenDialogueCanvasGroup.alpha, 0f, HiddenSubtitlePageFadeOutSeconds);
                    if (generation != subtitleGeneration) yield break;
                    if (subtitlePlaybackStoppedSignal) break;
                    hiddenSubtitlePageState = HiddenSubtitlePageState.PreparingNextPage;
                    PrepareSubtitlePageForFadeIn(page, false);
                    CommitSubtitlePageForFadeIn();
                    hiddenSubtitlePageState = HiddenSubtitlePageState.FadingIn;
                    yield return FadeSubtitle(generation, 0f, 1f, HiddenSubtitlePageFadeInSeconds, true);
                    if (generation != subtitleGeneration) yield break;
                    hiddenSubtitlePageState = HiddenSubtitlePageState.ShowingPage;
                }
                while (generation == subtitleGeneration &&
                    !IsSubtitlePageVisuallyComplete(page))
                {
                    AdvanceSubtitleReveal();
                    SyncHiddenDialogueText();
                    if (subtitlePlaybackStoppedSignal) break;
                    yield return null;
                }
                if (generation != subtitleGeneration) yield break;
                if (subtitlePlaybackStoppedSignal || page == subtitlePages.Count - 1) break;
                // The page is now a closed visual unit: its final word is due
                // and fully visible, so proceed directly into the existing
                // short transition without adding a separate hold timer.
                if (subtitlePlaybackStoppedSignal) break;
            }

            // A live TTS response holds its final page until stop; fallback
            // holds briefly and always expires.
            if (!subtitlePlaybackStoppedSignal && subtitlePlaybackStartedSignal)
            {
                float safetyUntil = Time.unscaledTime + Mathf.Max(1.5f, subtitleSpeechDuration);
                while (generation == subtitleGeneration && !subtitlePlaybackStoppedSignal && Time.unscaledTime < safetyUntil) yield return null;
            }
            if (generation != subtitleGeneration) yield break;
            yield return new WaitForSecondsRealtime(.12f);
            if (generation != subtitleGeneration) yield break;
            yield return FadeSubtitle(generation, hiddenDialogueCanvasGroup.alpha, 0f, .45f);
            if (generation != subtitleGeneration) yield break;
            hiddenDialogueText.text = string.Empty;
            hiddenDialogueViewport.gameObject.SetActive(false);
            subtitlePresentationCoroutine = null;
        }

        private IEnumerator FadeSubtitle(int generation, float from, float to, float duration, bool revealDuringFade = false)
        {
            for (float elapsed = 0f; generation == subtitleGeneration && elapsed < duration; elapsed += Time.unscaledDeltaTime)
            {
                hiddenDialogueCanvasGroup.alpha = Mathf.Lerp(from, to, elapsed / duration);
                if (revealDuringFade)
                {
                    AdvanceSubtitleReveal();
                    SyncHiddenDialogueText();
                }
                yield return null;
            }
            if (generation == subtitleGeneration) hiddenDialogueCanvasGroup.alpha = to;
        }

        private void ConfigureSubtitleTimingPlan(float durationSeconds, bool playbackClock, float[] alignedWordStarts)
        {
            string spoken = string.Join(" ", subtitlePages);
            subtitleWordSchedule.Clear();
            int expectedWords = SubtitleTimingPlan.WordCount(spoken);
            bool validAlignment = alignedWordStarts != null && alignedWordStarts.Length == expectedWords;
            if (validAlignment)
            {
                float previous = -0.001f;
                foreach (float start in alignedWordStarts)
                {
                    if (float.IsNaN(start) || float.IsInfinity(start) || start < previous || start < 0f ||
                        (durationSeconds > 0f && start > durationSeconds + .25f))
                    {
                        validAlignment = false;
                        break;
                    }
                    previous = start;
                }
            }
            if (validAlignment) subtitleWordSchedule.AddRange(alignedWordStarts);
            else subtitleWordSchedule.AddRange(SubtitleTimingPlan.Build(spoken, durationSeconds, revealWordsPerSecond));
            float rawFinalWordTimestamp = subtitleWordSchedule.Count > 0
                ? subtitleWordSchedule[subtitleWordSchedule.Count - 1] : 0f;
            SubtitleTimingPlan.ApplyLead(subtitleWordSchedule, HiddenSubtitleLeadSeconds);
            subtitleTimingUsesPlaybackClock = playbackClock && durationSeconds > 0f;
            LogSubtitleTimingDiagnostics(durationSeconds, validAlignment, rawFinalWordTimestamp);
            Debug.Log("[AIFren Subtitle] immutable timing plan=" + subtitleWordSchedule.Count +
                " words; duration=" + durationSeconds.ToString("F2") + "; playbackClock=" + subtitleTimingUsesPlaybackClock +
                "; source=" + (validAlignment ? "Kokoro token timestamps" : "weighted fallback") +
                "; lead=" + HiddenSubtitleLeadSeconds.ToString("F2") + "s.");
        }

        private void BeginSubtitlePage(int pageIndex)
        {
            if (pageIndex < 0 || pageIndex >= subtitlePages.Count) return;
            string page = subtitlePages[pageIndex];
            // This remains only as a local fallback if no plan can be built.
            hiddenSubtitleReveal.WordsPerSecond = revealWordsPerSecond;
            hiddenSubtitleReveal.Begin(page, false);
        }

        private void PrepareSubtitlePageForFadeIn(int pageIndex, bool seedInitialDueWord)
        {
            // Preparation is explicitly non-renderable. The root is disabled
            // before any text, mesh, or vertex-alpha mutation, so no page can
            // flash between a text replacement and its fade-start alpha.
            if (hiddenDialogueViewport != null) hiddenDialogueViewport.gameObject.SetActive(false);
            if (hiddenDialogueCanvasGroup != null) hiddenDialogueCanvasGroup.alpha = 0f;
            BeginSubtitlePage(pageIndex);
            InitializeSubtitlePagePresentation(pageIndex, seedInitialDueWord);
            SyncHiddenDialogueText(true);
        }

        private void CommitSubtitlePageForFadeIn()
        {
            // All child TMP layers have already received complete text and
            // vertex visibility while non-renderable. Alpha is established
            // before this can submit a frame to the renderer.
            if (hiddenDialogueCanvasGroup != null) hiddenDialogueCanvasGroup.alpha = 0f;
            if (hiddenDialogueViewport != null) hiddenDialogueViewport.gameObject.SetActive(true);
            SyncHiddenDialogueText();
        }

        private void InitializeSubtitlePagePresentation(int pageIndex, bool seedInitialDueWord)
        {
            if (subtitleWordSchedule.Count == 0)
            {
                if (seedInitialDueWord) hiddenSubtitleReveal.RevealNext();
                return;
            }

            if (pageIndex < 0 || pageIndex >= subtitlePageWordRanges.Count) return;
            // Timestamp-due words during a non-renderable page transition are
            // pending presentation, not already shown. Seed only the initial
            // page's first due word so its root fade has visible glyphs.
            if (seedInitialDueWord && GetSubtitleDueWordCount() >
                subtitlePageWordRanges[pageIndex].FirstWordIndex)
                hiddenSubtitleReveal.RevealNext();
        }

        private void AdvanceSubtitleReveal()
        {
            if (subtitleWordSchedule.Count == 0)
            {
                hiddenSubtitleReveal.Advance(Time.unscaledDeltaTime);
                return;
            }

            int dueWords = GetSubtitleDueWordCount();

            int pageStart = subtitlePageIndex >= 0 && subtitlePageIndex < subtitlePageWordRanges.Count
                ? subtitlePageWordRanges[subtitlePageIndex].FirstWordIndex : 0;
            int dueOnCurrentPage = Mathf.Clamp(dueWords - pageStart, 0, hiddenSubtitleReveal.WordCount);
            // Keep timingDue and presentationShown separate. A burst of words
            // due while the page was non-renderable is caught up one visible
            // token at a time, in order, rather than silently consumed by
            // RevealTo before the reader can see it.
            if (hiddenSubtitleReveal.RevealedTokenCount < dueOnCurrentPage &&
                !hiddenSubtitleReveal.LatestTokenIsFading)
                hiddenSubtitleReveal.RevealNext();
            hiddenSubtitleReveal.AdvanceLatestTokenFade(Time.unscaledDeltaTime);
        }

        private float SubtitleScheduleElapsed()
        {
            float origin = subtitleTimingUsesPlaybackClock ? subtitlePlaybackStartedAt : subtitlePresentationStartedAt;
            return Mathf.Max(0f, Time.unscaledTime - origin);
        }

        private int GetSubtitleDueWordCount()
        {
            float elapsed = SubtitleScheduleElapsed();
            int dueWords = 0;
            while (dueWords < subtitleWordSchedule.Count && subtitleWordSchedule[dueWords] <= elapsed) dueWords++;
            return dueWords;
        }

        private bool IsSubtitlePageFinalWordDue(int pageIndex)
        {
            if (subtitleWordSchedule.Count == 0) return hiddenSubtitleReveal.IsComplete;
            if (pageIndex < 0 || pageIndex >= subtitlePageWordRanges.Count) return false;
            return SubtitleTimingPlan.IsPageFinalWordDue(
                subtitlePageWordRanges[pageIndex], subtitleWordSchedule, SubtitleScheduleElapsed());
        }

        private bool IsSubtitlePageVisuallyComplete(int pageIndex)
        {
            return hiddenSubtitleReveal.IsComplete && !hiddenSubtitleReveal.LatestTokenIsFading &&
                IsSubtitlePageFinalWordDue(pageIndex);
        }

        private void LogSubtitleTimingDiagnostics(float audioDurationSeconds, bool validAlignment, float rawFinalWordTimestamp)
        {
            if (subtitleWordSchedule.Count == 0) return;
            float first = subtitleWordSchedule[0];
            float final = subtitleWordSchedule[subtitleWordSchedule.Count - 1];
            string ratio = audioDurationSeconds > 0f ? (rawFinalWordTimestamp / audioDurationSeconds).ToString("F3") : "n/a";
            List<string> pageRanges = new List<string>();
            foreach (SubtitlePageWordRange range in subtitlePageWordRanges)
            {
                float last = range.LastWordIndex >= 0 && range.LastWordIndex < subtitleWordSchedule.Count
                    ? subtitleWordSchedule[range.LastWordIndex] : -1f;
                pageRanges.Add(range.FirstWordIndex + "-" + range.LastWordIndex + "@" + last.ToString("F3"));
            }
            Debug.Log("[AIFren Subtitle] timing diagnostics source=" +
                (validAlignment ? "Kokoro" : "fallback") + "; audio=" + audioDurationSeconds.ToString("F3") +
                "s; first-visible=" + first.ToString("F3") + "s; final-visible=" + final.ToString("F3") +
                "s; raw-final/audio=" + ratio + "; words=" + subtitleWordSchedule.Count +
                "; pages=" + string.Join(",", pageRanges) + ".");
        }

        private void HideHiddenSubtitleImmediately()
        {
            hiddenSubtitleTemporarilySuppressed = false;
            hiddenSubtitlePresenter?.Cancel();
        }

        private void SuppressHiddenSubtitleForTemporaryReveal()
        {
            if (hiddenSubtitleTemporarilySuppressed || hiddenDialogueViewport == null) return;
            hiddenSubtitleTemporarilySuppressed = true;
            hiddenSubtitlePresenter?.SetSuppressed(true, Time.unscaledTime);
        }

        private void RestoreHiddenSubtitleAfterTemporaryReveal()
        {
            if (!hiddenSubtitleTemporarilySuppressed) return;
            bool restore = interfaceHidden && showDialogueWhenHidden &&
                hiddenSubtitlePresenter != null && hiddenSubtitlePresenter.IsActive;
            hiddenSubtitleTemporarilySuppressed = false;
            if (!restore || hiddenDialogueViewport == null) return;
            hiddenSubtitlePresenter?.SetSuppressed(false, Time.unscaledTime);
        }

        private void ResetPresentationDefaults()
        {
            // Global safe-settings reset. It deliberately excludes data,
            // secrets, assets, and avatar framing; none of those are settings.
            graphicsQuality = PresentationGraphicsQuality.High;
            avatarRenderScale = DefaultAvatarRenderScale(graphicsQuality);
            showDialogueWhenHidden = false;
            alwaysOnTop = false;
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
            ApplyAlwaysOnTop();
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
            if (alwaysOnTopToggle != null) alwaysOnTopToggle.SetIsOnWithoutNotify(alwaysOnTop);
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
            RefreshDisplayLayout();
            PresentationDisplaySettings normalized = PresentationDisplaySettingsPolicy.NormalizeForScreen(settings, Screen.width, Screen.height);
            if (normalized.displayMode != PresentationDisplayMode.Windowed &&
                normalized.displayIndex >= 0 && normalized.displayIndex < displayLayout.Count)
            {
                // DisplayInfo reports the physical display bounds, not the
                // desktop work area. Every non-windowed mode must use these
                // exact dimensions so a portrait monitor stays portrait.
                DisplayInfo target = displayLayout[normalized.displayIndex];
                if (target.width > 0 && target.height > 0)
                {
                    normalized.width = target.width;
                    normalized.height = target.height;
                }
            }
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
            PresentationDisplaySettings runtime = CaptureRuntimeDisplaySettings();
            FullScreenMode unityMode = UnityModeForDisplaySettings(normalized);
            bool requiresWindowChange = normalized.width != Screen.width ||
                normalized.height != Screen.height || unityMode != Screen.fullScreenMode;
            bool requiresDisplayMove = forceStartupDisplayMove ||
                PresentationDisplaySettingsPolicy.ShouldDeferResolutionUntilDisplayMove(
                    normalized.displayIndex, runtime.displayIndex);
            // Changing a monitor must move the native window first. Applying a
            // destination resolution while the window is still on the source
            // display is what leaked a secondary monitor's size onto primary.
            if (requiresWindowChange && !requiresDisplayMove)
            {
                LogFullscreenTransition("request", normalized, unityMode);
                Screen.SetResolution(normalized.width, normalized.height, unityMode);
            }
            currentDisplaySettings = normalized.Clone();
            pendingDisplaySettings = normalized.Clone();
            if (requiresWindowChange || requiresDisplayMove)
            {
                if (forceStartupDisplayMove) startupDisplayFinalizationPending = true;
                StartCoroutine(MoveMainWindowThenApplyResolution(normalized, requiresWindowChange, forceStartupDisplayMove));
            }
            else
            {
                LogFullscreenTransition("request", normalized, unityMode);
                ApplyNativeFullscreenState(normalized);
                LogFullscreenTransition("settled", normalized, unityMode);
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

        private IEnumerator MoveMainWindowThenApplyResolution(PresentationDisplaySettings settings, bool applyResolutionAfterMove, bool startupMove)
        {
            // Move first so a requested size is always applied to its selected
            // display, never the display the window is leaving.
            yield return null;
            RefreshDisplayLayout();
            if (settings.displayIndex >= 0 && settings.displayIndex < displayLayout.Count &&
                displayLayout[settings.displayIndex].width > 0)
            {
                DisplayInfo targetDisplay = displayLayout[settings.displayIndex];
                Screen.MoveMainWindowTo(targetDisplay, Vector2Int.zero);
            }
            yield return null;
            if (applyResolutionAfterMove)
            {
                LogFullscreenTransition("request", settings, UnityModeForDisplaySettings(settings));
                Screen.SetResolution(settings.width, settings.height,
                    UnityModeForDisplaySettings(settings));
                yield return null;
            }
            if (settings.displayIndex >= 0 && settings.displayIndex < displayLayout.Count &&
                displayLayout[settings.displayIndex].width > 0)
            {
                // Mode changes can make an X11 WM reapply work-area geometry.
                // Move again after the transition to pin the client origin to
                // the selected display's true (0,0) corner.
                Screen.MoveMainWindowTo(displayLayout[settings.displayIndex], Vector2Int.zero);
                yield return null;
            }
            ApplyNativeFullscreenState(settings);
            yield return null;
            LogFullscreenTransition("settled", settings, UnityModeForDisplaySettings(settings));
            // Native window movement and mode changes settle asynchronously;
            // finish with one canonical geometry refresh.
            Canvas.ForceUpdateCanvases();
            FinalizeDisplayGeometry();
            if (startupMove) startupDisplayFinalizationPending = false;
        }

        private static FullScreenMode UnityModeForDisplaySettings(PresentationDisplaySettings settings)
        {
            // Unity's ExclusiveFullScreen implementation can select an
            // unrotated landscape XRandR mode on Linux. EWMH fullscreen over
            // Unity's borderless window preserves the selected output's real
            // portrait geometry instead.
            if (Application.platform == RuntimePlatform.LinuxPlayer && settings != null &&
                settings.displayMode == PresentationDisplayMode.Fullscreen)
                return FullScreenMode.FullScreenWindow;
            return PresentationDisplaySettingsPolicy.ToUnityMode(settings.displayMode);
        }

        private static void ApplyNativeFullscreenState(PresentationDisplaySettings settings)
        {
            if (Application.platform != RuntimePlatform.LinuxPlayer || settings == null) return;
            if (!LinuxWindowAlwaysOnTop.TrySetFullscreen(settings.displayMode != PresentationDisplayMode.Windowed, out string detail))
                Debug.Log("AIFren borderless X11 state: " + detail);
        }

        private void LogFullscreenTransition(string stage, PresentationDisplaySettings settings, FullScreenMode unityMode)
        {
            if (settings == null || settings.displayMode != PresentationDisplayMode.Fullscreen) return;
            DisplayInfo target = settings.displayIndex >= 0 && settings.displayIndex < displayLayout.Count
                ? displayLayout[settings.displayIndex] : default(DisplayInfo);
            Resolution current = Screen.currentResolution;
            string geometry = LinuxWindowAlwaysOnTop.TryGetFocusedWindowGeometry(out string x11) ? x11 : "unavailable";
            Debug.Log("[AIFren Fullscreen] " + stage +
                "; selected=" + settings.displayIndex + " " + target.name + " " + target.width + "x" + target.height +
                "; screen=" + Screen.width + "x" + Screen.height +
                "; currentResolution=" + current.width + "x" + current.height +
                "; requested=" + settings.width + "x" + settings.height +
                "; unityMode=" + unityMode + "; x11=" + geometry + ".");
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
            if (alwaysOnTop) StartCoroutine(ApplyAlwaysOnTopAfterWindowCreation());
        }

        private void SynchronizeAppliedDisplaySettings()
        {
            if (currentDisplaySettings == null) return;
            PresentationDisplaySettings runtime = CaptureRuntimeDisplaySettings();
            // Linux regular fullscreen intentionally uses Unity's
            // FullScreenWindow plus EWMH, so retain the user's requested
            // Fullscreen setting instead of misreporting it as Borderless.
            if (!(Application.platform == RuntimePlatform.LinuxPlayer &&
                currentDisplaySettings.displayMode == PresentationDisplayMode.Fullscreen))
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
            // The avatar viewport is the full game window in every state.
            // Dialogue, controls, and hidden-UI transitions are overlays; they
            // must never resize or reposition the avatar presentation.
            Stretch(avatarFrameRect, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero);
            if (portrait)
            {
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
                avatarLoader.SetPresentationViewportPixels(FullAvatarPresentationPixels());
                avatarLoader.SetPreviewSurface(avatarSurface);
            }
            ApplyAvatarPresentationTransform(portrait);
            ApplyAvatarViewerBackground();
            LayoutHiddenSubtitleRegion();
            if (avatarViewEditing) SyncAvatarViewControls();
            LogAvatarContainerMetrics(interfaceHidden);
            PlacePttPresentation();
        }

        private static Vector2 FullAvatarPresentationPixels()
        {
            return new Vector2(Screen.width, Screen.height);
        }

        private void LayoutHiddenSubtitleRegion()
        {
            if (hiddenDialogueViewport == null || Screen.width <= 0 || Screen.height <= 0) return;

            // Anchor from the physical safe area, not an arbitrary vertical
            // center. The 24%-high reserved region begins just above a modest
            // bottom margin, leaving the subtitle in a classic lower-screen
            // position while top-aligned words grow safely downward inside it.
            Rect safe = Screen.safeArea;
            float safeLeft = safe.xMin / Screen.width;
            float safeRight = safe.xMax / Screen.width;
            float safeBottom = safe.yMin / Screen.height;
            float safeTop = safe.yMax / Screen.height;
            float left = Mathf.Clamp01(safeLeft + .03f);
            float right = Mathf.Clamp01(safeRight - .03f);
            float bottom = Mathf.Clamp01(safeBottom + .025f);
            float top = Mathf.Min(safeTop - .02f, bottom + .24f);
            if (right <= left) { left = .03f; right = .97f; }
            if (top <= bottom) top = Mathf.Min(1f, bottom + .18f);
            Stretch(hiddenDialogueViewport, new Vector2(left, bottom), new Vector2(right, top), Vector2.zero, Vector2.zero);
        }

        private void LogAvatarContainerMetrics(bool uiHidden)
        {
            if (avatarFrameRect == null) return;
            Vector3[] corners = new Vector3[4];
            avatarFrameRect.GetWorldCorners(corners);
            Vector2 bottomLeft = RectTransformUtility.WorldToScreenPoint(null, corners[0]);
            Vector2 topRight = RectTransformUtility.WorldToScreenPoint(null, corners[2]);
            Vector2 containerSize = new Vector2(
                Mathf.Abs(topRight.x - bottomLeft.x),
                Mathf.Abs(topRight.y - bottomLeft.y));
            // CanvasScaler can expose one transient pre-layout world rect.
            // Wait for the settled on-screen container rather than logging a
            // misleading oversized intermediate measurement.
            if (containerSize.x > Screen.width * 1.01f || containerSize.y > Screen.height * 1.01f) return;
            if (Vector2.SqrMagnitude(containerSize - lastLoggedAvatarContainerSize) < .25f &&
                uiHidden == lastLoggedAvatarContainerUiHidden) return;

            lastLoggedAvatarContainerSize = containerSize;
            lastLoggedAvatarContainerUiHidden = uiHidden;
            Debug.Log(string.Format(
                "[AIFren Avatar] presentation container {0:F0}x{1:F0} screen pixels ({2:P0} x {3:P0} of {4}x{5}); UI hidden={6}.",
                containerSize.x, containerSize.y,
                containerSize.x / Mathf.Max(1f, Screen.width), containerSize.y / Mathf.Max(1f, Screen.height),
                Screen.width, Screen.height, uiHidden));
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

        private void ApplyAvatarPresentationTransform(bool portrait)
        {
            if (avatarFrameRect == null)
            {
                return;
            }

            if (avatarPresentationState == null) avatarPresentationState = AvatarPresentationState.Load(AvatarConfiguration.Load());
            AvatarPresentationValues presentation = avatarPresentationState.GetValues(portrait);
            if (useDirectAvatarPresentation)
            {
                avatarLoader?.SetDirectPresentationValues(presentation);
                return;
            }
            if (avatarSurface == null) return;
            // Always sample the complete padded avatar render. The child is
            // scaled and translated inside its masked container to compose the
            // face/upper-body view without ever changing camera framing.
            avatarSurface.uvRect = new Rect(0f, 0f, 1f, 1f);
            avatarSurface.rectTransform.localScale = Vector3.one * presentation.scale;
            Rect container = avatarFrameRect.rect;
            avatarSurface.rectTransform.anchoredPosition = new Vector2(
                container.width * presentation.x,
                container.height * presentation.y
            );

            if (avatarAspectFitter != null)
            {
                Texture texture = avatarSurface.texture;
                avatarAspectFitter.aspectRatio = texture != null
                    ? texture.width / (float)Mathf.Max(1, texture.height)
                    : 1f;
                avatarAspectFitter.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
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

        private TMP_InputField CreateInputField(Transform parent, bool multiline = false)
        {
            GameObject field = CreatePanel(parent, "Input Field", new Color(0.12f, 0.12f, 0.20f, 1f));
            TMP_InputField inputField = field.AddComponent<TMP_InputField>();
            // TMP_InputField creates its internal Caret CanvasRenderer from
            // textComponent in OnEnable. This UI is built at runtime, so
            // assign its references while disabled, then enable it once they
            // exist; otherwise typing works but TMP never creates a caret.
            inputField.enabled = false;
            inputField.lineType = TMP_InputField.LineType.SingleLine;
            inputField.characterLimit = 4000;
            inputField.customCaretColor = true;
            inputField.caretColor = Ink;
            inputField.caretWidth = 2;
            inputField.caretBlinkRate = .85f;

            Transform textParent = field.transform;
            RectTransform viewport = null;
            if (multiline)
            {
                GameObject textArea = new GameObject("Text Area", typeof(RectTransform), typeof(RectMask2D));
                textArea.transform.SetParent(field.transform, false);
                viewport = textArea.GetComponent<RectTransform>();
                Stretch(viewport, new Vector2(0.03f, 0.08f), new Vector2(0.97f, 0.92f), Vector2.zero, Vector2.zero);
                textParent = textArea.transform;
            }
            TMP_Text placeholder = CreateText(textParent, "Say something…", 22f, new Color(0.60f, 0.59f, 0.68f, 1f), TextAlignmentOptions.MidlineLeft);
            Stretch(placeholder.rectTransform, multiline ? Vector2.zero : new Vector2(0.03f, 0.08f), multiline ? Vector2.one : new Vector2(0.97f, 0.92f), Vector2.zero, Vector2.zero);
            TMP_Text text = CreateText(textParent, string.Empty, 22f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(text.rectTransform, multiline ? Vector2.zero : new Vector2(0.03f, 0.08f), multiline ? Vector2.one : new Vector2(0.97f, 0.92f), Vector2.zero, Vector2.zero);
            if (multiline) ChatInputFieldLayout.Configure(inputField, viewport, text as TextMeshProUGUI, placeholder as TextMeshProUGUI);
            else
            {
                inputField.textViewport = field.GetComponent<RectTransform>();
                inputField.textComponent = text as TextMeshProUGUI;
                inputField.placeholder = placeholder as TextMeshProUGUI;
            }
            inputField.enabled = true;
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
            ReleaseUnityPushToTalk();
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
