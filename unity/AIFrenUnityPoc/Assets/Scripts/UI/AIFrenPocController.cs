using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
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
    public sealed partial class AIFrenPocController : MonoBehaviour
    {
        private enum HistoryNavigationLevel { Years, Months, Days, Messages }

        private sealed class PreparedSubtitlePlan
        {
            internal string Source;
            internal string Spoken;
            internal List<SubtitlePage> Pages;
            internal List<SubtitlePageWordRange> Ranges;
            internal float PreparationMilliseconds;
        }

        private static string BackendUri => NativeQaSession.Endpoint ?? "ws://127.0.0.1:8765";
        private const string RevealSpeedPreference = "AIFren.DialogueRevealSpeed";
        private const string InstantTextPreference = "AIFren.InstantDialogueText";
        private const string DisplaySettingsPreference = "AIFren.PresentationDisplaySettings.v1";
        private const string PushToTalkBindingPreference = "AIFren.PushToTalkBinding";
        private const string PttAutoSendPreference = "AIFren.PttAutoSend";
        private const string AvatarRenderScalePreference = "AIFren.AvatarRenderScale";
        private const string GraphicsQualityPreference = "AIFren.GraphicsQuality";
        private const string ShowDialogueWhenHiddenPreference = "AIFren.ShowDialogueWhenHidden";
        private const string AlwaysOnTopPreference = "AIFren.AlwaysOnTop";
        private const string AvatarLightingPreference = "AIFren.AvatarLighting";
        private const string SceneOverlayPreference = "AIFren.SceneOverlay";
        private const float DefaultAvatarLighting = 1.3f;
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
        private const int HistoryMessagePageSize = 80;

        private static readonly Color Ink = new Color(0.95f, 0.94f, 0.99f, 1f);
        private static readonly Color Panel = new Color(0.07f, 0.07f, 0.13f, 0.88f);
        private static readonly Color Accent = new Color(0.83f, 0.55f, 0.91f, 1f);
        private static readonly Color UserAccent = new Color(0.42f, 0.72f, 0.92f, 1f);

        private readonly List<ConversationMessage> messages = new List<ConversationMessage>();
        private readonly HashSet<string> canonicalMessageIds = new HashSet<string>(StringComparer.Ordinal);
        private bool historyDirty = true;
        private readonly WordReveal wordReveal = new WordReveal();

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
        private TMP_Text truthScopeIndicatorLabel;
        private TMP_Text continuityScopeValue;
        private TMP_Text continuityActivityValue;
        private TMP_Text continuityCompanionActivityValue;
        private TMP_Text continuitySceneValue;
        private ScrollRect continuitySceneScroll;
        private RectTransform continuitySceneContent;
        private Toggle sceneOverlayToggle;
        private GameObject sceneOverlayPanel;
        private SceneDrawerPresenter sceneDrawer;
        private Button sceneDrawerTab;
        private string sceneDrawerRowsKey;
        private float sceneDrawerWidth, sceneDrawerContentHeight;
        private Transform sceneOverlayContent;
        private ScrollRect sceneOverlayScroll;
        private bool showSceneOverlay;
        private readonly ResponsePresentationTurn presentationTurn = new ResponsePresentationTurn();
        private TMP_Text proactiveEligibilityValue;
        private Button clearContinuityActivityButton;
        private Button leaveContinuityScenarioButton;
        private Transform continuityThreadsContent;
        private ContinuitySnapshot authoritativeContinuity;
        private bool sceneProjectionDirty;
        private CharacterSessionOwner pendingContinuityOwner;
        private string pendingContinuityCommandId;
        private string pendingContinuityAction;
        private string pendingContinuityActionToken;
        private string pendingContinuityRevision;
        private bool pendingContinuityRetrySent;
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
        private GameObject memoryViewerPanel;
        private Transform memoryViewerRows;
        private ScrollRect memoryViewerScroll;
        private TMP_Text memoryViewerAuthority;
        private TMP_Text memoryViewerWarning;
        private TMP_Text memoryViewerDetails;
        private ScrollRect memoryViewerDetailsScroll;
        private TMP_InputField memoryViewerSearchInput;
        private TMP_InputField memoryViewerContentInput;
        private TMP_InputField memoryViewerCategoryInput;
        private TMP_InputField memoryViewerImportanceInput;
        private Button memoryViewerLaneButton;
        private Button memoryViewerStatusButton;
        private Button memoryViewerScopeButton;
        private Button memoryViewerPreviousButton;
        private Button memoryViewerNextButton;
        private Button memoryViewerSaveButton;
        private Button memoryViewerRetireButton;
        private Button memoryViewerMoreDetailButton;
        private readonly MemoryViewerState memoryViewerState = new MemoryViewerState();
        private bool memoryViewerRetireConfirmation;
        private Transform modelLibraryTiles;
        private Transform backgroundLibraryTiles;
        private readonly HashSet<string> selectedModelAssets = new HashSet<string>();
        private readonly HashSet<string> selectedBackgroundAssets = new HashSet<string>();
        private readonly HashSet<string> thumbnailGenerationInFlight = new HashSet<string>();
        // Model loading changes live Unity objects asynchronously. Queue the
        // most recent request and let only that request commit UI/state.
        private ManagedAssetRecord pendingModelApply;
        private bool pendingBundledModelApply;
        private bool pendingModelApplyRemoveOnFailure;
        private bool pendingModelApplyPersistsSelection = true;
        private string pendingModelApplyCharacterId;
        private bool modelApplyInProgress;
        private string modelApplyInFlightId;
        private int modelApplyGeneration;
        private bool characterAvatarSwitchInFlight;
        private PresentationMetadata authoritativeStatePresentation;
        private Button deleteModelAssetsButton;
        private Button deleteBackgroundAssetsButton;
        private Button renameModelAssetButton;
        private Button renameBackgroundAssetButton;
        private GameObject modelDeleteConfirmPanel;
        private GameObject backgroundDeleteConfirmPanel;
        private GameObject assetRenamePanel;
        private TMP_InputField assetRenameInput;
        private TMP_Text assetRenameMessage;
        private string renameAssetKind;
        private string renameAssetId;
        private Transform historyContent;
        private ScrollRect historyScroll;
        private Button historyBackButton;
        private TMP_Text historyPathLabel;
        private readonly ConversationHistoryIndex historyIndex = new ConversationHistoryIndex();
        private readonly List<TMP_Text> historyTextRows = new List<TMP_Text>();
        private readonly List<Button> historyButtonRows = new List<Button>();
        private int activeHistoryTextRows;
        private int activeHistoryButtonRows;
        private HistoryNavigationLevel historyLevel = HistoryNavigationLevel.Messages;
        private HistoryDayKey selectedHistoryDay;
        private int selectedHistoryYear;
        private int selectedHistoryMonth;
        private int selectedHistoryPage;
        private Slider volumeSlider;
        private bool ttsVolumeDirty;
        private float pendingTtsVolume;
        private float nextTtsVolumeSendAt;
        private Slider revealSlider;
        private Slider avatarLightingSlider;
        private TMP_Text avatarLightingValue;
        private float avatarLightingMultiplier = DefaultAvatarLighting;
        private Toggle instantTextToggle;
        private Toggle earlySpeechToggle;
        private Button proactiveIntervalButton;
        private bool authoritativeProactiveBehavior = true;
        private int authoritativeProactiveIntervalSeconds = 3600;
        private string latestProactiveEligibility = "unavailable";
        private int latestProactiveNextSeconds = -1;
        private int latestProactiveIgnoredStreak;
        private static readonly int[] ProactiveIntervals = { 0, 30, 60, 300, 600, 900, 1800, 2700, 3600, 7200, 10800, 14400, 18000, 21600 };
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
        private TMP_Text localSelectedModelValue;
        private TMP_Text localRuntimeStatusValue;
        private TMP_Text localComputeValue;
        private TMP_Text localContextCapacityValue;
        private TMP_Text ttsProviderValue;
        private TMP_Text ttsVoiceValue;
        private TMP_Text ttsDeviceValue;
        private TMP_InputField geminiApiKeyInput;
        private Button onlineModelModeButton;
        private Button localModelModeButton;
        private readonly List<GameObject> onlineModelControls = new List<GameObject>();
        private readonly List<GameObject> localModelControls = new List<GameObject>();
        private string selectedModelMode = "online";
        private string authoritativeModelMode = "online";
        private string pendingModelMode;
        private bool? pendingLocalAutoStart;
        private bool? pendingEarlySpeech;
        private bool authoritativeEarlySpeech = true;
        private ModelSettingsSnapshot authoritativeModelSettings;
        private TMP_InputField localEndpointInput;
        private TMP_Text localModelSelectionValue;
        private Toggle localAutoStartToggle;
        private Button startLocalModelButton;
        private Button stopLocalModelButton;
        private readonly List<LocalModelOption> localModelOptions = new List<LocalModelOption>();
        private LocalModelRuntimeSnapshot localModelRuntime;
        private GameObject localModelPickerPanel;
        private TMP_Text currentCharacterValue;
        private Transform characterListContent;
        private TMP_InputField newCharacterNameInput;
        private TMP_InputField newCharacterPersonalityInput;
        private readonly List<CharacterSummary> availableCharacters = new List<CharacterSummary>();
        private bool characterSwitchInFlight;
        private string activeCharacterSession;
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
        private string activeCharacterId;
        private string visibleState = "Disconnected";
        private ConnectionState lastObservedConnectionState = ConnectionState.Disconnected;
        private string detail = "Start backend_host.py to connect.";
        private bool submitInFlight;
        private readonly PttThinkingPresentationState pttThinkingPresentation =
            new PttThinkingPresentationState();
        private bool backendReconnectInProgress;
        private bool instantText;
        private float revealWordsPerSecond;
        // A fixed, non-accumulating subtitle lead keeps the caption close to
        // speech without distorting provider/fallback word spacing.
        private const float HiddenSubtitleLeadSeconds = .10f;
        private const float HiddenSubtitlePageFadeOutSeconds = .09f;
        private const float HiddenSubtitlePageFadeInSeconds = .12f;
        private string pendingAssistantContent;
        private bool assistantStreamVisible;
        private bool assistantStreamPresentationDirty;
        private float nextAssistantStreamPresentationAt;
        private const float AssistantStreamPresentationIntervalSeconds = .05f;
        private bool pendingAssistantReveal;
        private bool pendingSpeechReady;
        private float pendingSpeechDuration;
        private bool streamedSubtitleMode;
        private int streamedSubtitleTurnId;
        private readonly Dictionary<string, PreparedSubtitlePlan> preparedSubtitlePlans =
            new Dictionary<string, PreparedSubtitlePlan>();
        private readonly Queue<string> preparedSubtitlePlanOrder = new Queue<string>();
        private const int PreparedSubtitlePlanLimit = 8;
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
        private bool hiddenSubtitleSuppressedByUi;
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
        private TMP_Text hiddenSubtitleMeasurementText;
        private RectTransform hiddenDialogueViewport;
        private ScrollRect hiddenDialogueScroll;
        private Scrollbar hiddenDialogueScrollbar;
        private CanvasGroup hiddenDialogueCanvasGroup;
        private Material hiddenSubtitleMaterial;
        private TMP_FontAsset hiddenSubtitleFont;
        private TmpHiddenSubtitleRenderTarget hiddenSubtitleRenderTarget;
        private HiddenSubtitlePresenter hiddenSubtitlePresenter;
        private string currentAssistantPresentationText = string.Empty;
        private bool subtitleSpeechActive;
        private readonly List<string> subtitlePages = new List<string>();
        private int subtitleGeneration;
        private int subtitlePlaybackGeneration = -1;
        private float subtitleSpeechDuration;
        private bool subtitleAwaitingPlayback;
        private readonly List<float> subtitleWordSchedule = new List<float>();
        private readonly List<SubtitlePageWordRange> subtitlePageWordRanges = new List<SubtitlePageWordRange>();
        private float subtitlePlaybackStartedAt;
        private int subtitlePlaybackId;
        private float subtitleResponseReceivedAt;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private DevelopmentFrameProfiler developmentFrameProfiler;
        private DevelopmentFlightRecorder developmentFlightRecorder;
        private string flightRecorderDumpBuffer = string.Empty;
        private int flightRecorderTurnId;
        private bool activeTurnIsProactive;
        private bool flightRecorderFirstDeltaSeen;
        private bool developmentProfileQa;
        private bool verboseSubtitleDiagnostics;
        private bool developmentProfileQaStarted;
        private int developmentProfileScenarioIndex = -1;
        private int developmentProfileDeltaCount;
        private bool developmentProfileAdvancePending;
        private bool developmentLongPeekScheduled;
        private bool dialogueCanonicalFinalReceived;
        private bool dialogueMidRevealRecorded;
        private bool dialogueManualBottomRecorded;
        private readonly string[] developmentProfileScenarios = { "cold", "warm", "long" };
#endif

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
            avatarLoader.SetPresentationLightingMultiplier(avatarLightingMultiplier);
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
            // Rejected QA startup must never discover an ordinary backend.
            if (NativeQaSession.RejectOrdinaryStartup) return;
            string[] commandLine = Environment.GetCommandLineArgs();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentProfileQa = commandLine.Contains("-aifren-profile-qa");
            verboseSubtitleDiagnostics = commandLine.Contains("-aifren-verbose-subtitles");
            if (developmentProfileQa)
                developmentFrameProfiler = gameObject.AddComponent<DevelopmentFrameProfiler>();
#endif
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
                PlayerPrefs.DeleteKey(SceneOverlayPreference);
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
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (NativeQaSession.Active)
                currentDisplaySettings = new PresentationDisplaySettings {
                    displayIndex = NativeQaSession.DisplayIndex,
                    width = NativeQaSession.Current.width, height = NativeQaSession.Current.height,
                    displayMode = PresentationDisplayMode.BorderlessFullscreen,
                    frameLimit = 60 };
#endif
            pendingDisplaySettings = currentDisplaySettings.Clone();
            graphicsQuality = (PresentationGraphicsQuality)Mathf.Clamp(
                PlayerPrefs.GetInt(GraphicsQualityPreference, (int)PresentationGraphicsQuality.High),
                (int)PresentationGraphicsQuality.Low, (int)PresentationGraphicsQuality.Ultra);
            avatarRenderScale = Mathf.Clamp(PlayerPrefs.GetFloat(
                AvatarRenderScalePreference, DefaultAvatarRenderScale(graphicsQuality)), 1f, 2f);
            avatarLightingMultiplier = Mathf.Clamp(PlayerPrefs.GetFloat(AvatarLightingPreference, DefaultAvatarLighting), 0f, 2f);
            showDialogueWhenHidden = PlayerPrefs.GetInt(ShowDialogueWhenHiddenPreference, 0) == 1;
            alwaysOnTop = PlayerPrefs.GetInt(AlwaysOnTopPreference, 0) == 1;
            if (NativeQaSession.Active) alwaysOnTop = false;
            showSceneOverlay = PlayerPrefs.GetInt(SceneOverlayPreference, 0) == 1;
            avatarPresentationState = AvatarPresentationState.CreateUnbound(AvatarConfiguration.Load());
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
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (developmentProfileQa)
            {
                // The acceptance harness is deliberately portrait and local
                // to Development builds. It does not persist display or UI
                // settings into the ordinary product configuration.
                Screen.SetResolution(900, 1600, FullScreenMode.Windowed);
                showDialogueWhenHidden = true;
                interfaceHidden = true;
                inputRequested = false;
                inputVisibilityTarget = 0f;
                RefreshPresentationVisibility();
            }
#endif

            if (avatarLoader != null)
            {
                avatarLoader.SetPreviewSurface(avatarSurface);
                avatarLoader.SetPresentationRenderScale(avatarRenderScale);
                avatarLoader.SetPresentationLightingMultiplier(avatarLightingMultiplier);
            }
            ApplyAvatarPresentationMode();
            if (alwaysOnTop)
            {
                StartCoroutine(ApplyAlwaysOnTopAfterWindowCreation());
            }

            client = new AIFrenWebSocketClient();
            await ConnectAsync();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (NativeQaSession.Active) StartCoroutine(RunNativeQa());
#endif
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (!commandLine.Contains("-aifren-no-flight-recorder"))
            {
                developmentFlightRecorder = gameObject.AddComponent<DevelopmentFlightRecorder>();
                developmentFlightRecorder.Initialize(client);
            }
#endif
        }

        private void Update()
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFlightRecorder?.SetPresentationState(
                interfaceHidden,
                hiddenSubtitlePresenter != null && hiddenSubtitlePresenter.IsActive,
                avatarAnimation != null,
                subtitleSpeechActive);
#endif
            if (Screen.width != lastScreenWidth || Screen.height != lastScreenHeight)
            {
                lastScreenWidth = Screen.width;
                lastScreenHeight = Screen.height;
                UpdateDialogueLayout(false);
                UpdateCompositionLayout();
                UpdateBackgroundCover();
                RefreshSceneOverlay(authoritativeContinuity);
            }

            UpdateDisplayConfirmation();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (!developmentProfileQa && !NativeQaSession.Active) UpdateHiddenInterfaceReveal();
#else
            UpdateHiddenInterfaceReveal();
#endif
            if (!NativeQaSession.Active) UpdateUnityPushToTalk();
            UpdateSubtitlePaging();

            if (!NativeQaSession.Active) HandlePresentationInput();
            UpdateInputPresentation();

            if (client != null)
            {
                while (client.TryDequeue(out ServerMessage message))
                {
                    HandleServerMessage(message);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    ObserveNativeQaMessage(message);
#endif
                }

                // Conversation messages commonly arrive as a user/assistant pair.
                // Render the visible history once after draining that event burst,
                // and do no TMP/layout work at all while the panel is hidden.
                CheckViewRequests(Time.realtimeSinceStartup);
                RefreshHistoryIfVisible();

                if (assistantStreamPresentationDirty &&
                    Time.unscaledTime >= nextAssistantStreamPresentationAt)
                {
                    RefreshStreamedAssistantDialogue(false);
                }

                if (client.State == ConnectionState.Disconnected && visibleState != "Disconnected")
                {
                    presentationTurn.Reset();
                    avatarAnimation?.RetireResponseMotion();
                    ApplyStatus("disconnected", "Backend is unavailable or disconnected.");
                    ApplyTruthScopeIndicator("real_world", string.Empty);
                    backendGlobalPtt = false;
                    UpdatePttIndicator("ready");
                }
                else if (client.State == ConnectionState.Error)
                {
                    ApplyStatus("error", client.LastError);
                    ApplyTruthScopeIndicator("real_world", string.Empty);
                    submitInFlight = false;
                    backendGlobalPtt = false;
                    UpdatePttIndicator("ready");
                    RefreshInputAvailability();
                }

                UpdateBackendDisconnectWarning();
            }

            if (wordReveal.Advance(Time.unscaledDeltaTime))
            {
                RefreshDialogueRevealText(!wordReveal.IsComplete || assistantStreamVisible);
                bool dialogueContentResized = RefreshDialogueScrollableContent();
                if (dialogueAutoFollow && dialogueContentResized)
                    FollowScrollIfNearBottom(dialogueScroll);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (dialogueCanonicalFinalReceived && !dialogueMidRevealRecorded &&
                    !wordReveal.IsComplete && wordReveal.WordCount > 0 &&
                    wordReveal.RevealedTokenCount * 2 >= wordReveal.WordCount)
                {
                    dialogueMidRevealRecorded = true;
                    RecordDialogueLayoutState("mid_reveal");
                }
                if (wordReveal.IsComplete)
                {
                    RecordDialogueLayoutState("reveal_complete");
                    ScheduleDialogueLayoutFollowup("reveal_complete");
                }
#endif
            }

            if (ttsVolumeDirty && Time.unscaledTime >= nextTtsVolumeSendAt)
            {
                SendPendingTtsVolume();
            }

            if (visibleState == "Thinking" && dialogueTextLabel != null && !pendingAssistantReveal &&
                !assistantStreamVisible)
            {
                thinkingElapsed += Time.unscaledDeltaTime;
                dialogueTextLabel.text = "Thinking" + new string('.', 1 + (int)(thinkingElapsed * 2f) % 3);
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
                HandlePresentationReturn(Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift));
            }

            if (!interfaceHidden && messageInput != null && messageInput.isFocused)
            {
                inputRequested = true;
                inputVisibilityTarget = 1f;
            }
        }

        private void HandlePresentationReturn(bool shift)
        {
            // Let the EventSystem submit the selected drawer Button. Opening
            // chat here would steal focus before that owner can use Return.
            if (sceneDrawer != null && sceneDrawer.HasKeyboardFocus) return;
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
                });
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                AdvanceHiddenSequence(ref avatarQaUnlockBuffer, character, '7', 7, () =>
                {
                    AvatarQaVisibility.Toggle();
                    Debug.Log("[AIFren QA] avatar frames visible=" + AvatarQaVisibility.Visible + ".");
                });
                AdvanceHiddenSequence(ref flightRecorderDumpBuffer, character, '6', 7, () =>
                {
                    developmentFlightRecorder?.ManualDump();
                    Debug.Log("[AIFren Flight Recorder] manual dump requested.");
                });
#endif
            }
        }

        internal static void AdvanceHiddenSequence(ref string buffer, char input, char expected, int length, Action matched)
        {
            if (input != expected)
            {
                buffer = string.Empty;
                return;
            }
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
            RefreshSceneOverlay(authoritativeContinuity);
            SetTopControlLabel(hideUiButton, interfaceHidden || temporarilyRevealed ? "Show" : "Hide");
        }

        private IEnumerator TransitionUiVisibility(bool show)
        {
            // A temporary edge peek changes only renderability and must restore
            // the same subtitle session. A committed Show is a separate product
            // state and cancels hidden-subtitle presentation.
            if (show)
            {
                if (temporarilyRevealed) SuppressHiddenSubtitleForUiReveal();
                else HideHiddenSubtitleImmediately();
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
            // screen, so the two text presentations never overlap. The sole
            // subtitle presenter retained its page, schedule, and revealed
            // word count while its root was suppressed.
            if (!show) RestoreHiddenSubtitleAfterUiReveal();
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
            if (avatarCuesSaving) { avatarCuesSaving = false; avatarCuesDirty = false; }
            savingCompanionPreference = CompanionPreference.None;
            committedSpeechTimeline = null;
            committedSpeechRetired = true;
            presentationTurn.Reset();
            avatarAnimation?.RetireResponseMotion();
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

            if (Application.platform == RuntimePlatform.LinuxPlayer && !NativeQaSession.Active)
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

#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFlightRecorder?.ObserveTransportEvent();
#endif

            if (message.type == "snapshot")
            {
                if (HandleViewSnapshot(message)) return;
                ApplySnapshot(message.data);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (developmentProfileQa && !developmentProfileQaStarted)
                {
                    developmentProfileQaStarted = true;
                    StartCoroutine(BeginDevelopmentProfileQa());
                }
#endif
                return;
            }

            if (message.type == "event" && message.@event?.type == "character_switching")
            {
                BeginCharacterPresentationTransition();
                return;
            }

            if (message.type == "avatar_cues_settings")
            {
                ReceiveAvatarCuesSetting(message.data.explicit_avatar_cues, true);
                return;
            }
            if (message.type == "companion_preferences" && message.data != null)
            {
                ReceiveCompanionPreferences(message.data.conversation_style, message.data.responsive_speech,
                    message.data.automatic_expressions, message.data.automatic_expression_status, true);
                return;
            }

            if (message.type == "command_error")
            {
                if (HandleViewError(message.error)) return;
                if (message.error != null && message.error.code != null && message.error.code.Contains("avatar_cues"))
                    AvatarCuesSaveFailed(message.error.message);
                if (message.error != null && message.error.code != null && message.error.code.Contains("companion_preferences"))
                    CompanionPreferencesSaveFailed(message.error.message);
                if (HandleCharacterManagementError(message.error)) return;
                RestoreAuthoritativeModelSettingsAfterFailure();
                characterSwitchInFlight = client != null && client.CharacterSwitching;
                if (message.error != null && !string.IsNullOrEmpty(message.error.code)
                    && message.error.code.Contains("memory_view"))
                {
                    SetMemoryViewerWarning(message.error.message);
                    return;
                }
                if (message.error != null && !string.IsNullOrEmpty(message.error.code)
                    && message.error.code.Contains("continuity_control"))
                {
                    ClearPendingContinuityControl();
                    if (client != null) _ = client.RequestSnapshotAsync();
                }
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
            if (HandleCharacterManagementEvent(backendEvent)) return;

#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (backendEvent.type == "flight_recorder_backend_dumped")
            {
                developmentFlightRecorder?.AcceptBackendSummary(data);
                return;
            }
            if (backendEvent.type == "flight_recorder_auto_trigger")
            {
                developmentFlightRecorder?.AutomaticTrigger(data != null ? data.reason : "backend_trigger");
                return;
            }
            if (backendEvent.type == "voice_transcription")
                developmentFlightRecorder?.Mark("stt_final");
            if (backendEvent.type != "assistant_delta")
            {
                developmentFlightRecorder?.Mark(
                    "backend_" + backendEvent.type,
                    data != null ? data.turn_id : 0,
                    data != null ? data.playback_id : 0);
            }
#endif

            if (backendEvent.type == "status" && data != null)
            {
                ApplyStatus(data.state, data.message);
            }
            else if (backendEvent.type == "turn_started")
            {
                presentationTurn.Begin(data != null ? data.turn_id : 0);
                avatarAnimation?.RetireResponseMotion();
                activeTurnIsProactive = IsProactiveGeneration(data);
                // This PTT capture successfully crossed into the canonical
                // assistant-turn lifecycle. The thinking placeholder now
                // belongs to generation and must never be rolled back by a
                // late ready/cleanup event from microphone capture.
                if (!activeTurnIsProactive)
                    pttThinkingPresentation.MarkTurnStarted();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                flightRecorderTurnId = data != null ? data.turn_id : 0;
                flightRecorderFirstDeltaSeen = false;
                if (!activeTurnIsProactive)
                    developmentFlightRecorder?.ArmForUserTurn();
                developmentFlightRecorder?.Mark("turn_started", flightRecorderTurnId);
                developmentProfileDeltaCount = 0;
                developmentFrameProfiler?.Mark("turn_started:" + DevelopmentProfileScenarioName());
#endif
                // turn_started is the backend acknowledgement that this input
                // really entered arbitration. Release only this transport gate
                // here so a later user input can explicitly replace this turn.
                submitInFlight = false;
                RefreshInputAvailability();
                pendingAssistantContent = string.Empty;
                assistantStreamVisible = false;
                assistantStreamPresentationDirty = false;
                ClearPreparedSubtitlePlans();
                pendingAssistantReveal = false;
                pendingSpeechReady = false;
                pendingSpeechDuration = 0f;
                streamedSubtitleMode = false;
                streamedSubtitleTurnId = data != null ? data.turn_id : 0;
                subtitleGeneration++;
                HideHiddenSubtitleImmediately();
                // Proactive provider work stayed private and already produced
                // a publishable response. Preserve the current dialogue/status
                // until its immediately following response event arrives.
                if (!activeTurnIsProactive)
                    ApplyStatus("thinking", "Thinking...");
            }
            else if (backendEvent.type == "turn_cancelled")
            {
                if (presentationTurn.Retire(data != null ? data.turn_id : 0)) avatarAnimation?.RetireResponseMotion();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("interruption_cancelled", data != null ? data.turn_id : flightRecorderTurnId);
#endif
                // Freeze only text that was genuinely presented before the
                // interruption; discarded provider deltas must not continue
                // revealing while PTT records or a replacement waits.
                string shown = wordReveal.VisibleText;
                wordReveal.Begin(shown, true);
                currentAssistantPresentationText = shown;
                dialogueLayoutContent = DialoguePresentationParser.FormatVisible(shown);
                assistantStreamVisible = false;
                assistantStreamPresentationDirty = false;
                ClearPreparedSubtitlePlans();
                pendingAssistantReveal = false;
                pendingAssistantContent = null;
                RefreshDialogueRevealText(false);
                subtitleGeneration++;
                HideHiddenSubtitleImmediately();
            }
            else if (backendEvent.type == "conversation_message" && data != null)
            {
                // Canonical conversation_message is the only source for Log
                // history. It is emitted after each persisted message and is
                // intentionally independent of visibility/reveal state.
                AddCanonicalMessage(
                    data.role, data.content,
                    string.IsNullOrWhiteSpace(data.timestamp) ? DateTimeOffset.Now.ToString("o") : data.timestamp,
                    false, false, data.message_id);
            }
            else if (backendEvent.type == "truth_scope_changed" && data != null)
            {
                ApplyTruthScopeIndicator(data.scope_kind, data.scope_label);
            }
            else if (backendEvent.type == "continuity_changed" && data != null)
            {
                ApplyAuthoritativeContinuityChange(data.command_id, data.continuity);
            }
            else if (backendEvent.type == "continuity_control_result" && data != null)
            {
                if (data.accepted && data.command_id == pendingContinuityCommandId)
                {
                    CompletePendingContinuityControl(data.continuity);
                }
            }
            else if (backendEvent.type == "memory_view_page" && data != null)
            {
                bool accepted = memoryViewerState.Accept(data.request_id, data.memory_page);
                MarkView(accepted ? (memoryViewerState.PageRequest.Failed ? "memory_unavailable" : "memory_model") : "memory_reject_request", data.request_id,
                    memoryViewerState.Page?.items?.Length ?? 0);
                if (accepted)
                {
                    memoryModelRequestId = data.request_id;
                    RefreshMemoryViewerPage();
                    if (memoryViewerState.Selected != null && memoryViewerState.DetailRequest.Failed)
                        RequestMemoryViewerDetail(memoryViewerState.Selected);
                }
            }
            else if (backendEvent.type == "memory_view_detail" && data != null)
            {
                if (memoryViewerState.AcceptDetail(data.request_id, data.memory_detail))
                { RefreshMemoryViewerDetail(); UpdateMemoryViewStatus(); }
            }
            else if (backendEvent.type == "memory_view_mutation_result" && data != null)
            {
                if (!string.Equals(data.request_id, memoryViewerState.PendingRequestId,
                        StringComparison.Ordinal)
                    || !string.Equals(data.character_id, activeCharacterId,
                        StringComparison.Ordinal))
                    return;
                memoryViewerRetireConfirmation = false;
                memoryViewerState.PageRequest.Observe(0, data.accepted);
                if (!data.accepted)
                {
                    SetMemoryViewerWarning(string.IsNullOrWhiteSpace(data.message)
                        ? "Memory edit was not applied." : data.message);
                    return;
                }
                RequestMemoryViewerPage();
            }
            else if (backendEvent.type == "automatic_expression_status" && data != null)
            {
                automaticExpressionStatus = data.message;
                RefreshCompanionPreferenceControls();
            }
            else if (backendEvent.type == "automatic_expression" && data != null)
            {
                if (!savedAutomaticExpressions)
                {
                    presentationTurn.CancelAutomatic();
                    return;
                }
                AvatarPresentationResolver resolver = avatarLoader != null
                    ? avatarLoader.GetComponent<AvatarPresentationResolver>() : null;
                presentationTurn.PublishAutomatic(data.turn_id, resolver, data.presentation);
            }
            else if (backendEvent.type == "assistant_response" && data != null)
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("assistant_final", data.turn_id);
                developmentFrameProfiler?.Mark("assistant_response_begin:" + DevelopmentProfileScenarioName());
#endif
                // This is presentation-only. Its later canonical
                // conversation_message event appends history exactly once.
                if (assistantStreamPresentationDirty) RefreshStreamedAssistantDialogue(false);
                pendingAssistantContent = data.content;
                publishedSubtitleTurnId = data.turn_id;
                subtitleResponseReceivedAt = Time.unscaledTime;
                Debug.Log("[AIFren Timing] Unity assistant response received t=" + subtitleResponseReceivedAt.ToString("F3"));
                Debug.Log("[AIFren Subtitle] assistant response received hidden=" + interfaceHidden + " enabled=" + showDialogueWhenHidden);
                if (!streamedSubtitleMode) BeginSubtitleResponse(data.content);
                AvatarPresentationResolver presentationResolver = avatarLoader != null
                    ? avatarLoader.GetComponent<AvatarPresentationResolver>()
                    : null;
                presentationTurn.PublishFinal(data.turn_id, presentationResolver,
                    data.has_presentation ? data.presentation : null, data.content, characterName, data.automatic_expression_pending);
                if (assistantStreamVisible)
                {
                    // Reconcile against the canonical final response while
                    // preserving the stream's already-revealed word count and
                    // timing accumulator. Any not-yet-shown words continue at
                    // the configured reveal pace instead of being dumped or
                    // replayed from word one.
                    FinalizeStreamedAssistantDialogue(data.content);
                    pendingAssistantContent = null;
                    pendingAssistantReveal = false;
                    pendingSpeechReady = false;
                    pendingSpeechDuration = 0f;
                    assistantStreamVisible = false;
                }
                else
                {
                    pendingAssistantReveal = true;
                    TryBeginPendingAssistantReveal();
                }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFrameProfiler?.Mark("assistant_response_end:canonical_exact=" +
                    string.Equals(data.content, currentAssistantPresentationText, StringComparison.Ordinal));
#endif
            }
            else if (backendEvent.type == "assistant_delta" && data != null)
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (!flightRecorderFirstDeltaSeen)
                {
                    flightRecorderFirstDeltaSeen = true;
                    developmentFlightRecorder?.Mark("first_assistant_delta", data.turn_id);
                }
#endif
                // Streaming text is presentation-only; the later canonical
                // assistant message still enters history exactly once.
                bool firstDelta = !assistantStreamVisible;
                assistantStreamVisible = true;
                pendingAssistantContent = (pendingAssistantContent ?? string.Empty) + (data.content ?? string.Empty);
                assistantStreamPresentationDirty = true;
                // Preserve immediate time-to-first-text, then coalesce the
                // token-rate stream into bounded presentation updates. TTS and
                // canonical persistence continue to consume every delta.
                if (firstDelta) RefreshStreamedAssistantDialogue(true);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (developmentProfileQa)
                {
                    developmentProfileDeltaCount++;
                    if (developmentProfileDeltaCount == 1)
                        developmentFrameProfiler?.Mark("first_dialogue_delta:" + DevelopmentProfileScenarioName());
                    if (developmentProfileScenarioIndex == 1 && developmentProfileDeltaCount == 3)
                    {
                        instantTextToggle.SetIsOnWithoutNotify(true);
                        SetInstantText(true);
                        developmentFrameProfiler?.Mark("instant_on_midstream:visible_equals_received=" +
                            string.Equals(wordReveal.VisibleText, pendingAssistantContent, StringComparison.Ordinal));
                    }
                    else if (developmentProfileScenarioIndex == 1 && developmentProfileDeltaCount == 7)
                    {
                        string before = wordReveal.VisibleText;
                        instantTextToggle.SetIsOnWithoutNotify(false);
                        SetInstantText(false);
                        developmentFrameProfiler?.Mark("instant_off_midstream:visible_preserved=" +
                            string.Equals(before, wordReveal.VisibleText, StringComparison.Ordinal));
                    }
                }
#endif
            }
            else if (backendEvent.type == "local_models" && data != null)
            {
                SetLocalModelOptions(data.models);
                ApplyStatus("ready", localModelOptions.Count == 0 ? "No managed GGUF models found." : "Local models refreshed.");
            }
            else if (backendEvent.type == "local_model_runtime" && data != null)
            {
                localModelRuntime = data.local_runtime;
                RefreshLocalRuntimeUi();
            }
            else if (backendEvent.type == "tts_state" && data != null)
            {
                if (HandleCommittedSpeech(data)) return;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (data.state == "starting") developmentFlightRecorder?.Mark("tts_submit", data.turn_id, data.playback_id);
                else if (data.state == "playback_started")
                {
                    developmentFlightRecorder?.Mark("tts_synthesis_complete", data.turn_id, data.playback_id);
                    developmentFlightRecorder?.Mark("playback_started", data.turn_id, data.playback_id);
                }
                else if (data.state == "stopped" || data.state == "failed" || data.state == "not_started")
                    developmentFlightRecorder?.Mark("playback_stopped", data.turn_id, data.playback_id);
#endif
                if (data.state == "starting" && data.streamed)
                {
                    streamedSubtitleMode = true;
                    streamedSubtitleTurnId = data.turn_id;
                    ApplyStatus("speaking", data.message);
                }
                else if (data.state == "chunk_queued" && data.streamed &&
                    data.turn_id > 0 && data.turn_id == streamedSubtitleTurnId)
                {
                    string subtitleChunk = !string.IsNullOrWhiteSpace(data.subtitle_content)
                        ? data.subtitle_content : data.content;
                    CachePreparedSubtitlePlan(data.turn_id, data.chunk_index, subtitleChunk);
                }
                else if (data.state == "playback_started")
                {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    developmentFrameProfiler?.Mark("playback_started_begin:" + DevelopmentProfileScenarioName());
#endif
                    var playbackStartHandlerTimer = System.Diagnostics.Stopwatch.StartNew();
                    if (data.streamed && streamedSubtitleTurnId > 0 && data.turn_id > 0 &&
                        data.turn_id != streamedSubtitleTurnId)
                    {
                        Debug.Log("[AIFren Subtitle] ignored stale streamed playback for turn=" + data.turn_id +
                            "; current=" + streamedSubtitleTurnId + ".");
                        return;
                    }
                    if (data.streamed && !string.IsNullOrWhiteSpace(data.content))
                    {
                        streamedSubtitleMode = true;
                        streamedSubtitleTurnId = data.turn_id;
                        // The session now owns the same source chunk as this
                        // audio callback. Duration and word timestamps can no
                        // longer compress the entire response into each chunk.
                        string subtitleChunk = !string.IsNullOrWhiteSpace(data.subtitle_content)
                            ? data.subtitle_content : data.content;
                        subtitleResponseReceivedAt = Time.unscaledTime;
                        BeginSubtitleResponse(subtitleChunk,
                            TakePreparedSubtitlePlan(data.turn_id, data.chunk_index, subtitleChunk));
                    }
                    Debug.Log("[AIFren Subtitle] playback_started generation=" + subtitleGeneration);
                    pendingSpeechReady = true;
                    pendingSpeechDuration = data.duration_seconds;
                    subtitleSpeechDuration = data.duration_seconds;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    developmentFrameProfiler?.Mark("lip_sync_begin");
#endif
                    AvatarPresentationResolver speechResolver = avatarLoader != null
                        ? avatarLoader.GetComponent<AvatarPresentationResolver>() : null;
                    if (speechResolver == null || speechResolver.AllowsLipSync)
                        avatarAnimation?.BeginSpeech(data.duration_seconds, data.lip_sync_envelope);
                    else
                        avatarAnimation?.StopSpeech();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    developmentFrameProfiler?.Mark("lip_sync_ready");
#endif
                    subtitleSpeechActive = true;
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
                    ApplyStatus("speaking", data.message);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    if (developmentProfileQa && developmentProfileScenarioIndex == 2 && !developmentLongPeekScheduled)
                    {
                        developmentLongPeekScheduled = true;
                        StartCoroutine(RunDevelopmentTemporaryPeek("mid_response", 2f));
                    }
                    playbackStartHandlerTimer.Stop();
                    Debug.Log("[AIFren Timing] Unity playback_started handler=" +
                        playbackStartHandlerTimer.Elapsed.TotalMilliseconds.ToString("F2") + "ms.");
                    developmentFrameProfiler?.Mark("playback_started_end:handler_ms=" +
                        playbackStartHandlerTimer.Elapsed.TotalMilliseconds.ToString("F3"));
#endif
                }
                else if (data.state == "failed" || data.state == "not_started" || data.state == "stopped")
                {
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
                    if (data.state == "stopped") presentationTurn.FinishSpeech(data.turn_id, data.interrupted);
                    avatarAnimation?.StopSpeech();
                    pendingSpeechReady = true;
                    pendingSpeechDuration = 0f;
                    TryBeginPendingAssistantReveal();
                    subtitleSpeechActive = false;
                    subtitleAwaitingPlayback = false;
                    if (data.state == "stopped")
                        hiddenSubtitlePresenter?.OnPlaybackStopped(data.playback_id, Time.unscaledTime, data.interrupted);
                    else
                        hiddenSubtitlePresenter?.OnAudioUnavailable(subtitleGeneration, Time.unscaledTime);
                    // Natural completion lets the presenter finish pending reading-speed
                    // limited words. Interrupted playback clears immediately.
                    // Preserve the final readable page briefly after actual
                    // playback; failed/disabled TTS uses the text-duration
                    // fallback scheduled when the response arrived.
                    if (!data.streamed && !data.interrupted) ApplyStatus("ready", data.message);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    if (developmentProfileQa && data.state == "stopped" && !data.interrupted)
                    {
                        developmentFrameProfiler?.Mark("playback_stopped:" + DevelopmentProfileScenarioName());
                        if (!developmentProfileAdvancePending)
                        {
                            developmentProfileAdvancePending = true;
                            StartCoroutine(AdvanceDevelopmentProfileQa());
                        }
                    }
#endif
                }
                else
                {
                    ApplyStatus(data.state == "speaking" ? "speaking" : "ready", data.message);
                }
            }
            else if (backendEvent.type == "voice_state" && data != null)
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                if (data.state == "listening") developmentFlightRecorder?.Mark("stt_start");
                else if (data.state == "transcribed" || data.state == "transcription_ready") developmentFlightRecorder?.Mark("stt_final");
#endif
                if (string.Equals(data.state, "listening", StringComparison.OrdinalIgnoreCase))
                {
                    presentationTurn.Reset();
                    avatarAnimation?.RetireResponseMotion();
                    pttThinkingPresentation.BeginAttempt();
                }
                else if (string.Equals(data.state, "released", StringComparison.OrdinalIgnoreCase))
                {
                    pttThinkingPresentation.MarkReleased();
                }
                else if (string.Equals(data.state, "ready", StringComparison.OrdinalIgnoreCase) &&
                    pttThinkingPresentation.TryRestoreOnVoiceReady(out string previousDialogue))
                {
                    RestoreDialogueAfterPttEndedWithoutTurn(previousDialogue);
                }
                backendGlobalPtt = data.global_listener;
                Debug.Log("[AIFren PTT] Backend voice state=" + data.state +
                    ", globalListener=" + backendGlobalPtt + ".");
                RefreshGlobalPttStatus();
                UpdatePttIndicator(data.state);
            }
            else if (backendEvent.type == "voice_transcription" && data != null)
            {
                pttThinkingPresentation.MarkTranscription(data.content, pttAutoSend);
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
                    pttThinkingPresentation.MarkVoiceFailure();
                    // A transcription/microphone error is not evidence that
                    // the independently operating global hook is unavailable.
                    RefreshGlobalPttStatus();
                    UpdatePttIndicator("ready");
                }
                if (pttThinkingPresentation.TryRestoreOnTurnFailure(
                        out string previousDialogue))
                    RestoreDialogueAfterPttEndedWithoutTurn(previousDialogue);
                ApplyStatus("error", data.message);
                submitInFlight = false;
                RefreshInputAvailability();
            }
        }

        internal void ApplyLegacyAvatarGesture(string content)
        {
            List<string> emotes = DialoguePresentationParser.EmoteTexts(content);
            if (AvatarGestureMapper.TryFirstSupported(emotes, out AvatarGestureIntent gesture, out _))
            {
                Debug.Log("[AvatarGesture] mapped intent=" + gesture);
                avatarLoader?.GetComponent<AvatarPresentationResolver>()?.TryGesture(gesture);
            }
            else avatarAnimation?.PlayAttentiveReaction();
        }

        internal static bool IsProactiveGeneration(BackendEventData data)
        {
            return data != null && (data.proactive || string.Equals(
                data.generation_origin, "proactive", StringComparison.Ordinal));
        }

        private void ApplySnapshot(SnapshotData snapshot)
        {
            if (snapshot == null)
            {
                ApplyStatus("error", "Backend returned an invalid snapshot.");
                return;
            }

            if (snapshot.transport_version < 9)
            {
                ApplyStatus("error", "An older backend is listening on port 8765. Stop it, then launch the current AIFren backend.");
                return;
            }

            bool settlingCharacterTransition = characterSwitchInFlight;
            bool receivedCharacterIdentity = snapshot.character != null
                && !string.IsNullOrWhiteSpace(snapshot.character.character_id);
            bool characterChanged = receivedCharacterIdentity
                && HasCharacterChanged(activeCharacterId, snapshot.character.character_id);
            bool initialCharacterIdentity = receivedCharacterIdentity
                && string.IsNullOrWhiteSpace(activeCharacterId);
            bool bindingChanged = !string.IsNullOrWhiteSpace(activeCharacterSession)
                && !string.IsNullOrWhiteSpace(snapshot.character_session)
                && !string.Equals(activeCharacterSession, snapshot.character_session, StringComparison.Ordinal);
            activeCharacterSession = snapshot.character_session;
            if (snapshot.character != null && !string.IsNullOrWhiteSpace(snapshot.character.character_id))
            {
                activeCharacterId = snapshot.character.character_id;
            }
            if (snapshot.character != null && !string.IsNullOrWhiteSpace(snapshot.character.name))
            {
                characterName = snapshot.character.name;
            }
            characterStorageUnavailable = snapshot.storage_unavailable;
            characterRegistryRevision = snapshot.registry_revision;
            if (characterOperationReceipt != null && characterManagerConfirm != null)
                characterManagerConfirm.interactable = characterOperationReceipt.Revision >= characterRegistryRevision;
            availableCharacters.Clear();
            if (snapshot.characters != null) availableCharacters.AddRange(snapshot.characters);
            characterSwitchInFlight = false;
            RefreshCharacterSettings();
            backendReconnectInProgress = false;
            ClearBackendDisconnectWarning();

            if (characterChanged || bindingChanged)
            {
                messages.Clear(); canonicalMessageIds.Clear(); historyIndex.Rebuild(messages); historyDirty = true;
                presentationTurn.Reset();
                sceneDrawer?.CloseImmediately(); sceneDrawerRowsKey = null;
                ClearPendingContinuityControl();
                ClearTransientAssistantPresentationForSnapshot();
                ResetCharacterScopedAvatarPresentation();
            }
            memoryViewerState.ChangeCharacter(activeCharacterId);
            if (characterChanged || bindingChanged) { memoryViewerState.InvalidatePage(); RefreshMemoryViewerPage(); }

            ApplyHistoryView(snapshot, !(characterChanged || bindingChanged || initialCharacterIdentity || settlingCharacterTransition));

            if (characterNameLabel != null) characterNameLabel.text = characterName;

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
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                dialogueCanonicalFinalReceived = true;
                dialogueManualBottomRecorded = false;
                RecordDialogueLayoutState("snapshot_restore");
                ScheduleDialogueLayoutFollowup("snapshot_restore");
#endif
            }
            else
            {
                dialogueTextLabel.text = "I’m here when you’re ready to talk.";
            }

            if (snapshot.tts != null)
            {
                volumeSlider.SetValueWithoutNotify(snapshot.tts.volume);
                UpdateVolumeLabel(snapshot.tts.volume);
                RefreshTtsModelUi(snapshot.tts);
            }
            if (snapshot.companion != null)
            {
                ReceiveAvatarCuesSetting(snapshot.companion.explicit_avatar_cues, false);
                ReceiveCompanionPreferences(snapshot.companion.conversation_style, snapshot.companion.responsive_speech,
                    snapshot.companion.automatic_expressions, snapshot.companion.automatic_expression_status, false);
                authoritativeProactiveBehavior = snapshot.companion.proactive_behavior;
                authoritativeProactiveIntervalSeconds = snapshot.companion.proactive_interval_seconds;
                latestProactiveEligibility = snapshot.companion.proactive_eligibility;
                latestProactiveNextSeconds = snapshot.companion.proactive_next_opportunity_seconds;
                latestProactiveIgnoredStreak = snapshot.companion.proactive_ignored_streak;
                RefreshProactiveBehaviorUi();
                AvatarPresentationResolver stateResolver = avatarLoader != null
                    ? avatarLoader.GetComponent<AvatarPresentationResolver>() : null;
                authoritativeStatePresentation = snapshot.companion.state_presentation;
                if (!characterChanged) stateResolver?.Apply(authoritativeStatePresentation);
            }

            RefreshGeminiModelUi(snapshot.models != null ? snapshot.models.current : null,
                snapshot.models != null ? snapshot.models.local_runtime : null);
            ApplyTruthScopeIndicator(
                snapshot.truth_scope != null ? snapshot.truth_scope.kind : "real_world",
                snapshot.truth_scope != null ? snapshot.truth_scope.label : string.Empty);
            ApplyContinuitySnapshot(snapshot.continuity);
            RetryPendingContinuityControlAfterReconnect();
            UpdatePttIndicator(snapshot.voice != null ? snapshot.voice.state : "ready");
            Debug.Log(
                "AIFren snapshot received: model=" +
                (snapshot.models != null && snapshot.models.current != null ? snapshot.models.current.model : "missing") +
                ", provider=" +
                (snapshot.models != null && snapshot.models.current != null ? snapshot.models.current.provider : "missing") +
                ", TTS=" + (snapshot.tts != null ? snapshot.tts.provider : "missing") +
                ", voice=" + (snapshot.tts != null ? snapshot.tts.voice : "missing") +
                ", device=" + (snapshot.tts != null ? snapshot.tts.device : "missing") + "."
            );

            _ = client.SetPushToTalkTranscriptionModeAsync(pttAutoSend);
            backendGlobalPtt = snapshot.voice != null && snapshot.voice.global_listener;
            _ = client.SetPushToTalkBindingAsync(PresentationPttBinding.Save(pushToTalkKey));
            RefreshGlobalPttStatus(true);
            if (startupPanel != null) startupPanel.SetActive(false);

            if (characterChanged || initialCharacterIdentity || bindingChanged || settlingCharacterTransition)
                RequestCharacterAvatarPreference(activeCharacterId);

            ResumeMemoryRefresh();
            if (memoryViewerPanel != null && memoryViewerPanel.activeInHierarchy
                    && (characterChanged || initialCharacterIdentity || bindingChanged || settlingCharacterTransition))
                RequestMemoryViewerPage();

            if (!characterAvatarSwitchInFlight)
            {
                ApplyStatus(
                    snapshot.status != null ? snapshot.status.state : "ready",
                    snapshot.status != null ? snapshot.status.message : "Ready"
                );
            }
        }

        private void ClearTransientAssistantPresentationForSnapshot()
        {
            // A successful character selection is an authoritative session
            // replacement.  Invalidate every delayed reveal/subtitle path
            // before the new canonical history is applied, so a prior
            // character cannot append or present after the switch.
            pendingAssistantContent = null;
            assistantStreamVisible = false;
            assistantStreamPresentationDirty = false;
            ClearPreparedSubtitlePlans();
            pendingAssistantReveal = false;
            pendingSpeechReady = false;
            pendingSpeechDuration = 0f;
            subtitleGeneration++;
            subtitlePages.Clear();
            subtitlePageWordRanges.Clear();
            subtitleWordSchedule.Clear();
            subtitleAwaitingPlayback = false;
            subtitleSpeechActive = false;
            subtitlePlaybackGeneration = -1;
            subtitlePlaybackId = 0;
            currentAssistantPresentationText = string.Empty;
            wordReveal.Begin(string.Empty, true);
            HideHiddenSubtitleImmediately();
        }

        private void BeginCharacterPresentationTransition()
        {
            // Transport has retired the old binding before this notification.
            // Clear only derived/current presentation; canonical data stays backend-owned.
            characterSwitchInFlight = true;
            ClearOutgoingViews();
            RetireCharacterAvatarRequests();
            presentationTurn.Reset();
            avatarAnimation?.StopSpeech();
            sceneDrawer?.CloseImmediately(); sceneDrawerRowsKey = null;
            authoritativeContinuity = null;
            ClearPendingContinuityControl();
            ClearTransientAssistantPresentationForSnapshot();
            authoritativeStatePresentation = null;
            ResetCharacterScopedAvatarPresentation();
            if (dialogueTextLabel != null) dialogueTextLabel.text = "Loading character…";
            ApplyTruthScopeIndicator("real_world", string.Empty);
            RefreshCharacterSettings(); RefreshInputAvailability();
        }

        private void ResetCharacterScopedAvatarPresentation()
        {
            // Persistent emotion and manual QA gaze belong to the current
            // character identity independently from its selected avatar asset.
            // Do not retain a concrete morph or LookAt target when the
            // backend has selected a different character.  Ordinary snapshot
            // refreshes deliberately bypass this method.
            AvatarExpressionController expressions = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarExpressionController>()
                : null;
            expressions?.ClearExpression();

            AvatarGazeController gaze = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarGazeController>()
                : null;
            gaze?.CenterGaze();

            // The resolver has no gesture queue or durable semantic state.
            // Clear any active one-shot/reaction without rebinding the shared
            // avatar selection or altering VRMA retargeting behavior.
            avatarAnimation?.ClearTransientPresentationForCharacterChange();
        }

        internal static bool HasCharacterChanged(string previousCharacterId, string nextCharacterId)
        {
            return !string.IsNullOrWhiteSpace(previousCharacterId)
                && !string.IsNullOrWhiteSpace(nextCharacterId)
                && !string.Equals(previousCharacterId, nextCharacterId, StringComparison.Ordinal);
        }

        internal static bool IsCharacterAvatarApplyAuthoritative(
            int requestGeneration,
            int currentGeneration,
            string requestCharacterId,
            string activeCharacterId)
        {
            return requestGeneration == currentGeneration
                && !string.IsNullOrWhiteSpace(requestCharacterId)
                && string.Equals(requestCharacterId, activeCharacterId, StringComparison.Ordinal);
        }

        private void AddMessage(
            string role, string content, string timestamp, bool animateAssistant,
            bool presentAssistant = true)
        {
            AddCanonicalMessage(role, content, timestamp, animateAssistant, presentAssistant, null);
        }

        private void AddCanonicalMessage(
            string role, string content, string timestamp, bool animateAssistant,
            bool presentAssistant, string messageId)
        {
            if (string.IsNullOrWhiteSpace(content))
            {
                return;
            }
            if (!CanonicalMessageProjection.TryAdmit(canonicalMessageIds, messageId))
            {
                return;
            }

            ConversationMessage message = new ConversationMessage
            {
                message_id = messageId,
                role = role,
                content = content,
                timestamp = timestamp
            };
            messages.Add(message);
            historyIndex.Append(message);
            if (historyLevel == HistoryNavigationLevel.Messages
                    && PresentationHistoryTime.TryGetLocalTime(timestamp, out DateTime local)
                    && selectedHistoryDay.Equals(new HistoryDayKey(local.Year, local.Month, local.Day)))
            {
                int count = historyIndex.CountForDay(selectedHistoryDay);
                selectedHistoryPage = Math.Max(0, (count - 1) / HistoryMessagePageSize);
            }
            historyDirty = true;

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
            if (hiddenDialogueScroll != null)
            {
                Canvas.ForceUpdateCanvases();
                hiddenDialogueScroll.verticalNormalizedPosition = 1f;
            }
            dialogueAutoFollow = true;
            RefreshDialogueScrollableContent();
            if (wordReveal.IsComplete) FollowScrollIfNearBottom(dialogueScroll);
        }

        private void FinalizeStreamedAssistantDialogue(string content)
        {
            assistantStreamPresentationDirty = false;
            currentAssistantPresentationText = content ?? string.Empty;
            dialogueLayoutContent = DialoguePresentationParser.FormatVisible(currentAssistantPresentationText);
            wordReveal.UpdateText(currentAssistantPresentationText, instantText);
            assistantStreamVisible = false;
            RefreshDialogueRevealText(!wordReveal.IsComplete);
            UpdateDialogueLayout(false);
            RefreshDialogueScrollableContent();
            if (dialogueAutoFollow) FollowScrollIfNearBottom(dialogueScroll);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            dialogueCanonicalFinalReceived = true;
            dialogueMidRevealRecorded = false;
            dialogueManualBottomRecorded = false;
            RecordDialogueLayoutState("assistant_final");
            ScheduleDialogueLayoutFollowup("assistant_final");
#endif
        }

        private void RefreshStreamedAssistantDialogue(bool firstDelta)
        {
            if (!assistantStreamVisible || string.IsNullOrEmpty(pendingAssistantContent))
            {
                assistantStreamPresentationDirty = false;
                return;
            }
            currentAssistantPresentationText = pendingAssistantContent;
            dialogueLayoutContent = DialoguePresentationParser.FormatVisible(pendingAssistantContent);
            if (firstDelta)
            {
                wordReveal.WordsPerSecond = revealWordsPerSecond;
                wordReveal.Begin(pendingAssistantContent, instantText);
                dialogueAutoFollow = true;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                dialogueCanonicalFinalReceived = false;
                dialogueMidRevealRecorded = false;
                dialogueManualBottomRecorded = false;
#endif
            }
            else wordReveal.UpdateText(pendingAssistantContent, instantText);
            RefreshDialogueRevealText(true);
            UpdateDialogueLayout(firstDelta);
            RefreshDialogueScrollableContent();
            if (dialogueAutoFollow && !firstDelta) FollowScrollIfNearBottom(dialogueScroll);
            assistantStreamPresentationDirty = false;
            nextAssistantStreamPresentationAt = Time.unscaledTime + AssistantStreamPresentationIntervalSeconds;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (firstDelta)
            {
                RecordDialogueLayoutState("first_delta");
                ScheduleDialogueLayoutFollowup("first_delta");
            }
#endif
        }

        private void RefreshDialogueRevealText(bool revealing)
        {
            if (dialogueTextLabel != null)
                dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, revealing);
        }

        private void SkipCurrentReveal()
        {
            if (!wordReveal.IsComplete)
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                RecordDialogueLayoutState("skip_before");
#endif
                wordReveal.RevealAll();
                RefreshDialogueRevealText(false);
                // Revealing the tail in one step can add several lines. Keep
                // the ScrollRect's owned content bounds in sync before moving
                // to the bottom; otherwise RectMask2D clips the newly visible
                // final lines against the height of the partial reveal.
                UpdateDialogueLayout(false);
                RefreshDialogueScrollableContent();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("dialogue_skip_follow_requested",
                    value: dialogueAutoFollow ? 1 : 0);
#endif
                if (dialogueAutoFollow) FollowScrollIfNearBottom(dialogueScroll);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                RecordDialogueLayoutState("skip_after");
                ScheduleDialogueLayoutFollowup("skip_after");
#endif
            }
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private void ScheduleDialogueLayoutFollowup(string phase)
        {
            if (developmentFlightRecorder == null) return;
            StartCoroutine(RecordDialogueLayoutFollowup(phase,
                currentAssistantPresentationText ?? string.Empty, wordReveal.WordCount));
        }

        private IEnumerator RecordDialogueLayoutFollowup(string phase, string expectedText, int expectedTokens)
        {
            yield return null;
            if (!string.Equals(currentAssistantPresentationText, expectedText, StringComparison.Ordinal) ||
                wordReveal.WordCount != expectedTokens) yield break;
            RecordDialogueLayoutState(phase + "_frame1");
            yield return null;
            yield return null;
            yield return null;
            if (!string.Equals(currentAssistantPresentationText, expectedText, StringComparison.Ordinal) ||
                wordReveal.WordCount != expectedTokens) yield break;
            RecordDialogueLayoutState(phase + "_frame4");
        }

        private void RecordDialogueLayoutState(string phase)
        {
            if (developmentFlightRecorder == null || dialogueTextLabel == null) return;
            string prefix = "dialogue_" + phase + "_";
            RectTransform content = dialogueScroll != null ? dialogueScroll.content : null;
            float preferredHeight = DialoguePreferredHeight(dialogueTextLabel.text);
            RectTransform textRect = dialogueTextLabel.rectTransform;
            Bounds textBounds = dialogueTextLabel.textBounds;
            Vector3[] viewportCorners = new Vector3[4];
            Vector3[] contentCorners = new Vector3[4];
            dialogueViewportRect?.GetWorldCorners(viewportCorners);
            content?.GetWorldCorners(contentCorners);
            int lastVisibleCharacter = -1;
            TMP_TextInfo textInfo = dialogueTextLabel.textInfo;
            if (textInfo != null)
            {
                for (int index = textInfo.characterCount - 1; index >= 0; index--)
                {
                    if (!textInfo.characterInfo[index].isVisible) continue;
                    lastVisibleCharacter = index;
                    break;
                }
            }
            float lastBottomRelativeToViewport = float.NaN;
            float lastTopRelativeToViewport = float.NaN;
            if (lastVisibleCharacter >= 0 && dialogueViewportRect != null)
            {
                TMP_CharacterInfo character = textInfo.characterInfo[lastVisibleCharacter];
                float bottom = textRect.TransformPoint(character.bottomLeft).y;
                float top = textRect.TransformPoint(character.topRight).y;
                lastBottomRelativeToViewport = bottom - viewportCorners[0].y;
                lastTopRelativeToViewport = top - viewportCorners[1].y;
            }
            developmentFlightRecorder.Mark(prefix + "canonical_chars",
                value: (currentAssistantPresentationText ?? string.Empty).Length);
            developmentFlightRecorder.Mark(prefix + "total_tokens", value: wordReveal.WordCount);
            developmentFlightRecorder.Mark(prefix + "visible_tokens", value: wordReveal.RevealedTokenCount);
            developmentFlightRecorder.Mark(prefix + "reveal_complete", value: wordReveal.IsComplete ? 1 : 0);
            developmentFlightRecorder.Mark(prefix + "tmp_text_chars", value: dialogueTextLabel.text.Length);
            developmentFlightRecorder.Mark(prefix + "tmp_mesh_chars", value: dialogueTextLabel.textInfo.characterCount);
            developmentFlightRecorder.Mark(prefix + "max_visible_characters", value: dialogueTextLabel.maxVisibleCharacters);
            developmentFlightRecorder.Mark(prefix + "preferred_height_x100", value: Mathf.RoundToInt(preferredHeight * 100f));
            developmentFlightRecorder.Mark(prefix + "rendered_height_x100",
                value: Mathf.RoundToInt(dialogueTextLabel.renderedHeight * 100f));
            developmentFlightRecorder.Mark(prefix + "text_bounds_height_x100",
                value: Mathf.RoundToInt(textBounds.size.y * 100f));
            developmentFlightRecorder.Mark(prefix + "text_bounds_min_y_x100",
                value: Mathf.RoundToInt(textBounds.min.y * 100f));
            developmentFlightRecorder.Mark(prefix + "text_bounds_max_y_x100",
                value: Mathf.RoundToInt(textBounds.max.y * 100f));
            developmentFlightRecorder.Mark(prefix + "tmp_rect_height_x100",
                value: Mathf.RoundToInt(textRect.rect.height * 100f));
            developmentFlightRecorder.Mark(prefix + "content_height_x100",
                value: content != null ? Mathf.RoundToInt(content.rect.height * 100f) : -1);
            developmentFlightRecorder.Mark(prefix + "viewport_height_x100",
                value: dialogueViewportRect != null ? Mathf.RoundToInt(dialogueViewportRect.rect.height * 100f) : -1);
            developmentFlightRecorder.Mark(prefix + "viewport_width_x100",
                value: dialogueViewportRect != null ? Mathf.RoundToInt(dialogueViewportRect.rect.width * 100f) : -1);
            developmentFlightRecorder.Mark(prefix + "tmp_rect_width_x100",
                value: Mathf.RoundToInt(textRect.rect.width * 100f));
            developmentFlightRecorder.Mark(prefix + "tmp_inner_width_x100",
                value: Mathf.RoundToInt(DialogueInnerTextWidth() * 100f));
            developmentFlightRecorder.Mark(prefix + "scroll_position_x1000",
                value: dialogueScroll != null ? Mathf.RoundToInt(dialogueScroll.verticalNormalizedPosition * 1000f) : -1);
            developmentFlightRecorder.Mark(prefix + "content_anchored_y_x100",
                value: content != null ? Mathf.RoundToInt(content.anchoredPosition.y * 100f) : -1);
            developmentFlightRecorder.Mark(prefix + "text_anchored_y_x100",
                value: Mathf.RoundToInt(textRect.anchoredPosition.y * 100f));
            developmentFlightRecorder.Mark(prefix + "content_bottom_vs_viewport_x100",
                value: content != null && dialogueViewportRect != null
                    ? Mathf.RoundToInt((contentCorners[0].y - viewportCorners[0].y) * 100f) : -1);
            developmentFlightRecorder.Mark(prefix + "last_char_bottom_vs_viewport_x100",
                value: float.IsNaN(lastBottomRelativeToViewport)
                    ? int.MinValue : Mathf.RoundToInt(lastBottomRelativeToViewport * 100f));
            developmentFlightRecorder.Mark(prefix + "last_char_top_vs_viewport_x100",
                value: float.IsNaN(lastTopRelativeToViewport)
                    ? int.MinValue : Mathf.RoundToInt(lastTopRelativeToViewport * 100f));
            developmentFlightRecorder.Mark(prefix + "vertical_scroll_enabled",
                value: dialogueScroll != null && dialogueScroll.vertical ? 1 : 0);
            developmentFlightRecorder.Mark(prefix + "rect_mask_present",
                value: dialogueViewportRect != null && dialogueViewportRect.GetComponent<RectMask2D>() != null ? 1 : 0);
            developmentFlightRecorder.Mark(prefix + "auto_follow", value: dialogueAutoFollow ? 1 : 0);
            developmentFlightRecorder.Mark(prefix + "layout_rebuilding",
                value: CanvasUpdateRegistry.IsRebuildingLayout() ? 1 : 0);
        }
#endif

        private void UpdateDialogueLayout(bool scrollToTop)
        {
            if (dialogueCardRect == null || dialogueViewportRect == null || dialogueTextLabel == null)
            {
                return;
            }

            string measuredContent = string.IsNullOrWhiteSpace(dialogueLayoutContent)
                ? dialogueTextLabel.text : dialogueLayoutContent;
            float textHeight = DialoguePreferredHeight(measuredContent);
            bool portrait = currentDisplaySettings != null && PresentationDisplaySettingsPolicy.IsPortrait(
                currentDisplaySettings.layoutMode, Screen.width, Screen.height);
            float stableHeight = portrait ? DialoguePortraitHeight : DialogueLandscapeHeight;
            dialogueCardRect.sizeDelta = new Vector2(0f, stableHeight);

            Canvas.ForceUpdateCanvases();
            float viewportHeight = Mathf.Max(1f, dialogueViewportRect.rect.height);
            RectTransform textRect = dialogueTextLabel.rectTransform;
            textRect.sizeDelta = new Vector2(-2f * DialogueHorizontalPadding, Mathf.Max(viewportHeight, textHeight));
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
            float preferredHeight = DialoguePreferredHeight(dialogueTextLabel.text);
            float targetHeight = Mathf.Max(dialogueViewportRect.rect.height, preferredHeight);
            Vector2 current = dialogueTextLabel.rectTransform.sizeDelta;
            if (Mathf.Abs(current.y - targetHeight) < .5f) return false;
            dialogueTextLabel.rectTransform.sizeDelta = new Vector2(current.x, targetHeight);
            Canvas.ForceUpdateCanvases();
            UpdateDialogueScrollbarVisibility();
            return true;
        }

        private float DialoguePreferredHeight(string content)
        {
            if (dialogueTextLabel == null) return 0f;
            return dialogueTextLabel.GetPreferredValues(content ?? string.Empty, DialogueInnerTextWidth(), 0f).y;
        }

        private float DialogueInnerTextWidth()
        {
            if (dialogueTextLabel == null) return 1f;
            RectTransform textRect = dialogueTextLabel.rectTransform;
            Vector4 margin = dialogueTextLabel.margin;
            return Mathf.Max(1f, textRect.rect.width - margin.x - margin.z);
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

        private void ApplyTruthScopeIndicator(string kind, string label)
        {
            if (truthScopeIndicatorLabel == null) return;
            string rendered = TruthScopeIndicatorState.DisplayText(kind, label);
            truthScopeIndicatorLabel.text = rendered;
            truthScopeIndicatorLabel.gameObject.SetActive(!string.IsNullOrEmpty(rendered));
        }

        private void ApplyContinuitySnapshot(ContinuitySnapshot snapshot)
        {
            authoritativeContinuity = snapshot;
            RefreshContinuityPanel();
        }

        private void RefreshContinuityPanel()
        {
            ContinuitySnapshot snapshot = authoritativeContinuity;
            RefreshSceneOverlay(snapshot);
            if (continuityScopeValue != null)
                continuityScopeValue.text = ContinuityPanelState.ScopeText(snapshot != null ? snapshot.scope : null);
            if (continuityActivityValue != null)
                continuityActivityValue.text = ContinuityPanelState.ActivityText(snapshot != null ? snapshot.activity : null);
            if (continuityCompanionActivityValue != null)
                continuityCompanionActivityValue.text = ContinuityPanelState.ActivityText(snapshot != null ? snapshot.companion_activity : null);
            if (continuitySceneValue != null)
            {
                ContinuitySceneSubject[] subjects = snapshot != null ? snapshot.scene_subjects : null;
                continuitySceneValue.text = ContinuityPanelState.SceneText(
                    subjects, snapshot != null ? snapshot.scene_relations : null,
                    snapshot != null ? snapshot.capability_effects : null,
                    snapshot != null ? snapshot.profile_baseline : null);
                float width = continuitySceneScroll != null && continuitySceneScroll.viewport != null
                    ? Mathf.Max(120f, continuitySceneScroll.viewport.rect.width - 24f)
                    : 520f;
                float preferred = continuitySceneValue.GetPreferredValues(
                    continuitySceneValue.text, width, 0f).y + 18f;
                float viewportHeight = continuitySceneScroll != null && continuitySceneScroll.viewport != null
                    ? continuitySceneScroll.viewport.rect.height : 210f;
                RectTransform sceneContent = continuitySceneContent != null
                    ? continuitySceneContent : continuitySceneValue.rectTransform;
                sceneContent.SetSizeWithCurrentAnchors(
                    RectTransform.Axis.Vertical, Mathf.Max(viewportHeight, preferred));
                if (continuitySceneScroll != null)
                    continuitySceneScroll.verticalNormalizedPosition = 1f;
            }
            bool idle = string.IsNullOrEmpty(pendingContinuityCommandId);
            if (clearContinuityActivityButton != null)
                clearContinuityActivityButton.interactable = idle && snapshot != null && snapshot.activity != null && snapshot.activity.can_clear;
            if (leaveContinuityScenarioButton != null)
                leaveContinuityScenarioButton.interactable = idle && snapshot != null && snapshot.scope != null && snapshot.scope.kind == "scenario";
            // Keep the authoritative snapshot hot, but do not rebuild this
            // derived hierarchy while its settings page is hidden.
            if (continuityThreadsContent == null || settingsPanel == null
                || !settingsPanel.activeSelf || activeSettingsTab != "Context") return;
            for (int index = continuityThreadsContent.childCount - 1; index >= 0; index--)
                Destroy(continuityThreadsContent.GetChild(index).gameObject);
            ContinuityThread[] threads = ContinuityPanelState.BoundedThreads(snapshot != null ? snapshot.open_threads : null);
            if (threads.Length == 0)
            {
                TMP_Text empty = CreateText(continuityThreadsContent, "No active Open Threads.", 16f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
                PlaceTop(empty.rectTransform, 0f, 36f);
            }
            for (int index = 0; index < threads.Length; index++)
            {
                ContinuityThread thread = threads[index];
                GameObject row = CreatePanel(continuityThreadsContent, "Continuity Thread", theme.surfaceMuted);
                PlaceTop(row.GetComponent<RectTransform>(), -index * 66f, 58f);
                string shown = ContinuityPanelState.ThreadPrefix(thread.kind) + ": "
                    + ContinuityPanelState.Compact(thread.description, 144);
                TMP_Text label = CreateText(row.transform, shown, 15f, Ink, TextAlignmentOptions.MidlineLeft);
                Stretch(label.rectTransform, new Vector2(.02f, .08f), new Vector2(.61f, .92f), Vector2.zero, Vector2.zero);
                label.enableWordWrapping = true;
                Button resolve = CreateButton(row.transform, "Resolve", Panel);
                Stretch(resolve.GetComponent<RectTransform>(), new Vector2(.63f, .18f), new Vector2(.80f, .82f), Vector2.zero, Vector2.zero);
                Button cancel = CreateButton(row.transform, "Cancel", Panel);
                Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.82f, .18f), new Vector2(.98f, .82f), Vector2.zero, Vector2.zero);
                string token = thread.action_token;
                CharacterSessionOwner rowOwner = client?.CharacterOwner;
                string rowRevision = snapshot?.revision;
                resolve.interactable = idle;
                cancel.interactable = idle;
                resolve.onClick.AddListener(() => RequestOwnedContinuityControl("resolve_thread", token, rowOwner, rowRevision));
                cancel.onClick.AddListener(() => RequestOwnedContinuityControl("cancel_thread", token, rowOwner, rowRevision));
            }
            RectTransform content = continuityThreadsContent as RectTransform;
            if (content != null) content.sizeDelta = new Vector2(0f, Mathf.Max(52f, threads.Length * 66f));
        }

        private void BuildSceneOverlay(RectTransform root)
        {
            sceneOverlayPanel = CreatePanel(root, "Current Scene Overlay", new Color(.055f, .055f, .10f, .96f));
            RectTransform panelRect = sceneOverlayPanel.GetComponent<RectTransform>();
            Stretch(panelRect, new Vector2(.02f, .31f), new Vector2(.285f, .82f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(sceneOverlayPanel.transform, "CURRENT SCENE", 14f, Accent, TextAlignmentOptions.MidlineLeft);
            // The panel shrinks with its row count. Keep the header at a
            // readable fixed height even for the compact single-row case.
            Stretch(title.rectTransform, new Vector2(.055f, 1f), new Vector2(.90f, 1f),
                new Vector2(0f, -30f), new Vector2(0f, -4f));
            title.fontStyle = FontStyles.Bold;

            GameObject viewport = new GameObject("Scene Overlay Viewport", typeof(RectTransform), typeof(RectMask2D));
            viewport.transform.SetParent(sceneOverlayPanel.transform, false);
            RectTransform viewportRect = viewport.GetComponent<RectTransform>();
            Stretch(viewportRect, new Vector2(.035f, .035f), new Vector2(.955f, 1f),
                Vector2.zero, new Vector2(0f, -34f));
            GameObject content = new GameObject("Scene Overlay Rows", typeof(RectTransform));
            content.transform.SetParent(viewport.transform, false);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 1f);
            contentRect.anchorMax = new Vector2(1f, 1f);
            contentRect.pivot = new Vector2(.5f, 1f);
            contentRect.anchoredPosition = Vector2.zero;
            contentRect.sizeDelta = new Vector2(0f, 1f);
            sceneOverlayContent = content.transform;
            sceneOverlayScroll = sceneOverlayPanel.AddComponent<ScrollRect>();
            sceneOverlayScroll.viewport = viewportRect;
            sceneOverlayScroll.content = contentRect;
            sceneOverlayScroll.horizontal = false;
            sceneOverlayScroll.vertical = true;
            sceneOverlayScroll.movementType = ScrollRect.MovementType.Clamped;
            sceneOverlayScroll.scrollSensitivity = 24f;
            sceneDrawerTab = CreateButton(root, "Scene ›", Panel);
            var tabRect = sceneDrawerTab.GetComponent<RectTransform>();
            tabRect.anchorMin = tabRect.anchorMax = new Vector2(0, .82f);
            tabRect.pivot = new Vector2(0, 0);
            tabRect.anchoredPosition = new Vector2(6, 4);
            tabRect.sizeDelta = new Vector2(92, 34);
            sceneDrawer = gameObject.AddComponent<SceneDrawerPresenter>();
            sceneDrawer.Initialize(sceneDrawerTab, panelRect);
            sceneOverlayPanel.SetActive(false);
        }

        private void SetSceneOverlayVisible(bool value)
        {
            showSceneOverlay = value;
            PlayerPrefs.SetInt(SceneOverlayPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            RefreshSceneOverlay(authoritativeContinuity);
        }

        private bool RefreshSceneDrawerAvailability()
        {
            bool available = UpdateSceneDrawerAvailability();
            if (available && sceneProjectionDirty) RefreshSceneOverlay(authoritativeContinuity);
            return available;
        }

        private bool UpdateSceneDrawerAvailability()
        {
            bool normalUiVisible = (!interfaceHidden || inputRequested)
                && (modalScrim == null || !modalScrim.activeSelf) && !characterSwitchInFlight;
            bool available = SceneOverlayState.ShouldShow(showSceneOverlay, 0, normalUiVisible);
            sceneDrawer?.SetAvailable(available);
            return available;
        }

        private void RefreshSceneOverlay(ContinuitySnapshot snapshot)
        {
            sceneProjectionDirty = true;
            if (sceneOverlayPanel == null || sceneOverlayContent == null) return;
            SceneOverlayRow[] rows = SceneOverlayState.Rows(snapshot);
            if (!UpdateSceneDrawerAvailability()) return;
            if (rows.Length == 0) rows = new[] { new SceneOverlayRow { text = "No current scene items." } };
            RectTransform panelRect = sceneOverlayPanel.GetComponent<RectTransform>();
            if (panelRect != null)
            {
                var parentRect = panelRect.parent as RectTransform;
                float width = Mathf.Clamp((parentRect != null ? parentRect.rect.width : 900) * .44f, 290, 410);
                panelRect.anchorMin = panelRect.anchorMax = new Vector2(0, .82f);
                panelRect.pivot = new Vector2(0, 1);
                panelRect.SetSizeWithCurrentAnchors(RectTransform.Axis.Horizontal, width);
                string key = string.Join("\n", rows.Select(row => row.text + "|" + row.action + "|" + row.actionToken))
                    + "|" + pendingContinuityCommandId + "|" + client?.CharacterOwner?.Session + "|" + snapshot?.revision;
                if (sceneDrawerRowsKey == key && Mathf.Abs(sceneDrawerWidth - width) < .5f)
                {
                    panelRect.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical,
                        Mathf.Min(sceneDrawerContentHeight, (parentRect != null ? parentRect.rect.height : Screen.height) * .34f));
                    sceneProjectionDirty = false;
                    return;
                }
                sceneDrawerRowsKey = key; sceneDrawerWidth = width;
            }
            for (int index = sceneOverlayContent.childCount - 1; index >= 0; index--)
            {
                GameObject child = sceneOverlayContent.GetChild(index).gameObject;
                if (Application.isPlaying) Destroy(child);
                else DestroyImmediate(child);
            }
            bool idle = string.IsNullOrEmpty(pendingContinuityCommandId);
            float rowTop = 0;
            for (int index = 0; index < rows.Length; index++)
            {
                SceneOverlayRow row = rows[index];
                Color rowColor = theme.surfaceMuted;
                rowColor.a = Mathf.Min(rowColor.a, .58f);
                GameObject surface = CreatePanel(sceneOverlayContent, "Scene Overlay Row", rowColor);
                PlaceTop(surface.GetComponent<RectTransform>(), -rowTop, 44f, 0f, 1f);
                TMP_Text label = CreateText(surface.transform, row.text, 14f, Ink, TextAlignmentOptions.MidlineLeft);
                Stretch(label.rectTransform, new Vector2(.035f, .06f), new Vector2(.83f, .94f), Vector2.zero, Vector2.zero);
                label.enableWordWrapping = true;
                label.richText = false;
                float rowHeight = Mathf.Clamp(label.GetPreferredValues(row.text, sceneDrawerWidth * .70f, 0).y + 16, 44, 132);
                PlaceTop(surface.GetComponent<RectTransform>(), -rowTop, rowHeight, 0f, 1f);
                rowTop += rowHeight + 5;
                if (!string.IsNullOrEmpty(row.action) && !string.IsNullOrEmpty(row.actionToken))
                {
                    Button clear = CreateButton(surface.transform, "×", Panel);
                    Stretch(clear.GetComponent<RectTransform>(), new Vector2(.85f, .12f), new Vector2(.975f, .88f), Vector2.zero, Vector2.zero);
                    clear.interactable = idle;
                    string action = row.action;
                    string token = row.actionToken;
                    CharacterSessionOwner rowOwner = client?.CharacterOwner;
                    string rowRevision = snapshot?.revision;
                    clear.onClick.AddListener(() => RequestOwnedContinuityControl(action, token, rowOwner, rowRevision));
                }
            }
            RectTransform contentRect = sceneOverlayContent as RectTransform;
            if (contentRect != null)
                contentRect.sizeDelta = new Vector2(0f, Mathf.Max(1f, rowTop));
            if (sceneOverlayScroll != null) sceneOverlayScroll.verticalNormalizedPosition = 1f;
            sceneDrawerContentHeight = rowTop + 42;
            panelRect.SetSizeWithCurrentAnchors(RectTransform.Axis.Vertical,
                Mathf.Min(sceneDrawerContentHeight, (panelRect.parent as RectTransform).rect.height * .34f));
            sceneProjectionDirty = false;
        }

        private void RequestContinuityControl(string action, string actionToken = "") =>
            RequestOwnedContinuityControl(action, actionToken, client?.CharacterOwner, authoritativeContinuity?.revision);

        private void RequestOwnedContinuityControl(string action, string actionToken,
            CharacterSessionOwner owner, string revision)
        {
            if (client == null || client.State != ConnectionState.Connected
                || characterSwitchInFlight || authoritativeContinuity == null
                || !string.IsNullOrEmpty(pendingContinuityCommandId)) return;
            if (!client.OwnsCharacter(owner) || revision != authoritativeContinuity.revision) return;
            pendingContinuityOwner = owner;
            BeginPendingContinuityControl(
                Guid.NewGuid().ToString("D"), action, actionToken,
                revision ?? string.Empty);
            _ = client.ApplyContinuityControlAsync(
                pendingContinuityCommandId, pendingContinuityAction,
                pendingContinuityRevision, pendingContinuityActionToken, pendingContinuityOwner);
        }

        internal void BeginPendingContinuityControl(
            string commandId, string action, string actionToken, string revision)
        {
            pendingContinuityCommandId = commandId;
            pendingContinuityAction = action ?? string.Empty;
            pendingContinuityActionToken = actionToken ?? string.Empty;
            pendingContinuityRevision = revision ?? string.Empty;
            pendingContinuityRetrySent = true;
            // Pending state belongs only to the Context controls. In
            // particular, it must not enter the conversational Thinking
            // lifecycle or replace the currently presented dialogue.
            RefreshContinuityPanel();
        }

        internal void CompletePendingContinuityControl(ContinuitySnapshot snapshot)
        {
            // The authoritative acknowledgement updates only the structured
            // Context view. It is not a conversational ready/turn event.
            ApplyContinuitySnapshot(snapshot);
            ClearPendingContinuityControl();
        }

        internal void ApplyAuthoritativeContinuityChange(string commandId, ContinuitySnapshot snapshot)
        {
            // Immersive overlay mutations publish their authoritative snapshot
            // before Gemma reacts. Accept that boundary immediately so another
            // X can cancel stale prose and submit against the new revision.
            if (string.Equals(pendingContinuityAction, "interact_scene_relation", StringComparison.Ordinal)
                && !string.IsNullOrEmpty(commandId)
                && string.Equals(commandId, pendingContinuityCommandId, StringComparison.Ordinal))
            {
                CompletePendingContinuityControl(snapshot);
                return;
            }
            ApplyContinuitySnapshot(snapshot);
        }

        private void RetryPendingContinuityControlAfterReconnect()
        {
            if (client == null || client.State != ConnectionState.Connected
                || string.IsNullOrEmpty(pendingContinuityCommandId) || pendingContinuityRetrySent) return;
            if (!client.OwnsCharacter(pendingContinuityOwner)) { ClearPendingContinuityControl(); return; }
            pendingContinuityRetrySent = true;
            _ = client.ApplyContinuityControlAsync(
                pendingContinuityCommandId, pendingContinuityAction,
                pendingContinuityRevision, pendingContinuityActionToken, pendingContinuityOwner);
        }

        private void ClearPendingContinuityControl()
        {
            pendingContinuityCommandId = null;
            pendingContinuityAction = null;
            pendingContinuityActionToken = null;
            pendingContinuityRevision = null;
            pendingContinuityRetrySent = false;
            pendingContinuityOwner = null;
            RefreshContinuityPanel();
        }

        private void UpdateBackendDisconnectWarning()
        {
            if (client == null || client.State == lastObservedConnectionState) return;
            lastObservedConnectionState = client.State;
            if (client.State == ConnectionState.Disconnected || client.State == ConnectionState.Error)
            {
                pendingContinuityRetrySent = false;
                CharacterManagementDisconnected();
                ClearPendingModelSettings();
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
            if (submitInFlight || characterStorageUnavailable || string.IsNullOrEmpty(text) || client == null || client.State != ConnectionState.Connected)
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

            pttThinkingPresentation.CaptureBeforeThinking(wordReveal.VisibleText);
            thinkingElapsed = 0f;
            wordReveal.Begin("Thinking.", true);
            dialogueTextLabel.text = DialoguePresentationParser.FormatVisible(wordReveal.VisibleText, !wordReveal.IsComplete);
            // Thinking is presentation state, not a new dialogue measurement.
            // Preserve the current card geometry until the actual reply arrives.
        }

        private void RestoreDialogueAfterPttEndedWithoutTurn(string previousDialogue)
        {
            currentAssistantPresentationText = previousDialogue ?? string.Empty;
            dialogueLayoutContent = DialoguePresentationParser.FormatVisible(currentAssistantPresentationText);
            wordReveal.Begin(currentAssistantPresentationText, true);
            RefreshDialogueRevealText(false);
            UpdateDialogueLayout(false);
            RefreshDialogueScrollableContent();
            if (dialogueAutoFollow)
                FollowScrollIfNearBottom(dialogueScroll);
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
            RefreshSceneDrawerAvailability();
            if (show)
            {
                modalScrim.transform.SetAsLastSibling();
                historyPanel.transform.SetAsLastSibling();
                // Browsing state belongs only to one open-panel session.
                // Every closed -> open transition starts from the newest
                // canonical day and its newest bounded page.
                SelectLatestHistoryDay();
                historyDirty = true;
                RefreshHistoryIfVisible();
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
            RefreshSceneDrawerAvailability();
        }

        private void ToggleConsolePanel()
        {
            if (consolePanel == null) return;
            bool show = !consolePanel.activeSelf;
            consolePanel.SetActive(show);
            if (modalScrim != null) modalScrim.SetActive(show);
            RefreshSceneDrawerAvailability();
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
            RefreshSceneDrawerAvailability();
            SetTopControlLabel(consoleCopyButton, "Copy All");
            RefreshDeveloperControlVisibility();
        }

        private void ToggleSettingsPanel()
        {
            CancelAvatarCuesDraft();
            CancelCompanionPreferenceDraft();
            CancelSubtitleColor();
            bool show = !settingsPanel.activeSelf;
            settingsPanel.SetActive(show);
            if (modalScrim != null) modalScrim.SetActive(show);
            RefreshSceneDrawerAvailability();
            if (show)
            {
                pendingDisplaySettings = currentDisplaySettings.Clone();
                RefreshDisplaySettingsUi();
                ConfigureSettingsPanelForCurrentOrientation();
                if (activeSettingsTab == "Context") RefreshContinuityPanel();
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
            CancelAvatarCuesDraft();
            CancelCompanionPreferenceDraft();
            CancelSubtitleColor();
            selectedModelAssets.Clear();
            if (modelLibraryPanel != null) modelLibraryPanel.SetActive(false);
            selectedBackgroundAssets.Clear();
            if (backgroundLibraryPanel != null) backgroundLibraryPanel.SetActive(false);
            if (memoryViewerPanel != null) memoryViewerPanel.SetActive(false);
            if (settingsPanel != null) settingsPanel.SetActive(false);
            if (modalScrim != null) modalScrim.SetActive(false);
            RefreshSceneDrawerAvailability();
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
                if (text == hiddenDialogueText || text == hiddenSubtitleMeasurementText) continue;
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
            RefreshSubtitleColorPreview();
            ApplyFocusedReadability();
            if (sceneOverlayPanel != null)
            {
                Color drawerSurface = theme.surfaceStrong; drawerSurface.a = .96f;
                sceneOverlayPanel.GetComponent<Image>().color = drawerSurface;
            }
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
            // Unity-delivered input is always the primary path. The optional
            // OS-wide listener is layered on top in the backend; PushToTalk's
            // lock de-duplicates the matching global/local press or release.
            // UI visibility and Application.isFocused are not polling gates:
            // hidden-overlay transitions can report focus inconsistently even
            // while the player still delivers the configured key. Actual
            // focus/pause loss is handled by the callbacks below, which safely
            // releases any active local press.
            if (rebindingPushToTalk || settingsPanel == null || settingsPanel.activeSelf || client == null ||
                client.State != ConnectionState.Connected)
            {
                ReleaseUnityPushToTalk();
                return;
            }

            // When Unity does not deliver the binding (normally because a
            // different application owns keyboard input), the backend's
            // OS-level listener remains the independent global path.
            if (!unityPttPressed && PresentationPttInputPolicy.ShouldStart(Input.GetKeyDown(pushToTalkKey)))
            {
                unityPttPressed = true;
                restoreMessageInputAfterPtt = messageInput != null && messageInput.isFocused;
                Debug.Log("[AIFren PTT] Focused press detected; inputFocused=" + restoreMessageInputAfterPtt + ".");
                presentationAudio?.PlayInterrupt();
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                developmentFlightRecorder?.Mark("ptt_press");
#endif
                presentationTurn.Reset();
                avatarAnimation?.RetireResponseMotion();
                _ = client.SetPushToTalkPressedAsync(true);
            }
            else if (PresentationPttInputPolicy.ShouldRelease(unityPttPressed, Input.GetKey(pushToTalkKey)))
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
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFlightRecorder?.Mark("ptt_release");
#endif
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
            presentationTurn.CancelAutomatic();
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
            hiddenSubtitlePresenter?.ConfigureReveal(value, instantText);
            PlayerPrefs.SetFloat(RevealSpeedPreference, value);
            PlayerPrefs.Save();
            revealSpeedLabel.text = $"{value:0.0} words / sec";
        }

        private void SetInstantText(bool value)
        {
            bool refreshActivePresentation = InstantTextRequiresPresentationRefresh(
                value, assistantStreamPresentationDirty, wordReveal.IsComplete);
            instantText = value;
            hiddenSubtitlePresenter?.ConfigureReveal(revealWordsPerSecond, value);
            PlayerPrefs.SetInt(InstantTextPreference, value ? 1 : 0);
            PlayerPrefs.Save();
            // Show all canonical text received so far. Future stream deltas
            // use the same flag and appear immediately as they arrive.
            if (refreshActivePresentation)
            {
                // IsComplete describes the last coalesced buffer, not pending
                // provider deltas. Flush those first even when every older
                // word was already visible, then reveal the resulting buffer.
                if (assistantStreamPresentationDirty) RefreshStreamedAssistantDialogue(false);
                if (!wordReveal.IsComplete)
                {
                    wordReveal.RevealAll();
                    RefreshDialogueRevealText(assistantStreamVisible);
                    UpdateDialogueLayout(false);
                    RefreshDialogueScrollableContent();
                    if (dialogueAutoFollow) FollowScrollIfNearBottom(dialogueScroll);
                }
            }
        }

        internal static bool InstantTextRequiresPresentationRefresh(
            bool enabled, bool streamPresentationDirty, bool revealComplete)
        {
            return enabled && (streamPresentationDirty || !revealComplete);
        }

        private void UpdateVolumeLabel(float value)
        {
            volumeLabel.text = $"{Mathf.RoundToInt(value * 100f)}%";
        }

        private void RefreshInputAvailability()
        {
            bool connected = client != null && client.State == ConnectionState.Connected;
            bool enabled = connected && !submitInFlight && !characterSwitchInFlight && !characterStorageUnavailable && client.CharacterOwner != null;
            if (messageInput != null) messageInput.interactable = enabled;
            if (sendButton != null) sendButton.interactable = enabled;
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
            presentationTurn.Reset();
            avatarAnimation = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarAnimationController>()
                : null;
            AvatarPresentationResolver stateResolver = avatarLoader != null
                ? avatarLoader.GetComponent<AvatarPresentationResolver>()
                : null;
            stateResolver?.Apply(authoritativeStatePresentation);
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
            avatarPresentationInitialization = StartCoroutine(FinalizeAvatarPresentationAfterLayout(modelApplyGeneration, avatar));
        }

        private IEnumerator FinalizeAvatarPresentationAfterLayout(int generation, GameObject avatar)
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
            if (generation != modelApplyGeneration || avatarLoader == null || avatarLoader.ActiveAvatar != avatar) yield break;
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
            if (historyContent == null) return;
            bool followLatest = IsNearBottom(historyScroll);
            ResetHistoryRowPool();
            float y = -8f;
            if (historyIndex.RenderableCount == 0)
            {
                string emptyLabel = historyViewRequest.Describe("history", 0);
                if (string.IsNullOrEmpty(emptyLabel)) emptyLabel = messages.Count == 0
                    ? "No conversation messages in this timeline." : "No displayable messages in this history.";
                AddHistoryTextRow(emptyLabel, 16f, theme.mutedText,
                    TextAlignmentOptions.MidlineLeft, ref y, 34f);
            }
            else if (historyLevel == HistoryNavigationLevel.Years)
            {
                foreach (int year in historyIndex.Years())
                {
                    string label = year > 0 ? year.ToString(CultureInfo.InvariantCulture) : "Date unavailable";
                    AddHistoryNavigationRow(
                        label + "  ·  " + historyIndex.CountForYear(year) + " messages",
                        () => SelectHistoryYear(year), ref y);
                }
            }
            else if (historyLevel == HistoryNavigationLevel.Months)
            {
                foreach (int month in historyIndex.Months(selectedHistoryYear))
                {
                    int captured = month;
                    string label = CultureInfo.CurrentCulture.DateTimeFormat.GetMonthName(month);
                    AddHistoryNavigationRow(
                        label + "  ·  " + historyIndex.CountForMonth(selectedHistoryYear, month) + " messages",
                        () => SelectHistoryMonth(captured), ref y);
                }
            }
            else if (historyLevel == HistoryNavigationLevel.Days)
            {
                foreach (HistoryDayKey day in historyIndex.Days(selectedHistoryYear, selectedHistoryMonth))
                {
                    HistoryDayKey captured = day;
                    string label = day.IsDated
                        ? new DateTime(day.Year, day.Month, day.Day).ToString("dddd, MMMM d")
                        : "Older history — date unavailable";
                    AddHistoryNavigationRow(
                        label + "  ·  " + historyIndex.CountForDay(day) + " messages",
                        () => SelectHistoryDay(captured), ref y);
                }
            }
            else
            {
                int total = historyIndex.CountForDay(selectedHistoryDay);
                int pageCount = Math.Max(1, (total + HistoryMessagePageSize - 1) / HistoryMessagePageSize);
                selectedHistoryPage = Math.Max(0, Math.Min(pageCount - 1, selectedHistoryPage));
                HistoryMessagePage page = historyIndex.Page(
                    selectedHistoryDay, selectedHistoryPage, HistoryMessagePageSize);
                string date = selectedHistoryDay.IsDated
                    ? new DateTime(selectedHistoryDay.Year, selectedHistoryDay.Month, selectedHistoryDay.Day)
                        .ToString("dddd, MMMM d, yyyy")
                    : "Older history — date unavailable";
                AddHistoryTextRow(
                    "<b>" + date + "</b>  ·  page " + (page.PageIndex + 1) + " of "
                    + Math.Max(1, page.PageCount), 15f, theme.sectionHeader,
                    TextAlignmentOptions.MidlineLeft, ref y, 30f);
                if (page.PageCount > 1)
                {
                    AddHistoryPageControls(page, ref y);
                }
                float availableWidth = historyScroll != null && historyScroll.viewport != null
                    ? Mathf.Max(220f, historyScroll.viewport.rect.width - 42f) : 620f;
                foreach (ConversationMessage message in page.Messages)
                {
                    if (message == null || string.IsNullOrWhiteSpace(message.content)) continue;
                    bool isUser = message.role == "user";
                    TMP_Text bubble = AcquireHistoryText();
                    bubble.fontSize = 18f;
                    bubble.color = theme.text;
                    bubble.alignment = TextAlignmentOptions.TopLeft;
                    bubble.enableWordWrapping = true;
                    bubble.overflowMode = TextOverflowModes.Overflow;
                    bubble.lineSpacing = -3f;
                    bubble.paragraphSpacing = -4f;
                    string timestamp = FormatHistoryTimestamp(message.timestamp);
                    string speakerColor = "#" + ColorUtility.ToHtmlStringRGB(
                        isUser ? theme.userText : theme.sectionHeader);
                    string timestampColor = "#" + ColorUtility.ToHtmlStringRGB(theme.mutedText);
                    bubble.text = string.IsNullOrEmpty(timestamp)
                        ? $"<b><color={speakerColor}>{(isUser ? "You" : characterName)}</color></b>\n{message.content}"
                        : $"<b><color={speakerColor}>{(isUser ? "You" : characterName)}</color></b>  <size=65%><color={timestampColor}>{timestamp}</color></size>\n{message.content}";
                    float height = Mathf.Max(46f,
                        bubble.GetPreferredValues(bubble.text, availableWidth, 0f).y + 8f);
                    PlaceHistoryRow(bubble.rectTransform, y, height, 14f, 28f);
                    y -= height + 6f;
                }
            }

            RectTransform contentRect = historyContent as RectTransform;
            contentRect.sizeDelta = new Vector2(0f, Mathf.Max(20f, -y));
            if (historyScroll != null) historyScroll.content = contentRect;
            UpdateHistoryNavigationHeader();
            Canvas.ForceUpdateCanvases();
            if (historyScroll != null && (followLatest || historyLevel != HistoryNavigationLevel.Messages))
                historyScroll.verticalNormalizedPosition = historyLevel == HistoryNavigationLevel.Messages ? 0f : 1f;
            UpdateHistoryViewStatus();
            historyDirty = false;
            MarkView("history_rendered", historyModelRequestId, activeHistoryTextRows);
        }

        private void ResetHistoryRowPool()
        {
            activeHistoryTextRows = 0;
            activeHistoryButtonRows = 0;
            foreach (TMP_Text row in historyTextRows) if (row != null) row.gameObject.SetActive(false);
            foreach (Button row in historyButtonRows) if (row != null) row.gameObject.SetActive(false);
        }

        private TMP_Text AcquireHistoryText()
        {
            TMP_Text value;
            if (activeHistoryTextRows < historyTextRows.Count)
                value = historyTextRows[activeHistoryTextRows];
            else
            {
                value = CreateText(historyContent, string.Empty, 16f, theme.text, TextAlignmentOptions.TopLeft);
                historyTextRows.Add(value);
            }
            activeHistoryTextRows++;
            value.gameObject.SetActive(true);
            return value;
        }

        private Button AcquireHistoryButton()
        {
            Button value;
            if (activeHistoryButtonRows < historyButtonRows.Count)
                value = historyButtonRows[activeHistoryButtonRows];
            else
            {
                value = CreateButton(historyContent, string.Empty, Panel);
                historyButtonRows.Add(value);
            }
            activeHistoryButtonRows++;
            value.gameObject.SetActive(true);
            value.onClick.RemoveAllListeners();
            return value;
        }

        private void AddHistoryTextRow(
            string text, float size, Color color, TextAlignmentOptions alignment,
            ref float y, float height)
        {
            TMP_Text row = AcquireHistoryText();
            row.text = text;
            row.fontSize = size;
            row.color = color;
            row.alignment = alignment;
            row.enableWordWrapping = true;
            PlaceHistoryRow(row.rectTransform, y, height, 14f, 24f);
            y -= height + 6f;
        }

        private void AddHistoryNavigationRow(string label, Action onClick, ref float y)
        {
            Button row = AcquireHistoryButton();
            SetTopControlLabel(row, label);
            row.onClick.AddListener(() => onClick());
            PlaceHistoryRow(row.GetComponent<RectTransform>(), y, 42f, 14f, 26f);
            y -= 48f;
        }

        private void AddHistoryPageControls(HistoryMessagePage page, ref float y)
        {
            Button older = AcquireHistoryButton();
            SetTopControlLabel(older, "Older page");
            older.interactable = page.PageIndex > 0;
            older.onClick.AddListener(() => ChangeHistoryPage(-1));
            PlaceHistoryRow(older.GetComponent<RectTransform>(), y, 38f, 14f, 0f, .02f, .49f);
            Button newer = AcquireHistoryButton();
            SetTopControlLabel(newer, "Newer page");
            newer.interactable = page.PageIndex + 1 < page.PageCount;
            newer.onClick.AddListener(() => ChangeHistoryPage(1));
            PlaceHistoryRow(newer.GetComponent<RectTransform>(), y, 38f, 0f, 26f, .51f, .98f);
            y -= 44f;
        }

        private static void PlaceHistoryRow(
            RectTransform row, float y, float height, float left, float right,
            float minX = 0f, float maxX = 1f)
        {
            row.anchorMin = new Vector2(minX, 1f);
            row.anchorMax = new Vector2(maxX, 1f);
            row.pivot = new Vector2(.5f, 1f);
            row.anchoredPosition = new Vector2(0f, y);
            row.sizeDelta = new Vector2(-(left + right), height);
        }

        private void SelectLatestHistoryDay()
        {
            if (!historyIndex.TryLatestDay(out HistoryDayKey day)) return;
            selectedHistoryDay = day;
            selectedHistoryYear = day.Year;
            selectedHistoryMonth = day.Month;
            int count = historyIndex.CountForDay(day);
            selectedHistoryPage = Math.Max(0, (count - 1) / HistoryMessagePageSize);
            historyLevel = HistoryNavigationLevel.Messages;
        }

        private void SelectHistoryYear(int year)
        {
            selectedHistoryYear = year;
            selectedHistoryMonth = 0;
            historyLevel = year <= 0 ? HistoryNavigationLevel.Days : HistoryNavigationLevel.Months;
            historyDirty = true;
            RebuildHistory();
        }

        private void SelectHistoryMonth(int month)
        {
            selectedHistoryMonth = month;
            historyLevel = HistoryNavigationLevel.Days;
            historyDirty = true;
            RebuildHistory();
        }

        private void SelectHistoryDay(HistoryDayKey day)
        {
            selectedHistoryDay = day;
            selectedHistoryYear = day.Year;
            selectedHistoryMonth = day.Month;
            int count = historyIndex.CountForDay(day);
            selectedHistoryPage = Math.Max(0, (count - 1) / HistoryMessagePageSize);
            historyLevel = HistoryNavigationLevel.Messages;
            historyDirty = true;
            RebuildHistory();
        }

        private void ChangeHistoryPage(int delta)
        {
            selectedHistoryPage = Math.Max(0, selectedHistoryPage + delta);
            historyDirty = true;
            RebuildHistory();
        }

        private void NavigateHistoryBack()
        {
            if (historyLevel == HistoryNavigationLevel.Messages)
                historyLevel = HistoryNavigationLevel.Days;
            else if (historyLevel == HistoryNavigationLevel.Days)
                historyLevel = selectedHistoryYear <= 0
                    ? HistoryNavigationLevel.Years : HistoryNavigationLevel.Months;
            else if (historyLevel == HistoryNavigationLevel.Months)
                historyLevel = HistoryNavigationLevel.Years;
            historyDirty = true;
            RebuildHistory();
        }

        private void UpdateHistoryNavigationHeader()
        {
            if (historyBackButton != null)
            {
                historyBackButton.gameObject.SetActive(historyLevel != HistoryNavigationLevel.Years);
                historyBackButton.interactable = historyLevel != HistoryNavigationLevel.Years;
            }
            if (historyPathLabel == null) return;
            if (historyLevel == HistoryNavigationLevel.Years) historyPathLabel.text = "Years";
            else if (historyLevel == HistoryNavigationLevel.Months)
                historyPathLabel.text = selectedHistoryYear.ToString(CultureInfo.InvariantCulture);
            else if (historyLevel == HistoryNavigationLevel.Days)
                historyPathLabel.text = selectedHistoryYear > 0
                    ? selectedHistoryYear + " / " + CultureInfo.CurrentCulture.DateTimeFormat.GetMonthName(selectedHistoryMonth)
                    : "Date unavailable";
            else historyPathLabel.text = selectedHistoryDay.IsDated
                ? new DateTime(selectedHistoryDay.Year, selectedHistoryDay.Month, selectedHistoryDay.Day).ToString("yyyy / MMMM / d")
                : "Date unavailable";
        }

        private void RefreshHistoryIfVisible()
        {
            if (!historyDirty || historyPanel == null || !historyPanel.activeInHierarchy)
            {
                return;
            }

            RebuildHistory();
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
            // Backend truth scope stays available independently of normal UI
            // visibility, but occupies a subtle safe-margin corner.
            truthScopeIndicatorLabel = CreateText(
                root, string.Empty, 14f, new Color(.90f, .86f, .96f, .74f),
                TextAlignmentOptions.BottomLeft);
            truthScopeIndicatorLabel.gameObject.name = "RP Truth Scope Indicator";
            truthScopeIndicatorLabel.outlineColor = TruthScopeIndicatorState.OutlineColor();
            truthScopeIndicatorLabel.outlineWidth = TruthScopeIndicatorState.OutlineWidth;
            truthScopeIndicatorLabel.UpdateMeshPadding();
            LayoutTruthScopeIndicator();
            truthScopeIndicatorLabel.enableWordWrapping = false;
            truthScopeIndicatorLabel.overflowMode = TextOverflowModes.Ellipsis;
            truthScopeIndicatorLabel.raycastTarget = false;
            truthScopeIndicatorLabel.gameObject.SetActive(false);
            BuildSceneOverlay(root);
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
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                    if (dialogueAutoFollow && !dialogueManualBottomRecorded)
                    {
                        dialogueManualBottomRecorded = true;
                        RecordDialogueLayoutState("manual_bottom");
                        ScheduleDialogueLayoutFollowup("manual_bottom");
                    }
#endif
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
            LayoutTruthScopeIndicator();
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
            // Page fitting is isolated from the visible TMP. Changing the
            // visible font size for GetPreferredValues dirtied its current
            // mesh and moved that rebuild onto later audio chunk boundaries.
            hiddenSubtitleMeasurementText = CreateText(hiddenDialogueViewport, string.Empty, 23f,
                Color.clear, TextAlignmentOptions.Top);
            hiddenSubtitleMeasurementText.enableWordWrapping = true;
            hiddenSubtitleMeasurementText.fontStyle = FontStyles.Bold;
            hiddenSubtitleMeasurementText.lineSpacing = -5f;
            hiddenSubtitleMeasurementText.paragraphSpacing = -3f;
            hiddenSubtitleMeasurementText.margin = new Vector4(18f, 12f, 18f, 12f);
            Stretch(hiddenSubtitleMeasurementText.rectTransform, Vector2.zero, Vector2.one,
                new Vector2(18f, 10f), new Vector2(-18f, -10f));
            hiddenSubtitleMeasurementText.gameObject.SetActive(false);
            // One TMP owns both face and edge presentation through its private
            // SDF material. No backing text hierarchy or UI shadow geometry is
            // needed; the inactive TMP above remains measurement-only.
            EnsureHiddenSubtitlePresentation();
            hiddenSubtitleRenderTarget = new TmpHiddenSubtitleRenderTarget(hiddenDialogueViewportObject,
                hiddenDialogueCanvasGroup, hiddenDialogueViewport, hiddenDialogueText, hiddenSubtitleMeasurementText);
            hiddenSubtitlePresenter = new HiddenSubtitlePresenter(hiddenSubtitleRenderTarget);
            hiddenSubtitlePresenter.ConfigureReveal(revealWordsPerSecond, instantText);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            hiddenSubtitlePresenter.PageActivated += page =>
            {
                developmentFrameProfiler?.Mark("subtitle_page_activated:" + page);
                developmentFlightRecorder?.SetSubtitlePage(page);
            };
            hiddenSubtitlePresenter.WordPresented += word =>
            {
                developmentFrameProfiler?.Mark("subtitle_word_presented:" + word);
                developmentFlightRecorder?.SetSubtitleWord(word);
            };
#endif

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
            Stretch(title.rectTransform, new Vector2(0.06f, 0.89f), new Vector2(0.34f, 0.98f), Vector2.zero, Vector2.zero);
            historyPathLabel = CreateText(panel.transform, string.Empty, 14f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
            Stretch(historyPathLabel.rectTransform, new Vector2(.34f, .89f), new Vector2(.48f, .98f), Vector2.zero, Vector2.zero);
            historyRefreshButton = CreateButton(panel.transform, "Refresh", Panel);
            Stretch(historyRefreshButton.GetComponent<RectTransform>(), new Vector2(.49f, .89f), new Vector2(.64f, .98f), Vector2.zero, Vector2.zero);
            historyRefreshButton.onClick.AddListener(RequestHistoryRefresh);
            historyViewStatus = CreateText(panel.transform, string.Empty, 14f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
            Stretch(historyViewStatus.rectTransform, new Vector2(.055f, .81f), new Vector2(.945f, .885f), Vector2.zero, Vector2.zero);
            historyViewStatus.enableWordWrapping = true;
            UpdateHistoryViewStatus();
            historyBackButton = CreateButton(panel.transform, "Back", Panel);
            Stretch(historyBackButton.GetComponent<RectTransform>(), new Vector2(.65f, .89f), new Vector2(.75f, .98f), Vector2.zero, Vector2.zero);
            historyBackButton.onClick.AddListener(NavigateHistoryBack);
            Button closeButton = CreateButton(panel.transform, "Close", new Color(0.22f, 0.19f, 0.31f, 1f));
            Stretch(closeButton.GetComponent<RectTransform>(), new Vector2(0.78f, 0.89f), new Vector2(0.94f, 0.98f), Vector2.zero, Vector2.zero);
            closeButton.onClick.AddListener(CloseHistoryPanel);

            GameObject viewport = CreatePanel(panel.transform, "Viewport", new Color(0f, 0f, 0f, 0.18f));
            Stretch(viewport.GetComponent<RectTransform>(), new Vector2(0.045f, 0.05f), new Vector2(0.955f, 0.805f), Vector2.zero, Vector2.zero);
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
            // Diagnostics are requested when the modal opens. If an older
            // backend sends an unsolicited refresh while it is closed, retain
            // the bounded lines but do no TMP/layout/canvas work.
            if (consolePanel == null || !consolePanel.activeInHierarchy) return;
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
            if (avatarViewEditing || !HasCurrentFramingOwner) return;
            avatarViewEditOwner = avatarPresentationState;
            avatarViewEditGeneration = modelApplyGeneration;
            avatarViewPortraitSnapshot = avatarPresentationState.GetValues(true);
            avatarViewLandscapeSnapshot = avatarPresentationState.GetValues(false);
            avatarViewEditing = true;
            CloseSettingsPanel();
            avatarViewGrid.SetActive(true); avatarViewPanel.SetActive(true); avatarViewPanel.transform.SetAsLastSibling();
            SyncAvatarViewControls();
        }

        private void SaveAvatarViewEditor()
        {
            if (!HasCurrentFramingEdit) { CancelAvatarViewEditor(); return; }
            try { avatarViewEditOwner.Commit(AvatarViewPortrait); }
            catch (InvalidOperationException error) { ApplyStatus("error", error.Message); return; }
            avatarPresentationState.SetValues(!AvatarViewPortrait, !AvatarViewPortrait ? avatarViewPortraitSnapshot : avatarViewLandscapeSnapshot, false);
            ExitAvatarViewEditor();
        }

        private void CancelAvatarViewEditor()
        {
            if (avatarViewEditing && avatarViewEditOwner != null)
            {
                avatarViewEditOwner.SetValues(true, avatarViewPortraitSnapshot, false);
                avatarViewEditOwner.SetValues(false, avatarViewLandscapeSnapshot, false);
                if (HasCurrentFramingEdit) ApplyAvatarPresentationTransform(AvatarViewPortrait);
            }
            ExitAvatarViewEditor();
        }

        private void ResetAvatarViewEditor()
        {
            if (!HasCurrentFramingEdit) return;
            avatarPresentationState.Reset(AvatarViewPortrait, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait); SyncAvatarViewControls();
        }

        private void ExitAvatarViewEditor()
        {
            avatarViewEditing = false; avatarViewEditOwner = null;
            if (avatarViewGrid != null) avatarViewGrid.SetActive(false);
            if (avatarViewPanel != null) avatarViewPanel.SetActive(false);
        }

        private void SetAvatarViewValue(int field, float value)
        {
            if (suppressAvatarViewCallbacks || !HasCurrentFramingEdit) return;
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
            if (!HasCurrentFramingEdit) return;
            AvatarPresentationValues values = avatarPresentationState.GetValues(AvatarViewPortrait);
            values.x += delta.x / Mathf.Max(1f, Screen.width);
            values.y += delta.y / Mathf.Max(1f, Screen.height);
            avatarPresentationState.SetValues(AvatarViewPortrait, values, false);
            ApplyAvatarPresentationTransform(AvatarViewPortrait); SyncAvatarViewControls();
        }

        private void HandleAvatarViewScroll(float delta)
        {
            if (!HasCurrentFramingEdit) return;
            SetAvatarViewValue(2, avatarPresentationState.GetValues(AvatarViewPortrait).scale + delta * .08f);
        }

        private void SyncAvatarViewControls()
        {
            if (!HasCurrentFramingEdit) return;
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
            string[] tabs = { "Display", "Character", "Context", "Models", "Audio", "Dialogue", "Controls", "Appearance", "Advanced" };
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

            Transform characterSettings = settingsTabContent["Character"]; y = -18f;
            AddSettingsHeading(characterSettings, "CHARACTER", ref y);
            currentCharacterValue = AddSettingsValue(characterSettings, "Current", ref y);
            TMP_Text switchHint = CreateText(characterSettings, "Choose a character below. Each character remembers its avatar and framing. Background and voice remain global.", 15f, new Color(.72f, .72f, .82f, 1f), TextAlignmentOptions.MidlineLeft);
            PlaceTop(switchHint.rectTransform, y, 32f); y -= 38f;
            GameObject characterListViewport = CreatePanel(characterSettings, "Character List Viewport", theme.surfaceMuted);
            PlaceTop(characterListViewport.GetComponent<RectTransform>(), y, 150f);
            characterListViewport.AddComponent<RectMask2D>();
            GameObject characterList = new GameObject("Character List", typeof(RectTransform));
            characterList.transform.SetParent(characterListViewport.transform, false);
            RectTransform characterListRect = characterList.GetComponent<RectTransform>();
            characterListRect.anchorMin = new Vector2(0f, 1f);
            characterListRect.anchorMax = new Vector2(1f, 1f);
            characterListRect.pivot = new Vector2(.5f, 1f);
            characterListRect.sizeDelta = new Vector2(0f, 150f);
            ScrollRect characterListScroll = characterListViewport.AddComponent<ScrollRect>();
            characterListScroll.viewport = characterListViewport.GetComponent<RectTransform>();
            characterListScroll.content = characterListRect;
            characterListScroll.horizontal = false;
            characterListScroll.vertical = true;
            characterListScroll.movementType = ScrollRect.MovementType.Clamped;
            characterListScroll.scrollSensitivity = 28f;
            characterListContent = characterList.transform;
            y -= 160f;
            AddSettingsHeading(characterSettings, "NEW CHARACTER", ref y);
            TMP_Text nameLabel = CreateText(characterSettings, "Display name", 18f, Ink, TextAlignmentOptions.MidlineLeft);
            PlaceTop(nameLabel.rectTransform, y, 30f, SettingsOuterMargin, SettingsLabelColumnEnd);
            newCharacterNameInput = CreateInputField(characterSettings);
            PlaceTop(newCharacterNameInput.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, 1f - SettingsOuterMargin);
            y -= 48f;
            TMP_Text personalityLabel = CreateText(characterSettings, "Personality", 18f, Ink, TextAlignmentOptions.MidlineLeft);
            PlaceTop(personalityLabel.rectTransform, y, 30f, SettingsOuterMargin, SettingsLabelColumnEnd);
            newCharacterPersonalityInput = CreateInputField(characterSettings);
            newCharacterPersonalityInput.lineType = TMP_InputField.LineType.MultiLineNewline;
            PlaceTop(newCharacterPersonalityInput.GetComponent<RectTransform>(), y, 88f, SettingsControlColumnStart, 1f - SettingsOuterMargin);
            y -= 98f;
            createCharacterButton = CreateButton(characterSettings, "Create Character", Accent);
            PlaceTop(createCharacterButton.GetComponent<RectTransform>(), y, StandardControlHeight);
            createCharacterButton.onClick.AddListener(CreateCharacterFromSettings);
            y -= 44f;
            characterCreationStatus = CreateText(characterSettings, string.Empty, 15f, theme.mutedText, TextAlignmentOptions.TopLeft);
            characterCreationStatus.richText = false; characterCreationStatus.enableWordWrapping = true;
            PlaceTop(characterCreationStatus.rectTransform, y, 66f);
            RefreshCharacterCreationUi();

            Transform context = settingsTabContent["Context"]; y = -18f;
            AddSettingsHeading(context, "CURRENT CONTEXT", ref y);
            TMP_Text contextHint = CreateText(context,
                "A compact view of backend-owned current continuity. These controls preserve conversation history.",
                15f, theme.mutedText, TextAlignmentOptions.MidlineLeft);
            PlaceTop(contextHint.rectTransform, y, 50f); contextHint.enableWordWrapping = true; y -= 58f;
            continuityScopeValue = AddSettingsValue(context, "Truth scope", ref y);
            leaveContinuityScenarioButton = CreateButton(context, "Return to Real World", Panel);
            PlaceTop(leaveContinuityScenarioButton.GetComponent<RectTransform>(), y, StandardControlHeight);
            leaveContinuityScenarioButton.onClick.AddListener(() => RequestContinuityControl("leave_scenario")); y -= 52f;
            continuityActivityValue = AddSettingsValue(context, "User activity", ref y);
            clearContinuityActivityButton = CreateButton(context, "Clear Current Activity", Panel);
            PlaceTop(clearContinuityActivityButton.GetComponent<RectTransform>(), y, StandardControlHeight);
            clearContinuityActivityButton.onClick.AddListener(() => RequestContinuityControl("clear_activity")); y -= 62f;
            continuityCompanionActivityValue = AddSettingsValue(context, "Companion activity", ref y);
            sceneOverlayToggle = CreateToggle(context, "Show compact scene overlay", showSceneOverlay);
            PlaceTop(sceneOverlayToggle.GetComponent<RectTransform>(), y, 34f);
            sceneOverlayToggle.onValueChanged.AddListener(SetSceneOverlayVisible);
            y -= 42f;
            continuitySceneValue = AddSceneDetailsView(context, ref y, 230f);
            proactiveIntervalButton = CreateButton(context, "Proactive: 1 h", Panel);
            PlaceTop(proactiveIntervalButton.GetComponent<RectTransform>(), y, 38f);
            proactiveIntervalButton.onClick.AddListener(CycleProactiveInterval); y -= 48f;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            proactiveEligibilityValue = AddSettingsValue(context, "Proactive eligibility", ref y);
#endif
            AddSettingsHeading(context, "OPEN THREADS", ref y);
            GameObject threadArea = new GameObject("Continuity Threads", typeof(RectTransform));
            threadArea.transform.SetParent(context, false);
            RectTransform threadRect = threadArea.GetComponent<RectTransform>();
            PlaceTop(threadRect, y, 420f);
            continuityThreadsContent = threadArea.transform;
            ((RectTransform)context).sizeDelta = new Vector2(0f, 1240f);
            RefreshProactiveBehaviorUi();
            RefreshContinuityPanel();

            Transform models = settingsTabContent["Models"]; y = -18f;
            AddSettingsHeading(models, "MODEL", ref y);
            geminiProviderStatus = AddSettingsValue(models, "Provider", ref y);
            geminiModelValue = AddSettingsValue(models, "Current model", ref y);
            TMP_Text modeLabel = CreateText(models, "Mode", 18f, Ink, TextAlignmentOptions.MidlineLeft); PlaceTop(modeLabel.rectTransform, y, 34f, SettingsOuterMargin, SettingsLabelColumnEnd);
            onlineModelModeButton = CreateButton(models, "Online", Panel); PlaceTop(onlineModelModeButton.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, .65f); onlineModelModeButton.onClick.AddListener(() => SelectModelMode("online"));
            localModelModeButton = CreateButton(models, "Local", Panel); PlaceTop(localModelModeButton.GetComponent<RectTransform>(), y, 38f, .65f, .96f); localModelModeButton.onClick.AddListener(() => SelectModelMode("local")); y -= 48f;
            TMP_Text keyLabel = CreateText(models, "Model API Key", 18f, Ink, TextAlignmentOptions.MidlineLeft); PlaceTop(keyLabel.rectTransform, y, 34f, SettingsOuterMargin, SettingsLabelColumnEnd);
            geminiApiKeyInput = CreateInputField(models); geminiApiKeyInput.contentType = TMP_InputField.ContentType.Password; geminiApiKeyInput.characterLimit = 512; PlaceTop(geminiApiKeyInput.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, .77f);
            if (geminiApiKeyInput.placeholder is TMP_Text keyPlaceholder)
            {
                keyPlaceholder.text = "Online provider key";
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
            Button saveKeyButton = CreateButton(models, "Save", Accent); PlaceTop(saveKeyButton.GetComponent<RectTransform>(), y, 38f, .88f, .96f); saveKeyButton.onClick.AddListener(SaveOnlineModelSettings); y -= 52f;
            onlineModelControls.Add(keyLabel.gameObject); onlineModelControls.Add(geminiApiKeyInput.gameObject); onlineModelControls.Add(showKeyButton.gameObject); onlineModelControls.Add(saveKeyButton.gameObject);
            TMP_Text localModelHeading = CreateText(models, "LOCAL MODEL", 16f, Accent, TextAlignmentOptions.MidlineLeft); PlaceTop(localModelHeading.rectTransform, y, 30f); y -= 38f;
            localSelectedModelValue = AddSettingsValue(models, "Selected model", ref y);
            localRuntimeStatusValue = AddSettingsValue(models, "Local model status", ref y);
            localComputeValue = AddSettingsValue(models, "Compute", ref y);
            localContextCapacityValue = AddSettingsValue(models, "Context capacity", ref y);
            localModelSelectionValue = AddSettingsChoice(models, "Model", ref y, OpenLocalModelPicker);
            startLocalModelButton = CreateButton(models, "Start Local Model", Accent); PlaceTop(startLocalModelButton.GetComponent<RectTransform>(), y, StandardControlHeight); startLocalModelButton.onClick.AddListener(StartLocalModel); y -= 52f;
            stopLocalModelButton = CreateButton(models, "Stop Local Model", Panel); PlaceTop(stopLocalModelButton.GetComponent<RectTransform>(), y, StandardControlHeight); stopLocalModelButton.onClick.AddListener(StopLocalModel); y -= 52f;
            Button discoverLocalButton = CreateButton(models, "Discover / Refresh Models", Panel); PlaceTop(discoverLocalButton.GetComponent<RectTransform>(), y, StandardControlHeight); discoverLocalButton.onClick.AddListener(DiscoverLocalModels); y -= 52f;
            localAutoStartToggle = CreateToggle(models, "Start local model with AIFren", false); PlaceTop(localAutoStartToggle.GetComponent<RectTransform>(), y, 34f); localAutoStartToggle.onValueChanged.AddListener(SetLocalAutoStart); y -= 48f;
            TMP_Text endpointLabel = CreateText(models, "Advanced endpoint", 18f, Ink, TextAlignmentOptions.MidlineLeft); PlaceTop(endpointLabel.rectTransform, y, 34f, SettingsOuterMargin, SettingsLabelColumnEnd);
            localEndpointInput = CreateInputField(models); localEndpointInput.text = "http://127.0.0.1:8000/v1"; PlaceTop(localEndpointInput.GetComponent<RectTransform>(), y, 38f, SettingsControlColumnStart, .96f); y -= 48f;
            Button applyLocalButton = CreateButton(models, "Apply endpoint", Panel); PlaceTop(applyLocalButton.GetComponent<RectTransform>(), y, StandardControlHeight); applyLocalButton.onClick.AddListener(SaveLocalModelSettings); y -= 52f;
            localModelControls.Add(localModelHeading.gameObject); localModelControls.Add(SettingsValueLabel(localSelectedModelValue)); localModelControls.Add(localSelectedModelValue.gameObject); localModelControls.Add(SettingsValueLabel(localRuntimeStatusValue)); localModelControls.Add(localRuntimeStatusValue.gameObject); localModelControls.Add(SettingsValueLabel(localComputeValue)); localModelControls.Add(localComputeValue.gameObject); localModelControls.Add(SettingsValueLabel(localContextCapacityValue)); localModelControls.Add(localContextCapacityValue.gameObject); localModelControls.Add(localModelSelectionValue.transform.parent.gameObject); localModelControls.Add(startLocalModelButton.gameObject); localModelControls.Add(stopLocalModelButton.gameObject); localModelControls.Add(discoverLocalButton.gameObject); localModelControls.Add(localAutoStartToggle.gameObject); localModelControls.Add(endpointLabel.gameObject); localModelControls.Add(localEndpointInput.gameObject); localModelControls.Add(applyLocalButton.gameObject);
            Button clearKeyButton = CreateButton(models, "Clear saved API key", Panel); PlaceTop(clearKeyButton.GetComponent<RectTransform>(), y, StandardControlHeight); clearKeyButton.onClick.AddListener(ClearGeminiApiKey); onlineModelControls.Add(clearKeyButton.gameObject);
            y -= 62f;
            AddSettingsHeading(models, "TEXT TO SPEECH", ref y);
            ttsProviderValue = AddSettingsValue(models, "Provider", ref y);
            ttsVoiceValue = AddSettingsValue(models, "Voice", ref y);
            ttsDeviceValue = AddSettingsValue(models, "Device", ref y);
            ((RectTransform)models).sizeDelta = new Vector2(0f, 1160f);

            Transform audio = settingsTabContent["Audio"]; y = -18f;
            AddSettingsHeading(audio, "SPEECH", ref y); volumeLabel = AddSettingsValue(audio, "TTS volume", ref y); volumeSlider = CreateSlider(audio, 0f, 1f, 1f); PlaceTop(volumeSlider.GetComponent<RectTransform>(), y, 30f); volumeSlider.onValueChanged.AddListener(SetVolume); AddPointerUpHandler(volumeSlider.gameObject, FlushTtsVolume); y -= 46f;
            AddResponsiveSpeechControls(audio, ref y);
            earlySpeechToggle = CreateToggle(audio, "Legacy generation streaming", true); PlaceTop(earlySpeechToggle.GetComponent<RectTransform>(), y, 34f); earlySpeechToggle.onValueChanged.AddListener(SetEarlySpeech); y -= 42f;
            Button stopSpeechButton = CreateButton(audio, "Stop speaking", Panel); PlaceTop(stopSpeechButton.GetComponent<RectTransform>(), y, StandardControlHeight); stopSpeechButton.onClick.AddListener(StopSpeech); y -= 58f;
            AddSettingsHeading(audio, "PRESENTATION AUDIO", ref y); sfxMuteToggle = CreateToggle(audio, "Mute UI SFX", presentationAudio == null || presentationAudio.SfxMuted); PlaceTop(sfxMuteToggle.GetComponent<RectTransform>(), y, 34f); sfxMuteToggle.onValueChanged.AddListener(value => presentationAudio?.SetSfxMuted(value)); y -= 42f;
            sfxVolumeSlider = CreateSlider(audio, 0f, 1f, presentationAudio == null ? .45f : presentationAudio.SfxVolume); PlaceTop(sfxVolumeSlider.GetComponent<RectTransform>(), y, 30f); sfxVolumeSlider.onValueChanged.AddListener(value => presentationAudio?.SetSfxVolume(value)); y -= 46f;
            bgmMuteToggle = CreateToggle(audio, "Mute background music", presentationAudio == null || presentationAudio.BgmMuted); PlaceTop(bgmMuteToggle.GetComponent<RectTransform>(), y, 34f); bgmMuteToggle.onValueChanged.AddListener(value => presentationAudio?.SetBgmMuted(value)); y -= 42f;
            bgmVolumeSlider = CreateSlider(audio, 0f, .35f, presentationAudio == null ? .14f : presentationAudio.BgmVolume); PlaceTop(bgmVolumeSlider.GetComponent<RectTransform>(), y, 30f); bgmVolumeSlider.onValueChanged.AddListener(value => presentationAudio?.SetBgmVolume(value));
            ((RectTransform)audio).sizeDelta = new Vector2(0f, Mathf.Max(900f, -y + 66f));

            Transform dialogue = settingsTabContent["Dialogue"]; y = -18f;
            AddConversationStyleControls(dialogue, ref y);
            AddSettingsHeading(dialogue, "DIALOGUE", ref y); revealSpeedLabel = AddSettingsValue(dialogue, "Reveal speed", ref y); revealSlider = CreateSlider(dialogue, 2f, 16f, revealWordsPerSecond); PlaceTop(revealSlider.GetComponent<RectTransform>(), y, 30f); revealSlider.onValueChanged.AddListener(SetRevealSpeed); y -= 46f;
            instantTextToggle = CreateToggle(dialogue, "Instant assistant text", instantText); PlaceTop(instantTextToggle.GetComponent<RectTransform>(), y, 34f); instantTextToggle.onValueChanged.AddListener(SetInstantText);
            y -= 52f;
            AddSubtitleColorControls(dialogue, ref y);
            ((RectTransform)dialogue).sizeDelta = new Vector2(0f, Mathf.Max(900f, -y + 24f));

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
            Button resetModelButton = CreateButton(appearance, "Reset to Default", Panel); PlaceTop(resetModelButton.GetComponent<RectTransform>(), y, StandardControlHeight, .76f, .95f); resetModelButton.onClick.AddListener(() => RequestBundledAvatarModel()); y -= 52f;
            AddAvatarCueControls(appearance, ref y);
            AddAutomaticExpressionControls(appearance, ref y);
            AddSettingsHeading(appearance, "AVATAR LIGHTING", ref y);
            avatarLightingValue = AddSettingsValue(appearance, "Brightness", ref y);
            avatarLightingSlider = CreateSlider(appearance, 0f, 2f, avatarLightingMultiplier);
            PlaceTop(avatarLightingSlider.GetComponent<RectTransform>(), y, 30f);
            avatarLightingSlider.onValueChanged.AddListener(SetAvatarLighting);
            RefreshAvatarLightingLabel();
            y -= 52f;
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
            ((RectTransform)appearance).sizeDelta = new Vector2(0f, Mathf.Max(900f, -y + 24f));
            CreateBackgroundLibraryPanel(panel.transform);
            CreateModelLibraryPanel(panel.transform);
            CreateMemoryViewerPanel(panel.transform);
            Transform advanced = settingsTabContent["Advanced"]; y = -18f;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (System.IO.File.Exists(System.IO.Path.Combine(Application.streamingAssetsPath, "OriginalMotionTrials/AcknowledgmentNod.vrma")))
            {
            AddSettingsHeading(advanced, "ORIGINAL MOTION TRIAL", ref y);
            Button motionTrial = CreateButton(advanced, "Nod: established / original trial", Panel);
            PlaceTop(motionTrial.GetComponent<RectTransform>(), y, StandardControlHeight);
            motionTrial.onClick.AddListener(() => {
                var player = avatarLoader?.GetComponent<AvatarVrmaGesturePlayer>();
                if (player == null) return;
                player.SelectOriginalNodTrial(!player.OriginalNodTrialSelected);
                SetTopControlLabel(motionTrial, player.OriginalNodTrialSelected ? "Nod: original trial (session only)" : "Nod: established");
            });
            y -= 58;
            }
#endif
            AddSettingsHeading(advanced, "MEMORY", ref y);
            TMP_Text memoryHint = CreateText(advanced,
                "Inspect Memory V2 facts, history, current state and corrections. V1 is compatibility-only.",
                15f, theme.mutedText, TextAlignmentOptions.TopLeft);
            PlaceTop(memoryHint.rectTransform, y, 54f); memoryHint.enableWordWrapping = true; y -= 62f;
            Button memoryViewerButton = CreateButton(advanced, "Open Memory Viewer / Editor", Accent);
            PlaceTop(memoryViewerButton.GetComponent<RectTransform>(), y, StandardControlHeight);
            memoryViewerButton.onClick.AddListener(OpenMemoryViewer); y -= 58f;
            AddSettingsHeading(advanced, "GRAPHICS", ref y);
            graphicsQualityValue = AddSettingsChoice(advanced, "Quality preset", ref y, CycleGraphicsQuality);
            avatarRenderScaleValue = AddSettingsChoice(advanced, "Avatar render scale", ref y, CycleAvatarRenderScale);
            antiAliasingValue = AddSettingsChoice(advanced, "Anti-aliasing", ref y, CycleAntiAliasing);
            SetPttAutoSend(pttAutoSend);
            SetRevealSpeed(revealWordsPerSecond);
            RefreshModelModeControls();
            RefreshDisplaySettingsUi();
            SelectSettingsTab(activeSettingsTab);
            return panel;
        }

        private void RefreshCharacterSettings()
        {
            if (currentCharacterValue != null)
            {
                currentCharacterValue.text = string.IsNullOrWhiteSpace(characterName) ? "Loading…" : characterName;
            }
            RefreshCharacterCreationUi();
            if (characterListContent == null) return;
            for (int index = characterListContent.childCount - 1; index >= 0; index--)
            {
                Destroy(characterListContent.GetChild(index).gameObject);
            }
            for (int index = 0; index < availableCharacters.Count; index++)
            {
                CharacterSummary summary = availableCharacters[index];
                bool duplicateName = availableCharacters.Count(item => item.display_name == summary.display_name) > 1;
                string label = (summary.is_active ? "Current · " : "Switch to · ") + summary.display_name +
                    (duplicateName ? " · " + ShortCharacterId(summary.character_id) : string.Empty);
                Button button = CreateButton(characterListContent, label, summary.is_active ? Accent : Panel);
                PlaceTop(button.GetComponent<RectTransform>(), -index * 42f, 34f, SettingsOuterMargin, .73f);
                button.GetComponentInChildren<TMP_Text>().richText = false;
                button.interactable = !summary.is_active && !characterSwitchInFlight && !characterCreateInFlight && !CharacterMaintenanceBusy &&
                    summary.storage_status != "creating" && summary.storage_status != "incomplete";
                string selectedId = summary.character_id;
                button.onClick.AddListener(() => RequestCharacterSwitch(selectedId));
                Button manage = CreateButton(characterListContent, "Manage", Panel);
                PlaceTop(manage.GetComponent<RectTransform>(), -index * 42f, 34f, .76f, 1f - SettingsOuterMargin);
                manage.interactable = !characterSwitchInFlight && !characterCreateInFlight && !CharacterMaintenanceBusy;
                manage.onClick.AddListener(() => OpenCharacterManager(summary));
            }
            RectTransform listRect = characterListContent as RectTransform;
            if (listRect != null)
            {
                listRect.sizeDelta = new Vector2(0f, Mathf.Max(150f, availableCharacters.Count * 42f));
            }
        }

        private void RequestCharacterSwitch(string characterId)
        {
            if (client == null || string.IsNullOrWhiteSpace(characterId) || characterSwitchInFlight || characterCreateInFlight || CharacterMaintenanceBusy) return;
            characterSwitchInFlight = true;
            ClearOutgoingViews();
            RefreshCharacterSettings();
            ApplyStatus("thinking", "Switching character…");
            _ = client.SelectCharacterAsync(characterId);
        }

        private void CreateCharacterFromSettings()
        {
            if (client == null || newCharacterNameInput == null || characterCreateInFlight || CharacterMaintenanceBusy) return;
            string displayName = newCharacterNameInput.text == null ? string.Empty : newCharacterNameInput.text.Trim();
            if (string.IsNullOrWhiteSpace(displayName))
            {
                ApplyStatus("error", "A character display name is required.");
                return;
            }
            string personality = newCharacterPersonalityInput != null ? newCharacterPersonalityInput.text : string.Empty;
            if (string.IsNullOrWhiteSpace(personality))
            {
                ApplyStatus("error", "A personality prompt is required.");
                return;
            }
            if (client.State != ConnectionState.Connected)
            {
                SetCharacterCreationStatus("Connect to the backend to create this character. Your form is kept.");
                return;
            }
            SubmitCharacterCreation(displayName, personality);
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
            if (tab == "Context" && settingsPanel != null && settingsPanel.activeSelf)
                RefreshContinuityPanel();
        }

        private void RefreshGeminiModelUi(ModelSettingsSnapshot model, LocalModelRuntimeSnapshot runtime = null)
        {
            if (geminiProviderStatus == null || geminiModelValue == null) return;
            if (model == null)
            {
                geminiProviderStatus.text = "Unavailable";
                geminiModelValue.text = "—";
                return;
            }
            string mode = NormalizeModelMode(model.mode);
            // A delayed pre-save snapshot must not undo a visible mode choice.
            if (!string.IsNullOrEmpty(pendingModelMode) && mode != pendingModelMode) return;

            authoritativeModelSettings = model;
            localModelRuntime = runtime;
            authoritativeModelMode = mode;
            selectedModelMode = mode;
            if (mode == pendingModelMode) pendingModelMode = null;

            string availability = string.IsNullOrWhiteSpace(model.availability) ? "configured" : model.availability;
            if (!model.configured || availability == "unconfigured")
            {
                geminiProviderStatus.text = "Not configured";
                geminiModelValue.text = "—";
            }
            else if (mode == "local")
            {
                string runtimeState = runtime != null ? runtime.state : "off";
                bool ready = string.Equals(runtimeState, "ready", StringComparison.OrdinalIgnoreCase);
                bool external = runtime != null && string.Equals(runtime.ownership, "external", StringComparison.OrdinalIgnoreCase);
                bool mismatch = string.Equals(runtimeState, "mismatch", StringComparison.OrdinalIgnoreCase);
                geminiProviderStatus.text = ready ? (external ? "Local (external)" : "Local") : mismatch ? "Local model mismatch" :
                    (runtimeState == "error" || availability == "unavailable" ? "Local unavailable" : "Local");
                geminiModelValue.text = (ready || mismatch) && runtime != null && !string.IsNullOrWhiteSpace(runtime.active_model)
                    ? runtime.active_model : "—";
            }
            else
            {
                string provider = string.IsNullOrWhiteSpace(model.provider) ? "Online" : model.provider.Replace("_", " ");
                geminiProviderStatus.text = availability == "unavailable" ? "Online unavailable" : "Online · " + provider;
                geminiModelValue.text = string.IsNullOrWhiteSpace(model.model) ? "—" : model.model;
            }
            if (localEndpointInput != null && !string.IsNullOrWhiteSpace(model.endpoint) && mode == "local") localEndpointInput.text = model.endpoint;
            SetLocalModelOptions(runtime != null ? runtime.installed_models : null);
            if (pendingLocalAutoStart.HasValue && pendingLocalAutoStart.Value == model.local_auto_start)
                pendingLocalAutoStart = null;
            if (localAutoStartToggle != null && !pendingLocalAutoStart.HasValue)
                localAutoStartToggle.SetIsOnWithoutNotify(model.local_auto_start);
            // Mode visibility first; runtime ownership then decides whether
            // Start or Stop is actually available. This prevents both buttons
            // reappearing when a snapshot refreshes the Local page.
            RefreshModelModeControls();
            RefreshLocalRuntimeUi();
        }

        public static string NormalizeModelMode(string mode)
        {
            return string.Equals(mode, "local", StringComparison.OrdinalIgnoreCase) ? "local" : "online";
        }

        private void SelectModelMode(string mode)
        {
            string selected = NormalizeModelMode(mode);
            if (selected == selectedModelMode && string.IsNullOrEmpty(pendingModelMode)) return;
            if (client == null || client.State != ConnectionState.Connected)
            {
                ApplyStatus("error", "Connect to the backend before changing Model mode.");
                return;
            }
            selectedModelMode = selected;
            pendingModelMode = selected;
            RefreshModelModeControls();
            ApplyStatus("thinking", "Saving " + (selected == "local" ? "Local" : "Online") + " model mode...");
            _ = client.SetModelModeAsync(selected);
        }

        private void RestoreAuthoritativeModelSettingsAfterFailure()
        {
            bool restoreModel = !string.IsNullOrEmpty(pendingModelMode) || pendingLocalAutoStart.HasValue;
            if (restoreModel)
            {
                ClearPendingModelSettings();
                selectedModelMode = authoritativeModelMode;
                if (authoritativeModelSettings != null) RefreshGeminiModelUi(authoritativeModelSettings, localModelRuntime);
                else RefreshModelModeControls();
            }
            if (pendingEarlySpeech.HasValue)
            {
                pendingEarlySpeech = null;
                if (earlySpeechToggle != null)
                    earlySpeechToggle.SetIsOnWithoutNotify(authoritativeEarlySpeech);
            }
        }

        private void ClearPendingModelSettings()
        {
            pendingModelMode = null;
            pendingLocalAutoStart = null;
        }

        private void RefreshModelModeControls()
        {
            bool local = selectedModelMode == "local";
            foreach (GameObject control in onlineModelControls) if (control != null) control.SetActive(!local);
            foreach (GameObject control in localModelControls) if (control != null) control.SetActive(local);
            SetModelModeButtonAppearance(onlineModelModeButton, !local);
            SetModelModeButtonAppearance(localModelModeButton, local);
        }

        private void SetModelModeButtonAppearance(Button button, bool selected)
        {
            if (button == null) return;
            Image image = button.GetComponent<Image>();
            if (image != null) image.color = selected ? Accent : Panel;
            TMP_Text label = button.GetComponentInChildren<TMP_Text>();
            if (label != null) label.color = selected ? Color.white : Ink;
        }

        private void RefreshTtsModelUi(TtsSnapshot tts)
        {
            if (tts == null) return;
            authoritativeEarlySpeech = tts.early_speech_configured;
            if (pendingEarlySpeech.HasValue && pendingEarlySpeech.Value == tts.early_speech_configured)
                pendingEarlySpeech = null;
            if (earlySpeechToggle != null)
            {
                // The normal committed-reply control owns V2 speech. Show the
                // compatibility setting only when this provider can use it.
                earlySpeechToggle.gameObject.SetActive(tts.early_speech_supported || tts.early_speech_overridden);
                if (!pendingEarlySpeech.HasValue)
                    earlySpeechToggle.SetIsOnWithoutNotify(tts.early_speech_overridden
                        ? tts.early_speech : tts.early_speech_configured);
                earlySpeechToggle.interactable = tts.early_speech_supported && !tts.early_speech_overridden;
                TMP_Text label = earlySpeechToggle.GetComponentInChildren<TMP_Text>();
                if (label != null)
                    label.text = tts.early_speech_overridden
                        ? "Legacy generation streaming (developer override)"
                        : "Legacy generation streaming";
            }
            if (ttsProviderValue == null) return;
            ttsProviderValue.text = string.IsNullOrWhiteSpace(tts.provider) ? "Unavailable" : tts.provider +
                (string.IsNullOrWhiteSpace(tts.fallback_reason) ? string.Empty : " (Audio8 unavailable)");
            ttsVoiceValue.text = string.IsNullOrWhiteSpace(tts.voice) ? "Default" : tts.voice;
            ttsDeviceValue.text = string.IsNullOrWhiteSpace(tts.device) ? "Automatic" : tts.device;
        }

        private void SetEarlySpeech(bool enabled)
        {
            if (client == null || client.State != ConnectionState.Connected)
            {
                if (earlySpeechToggle != null)
                    earlySpeechToggle.SetIsOnWithoutNotify(authoritativeEarlySpeech);
                ApplyStatus("error", "Connect to the backend before changing Early speech.");
                return;
            }
            pendingEarlySpeech = enabled;
            ApplyStatus("thinking", "Saving Early speech setting...");
            _ = client.SetKokoroEarlySpeechAsync(enabled);
        }

        private void CycleProactiveInterval()
        {
            if (client == null || client.State != ConnectionState.Connected)
            {
                RefreshProactiveBehaviorUi();
                ApplyStatus("error", "Connect to the backend before changing proactive behavior.");
                return;
            }
            int index = Array.IndexOf(ProactiveIntervals, authoritativeProactiveIntervalSeconds);
            int selected = ProactiveIntervals[(index < 0 ? 0 : index + 1) % ProactiveIntervals.Length];
            ApplyStatus("thinking", "Saving proactive behavior setting...");
            _ = client.SetProactiveIntervalAsync(selected);
        }

        private static string ProactiveIntervalLabel(int seconds)
        {
            if (seconds <= 0) return "Off";
            if (seconds < 60) return seconds + " sec";
            if (seconds < 3600) return (seconds / 60) + " min";
            return (seconds / 3600) + " h";
        }

        private void RefreshProactiveBehaviorUi()
        {
            if (proactiveIntervalButton != null)
                SetTopControlLabel(proactiveIntervalButton, "Proactive: " + ProactiveIntervalLabel(authoritativeProactiveIntervalSeconds));
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (proactiveEligibilityValue != null)
                proactiveEligibilityValue.text = ContinuityPanelState.ProactiveEligibilityText(
                    latestProactiveEligibility, latestProactiveNextSeconds, latestProactiveIgnoredStreak);
#endif
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

        private void SaveOnlineModelSettings()
        {
            if (client == null || geminiApiKeyInput == null) return;
            selectedModelMode = "online";
            pendingModelMode = "online";
            RefreshModelModeControls();
            _ = client.SetOnlineModelSettingsAsync(geminiApiKeyInput.text);
            geminiApiKeyInput.text = string.Empty;
            ApplyStatus("ready", "Saving model configuration...");
        }

        private void ClearGeminiApiKey()
        {
            if (client == null) return;
            selectedModelMode = "online";
            pendingModelMode = "online";
            RefreshModelModeControls();
            _ = client.SetOnlineModelSettingsAsync(string.Empty);
            ApplyStatus("ready", "Clearing saved model key...");
        }

        private void SaveLocalModelSettings()
        {
            if (client == null || localEndpointInput == null) return;
            selectedModelMode = "local";
            pendingModelMode = "local";
            RefreshModelModeControls();
            _ = client.SetLocalModelSettingsAsync(localEndpointInput.text,
                authoritativeModelSettings != null ? authoritativeModelSettings.selected_model : string.Empty);
            ApplyStatus("ready", "Saving local model settings...");
        }

        private void DiscoverLocalModels()
        {
            if (client == null || localEndpointInput == null) return;
            _ = client.DiscoverLocalModelsAsync(localEndpointInput.text);
            ApplyStatus("thinking", "Discovering local models...");
        }

        private void SetLocalModelOptions(LocalModelOption[] options)
        {
            if (options != null)
            {
                localModelOptions.Clear();
                localModelOptions.AddRange(options);
            }
            if (localModelSelectionValue == null) return;
            string selected = authoritativeModelSettings != null ? authoritativeModelSettings.selected_model : string.Empty;
            LocalModelOption option = localModelOptions.Find(item => item != null && item.identifier == selected);
            localModelSelectionValue.text = option != null ? option.display_name + "  ▼" :
                (localModelOptions.Count == 0 ? "No installed models — Refresh" : "Select a model  ▼");
        }

        private void OpenLocalModelPicker()
        {
            if (localModelOptions.Count == 0)
            {
                ApplyStatus("error", "No managed local models found. Refresh Models first.");
                return;
            }
            if (localModelPickerPanel == null)
            {
                localModelPickerPanel = CreatePanel(settingsPanel.transform, "Local Model Picker", new Color(.10f, .08f, .16f, .99f));
                Stretch(localModelPickerPanel.GetComponent<RectTransform>(), new Vector2(.18f, .25f), new Vector2(.82f, .75f), Vector2.zero, Vector2.zero);
                TMP_Text title = CreateText(localModelPickerPanel.transform, "Select Local Model", 22f, Ink, TextAlignmentOptions.Center);
                Stretch(title.rectTransform, new Vector2(.08f, .84f), new Vector2(.92f, .96f), Vector2.zero, Vector2.zero);
            }
            for (int index = localModelPickerPanel.transform.childCount - 1; index >= 0; index--)
            {
                Transform child = localModelPickerPanel.transform.GetChild(index);
                if (child.gameObject.name != "Text") Destroy(child.gameObject);
            }
            float y = -68f;
            foreach (LocalModelOption option in localModelOptions)
            {
                Button choice = CreateButton(localModelPickerPanel.transform, option.display_name, Panel);
                PlaceTop(choice.GetComponent<RectTransform>(), y, 38f, .09f, .91f);
                LocalModelOption captured = option;
                choice.onClick.AddListener(() => SelectLocalModel(captured));
                y -= 46f;
            }
            Button cancel = CreateButton(localModelPickerPanel.transform, "Cancel", Panel);
            Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.30f, .07f), new Vector2(.70f, .18f), Vector2.zero, Vector2.zero);
            cancel.onClick.AddListener(() => localModelPickerPanel.SetActive(false));
            localModelPickerPanel.SetActive(true);
            localModelPickerPanel.transform.SetAsLastSibling();
        }

        private void SelectLocalModel(LocalModelOption selected)
        {
            if (client == null || selected == null) return;
            if (localModelPickerPanel != null) localModelPickerPanel.SetActive(false);
            if (authoritativeModelSettings != null) authoritativeModelSettings.selected_model = selected.identifier;
            SetLocalModelOptions(null);
            ApplyStatus("thinking", "Selecting local model...");
            _ = client.SetLocalModelSettingsAsync(localEndpointInput != null ? localEndpointInput.text : string.Empty, selected.identifier);
        }

        private void StartLocalModel()
        {
            if (client == null) return;
            ApplyStatus("thinking", "Starting local model...");
            _ = client.StartLocalModelAsync();
        }

        private void StopLocalModel()
        {
            if (client == null) return;
            ApplyStatus("thinking", "Stopping local model...");
            _ = client.StopLocalModelAsync();
        }

        private void SetLocalAutoStart(bool enabled)
        {
            if (client == null || client.State != ConnectionState.Connected)
            {
                if (localAutoStartToggle != null && authoritativeModelSettings != null)
                    localAutoStartToggle.SetIsOnWithoutNotify(authoritativeModelSettings.local_auto_start);
                ApplyStatus("error", "Connect to the backend before changing local model auto-start.");
                return;
            }
            pendingLocalAutoStart = enabled;
            ApplyStatus("thinking", "Saving local model auto-start...");
            _ = client.SetLocalAutoStartAsync(enabled);
        }

        private void RefreshLocalRuntimeUi()
        {
            if (localSelectedModelValue != null)
                localSelectedModelValue.text = authoritativeModelSettings != null && !string.IsNullOrWhiteSpace(authoritativeModelSettings.selected_model)
                    ? authoritativeModelSettings.selected_model : "—";
            if (localRuntimeStatusValue != null)
            {
                string state = localModelRuntime != null ? localModelRuntime.state : "off";
                string ownership = localModelRuntime != null ? localModelRuntime.ownership : "none";
                string detail = localModelRuntime != null ? localModelRuntime.error : string.Empty;
                localRuntimeStatusValue.text = state == "ready" ? (ownership == "external" ? "Ready (external)" : "Ready") :
                    (state == "starting" ? "Starting..." : state == "switching" ? "Switching..." :
                    (state == "mismatch" ? "Model mismatch" + (string.IsNullOrEmpty(detail) ? "" : ": " + detail) :
                    (state == "error" ? "Error" + (string.IsNullOrEmpty(detail) ? "" : ": " + detail) : "Off")));
            }
            if (localComputeValue != null) localComputeValue.text = localModelRuntime != null && !string.IsNullOrWhiteSpace(localModelRuntime.compute)
                ? localModelRuntime.compute : "—";
            if (localContextCapacityValue != null) localContextCapacityValue.text = localModelRuntime != null && localModelRuntime.effective_context_tokens > 0
                ? localModelRuntime.effective_context_tokens.ToString() + " tokens" : "—";
            string runtimeState = localModelRuntime != null ? localModelRuntime.state : "off";
            string runtimeOwnership = localModelRuntime != null ? localModelRuntime.ownership : "none";
            bool ownedReady = runtimeState == "ready" && runtimeOwnership == "managed";
            bool externalReady = runtimeState == "ready" && runtimeOwnership == "external";
            bool transition = runtimeState == "starting" || runtimeState == "switching";
            if (startLocalModelButton != null) startLocalModelButton.gameObject.SetActive(!ownedReady && !externalReady && !transition && runtimeState != "mismatch");
            if (stopLocalModelButton != null) stopLocalModelButton.gameObject.SetActive(ownedReady);
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

        private TMP_Text AddSettingsMultilineValue(Transform parent, string label, ref float y, float maximumHeight)
        {
            TMP_Text labelText = CreateText(parent, label, 18f, Ink, TextAlignmentOptions.TopLeft);
            PlaceTop(labelText.rectTransform, y, maximumHeight, SettingsOuterMargin, .34f);
            TMP_Text value = CreateText(parent, string.Empty, 16f, Ink, TextAlignmentOptions.TopRight);
            PlaceTop(value.rectTransform, y, maximumHeight, .36f, 1f - SettingsOuterMargin);
            value.enableWordWrapping = true;
            value.overflowMode = TextOverflowModes.Ellipsis;
            y -= maximumHeight + 8f;
            return value;
        }

        private TMP_Text AddSceneDetailsView(Transform parent, ref float y, float visibleHeight)
        {
            TMP_Text label = CreateText(parent, "Scene details", 18f, Ink, TextAlignmentOptions.MidlineLeft);
            PlaceTop(label.rectTransform, y, 30f, SettingsOuterMargin, 1f - SettingsOuterMargin);
            y -= 34f;
            GameObject frame = CreatePanel(parent, "Scene Details", new Color(0f, 0f, 0f, .18f));
            PlaceTop(frame.GetComponent<RectTransform>(), y, visibleHeight,
                SettingsOuterMargin, 1f - SettingsOuterMargin);
            GameObject viewport = new GameObject(
                "Scene Details Viewport", typeof(RectTransform), typeof(RectMask2D));
            viewport.transform.SetParent(frame.transform, false);
            Stretch(viewport.GetComponent<RectTransform>(), Vector2.zero, new Vector2(.965f, 1f),
                new Vector2(8f, 6f), new Vector2(-4f, -6f));
            TMP_Text value = CreateText(
                viewport.transform, string.Empty, 15f, Ink, TextAlignmentOptions.TopLeft);
            value.enableWordWrapping = true;
            value.overflowMode = TextOverflowModes.Overflow;
            value.lineSpacing = 3f;
            RectTransform content = value.rectTransform;
            content.anchorMin = new Vector2(0f, 1f);
            content.anchorMax = new Vector2(1f, 1f);
            content.pivot = new Vector2(.5f, 1f);
            content.anchoredPosition = Vector2.zero;
            content.sizeDelta = new Vector2(-12f, visibleHeight);
            continuitySceneScroll = frame.AddComponent<ScrollRect>();
            continuitySceneScroll.viewport = viewport.GetComponent<RectTransform>();
            continuitySceneScroll.content = content;
            continuitySceneScroll.horizontal = false;
            continuitySceneScroll.vertical = true;
            continuitySceneScroll.movementType = ScrollRect.MovementType.Clamped;
            continuitySceneScroll.scrollSensitivity = 28f;
            AddThinScrollbar(frame.transform, continuitySceneScroll, .971f, .986f);
            continuitySceneContent = content;
            y -= visibleHeight + 10f;
            return value;
        }

        private static GameObject SettingsValueLabel(TMP_Text value)
        {
            if (value == null || value.transform.parent == null) return null;
            int index = value.transform.GetSiblingIndex();
            return index > 0 ? value.transform.parent.GetChild(index - 1).gameObject : null;
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
            return ManagedAssetLibrary.DisplayName(record, "Imported avatar");
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
                if (Application.isPlaying) Destroy(child.gameObject);
                else DestroyImmediate(child.gameObject);
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

        private void CreateMemoryViewerPanel(Transform parent)
        {
            memoryViewerPanel = CreatePanel(parent, "Memory Viewer", new Color(.065f, .05f, .11f, .995f));
            Stretch(memoryViewerPanel.GetComponent<RectTransform>(), new Vector2(.035f, .035f),
                new Vector2(.965f, .965f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(memoryViewerPanel.transform, "Memory Viewer / Editor", 24f,
                Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.035f, .91f), new Vector2(.46f, .985f), Vector2.zero, Vector2.zero);
            Button close = CreateButton(memoryViewerPanel.transform, "Back to Advanced", Panel);
            Stretch(close.GetComponent<RectTransform>(), new Vector2(.78f, .92f), new Vector2(.965f, .98f), Vector2.zero, Vector2.zero);
            close.onClick.AddListener(() => memoryViewerPanel.SetActive(false));

            memoryViewerAuthority = CreateText(memoryViewerPanel.transform, string.Empty, 14f,
                theme.accent, TextAlignmentOptions.MidlineLeft);
            Stretch(memoryViewerAuthority.rectTransform, new Vector2(.035f, .865f),
                new Vector2(.965f, .915f), Vector2.zero, Vector2.zero);
            memoryViewerAuthority.enableWordWrapping = false;
            memoryViewerAuthority.overflowMode = TextOverflowModes.Ellipsis;

            memoryViewerLaneButton = CreateButton(memoryViewerPanel.transform, "V1 Memories", Panel);
            Stretch(memoryViewerLaneButton.GetComponent<RectTransform>(), new Vector2(.035f, .805f),
                new Vector2(.265f, .855f), Vector2.zero, Vector2.zero);
            memoryViewerLaneButton.onClick.AddListener(() =>
            {
                memoryViewerState.CycleLane();
                RequestMemoryViewerPage();
            });
            memoryViewerStatusButton = CreateButton(memoryViewerPanel.transform, "Status: Current", Panel);
            Stretch(memoryViewerStatusButton.GetComponent<RectTransform>(), new Vector2(.275f, .805f),
                new Vector2(.47f, .855f), Vector2.zero, Vector2.zero);
            memoryViewerStatusButton.onClick.AddListener(() =>
            {
                memoryViewerState.CycleStatus();
                RequestMemoryViewerPage();
            });
            memoryViewerScopeButton = CreateButton(memoryViewerPanel.transform, "Scope: Applicable", Panel);
            Stretch(memoryViewerScopeButton.GetComponent<RectTransform>(), new Vector2(.48f, .805f),
                new Vector2(.68f, .855f), Vector2.zero, Vector2.zero);
            memoryViewerScopeButton.onClick.AddListener(() =>
            {
                memoryViewerState.CycleScope();
                RequestMemoryViewerPage();
            });
            memoryViewerSearchInput = CreateMemoryInputField(memoryViewerPanel.transform);
            memoryViewerSearchInput.characterLimit = 160;
            Stretch(memoryViewerSearchInput.GetComponent<RectTransform>(), new Vector2(.69f, .805f),
                new Vector2(.875f, .855f), Vector2.zero, Vector2.zero);
            if (memoryViewerSearchInput.placeholder is TMP_Text searchPlaceholder)
                searchPlaceholder.text = "Search this lane";
            Button search = memoryViewerRefreshButton = CreateButton(memoryViewerPanel.transform, "Refresh", Accent);
            memoryViewerSearchInput.onValueChanged.AddListener(_ => UpdateMemoryViewStatus());
            Stretch(search.GetComponent<RectTransform>(), new Vector2(.885f, .805f),
                new Vector2(.965f, .855f), Vector2.zero, Vector2.zero);
            search.onClick.AddListener(() =>
            {
                string query = memoryViewerSearchInput.text ?? string.Empty;
                if (query != memoryViewerState.Query)
                { memoryViewerState.Query = query; memoryViewerState.InvalidatePage(); }
                RequestMemoryViewerPage();
            });

            GameObject listFrame = CreatePanel(memoryViewerPanel.transform, "Memory Page", new Color(0f, 0f, 0f, .22f));
            Stretch(listFrame.GetComponent<RectTransform>(), new Vector2(.035f, .16f),
                new Vector2(.515f, .785f), Vector2.zero, Vector2.zero);
            GameObject listViewport = new GameObject("Memory Page Viewport", typeof(RectTransform), typeof(RectMask2D));
            listViewport.transform.SetParent(listFrame.transform, false);
            Stretch(listViewport.GetComponent<RectTransform>(), Vector2.zero, new Vector2(.965f, 1f),
                new Vector2(8f, 8f), new Vector2(-4f, -8f));
            GameObject listContent = new GameObject("Memory Page Rows", typeof(RectTransform));
            listContent.transform.SetParent(listViewport.transform, false);
            RectTransform listRect = listContent.GetComponent<RectTransform>();
            listRect.anchorMin = new Vector2(0f, 1f); listRect.anchorMax = new Vector2(1f, 1f);
            listRect.pivot = new Vector2(.5f, 1f); listRect.sizeDelta = new Vector2(0f, 40f);
            memoryViewerRows = listContent.transform;
            memoryViewerScroll = listFrame.AddComponent<ScrollRect>();
            memoryViewerScroll.viewport = listViewport.GetComponent<RectTransform>();
            memoryViewerScroll.content = listRect;
            memoryViewerScroll.horizontal = false;
            memoryViewerScroll.vertical = true;
            memoryViewerScroll.movementType = ScrollRect.MovementType.Clamped;
            memoryViewerScroll.scrollSensitivity = 30f;
            AddThinScrollbar(listFrame.transform, memoryViewerScroll, .971f, .986f);

            GameObject detailsFrame = CreatePanel(memoryViewerPanel.transform, "Memory Details", new Color(0f, 0f, 0f, .22f));
            Stretch(detailsFrame.GetComponent<RectTransform>(), new Vector2(.53f, .16f),
                new Vector2(.965f, .785f), Vector2.zero, Vector2.zero);
            GameObject detailsViewport = new GameObject(
                "Memory Detail Viewport", typeof(RectTransform), typeof(RectMask2D));
            detailsViewport.transform.SetParent(detailsFrame.transform, false);
            Stretch(detailsViewport.GetComponent<RectTransform>(), new Vector2(.035f, .51f),
                new Vector2(.965f, .97f), Vector2.zero, Vector2.zero);
            memoryViewerDetails = CreateText(detailsViewport.transform,
                "Select a memory to inspect its authority, scope, status, and provenance.",
                16f, Ink, TextAlignmentOptions.TopLeft);
            RectTransform detailTextRect = memoryViewerDetails.rectTransform;
            detailTextRect.anchorMin = new Vector2(0f, 1f);
            detailTextRect.anchorMax = new Vector2(1f, 1f);
            detailTextRect.pivot = new Vector2(.5f, 1f);
            detailTextRect.anchoredPosition = Vector2.zero;
            detailTextRect.sizeDelta = new Vector2(-8f, 120f);
            memoryViewerDetails.enableWordWrapping = true;
            memoryViewerDetails.richText = false;
            memoryViewerDetails.lineSpacing = 3f;
            memoryViewerDetails.overflowMode = TextOverflowModes.Overflow;
            ContentSizeFitter detailFitter = memoryViewerDetails.gameObject.AddComponent<ContentSizeFitter>();
            detailFitter.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;
            detailFitter.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            memoryViewerDetailsScroll = detailsViewport.AddComponent<ScrollRect>();
            memoryViewerDetailsScroll.viewport = detailsViewport.GetComponent<RectTransform>();
            memoryViewerDetailsScroll.content = detailTextRect;
            memoryViewerDetailsScroll.horizontal = false;
            memoryViewerDetailsScroll.vertical = true;
            memoryViewerDetailsScroll.movementType = ScrollRect.MovementType.Clamped;
            memoryViewerDetailsScroll.scrollSensitivity = 28;
            AddThinScrollbar(detailsViewport.transform, memoryViewerDetailsScroll, .975f, .995f);
            memoryViewerContentInput = CreateMemoryInputField(detailsFrame.transform, true);
            memoryViewerContentInput.lineType = TMP_InputField.LineType.MultiLineNewline;
            memoryViewerContentInput.characterLimit = 512;
            Stretch(memoryViewerContentInput.GetComponent<RectTransform>(), new Vector2(.035f, .34f),
                new Vector2(.965f, .49f), Vector2.zero, Vector2.zero);
            memoryViewerCategoryInput = CreateMemoryInputField(detailsFrame.transform);
            memoryViewerCategoryInput.characterLimit = 80;
            Stretch(memoryViewerCategoryInput.GetComponent<RectTransform>(), new Vector2(.035f, .255f),
                new Vector2(.69f, .32f), Vector2.zero, Vector2.zero);
            memoryViewerImportanceInput = CreateMemoryInputField(detailsFrame.transform);
            memoryViewerImportanceInput.contentType = TMP_InputField.ContentType.IntegerNumber;
            memoryViewerImportanceInput.characterLimit = 2;
            Stretch(memoryViewerImportanceInput.GetComponent<RectTransform>(), new Vector2(.715f, .255f),
                new Vector2(.965f, .32f), Vector2.zero, Vector2.zero);
            memoryViewerSaveButton = CreateButton(detailsFrame.transform, "Save correction", Accent);
            Stretch(memoryViewerSaveButton.GetComponent<RectTransform>(), new Vector2(.035f, .14f),
                new Vector2(.49f, .225f), Vector2.zero, Vector2.zero);
            memoryViewerSaveButton.onClick.AddListener(SaveSelectedMemory);
            memoryViewerRetireButton = CreateButton(detailsFrame.transform, "Retire", new Color(.42f, .16f, .22f, 1f));
            Stretch(memoryViewerRetireButton.GetComponent<RectTransform>(), new Vector2(.51f, .14f),
                new Vector2(.965f, .225f), Vector2.zero, Vector2.zero);
            memoryViewerRetireButton.onClick.AddListener(RetireSelectedMemory);

            memoryViewerPreviousButton = CreateButton(memoryViewerPanel.transform, "Previous", Panel);
            Stretch(memoryViewerPreviousButton.GetComponent<RectTransform>(), new Vector2(.035f, .075f),
                new Vector2(.175f, .135f), Vector2.zero, Vector2.zero);
            memoryViewerPreviousButton.onClick.AddListener(() =>
            {
                if (memoryViewerState.PreviousPage()) RequestMemoryViewerPage();
            });
            memoryViewerNextButton = CreateButton(memoryViewerPanel.transform, "Next", Panel);
            Stretch(memoryViewerNextButton.GetComponent<RectTransform>(), new Vector2(.185f, .075f),
                new Vector2(.325f, .135f), Vector2.zero, Vector2.zero);
            memoryViewerNextButton.onClick.AddListener(() =>
            {
                if (memoryViewerState.NextPage()) RequestMemoryViewerPage();
            });
            memoryViewerMoreDetailButton = CreateButton(memoryViewerPanel.transform, "More audit", Panel);
            Stretch(memoryViewerMoreDetailButton.GetComponent<RectTransform>(), new Vector2(.35f, .075f),
                new Vector2(.485f, .135f), Vector2.zero, Vector2.zero);
            memoryViewerMoreDetailButton.onClick.AddListener(() =>
            {
                MemoryViewItem selected = memoryViewerState.Selected;
                MemoryViewDetail detail = memoryViewerState.Detail;
                if (selected != null && detail != null && detail.has_more)
                    RequestMemoryViewerDetail(selected, detail.offset + Math.Max(1, detail.limit));
            });
            memoryViewerWarning = CreateText(memoryViewerPanel.transform, string.Empty, 14f,
                theme.mutedText, TextAlignmentOptions.MidlineLeft);
            Stretch(memoryViewerWarning.rectTransform, new Vector2(.505f, .07f),
                new Vector2(.965f, .14f), Vector2.zero, Vector2.zero);
            memoryViewerWarning.enableWordWrapping = true;

            RefreshMemoryViewerPage();
            memoryViewerPanel.SetActive(false);
        }

        private void OpenMemoryViewer()
        {
            if (memoryViewerPanel == null) return;
            memoryViewerState.ChangeCharacter(activeCharacterId);
            memoryViewerRetireConfirmation = false;
            memoryViewerPanel.SetActive(true);
            ApplyFocusedReadability();
            memoryViewerPanel.transform.SetAsLastSibling();
            RequestMemoryViewerPage();
        }

        private void RequestMemoryViewerPage()
        {
            if (client == null)
            { memoryViewerState.PageRequest.Observe(0, false); UpdateMemoryViewStatus(); return; }
            if (client.State != ConnectionState.Connected || characterSwitchInFlight
                || client.CharacterOwner == null || client.CharacterOwner.CharacterId != activeCharacterId)
            {
                memoryRefreshAfterResync = true;
                memoryViewerState.PageRequest.Reset(true);
                UpdateMemoryViewStatus();
                RequestHistoryRefresh();
                return;
            }
            memoryViewerState.ChangeCharacter(activeCharacterId);
            string requestId = memoryViewerState.BeginRequest(Time.realtimeSinceStartup, true);
            if (requestId == null) return;
            MarkView("memory_requested", requestId, 0);
            MarkView("memory_lane_" + memoryViewerState.Lane, requestId, memoryViewerState.Offset);
            MarkView("memory_status_" + memoryViewerState.StatusFilter, requestId, 0);
            MarkView("memory_scope_" + memoryViewerState.ScopeFilter, requestId, string.IsNullOrEmpty(memoryViewerState.Query) ? 0 : 1);
            // A filter/lane change clears the old rows immediately. Same-page refresh keeps safe drafts.
            if (memoryViewerState.Page == null) RefreshMemoryViewerPage();
            UpdateMemoryViewStatus();
            _ = client.QueryMemoryViewAsync(
                requestId, activeCharacterId, memoryViewerState.Lane,
                memoryViewerState.Query, memoryViewerState.StatusFilter,
                memoryViewerState.ScopeFilter, MemoryViewerState.PageSize,
                memoryViewerState.Offset);
            RefreshMemoryViewerControls();
        }

        private void RefreshMemoryViewerPage()
        {
            RefreshMemoryViewerControls();
            bool preserveSelection = memoryViewerState.Selected != null;
            float scrollPosition = memoryViewerScroll != null ? memoryViewerScroll.verticalNormalizedPosition : 1f;
            MemoryViewPage page = memoryViewerState.Page;
            if (memoryViewerAuthority != null)
            {
                string authority = page != null && !string.IsNullOrWhiteSpace(page.authority_label)
                    ? page.authority_label
                    : "Memory V2 is normal authority; V1 is explicit compatibility only.";
                memoryViewerAuthority.text = "Character: " + characterName + " · " + authority;
            }
            if (memoryViewerRows != null)
            {
                memoryRowButtons.Clear();
                memoryPageButtons.Clear();
                memoryMeasuredWidth = (memoryViewerRows as RectTransform).rect.width * .98f;
                for (int index = memoryViewerRows.childCount - 1; index >= 0; index--)
                    Destroy(memoryViewerRows.GetChild(index).gameObject);
                MemoryViewItem[] items = page != null && page.items != null
                    ? page.items : new MemoryViewItem[0];
                float rowTop = 0;
                for (int index = 0; index < items.Length; index++)
                {
                    MemoryViewItem selected = items[index];
                    Button row = CreateButton(memoryViewerRows, MemoryViewerState.ItemLabel(selected), Panel);
                    memoryPageButtons.Add(row);
                    float rowHeight = LayoutMemoryRow(row, memoryMeasuredWidth);
                    PlaceTop(row.GetComponent<RectTransform>(), -rowTop, rowHeight, .01f, .99f);
                    rowTop += rowHeight + 7;
                    if (!string.IsNullOrEmpty(selected.record_id)) memoryRowButtons[selected.record_id] = row;
                    CharacterSessionOwner rowOwner = client?.CharacterOwner;
                    row.onClick.AddListener(() => {
                        if (client != null && client.OwnsCharacter(rowOwner)) SelectMemoryViewerItem(selected);
                    });
                }
                RectTransform rowsRect = memoryViewerRows as RectTransform;
                if (rowsRect != null) rowsRect.sizeDelta = new Vector2(0f, Mathf.Max(40f, rowTop));
                if (memoryViewerScroll != null) memoryViewerScroll.verticalNormalizedPosition = preserveSelection ? scrollPosition : 1f;
            }
            if (!preserveSelection) SelectMemoryViewerItem(null);
            else RefreshMemorySelection();
            UpdateMemoryViewStatus();
            MarkView("memory_rendered", memoryModelRequestId, memoryPageButtons.Count);

        }

        private void RefreshMemoryViewerControls()
        {
            if (memoryViewerLaneButton != null) SetTopControlLabel(memoryViewerLaneButton, MemoryViewerState.LaneLabel(memoryViewerState.Lane));
            if (memoryViewerStatusButton != null) SetTopControlLabel(memoryViewerStatusButton,
                "Status: " + CultureInfo.InvariantCulture.TextInfo.ToTitleCase(memoryViewerState.StatusFilter));
            if (memoryViewerScopeButton != null) SetTopControlLabel(memoryViewerScopeButton,
                "Scope: " + CultureInfo.InvariantCulture.TextInfo.ToTitleCase(memoryViewerState.ScopeFilter));
            if (memoryViewerPreviousButton != null) memoryViewerPreviousButton.interactable = memoryViewerState.Offset > 0;
            if (memoryViewerNextButton != null)
                memoryViewerNextButton.interactable = memoryViewerState.Page != null && memoryViewerState.Page.has_more;
            if (memoryViewerMoreDetailButton != null)
                memoryViewerMoreDetailButton.interactable = memoryViewerState.Detail != null
                    && memoryViewerState.Detail.has_more;
        }

        private void SelectMemoryViewerItem(MemoryViewItem item)
        {
            memoryViewerRetireConfirmation = false;
            if (item != null && !memoryViewerState.Select(item)) item = null;
            RefreshMemorySelection();
            if (memoryViewerDetails == null) return;
            if (item == null)
            {
                memoryViewerDetails.text = "Select a memory to inspect its authority, scope, status, and provenance.";
                memoryViewerContentInput.SetTextWithoutNotify(string.Empty);
                memoryViewerCategoryInput.SetTextWithoutNotify(string.Empty);
                memoryViewerImportanceInput.SetTextWithoutNotify(string.Empty);
                memoryViewerContentInput.interactable = false;
                memoryViewerCategoryInput.interactable = false;
                memoryViewerImportanceInput.interactable = false;
                memoryViewerSaveButton.interactable = false;
                memoryViewerRetireButton.interactable = false;
                SetTopControlLabel(memoryViewerRetireButton, "Retire");
                return;
            }
            memoryViewerDetails.text =
                item.authority + "\n" +
                "Status: " + item.status + "\n" +
                "Scope: " + item.scope + "\n" +
                "Provenance: " + item.provenance +
                (string.IsNullOrWhiteSpace(item.source_reference) ? string.Empty
                    : "\nSource: " + item.source_reference) +
                (string.IsNullOrWhiteSpace(item.supersedes) ? string.Empty
                    : "\nSupersedes a prior record") +
                (string.IsNullOrWhiteSpace(item.corrected_by) ? string.Empty
                    : "\nCorrected by a newer record");
            memoryViewerContentInput.SetTextWithoutNotify(item.content ?? string.Empty);
            memoryViewerCategoryInput.SetTextWithoutNotify(item.category ?? string.Empty);
            memoryViewerImportanceInput.SetTextWithoutNotify(item.importance > 0
                ? item.importance.ToString(CultureInfo.InvariantCulture) : string.Empty);
            memoryViewerContentInput.interactable = item.editable;
            bool v1 = string.Equals(item.lane, "v1", StringComparison.Ordinal);
            memoryViewerCategoryInput.interactable = item.editable && v1;
            memoryViewerImportanceInput.interactable = item.editable && v1;
            memoryViewerSaveButton.interactable = item.editable;
            memoryViewerRetireButton.interactable = item.retirable;
            SetTopControlLabel(memoryViewerRetireButton, v1 ? "Remove V1…" : "Retire V2…");
            if (string.Equals(item.lane, "v2_claims", StringComparison.Ordinal)
                || string.Equals(item.lane, "episodes", StringComparison.Ordinal))
            {
                RequestMemoryViewerDetail(item);
            }
            UpdateMemoryViewStatus();
        }

        private void RequestMemoryViewerDetail(MemoryViewItem item, int offset = 0)
        {
            if (client == null || item == null || string.IsNullOrWhiteSpace(activeCharacterId)) return;
            string requestId = memoryViewerState.BeginDetailRequest(Time.realtimeSinceStartup);
            if (memoryViewerMoreDetailButton != null) memoryViewerMoreDetailButton.interactable = false;
            memoryViewerDetails.text += "\n\nLoading bounded diagnostic detail…";
            _ = client.QueryMemoryViewDetailAsync(
                requestId, activeCharacterId, item.lane, item.record_id, 8, offset);
        }

        private void RefreshMemoryViewerDetail()
        {
            MemoryViewItem item = memoryViewerState.Selected;
            MemoryViewDetail envelope = memoryViewerState.Detail;
            if (item == null || envelope == null || memoryViewerDetails == null) return;
            if (!string.IsNullOrWhiteSpace(envelope.warning))
            {
                SetMemoryViewerWarning(envelope.warning);
                return;
            }
            MemoryViewDetailData detail = envelope.detail;
            if (detail == null) return;
            string text =
                item.authority + "\nStatus: " + item.status + "\nScope: " + item.scope;
            if (string.Equals(detail.kind, "episode_diagnostic", StringComparison.Ordinal))
            {
                text += "\nValidity: " + detail.diagnostic_state
                    + "\nReason: " + detail.diagnostic_reason
                    + "\nCache: " + detail.cache_state + " — " + detail.cache_reason
                    + "\nGeneration: " + ShortMemoryAuditId(detail.generation_id)
                    + "\nSource coverage: " + detail.source_start_sequence + "–"
                    + detail.source_end_sequence + " (" + detail.source_count + " records)";
                if (detail.lower_episode_ids != null && detail.lower_episode_ids.Length > 0)
                    text += "\nLower episodes: " + detail.lower_episode_ids.Length;
            }
            else
            {
                text += "\nLifecycle: " + detail.status
                    + "\nClaim: " + ShortMemoryAuditId(detail.claim_id)
                    + "\nTruth scope ID: " + ShortMemoryAuditId(detail.truth_scope_id)
                    + "\nAudit slice begins at row " + envelope.offset;
                MemoryViewEvidence[] evidence = detail.evidence ?? new MemoryViewEvidence[0];
                int evidenceCount = Math.Min(evidence.Length, 4);
                for (int index = 0; index < evidenceCount; index++)
                {
                    MemoryViewEvidence value = evidence[index];
                    if (value == null) continue;
                    text += "\nEvidence " + (index + 1) + ": " + value.source_class
                        + " / " + value.source_type + " / " + value.evidence_role
                        + " / " + value.source_status
                        + " [" + ShortMemoryAuditId(value.event_id) + "]"
                        + (value.sequence > 0 ? " seq=" + value.sequence : string.Empty)
                        + (!string.IsNullOrWhiteSpace(value.recorded_at)
                            ? " at=" + value.recorded_at : string.Empty);
                    if (!string.IsNullOrWhiteSpace(value.source_reference))
                        text += " ref=" + MemoryViewerTextExtensions.JoinSingleLine(value.source_reference);
                }
                MemoryViewStatusAudit[] statuses = detail.status_history ?? new MemoryViewStatusAudit[0];
                int statusCount = Math.Min(statuses.Length, 2);
                for (int index = 0; index < statusCount; index++)
                {
                    MemoryViewStatusAudit value = statuses[index];
                    if (value == null) continue;
                    text += "\nLifecycle audit: " + value.status
                        + (!string.IsNullOrWhiteSpace(value.reason) ? " — " + value.reason : string.Empty)
                        + (!string.IsNullOrWhiteSpace(value.created_at) ? " at=" + value.created_at : string.Empty);
                }
                MemoryViewRelationAudit[] relations = detail.relations ?? new MemoryViewRelationAudit[0];
                int relationCount = Math.Min(relations.Length, 3);
                for (int index = 0; index < relationCount; index++)
                {
                    MemoryViewRelationAudit value = relations[index];
                    if (value == null) continue;
                    text += "\nRelation: " + value.direction + " "
                        + ShortMemoryAuditId(value.related_claim_id);
                }
                if (envelope.has_more) text += "\nMore audit rows are available in the next bounded detail page.";
            }
            memoryViewerDetails.text = text;
            Canvas.ForceUpdateCanvases();
            if (memoryViewerDetailsScroll != null)
                memoryViewerDetailsScroll.verticalNormalizedPosition = 1f;
            RefreshMemoryViewerControls();
        }

        private static string ShortMemoryAuditId(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "none";
            return value.Length <= 12 ? value : value.Substring(0, 12) + "…";
        }

        private void SaveSelectedMemory()
        {
            MemoryViewItem item = memoryViewerState.Selected;
            if (client == null || item == null || !item.editable || memoryViewerState.PageRequest.Pending
                || memoryViewerState.PageRequest.Failed || characterSwitchInFlight) return;
            int importance = item.importance > 0 ? item.importance : 5;
            if (string.Equals(item.lane, "v1", StringComparison.Ordinal)
                    && (!int.TryParse(memoryViewerImportanceInput.text, out importance)
                        || importance < 1 || importance > 10))
            {
                SetMemoryViewerWarning("V1 importance must be an integer from 1 to 10.");
                return;
            }
            string requestId = memoryViewerState.BeginRequest(Time.realtimeSinceStartup);
            _ = client.MutateMemoryViewAsync(
                requestId, Guid.NewGuid().ToString(), activeCharacterId,
                string.Equals(item.lane, "v1", StringComparison.Ordinal)
                    ? "edit_v1" : "correct_v2_durable",
                item.record_id, memoryViewerContentInput.text,
                memoryViewerCategoryInput.text, importance);
            SetMemoryViewerWarning("Applying semantic correction…");
        }

        private void RetireSelectedMemory()
        {
            MemoryViewItem item = memoryViewerState.Selected;
            if (client == null || item == null || !item.retirable || memoryViewerState.PageRequest.Pending
                || memoryViewerState.PageRequest.Failed || characterSwitchInFlight) return;
            if (!memoryViewerRetireConfirmation)
            {
                memoryViewerRetireConfirmation = true;
                SetTopControlLabel(memoryViewerRetireButton,
                    string.Equals(item.lane, "v1", StringComparison.Ordinal)
                        ? "Confirm V1 removal" : "Confirm V2 retirement");
                SetMemoryViewerWarning(string.Equals(item.lane, "v1", StringComparison.Ordinal)
                    ? "Confirm removal from canonical Memory V1. A .bak file is retained by V1 persistence."
                    : "Confirm reversible V2 retirement. Provenance and history remain stored.");
                return;
            }
            string requestId = memoryViewerState.BeginRequest(Time.realtimeSinceStartup);
            _ = client.MutateMemoryViewAsync(
                requestId, Guid.NewGuid().ToString(), activeCharacterId,
                string.Equals(item.lane, "v1", StringComparison.Ordinal)
                    ? "remove_v1" : "retire_v2_durable",
                item.record_id, string.Empty, string.Empty, 5);
            memoryViewerRetireConfirmation = false;
            SetMemoryViewerWarning("Applying explicit memory lifecycle change…");
        }

        private void SetMemoryViewerWarning(string message)
        {
            if (memoryViewerWarning != null) memoryViewerWarning.text = message ?? string.Empty;
        }

        private void CreateModelLibraryPanel(Transform parent)
        {
            modelLibraryPanel = CreatePanel(parent, "Avatar Model Library", new Color(.08f,.06f,.13f,.98f));
            Stretch(modelLibraryPanel.GetComponent<RectTransform>(), new Vector2(.12f,.14f),new Vector2(.88f,.86f),Vector2.zero,Vector2.zero);
            TMP_Text title=CreateText(modelLibraryPanel.transform,"Avatar Model",24f,Ink,TextAlignmentOptions.MidlineLeft); Stretch(title.rectTransform,new Vector2(.06f,.87f),new Vector2(.34f,.96f),Vector2.zero,Vector2.zero);
            Button import=CreateButton(modelLibraryPanel.transform,"Import",Panel); Stretch(import.GetComponent<RectTransform>(),new Vector2(.35f,.87f),new Vector2(.48f,.96f),Vector2.zero,Vector2.zero); import.onClick.AddListener(ChangeAvatarModel);
            renameModelAssetButton=CreateButton(modelLibraryPanel.transform,"Rename",Panel); Stretch(renameModelAssetButton.GetComponent<RectTransform>(),new Vector2(.49f,.87f),new Vector2(.61f,.96f),Vector2.zero,Vector2.zero); renameModelAssetButton.onClick.AddListener(()=>OpenAssetRenameDialog(ManagedAssetLibrary.ModelKind));
            deleteModelAssetsButton=CreateButton(modelLibraryPanel.transform,"Delete Selected",new Color(.42f,.16f,.22f,1f)); Stretch(deleteModelAssetsButton.GetComponent<RectTransform>(),new Vector2(.62f,.87f),new Vector2(.79f,.96f),Vector2.zero,Vector2.zero); deleteModelAssetsButton.onClick.AddListener(OpenModelDeleteConfirmation);
            Button back=CreateButton(modelLibraryPanel.transform,"Back",Panel); Stretch(back.GetComponent<RectTransform>(),new Vector2(.80f,.87f),new Vector2(.94f,.96f),Vector2.zero,Vector2.zero); back.onClick.AddListener(()=>{selectedModelAssets.Clear();modelLibraryPanel.SetActive(false);});
            modelLibraryTiles=CreateLibraryTileGrid(modelLibraryPanel.transform,"Model Library Tiles");
            deleteModelAssetsButton.transform.SetAsLastSibling();
            LogDeleteHeaderState("model", deleteModelAssetsButton, selectedModelAssets.Count, "created");
            BuildModelLibraryTiles(); modelLibraryPanel.SetActive(false);
        }

        private void BuildModelLibraryTiles()
        {
            float scrollPosition=ClearLibraryTiles(modelLibraryTiles);
            string bundledThumbnailPath = managedAssetLibrary.BundledAvatarThumbnailPath();
            Button bundled=CreateButton(modelLibraryTiles,"Bundled avatar",Panel); AddModelTilePreview(bundled, bundledThumbnailPath); bundled.onClick.AddListener(()=>{selectedModelAssets.Clear(); RequestBundledAvatarModel(); RefreshModelLibrarySelection();});
            if (VrmThumbnailGenerator.NeedsGeneration(bundledThumbnailPath)) _=EnsureBundledAvatarThumbnailAsync();
            List<ManagedAssetRecord> records = managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind);
            records.Sort((left, right) => { int name = string.Compare(left.displayName, right.displayName, StringComparison.OrdinalIgnoreCase); return name != 0 ? name : string.CompareOrdinal(left.id, right.id); });
            Dictionary<string, int> nameCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach(ManagedAssetRecord asset in records) { ManagedAssetRecord selected=asset; string baseName=ManagedAssetLibrary.DisplayName(asset,"Imported model"); nameCounts.TryGetValue(baseName,out int occurrence); occurrence++; nameCounts[baseName]=occurrence; string visibleName=occurrence==1?baseName:baseName+" ("+occurrence+")"; Button tile=CreateButton(modelLibraryTiles,visibleName,Panel); AddModelTilePreview(tile, asset.thumbnailPath); tile.gameObject.name="Managed Model "+asset.id; tile.onClick.AddListener(()=>{if(Input.GetKey(KeyCode.LeftControl)||Input.GetKey(KeyCode.RightControl)){ToggleModelDeletionSelection(selected.id);return;}SelectOnlyModelForDeletion(selected.id);RequestManagedAvatarModel(selected);}); if(VrmThumbnailGenerator.NeedsGeneration(managedAssetLibrary.ThumbnailPath(asset.id))) _=EnsureModelThumbnailAsync(asset,modelApplyGeneration); }
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

        private void PositionLibraryTileLabel(Button tile)
        {
            TMP_Text label = tile.GetComponentInChildren<TMP_Text>();
            if (label == null) return;
            Transform existingBacking = tile.transform.Find("Tile Caption Backing");
            Image backing = existingBacking != null ? existingBacking.GetComponent<Image>() :
                CreateImage(tile.transform, "Tile Caption Backing", new Color(.02f,.02f,.06f,.72f));
            backing.raycastTarget = false;
            Stretch(backing.rectTransform, new Vector2(.04f,.035f), new Vector2(.96f,.27f), Vector2.zero, Vector2.zero);
            label.raycastTarget = false;
            label.enableWordWrapping = false;
            label.overflowMode = TextOverflowModes.Ellipsis;
            label.enableAutoSizing = true;
            label.fontSizeMin = 12f;
            label.fontSizeMax = 17f;
            Stretch(label.rectTransform, new Vector2(.07f,.06f), new Vector2(.93f,.25f), Vector2.zero, Vector2.zero);
            // Keep the caption over an imported texture regardless of child
            // creation order, so renamed background labels remain readable.
            label.transform.SetAsLastSibling();
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
        private void RequestManagedAvatarModel(
            ManagedAssetRecord asset,
            bool removeOnFailure = false,
            bool persistSelection = true,
            string characterId = null)
        {
            if (asset == null || string.IsNullOrWhiteSpace(asset.id)) return;
            if (!CanStartCharacterAvatarSelection(characterId ?? activeCharacterId)) return;
            if (!modelApplyInProgress && avatarLoader != null && avatarLoader.ActiveModelPath == asset.path)
            {
                // Repeatedly clicking the already active card is deliberately
                // idempotent. An explicit user selection still establishes
                // this character's preference without reloading the VRM.
                if (persistSelection) PersistAvatarSelection(characterId ?? activeCharacterId, asset);
                BindReadyCharacterFraming(characterId ?? activeCharacterId);
                RefreshModelLibrarySelection();
                return;
            }
            RetireAvatarFramingOwner();
            avatarLoader?.ClaimCharacterSelection();
            pendingModelApply = asset;
            pendingBundledModelApply = false;
            pendingModelApplyRemoveOnFailure = removeOnFailure;
            pendingModelApplyPersistsSelection = persistSelection;
            pendingModelApplyCharacterId = characterId ?? activeCharacterId;
            modelApplyGeneration++;
            if (!modelApplyInProgress) _ = ProcessManagedAvatarModelRequestsAsync();
        }

        private void RequestBundledAvatarModel(bool persistSelection = true, string characterId = null)
        {
            if (!CanStartCharacterAvatarSelection(characterId ?? activeCharacterId)) return;
            RetireAvatarFramingOwner();
            avatarLoader?.ClaimCharacterSelection();
            pendingModelApply = null;
            pendingBundledModelApply = true;
            pendingModelApplyRemoveOnFailure = false;
            pendingModelApplyPersistsSelection = persistSelection;
            pendingModelApplyCharacterId = characterId ?? activeCharacterId;
            modelApplyGeneration++;
            if (!modelApplyInProgress) _ = ProcessManagedAvatarModelRequestsAsync();
        }

        private void RequestCharacterAvatarPreference(string characterId)
        {
            if (avatarLoader == null || managedAssetLibrary == null || string.IsNullOrWhiteSpace(characterId)) return;
            RetireCharacterAvatarRequests();
            if (!Guid.TryParse(characterId, out _))
            {
                // An empty-library shell is presentation only, not a character.
                characterAvatarSwitchInFlight = false;
                return;
            }
            CharacterAvatarPreference.Resolution desired = CharacterAvatarPreference.Resolve(
                characterId,
                managedAssetLibrary,
                PlayerPrefs.GetString(AvatarLoader.CustomModelPathPreference, string.Empty));
            bool alreadyLoaded = desired.IsBundled
                ? avatarLoader.ActiveAvatar != null && avatarLoader.ActiveModelPath == "Bundled model"
                : desired.Asset != null && avatarLoader.ActiveModelPath == desired.Asset.path;
            if (alreadyLoaded && !modelApplyInProgress)
            {
                if (!BindReadyCharacterFraming(characterId)) return;
                avatarLoader.SetAvatarVisible(true);
                avatarLoader.GetComponent<AvatarPresentationResolver>()?.Apply(authoritativeStatePresentation);
                return;
            }

            characterAvatarSwitchInFlight = true;
            avatarLoader.SetAvatarVisible(false);
            ApplyStatus("connecting", "Loading character avatar…");
            if (desired.IsBundled) RequestBundledAvatarModel(false, characterId);
            else RequestManagedAvatarModel(desired.Asset, false, false, characterId);
        }

        private async Task ProcessManagedAvatarModelRequestsAsync()
        {
            modelApplyInProgress = true;
            try
            {
                while (pendingModelApply != null || pendingBundledModelApply)
                {
                    ManagedAssetRecord asset = pendingModelApply;
                    bool bundled = pendingBundledModelApply;
                    bool removeOnFailure = pendingModelApplyRemoveOnFailure;
                    bool persistSelection = pendingModelApplyPersistsSelection;
                    string requestCharacterId = pendingModelApplyCharacterId;
                    int request = modelApplyGeneration;
                    pendingModelApply = null;
                    pendingBundledModelApply = false;
                    pendingModelApplyRemoveOnFailure = false;
                    pendingModelApplyPersistsSelection = true;
                    pendingModelApplyCharacterId = null;
                    modelApplyInFlightId = bundled ? ManagedAssetLibrary.BundledAvatarThumbnailId : asset.id;
                    ApplyStatus("connecting", "Loading visual avatar model...");
                    bool loaded = avatarLoader != null && (bundled
                        ? avatarLoader.LoadConfiguredAvatar()
                        : await avatarLoader.LoadAvatarFromPathAsync(asset.path));

                    // A later click supersedes every UI/state side effect from
                    // this completion. The loop will then load only the latest
                    // pending asset, rather than racing parallel avatar swaps.
                    if (!IsCharacterAvatarApplyAuthoritative(
                            request, modelApplyGeneration, requestCharacterId, activeCharacterId)) continue;
                    if (!loaded)
                    {
                        if (removeOnFailure && asset != null)
                            managedAssetLibrary.Delete(ManagedAssetLibrary.ModelKind, new[] { asset.id });
                        if (!persistSelection && avatarLoader != null)
                            loaded = avatarLoader.LoadConfiguredAvatar();
                    }
                    if (!loaded)
                    {
                        bool restored = BindReadyCharacterFraming(requestCharacterId);
                        avatarLoader?.SetAvatarVisible(restored);
                        ApplyStatus("error", avatarLoader != null ? avatarLoader.LastError : "Avatar loader is unavailable.");
                        if (modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy) BuildModelLibraryTiles();
                        continue;
                    }
                    if (persistSelection)
                    {
                        if (bundled) PersistBundledAvatarSelection(requestCharacterId);
                        else PersistAvatarSelection(requestCharacterId, asset);
                    }
                    if (!BindReadyCharacterFraming(requestCharacterId))
                    {
                        ApplyStatus("error", "Character avatar framing is unavailable.");
                        continue;
                    }
                    avatarLoader?.SetAvatarVisible(true);
                    ApplyStatus("ready", "Visual avatar model loaded.");
                    RefreshModelLibrarySelection();
                    RefreshDisplaySettingsUi();
                    if (asset != null) _ = EnsureModelThumbnailAsync(asset, request);
                }
            }
            finally
            {
                modelApplyInFlightId = null;
                modelApplyInProgress = false;
                RefreshModelLibrarySelection();
            }
        }

        private static void PersistAvatarSelection(string characterId, ManagedAssetRecord asset)
        {
            if (asset == null) return;
            if (!string.IsNullOrWhiteSpace(characterId))
                CharacterAvatarPreference.SetManaged(characterId, asset.id);
            // Retain the former global choice as the fallback for existing
            // characters that have no explicit character preference yet.
            PlayerPrefs.SetString(AvatarLoader.CustomModelPathPreference, asset.path);
            PlayerPrefs.Save();
        }

        private static void PersistBundledAvatarSelection(string characterId)
        {
            if (!string.IsNullOrWhiteSpace(characterId))
                CharacterAvatarPreference.SetBundled(characterId);
            AvatarLoader.ClearCustomModelPathPreference();
        }
        private void RefreshModelLibrarySelection(){ if(modelLibraryPanel==null)return; string active=avatarLoader!=null?avatarLoader.ActiveModelPath:string.Empty; foreach(Button tile in modelLibraryPanel.GetComponentsInChildren<Button>(true)){bool imported=tile.gameObject.name.StartsWith("Managed Model ");bool bundled=tile.gameObject.name=="Bundled avatar";if(!imported&&!bundled)continue;bool selected=imported&&selectedModelAssets.Contains(tile.gameObject.name.Substring("Managed Model ".Length));bool on=imported?active==managedAssetLibrary.Assets(ManagedAssetLibrary.ModelKind).Find(x=>tile.gameObject.name=="Managed Model "+x.id)?.path:bundled&&(string.IsNullOrEmpty(active)||active=="Bundled model");SetLibraryTileVisual(tile,on,selected);} UpdateDeleteSelectedHeader(deleteModelAssetsButton,selectedModelAssets.Count); UpdateRenameHeader(renameModelAssetButton, selectedModelAssets.Count); LogDeleteHeaderState("model",deleteModelAssetsButton,selectedModelAssets.Count,"refresh"); }

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
            if(activeWasDeleted) RequestBundledAvatarModel();
            foreach (string assetId in selectedModelAssets)
                CharacterAvatarPreference.RepairDeletedAsset(
                    availableCharacters.Select(item => item.character_id), assetId);
            managedAssetLibrary.Delete(ManagedAssetLibrary.ModelKind, selectedModelAssets); selectedModelAssets.Clear();
            if(modelDeleteConfirmPanel!=null)modelDeleteConfirmPanel.SetActive(false);
            BuildModelLibraryTiles(); RefreshDisplaySettingsUi();
        }

        private void ChangeAvatarModel()
        {
            StartCoroutine(ChangeAvatarModelRoutine());
        }

        private IEnumerator ChangeAvatarModelRoutine()
        {
            string requestedCharacter = activeCharacterId;
            int requestedGeneration = modelApplyGeneration;
            if (!CanStartCharacterAvatarSelection(requestedCharacter)) yield break;
            Task<LinuxNativeFilePicker.Result> pickerTask = LinuxNativeFilePicker.PickAsync(
                "Choose VRM avatar",
                LinuxNativeFilePicker.AvatarModelFilters);
            yield return new WaitUntil(() => pickerTask.IsCompleted);
            if (!IsAvatarPickerCompletionCurrent(requestedGeneration, modelApplyGeneration,
                    requestedCharacter, activeCharacterId, characterSwitchInFlight)) yield break;
            LinuxNativeFilePicker.Result result = pickerTask.Result;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            LogNativePickerTiming("avatar", result);
#endif
            if (!string.IsNullOrWhiteSpace(result.error))
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                Debug.LogError("[AIFren Picker] " + result.error);
#endif
                yield break;
            }
            string path = result.path;
            if (string.IsNullOrWhiteSpace(path)) yield break;
            LinuxNativeFilePicker.Remember(path);
            if (!managedAssetLibrary.TryImport(path, ManagedAssetLibrary.ModelKind, out ManagedAssetRecord asset, out string importError))
            {
                ApplyStatus("error", importError); yield break;
            }
            SelectOnlyModelForDeletion(asset.id);
            if (modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy) BuildModelLibraryTiles();
            RequestManagedAvatarModel(asset, true, true, requestedCharacter);
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private static void LogNativePickerTiming(string kind, LinuxNativeFilePicker.Result result)
        {
            double launch = LinuxNativeFilePicker.ElapsedMilliseconds(result.requestedAt, result.processStartedAt);
            double completed = LinuxNativeFilePicker.ElapsedMilliseconds(result.processStartedAt, result.completedAt);
            Debug.Log("[AIFren Picker] " + kind + " process launch=" +
                (launch >= 0d ? launch.ToString("0") : "n/a") + "ms, return=" +
                (completed >= 0d ? completed.ToString("0") : "n/a") + "ms, result=" +
                (string.IsNullOrWhiteSpace(result.path) ? "cancelled-or-failed" : "selected") + ".");
        }
#endif

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

        private async Task EnsureBundledAvatarThumbnailAsync()
        {
            const string thumbnailId = ManagedAssetLibrary.BundledAvatarThumbnailId;
            if (!thumbnailGenerationInFlight.Add(thumbnailId)) return;
            try
            {
                AvatarConfiguration configuration = AvatarConfiguration.Load();
                GameObject bundledPrefab = Resources.Load<GameObject>(configuration.avatarResourcePath);
                string thumbnailPath = managedAssetLibrary.BundledAvatarThumbnailPath();
                if (bundledPrefab != null && await VrmThumbnailGenerator.TryGenerateFromPrefabAsync(bundledPrefab, thumbnailPath))
                {
                    if (modelLibraryPanel != null && modelLibraryPanel.activeInHierarchy)
                        BuildModelLibraryTiles();
                }
            }
            finally { thumbnailGenerationInFlight.Remove(thumbnailId); }
        }

        private void CreateBackgroundLibraryPanel(Transform parent)
        {
            backgroundLibraryPanel = CreatePanel(parent, "Background Library", new Color(.08f, .06f, .13f, .98f));
            Stretch(backgroundLibraryPanel.GetComponent<RectTransform>(), new Vector2(.12f, .14f), new Vector2(.88f, .86f), Vector2.zero, Vector2.zero);
            TMP_Text title = CreateText(backgroundLibraryPanel.transform, "Viewer Background", 24f, Ink, TextAlignmentOptions.MidlineLeft);
            Stretch(title.rectTransform, new Vector2(.06f, .87f), new Vector2(.34f, .96f), Vector2.zero, Vector2.zero);
            Button import = CreateButton(backgroundLibraryPanel.transform, "Import", Panel);
            Stretch(import.GetComponent<RectTransform>(), new Vector2(.35f, .87f), new Vector2(.48f, .96f), Vector2.zero, Vector2.zero);
            import.onClick.AddListener(ChangeCustomBackground);
            renameBackgroundAssetButton = CreateButton(backgroundLibraryPanel.transform, "Rename", Panel);
            Stretch(renameBackgroundAssetButton.GetComponent<RectTransform>(), new Vector2(.49f, .87f), new Vector2(.61f, .96f), Vector2.zero, Vector2.zero);
            renameBackgroundAssetButton.onClick.AddListener(() => OpenAssetRenameDialog(ManagedAssetLibrary.BackgroundKind));
            deleteBackgroundAssetsButton = CreateButton(backgroundLibraryPanel.transform, "Delete Selected", new Color(.42f,.16f,.22f,1f));
            Stretch(deleteBackgroundAssetsButton.GetComponent<RectTransform>(), new Vector2(.62f,.87f), new Vector2(.79f,.96f), Vector2.zero, Vector2.zero);
            deleteBackgroundAssetsButton.onClick.AddListener(OpenBackgroundDeleteConfirmation);
            Button back = CreateButton(backgroundLibraryPanel.transform, "Back", Panel);
            Stretch(back.GetComponent<RectTransform>(), new Vector2(.80f, .87f), new Vector2(.94f, .96f), Vector2.zero, Vector2.zero);
            back.onClick.AddListener(() => { selectedBackgroundAssets.Clear(); backgroundLibraryPanel.SetActive(false); });
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
            string name = ManagedAssetLibrary.DisplayName(asset, fallback);
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
            UpdateRenameHeader(renameBackgroundAssetButton, selectedBackgroundAssets.Count);
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

        private static void UpdateRenameHeader(Button button, int count)
        {
            if (button == null) return;
            button.gameObject.SetActive(true);
            button.interactable = count == 1;
        }

        private void OpenAssetRenameDialog(string kind)
        {
            HashSet<string> selection = kind == ManagedAssetLibrary.ModelKind
                ? selectedModelAssets : selectedBackgroundAssets;
            if (selection.Count != 1) return;
            string id = selection.First();
            ManagedAssetRecord asset = managedAssetLibrary.Assets(kind).Find(item => item.id == id);
            if (asset == null) return;

            if (assetRenamePanel == null)
            {
                Transform parent = settingsPanel != null ? settingsPanel.transform : transform;
                assetRenamePanel = CreatePanel(parent, "Rename Imported Asset", new Color(.08f,.06f,.13f,.99f));
                Stretch(assetRenamePanel.GetComponent<RectTransform>(), new Vector2(.28f,.36f), new Vector2(.72f,.64f), Vector2.zero, Vector2.zero);
                TMP_Text title = CreateText(assetRenamePanel.transform, "Rename", 22f, Ink, TextAlignmentOptions.Center);
                title.gameObject.name = "Title";
                Stretch(title.rectTransform, new Vector2(.08f,.69f), new Vector2(.92f,.89f), Vector2.zero, Vector2.zero);
                assetRenameInput = CreateInputField(assetRenamePanel.transform);
                assetRenameInput.characterLimit = 120;
                Stretch(assetRenameInput.GetComponent<RectTransform>(), new Vector2(.09f,.45f), new Vector2(.91f,.63f), Vector2.zero, Vector2.zero);
                assetRenameMessage = CreateText(assetRenamePanel.transform, string.Empty, 15f, new Color(.96f,.63f,.70f,1f), TextAlignmentOptions.Center);
                Stretch(assetRenameMessage.rectTransform, new Vector2(.09f,.34f), new Vector2(.91f,.43f), Vector2.zero, Vector2.zero);
                Button cancel = CreateButton(assetRenamePanel.transform, "Cancel", Panel);
                Stretch(cancel.GetComponent<RectTransform>(), new Vector2(.09f,.11f), new Vector2(.46f,.28f), Vector2.zero, Vector2.zero);
                cancel.onClick.AddListener(CloseAssetRenameDialog);
                Button save = CreateButton(assetRenamePanel.transform, "Save", Accent);
                Stretch(save.GetComponent<RectTransform>(), new Vector2(.54f,.11f), new Vector2(.91f,.28f), Vector2.zero, Vector2.zero);
                save.onClick.AddListener(SaveAssetRename);
                assetRenamePanel.SetActive(false);
            }

            renameAssetKind = kind;
            renameAssetId = asset.id;
            assetRenamePanel.transform.Find("Title").GetComponent<TMP_Text>().text =
                "Rename " + (kind == ManagedAssetLibrary.ModelKind ? "Model" : "Background");
            assetRenameInput.SetTextWithoutNotify(asset.displayName ?? string.Empty);
            assetRenameMessage.text = string.Empty;
            assetRenamePanel.SetActive(true);
            assetRenamePanel.transform.SetAsLastSibling();
            assetRenameInput.ActivateInputField();
        }

        private void CloseAssetRenameDialog()
        {
            if (assetRenamePanel != null) assetRenamePanel.SetActive(false);
            renameAssetKind = null;
            renameAssetId = null;
        }

        private void SaveAssetRename()
        {
            if (!managedAssetLibrary.TryRename(renameAssetKind, renameAssetId,
                    assetRenameInput != null ? assetRenameInput.text : string.Empty, out string error))
            {
                if (assetRenameMessage != null) assetRenameMessage.text = error;
                return;
            }
            bool model = renameAssetKind == ManagedAssetLibrary.ModelKind;
            CloseAssetRenameDialog();
            if (model) BuildModelLibraryTiles();
            else BuildBackgroundLibraryTiles(new[] { AvatarViewerBackground.LightNeutral, AvatarViewerBackground.NeutralGrey, AvatarViewerBackground.Bedroom });
            RefreshDisplaySettingsUi();
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

        private void ChangeCustomBackground()
        {
            StartCoroutine(ChangeCustomBackgroundRoutine());
        }

        private IEnumerator ChangeCustomBackgroundRoutine()
        {
            Task<LinuxNativeFilePicker.Result> pickerTask = LinuxNativeFilePicker.PickAsync("Choose viewer background", "Images | *.png *.jpg *.jpeg");
            yield return new WaitUntil(() => pickerTask.IsCompleted);
            LinuxNativeFilePicker.Result result = pickerTask.Result;
            if (!string.IsNullOrWhiteSpace(result.error))
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                Debug.LogError("[AIFren Picker] " + result.error);
#endif
                yield break;
            }
            string path = result.path;
            if (string.IsNullOrWhiteSpace(path)) yield break;
            LinuxNativeFilePicker.Remember(path);
            string extension = System.IO.Path.GetExtension(path);
            if (!string.Equals(extension, ".png", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(extension, ".jpg", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(extension, ".jpeg", StringComparison.OrdinalIgnoreCase))
            {
                ApplyStatus("error", "Choose a PNG or JPEG viewer background.");
                yield break;
            }
            if (!managedAssetLibrary.TryImport(path, ManagedAssetLibrary.BackgroundKind, out ManagedAssetRecord asset, out string importError))
            {
                ApplyStatus("error", "Could not import background: " + importError);
                yield break;
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

        private void EnsureHiddenSubtitlePresentation()
        {
            if (hiddenDialogueText == null) return;
            if (hiddenSubtitleFont == null) hiddenSubtitleFont = SubtitleStyle.CreateFont(font);
            if (hiddenSubtitleFont != null)
            {
                hiddenDialogueText.font = hiddenSubtitleFont;
                if (hiddenSubtitleMeasurementText != null) hiddenSubtitleMeasurementText.font = hiddenSubtitleFont;
            }
            if (hiddenSubtitleMaterial == null)
            {
                hiddenSubtitleMaterial = new Material(hiddenDialogueText.fontSharedMaterial) { name = "AIFren Hidden Subtitle Material" };
                hiddenDialogueText.fontSharedMaterial = hiddenSubtitleMaterial;
            }
            SubtitleStyle.Apply(hiddenDialogueText, hiddenSubtitleMaterial, SubtitleTextColor.Current);
            if (hiddenSubtitleMeasurementText != null) SubtitleStyle.Apply(hiddenSubtitleMeasurementText, null);
        }

        private void OnDestroy()
        {
            hiddenSubtitleRenderTarget?.Dispose();
            if (hiddenSubtitleMaterial != null) Destroy(hiddenSubtitleMaterial);
            if (hiddenSubtitleFont != null)
            {
                foreach (var atlas in hiddenSubtitleFont.atlasTextures) if (atlas != null) Destroy(atlas);
                if (hiddenSubtitleFont.material != null) Destroy(hiddenSubtitleFont.material);
                Destroy(hiddenSubtitleFont);
            }
        }

        private void LogHiddenSubtitleState(string phase)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (!verboseSubtitleDiagnostics || hiddenDialogueText == null || hiddenDialogueCanvasGroup == null) return;
#else
            return;
#endif
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
        }

        private void BeginSubtitleResponse(string rawResponse, PreparedSubtitlePlan prepared = null)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFrameProfiler?.Mark("subtitle_begin_prepare");
#endif
            subtitleGeneration++;
            PreparedSubtitlePlan plan = prepared ?? BuildSubtitlePlan(rawResponse);
            if (plan == null)
            {
                // Invalid input cannot inherit an older render session.
                hiddenSubtitlePresenter?.Cancel();
                return;
            }
            string spokenSubtitleText = plan.Spoken;
            subtitlePages.Clear();
            subtitlePages.AddRange(plan.Pages.Select(page => page.SpokenText));
            foreach (SubtitlePage page in plan.Pages) hiddenSubtitlePresenter?.Preload(page);
            subtitlePageWordRanges.Clear();
            subtitlePageWordRanges.AddRange(plan.Ranges);
            LogSubtitlePageOwnership(spokenSubtitleText);
            subtitleSpeechActive = false;
            subtitlePlaybackGeneration = -1;
            subtitleAwaitingPlayback = true;
            subtitlePlaybackId = 0;
            subtitleSpeechDuration = 0f;
            subtitlePlaybackStartedAt = 0f;
            ConfigureSubtitleTimingPlan(0f, false, null);
            int generation = subtitleGeneration;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (verboseSubtitleDiagnostics)
#endif
                Debug.Log("[AIFren Subtitle] activated generation=" + generation + " pages=" + subtitlePages.Count +
                    " precompute=" + plan.PreparationMilliseconds.ToString("F2") + "ms enabled=" +
                    showDialogueWhenHidden + " hidden=" + interfaceHidden);
            // Begin atomically replaces the prior session while preserving
            // plain page layout prepared by chunk_queued.
            hiddenSubtitlePresenter?.Begin(new SubtitleSession(
                plan.Pages, new List<SubtitlePageWordRange>(subtitlePageWordRanges),
                new List<float>(subtitleWordSchedule), generation, Time.unscaledTime));
            // A response may finish while the ordinary card is already shown,
            // without passing through TransitionUiVisibility. A temporary peek
            // suppresses this session for later restoration; committed Show
            // owns the normal dialogue and cancels hidden-subtitle presentation.
            if (!interfaceHidden)
            {
                if (temporarilyRevealed) SuppressHiddenSubtitleForUiReveal();
                else HideHiddenSubtitleImmediately();
            }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            developmentFrameProfiler?.Mark("subtitle_ready:pages=" + subtitlePages.Count);
#endif
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        private IEnumerator BeginDevelopmentProfileQa()
        {
            // A fixed launch delay previously let the first QA response race
            // the avatar's asynchronous import/final-layout collections. Wait
            // for the actual production-ready boundary so those costs remain
            // separately attributable from first speech presentation.
            float avatarDeadline = Time.realtimeSinceStartup + 20f;
            while ((avatarAnimation == null || avatarPresentationInitialization != null) &&
                   Time.realtimeSinceStartup < avatarDeadline)
                yield return null;
            yield return new WaitForSecondsRealtime(.5f);
            interfaceHidden = true;
            showDialogueWhenHidden = true;
            inputRequested = false;
            RefreshPresentationVisibility();
            yield return new WaitForSecondsRealtime(.35f);
            developmentFrameProfiler?.Begin("portrait_hidden_ui_avatar_subtitles_kokoro");
            developmentFrameProfiler?.Mark("portrait_hidden_ready");
            developmentProfileScenarioIndex = 0;
            instantTextToggle.SetIsOnWithoutNotify(true);
            SetInstantText(true);
            developmentFrameProfiler?.Mark("instant_on_before_response");
            _ = client.RunDevelopmentPresentationQaAsync(DevelopmentProfileScenarioName());
        }

        private IEnumerator AdvanceDevelopmentProfileQa()
        {
            yield return new WaitForSecondsRealtime(.75f);
            developmentProfileAdvancePending = false;
            if (developmentProfileScenarioIndex == 0)
            {
                // Exact regression: response one retires, a temporary peek
                // completes with no active subtitle session, then response two
                // must begin renderable without another transition.
                yield return RunDevelopmentTemporaryPeek("between_responses", 0f);
            }
            developmentProfileScenarioIndex++;
            if (developmentProfileScenarioIndex >= developmentProfileScenarios.Length)
            {
                yield return new WaitForSecondsRealtime(1f);
                developmentFrameProfiler?.Finish();
                yield break;
            }
            // Warm and long runs start paced. The warm stream toggles Instant
            // Text on and back off through the actual controller binding.
            instantTextToggle.SetIsOnWithoutNotify(false);
            SetInstantText(false);
            developmentFrameProfiler?.Mark("scenario_request:" + DevelopmentProfileScenarioName());
            _ = client.RunDevelopmentPresentationQaAsync(DevelopmentProfileScenarioName());
        }

        private IEnumerator RunDevelopmentTemporaryPeek(string label, float delaySeconds)
        {
            if (delaySeconds > 0f) yield return new WaitForSecondsRealtime(delaySeconds);
            developmentFrameProfiler?.Mark("temporary_peek_begin:" + label +
                ":active=" + (hiddenSubtitlePresenter != null && hiddenSubtitlePresenter.IsActive));
            interfaceHidden = false;
            edgeRevealActive = true;
            temporarilyRevealed = true;
            RefreshPresentationVisibility();
            yield return new WaitForSecondsRealtime(.55f);
            interfaceHidden = true;
            edgeRevealActive = false;
            temporarilyRevealed = false;
            RefreshPresentationVisibility();
            yield return new WaitForSecondsRealtime(.35f);
            bool presenterActive = hiddenSubtitlePresenter != null && hiddenSubtitlePresenter.IsActive;
            bool presenterSuppressed = hiddenSubtitlePresenter != null && hiddenSubtitlePresenter.IsSuppressed;
            bool renderable = hiddenDialogueText != null && hiddenDialogueText.enabled;
            developmentFrameProfiler?.Mark("temporary_peek_end:" + label +
                ":active=" + presenterActive + ":suppressed=" + presenterSuppressed +
                ":renderable=" + renderable);
        }

        private string DevelopmentProfileScenarioName()
        {
            return developmentProfileScenarioIndex >= 0 && developmentProfileScenarioIndex < developmentProfileScenarios.Length
                ? developmentProfileScenarios[developmentProfileScenarioIndex] : "none";
        }
#endif

        private bool HiddenSubtitlePageFits(SubtitlePage page)
        {
            TMP_Text measurement = hiddenSubtitleMeasurementText != null
                ? hiddenSubtitleMeasurementText : hiddenDialogueText;
            if (measurement == null || hiddenDialogueViewport == null) return true;
            string formatted = page.FormattedText;
            float width = Mathf.Max(1f, hiddenDialogueViewport.rect.width - 36f);
            float height = Mathf.Max(1f, hiddenDialogueViewport.rect.height - 20f);
            float previousSize = measurement.fontSize;
            measurement.fontSize = SubtitleStyle.MinimumFontSize;
            float preferredHeight = measurement.GetPreferredValues(formatted, width, 0f).y;
            measurement.fontSize = previousSize;
            return preferredHeight <= height + .5f;
        }

        private PreparedSubtitlePlan BuildSubtitlePlan(string rawResponse)
        {
            var timer = System.Diagnostics.Stopwatch.StartNew();
            string source = rawResponse ?? string.Empty;
            DialogueDocument semantics = DialoguePresentationParser.ParseDocument(source);
            string spoken = semantics.SpokenText;
            List<SubtitlePage> pages = SubtitlePagination.SplitOwned(
                semantics, SubtitleStyle.MaximumPageWords, HiddenSubtitlePageFits);
            List<string> spokenPages = pages.ConvertAll(page => page.SpokenText);
            List<SubtitlePageWordRange> ranges = SubtitleTimingPlan.BuildPageWordRanges(
                spokenPages);
            if (!SubtitleTimingPlan.TryValidatePagesMatchCanonicalText(
                spoken, spokenPages, ranges, out string ownershipError))
            {
                Debug.LogError("[AIFren Subtitle] invalid page ownership; refusing hidden subtitle: " + ownershipError);
                return null;
            }
            timer.Stop();
            return new PreparedSubtitlePlan
            {
                Source = source,
                Spoken = spoken,
                Pages = pages,
                Ranges = ranges,
                PreparationMilliseconds = (float)timer.Elapsed.TotalMilliseconds,
            };
        }

        private static string SubtitlePlanKey(int turnId, int chunkIndex) => turnId + ":" + chunkIndex;

        private void CachePreparedSubtitlePlan(int turnId, int chunkIndex, string source)
        {
            if (string.IsNullOrWhiteSpace(source)) return;
            var timer = System.Diagnostics.Stopwatch.StartNew();
            PreparedSubtitlePlan plan = BuildSubtitlePlan(source);
            if (plan == null) return;
            string key = SubtitlePlanKey(turnId, chunkIndex);
            if (!preparedSubtitlePlans.ContainsKey(key)) preparedSubtitlePlanOrder.Enqueue(key);
            preparedSubtitlePlans[key] = plan;
            foreach (SubtitlePage page in plan.Pages) hiddenSubtitlePresenter?.Preload(page);
            timer.Stop();
            plan.PreparationMilliseconds = (float)timer.Elapsed.TotalMilliseconds;
            while (preparedSubtitlePlanOrder.Count > PreparedSubtitlePlanLimit)
                preparedSubtitlePlans.Remove(preparedSubtitlePlanOrder.Dequeue());
            Debug.Log("[AIFren Timing] subtitle chunk precomputed turn=" + turnId + " chunk=" + chunkIndex +
                " in " + plan.PreparationMilliseconds.ToString("F2") + "ms.");
        }

        private PreparedSubtitlePlan TakePreparedSubtitlePlan(int turnId, int chunkIndex, string source)
        {
            string key = SubtitlePlanKey(turnId, chunkIndex);
            if (!preparedSubtitlePlans.TryGetValue(key, out PreparedSubtitlePlan plan) ||
                !string.Equals(plan.Source, source ?? string.Empty, StringComparison.Ordinal)) return null;
            preparedSubtitlePlans.Remove(key);
            return plan;
        }

        private void ClearPreparedSubtitlePlans()
        {
            committedSpeechTimeline = null;
            committedSpeechRetired = false;
            publishedSubtitleTurnId = 0;
            preparedSubtitlePlans.Clear();
            preparedSubtitlePlanOrder.Clear();
        }

        private void LogSubtitlePageOwnership(string spokenText)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (!verboseSubtitleDiagnostics) return;
#else
            return;
#endif
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
            LogSubtitleTimingDiagnostics(durationSeconds, validAlignment, rawFinalWordTimestamp);
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (verboseSubtitleDiagnostics)
#endif
                Debug.Log("[AIFren Subtitle] immutable timing plan=" + subtitleWordSchedule.Count +
                    " words; duration=" + durationSeconds.ToString("F2") + "; playbackClock=" + (playbackClock && durationSeconds > 0f) +
                    "; source=" + (validAlignment ? "Kokoro token timestamps" : "weighted fallback") +
                    "; lead=" + HiddenSubtitleLeadSeconds.ToString("F2") + "s.");
        }

        private void LogSubtitleTimingDiagnostics(float audioDurationSeconds, bool validAlignment, float rawFinalWordTimestamp)
        {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
            if (!verboseSubtitleDiagnostics) return;
#else
            return;
#endif
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
            hiddenSubtitleSuppressedByUi = false;
            hiddenSubtitlePresenter?.Cancel();
        }

        private void SuppressHiddenSubtitleForUiReveal()
        {
            if (hiddenSubtitleSuppressedByUi || hiddenDialogueViewport == null) return;
            hiddenSubtitleSuppressedByUi = true;
            hiddenSubtitlePresenter?.SetSuppressed(true, Time.unscaledTime);
        }

        private void RestoreHiddenSubtitleAfterUiReveal()
        {
            if (!hiddenSubtitleSuppressedByUi) return;
            bool releaseSuppression = interfaceHidden && showDialogueWhenHidden &&
                hiddenSubtitlePresenter != null;
            hiddenSubtitleSuppressedByUi = false;
            if (!releaseSuppression || hiddenDialogueViewport == null) return;
            // Always relay the suppression release, even if the preceding
            // subtitle naturally retired during the peek. Otherwise the
            // presenter's private suppression bit survives into the next
            // response and keeps it invisible until another UI transition.
            hiddenSubtitlePresenter?.SetSuppressed(false, Time.unscaledTime);
        }

        private void ResetPresentationDefaults()
        {
            // Global safe-settings reset. It deliberately excludes data,
            // secrets, assets, and avatar framing; none of those are settings.
            graphicsQuality = PresentationGraphicsQuality.High;
            avatarRenderScale = DefaultAvatarRenderScale(graphicsQuality);
            showDialogueWhenHidden = false;
            showSceneOverlay = false;
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
            SetAvatarLighting(DefaultAvatarLighting);
            ApplyPresentationGraphics();
            PlayerPrefs.SetInt(GraphicsQualityPreference, (int)graphicsQuality);
            PlayerPrefs.SetFloat(AvatarRenderScalePreference, avatarRenderScale);
            PlayerPrefs.SetInt(ShowDialogueWhenHiddenPreference, 0);
            PlayerPrefs.SetInt(SceneOverlayPreference, 0);
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
        }

        private void RefreshOrdinarySettingsControls()
        {
            if (revealSlider != null) revealSlider.SetValueWithoutNotify(revealWordsPerSecond);
            if (avatarLightingSlider != null) avatarLightingSlider.SetValueWithoutNotify(avatarLightingMultiplier);
            RefreshAvatarLightingLabel();
            if (instantTextToggle != null) instantTextToggle.SetIsOnWithoutNotify(instantText);
            if (hiddenDialogueToggle != null) hiddenDialogueToggle.SetIsOnWithoutNotify(showDialogueWhenHidden);
            if (sceneOverlayToggle != null) sceneOverlayToggle.SetIsOnWithoutNotify(showSceneOverlay);
            if (alwaysOnTopToggle != null) alwaysOnTopToggle.SetIsOnWithoutNotify(alwaysOnTop);
            if (sfxMuteToggle != null) sfxMuteToggle.SetIsOnWithoutNotify(presentationAudio != null && presentationAudio.SfxMuted);
            if (sfxVolumeSlider != null) sfxVolumeSlider.SetValueWithoutNotify(presentationAudio != null ? presentationAudio.SfxVolume : .45f);
            if (bgmMuteToggle != null) bgmMuteToggle.SetIsOnWithoutNotify(presentationAudio != null && presentationAudio.BgmMuted);
            if (bgmVolumeSlider != null) bgmVolumeSlider.SetValueWithoutNotify(presentationAudio != null ? presentationAudio.BgmVolume : .14f);
            RefreshSceneOverlay(authoritativeContinuity);
        }

        private void SetAvatarLighting(float multiplier)
        {
            avatarLightingMultiplier = Mathf.Clamp(multiplier, 0f, 2f);
            PlayerPrefs.SetFloat(AvatarLightingPreference, avatarLightingMultiplier);
            PlayerPrefs.Save();
            avatarLoader?.SetPresentationLightingMultiplier(avatarLightingMultiplier);
            RefreshAvatarLightingLabel();
        }

        private void RefreshAvatarLightingLabel()
        {
            if (avatarLightingValue != null) avatarLightingValue.text = avatarLightingMultiplier.ToString("0.00") + "×";
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
            if (NativeQaSession.Active && Application.productName != "AIFren QA") return;
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
            ReflowVisibleMemoryRows();
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
            LayoutTruthScopeIndicator();
            if (avatarViewEditing) SyncAvatarViewControls();
            LogAvatarContainerMetrics(interfaceHidden);
            PlacePttPresentation();
        }

        private void LayoutTruthScopeIndicator()
        {
            if (truthScopeIndicatorLabel == null || Screen.width <= 0 || Screen.height <= 0) return;
            RectTransform rect = truthScopeIndicatorLabel.rectTransform;
            Vector2 anchor = TruthScopeIndicatorState.SafeAreaAnchor(
                Screen.safeArea, new Vector2(Screen.width, Screen.height));
            rect.anchorMin = anchor;
            rect.anchorMax = anchor;
            rect.pivot = Vector2.zero;
            rect.anchoredPosition = TruthScopeIndicatorState.SafeMargin();
            bool portrait = Screen.height >= Screen.width;
            rect.sizeDelta = TruthScopeIndicatorState.Size(portrait);
        }

        private static Vector2 FullAvatarPresentationPixels()
        {
            return new Vector2(Screen.width, Screen.height);
        }

        private void LayoutHiddenSubtitleRegion()
        {
            if (hiddenDialogueViewport == null || Screen.width <= 0 || Screen.height <= 0) return;

            // Anchor from the physical safe area, not an arbitrary vertical
            // center. The subtitle style owns a compact region above a modest
            // bottom margin, leaving the subtitle in a classic lower-screen
            // position while top-aligned words grow safely downward inside it.
            Rect safe = Screen.safeArea;
            float safeLeft = safe.xMin / Screen.width;
            float safeRight = safe.xMax / Screen.width;
            float safeBottom = safe.yMin / Screen.height;
            float safeTop = safe.yMax / Screen.height;
            float left = Mathf.Clamp01(safeLeft + SubtitleStyle.SideInset);
            float right = Mathf.Clamp01(safeRight - SubtitleStyle.SideInset);
            float bottom = Mathf.Clamp01(safeBottom + SubtitleStyle.BottomInset);
            float top = Mathf.Min(safeTop - .02f, bottom + SubtitleStyle.RegionHeight);
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

            if (avatarPresentationState == null) avatarPresentationState = AvatarPresentationState.CreateUnbound(AvatarConfiguration.Load());
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
            if (NativeQaSession.RejectOrdinaryStartup) return;
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
