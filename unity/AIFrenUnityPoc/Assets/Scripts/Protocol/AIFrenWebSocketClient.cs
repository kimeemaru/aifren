using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace AIFren.UnityPoc.Protocol
{
    public enum ConnectionState
    {
        Disconnected,
        Connecting,
        Connected,
        Error
    }

    public sealed class AIFrenWebSocketClient : IDisposable
    {
        private readonly ConcurrentQueue<(ClientWebSocket owner, ServerMessage message)> receivedMessages =
            new ConcurrentQueue<(ClientWebSocket, ServerMessage)>();
        private ClientWebSocket socket;
        private CancellationTokenSource cancellation;
        private Task receiveTask;
        private readonly SemaphoreSlim sendLock = new SemaphoreSlim(1, 1);
        private readonly CharacterSessionFence characterSession = new CharacterSessionFence();
        private string snapshotRequestId;
#if UNITY_EDITOR || DEVELOPMENT_BUILD
        // Finite synthetic transport tests only; never enabled by normal requests/settings.
        internal string DropNextViewResponseForTest;
#endif
        // Structural observations only. Request IDs are aliased inside the recorder, never emitted.
        internal event Action<string, string, int> ViewObserved;

        public CharacterSessionOwner CharacterOwner => characterSession.Current;
        public bool CharacterSwitching => characterSession.Switching;
        public bool OwnsCharacter(CharacterSessionOwner owner) => characterSession.IsCurrent(owner);

        public ConnectionState State { get; private set; } = ConnectionState.Disconnected;
        public string LastError { get; private set; } = string.Empty;
        public string LastDisconnectReason { get; private set; } = string.Empty;

        public async Task ConnectAsync(string endpoint)
        {
            await DisconnectAsync();
            State = ConnectionState.Connecting;
            LastError = string.Empty;
            LastDisconnectReason = string.Empty;
            Debug.Log("[AIFren Transport] Connecting to local backend.");

            try
            {
                socket = new ClientWebSocket();
                cancellation = new CancellationTokenSource();
                await socket.ConnectAsync(new Uri(endpoint), cancellation.Token);
                State = ConnectionState.Connected;
                Debug.Log("[AIFren Transport] Connected to local backend.");
                receiveTask = ReceiveLoopAsync(socket, cancellation.Token);
                await SendCommandAsync(new ClientCommand { command = "get_snapshot" });
            }
            catch (Exception exception)
            {
                SetError("Could not connect to the local backend. Use Reconnect or check backend settings.");
            }
        }

        public async Task SubmitTextAsync(string text)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "submit_text",
                text = text
            });
        }

        public async Task RequestSnapshotAsync(string requestId = null)
        {
            if (!string.IsNullOrEmpty(requestId)) snapshotRequestId = requestId;
            await SendCommandAsync(new ClientCommand { command = "get_snapshot", request_id = requestId });
        }

        internal void RetireSnapshotRequest(string id)
        { if (id == snapshotRequestId) snapshotRequestId = null; }

        private void ObserveView(string stage, ServerMessage message)
        {
            string kind; string id; int count;
            if (message?.type == "snapshot")
            { kind = "history"; id = message.request_id; count = message.data?.conversation?.Length ?? -1; }
            else if (message?.@event?.type == "memory_view_page")
            { kind = "memory"; id = message.@event.data?.request_id; count = message.@event.data?.memory_page?.items?.Length ?? -1; }
            else if (message?.@event?.type == "memory_view_detail")
            { kind = "detail"; id = message.@event.data?.request_id; count = message.@event.data?.memory_detail?.detail != null ? 1 : 0; }
            else return;
            ViewObserved?.Invoke(kind + "_" + stage, id, count);
        }

        public async Task RequestConsoleLogAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "get_console_log" });
        }

        public async Task RunDevelopmentPresentationQaAsync(string scenario)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "development_presentation_qa",
                scenario = scenario ?? string.Empty,
            });
        }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
        public async Task StartDevelopmentFlightRecorderAsync(int unityPid)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "development_flight_recorder_start",
                unity_pid = unityPid,
            });
        }

        public async Task TriggerDevelopmentFlightRecorderAsync(string captureId, string reason)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "development_flight_recorder_trigger",
                capture_id = captureId ?? string.Empty,
                reason = reason ?? string.Empty,
            });
        }

        public async Task DumpDevelopmentFlightRecorderAsync(string captureId)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "development_flight_recorder_dump",
                capture_id = captureId ?? string.Empty,
            });
        }
#endif

        public async Task StopTtsAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "stop_tts" });
        }

        public async Task SetPushToTalkPressedAsync(bool pressed)
        {
            await SendCommandAsync(new ClientCommand { command = pressed ? "ptt_press" : "ptt_release" });
        }

        public async Task SetPushToTalkTranscriptionModeAsync(bool autoSend)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_ptt_transcription_mode",
                mode = autoSend ? "auto_send" : "review"
            });
        }

        public async Task SetPushToTalkBindingAsync(string binding)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_ptt_binding",
                binding = binding ?? string.Empty
            });
        }

        public async Task SetTtsVolumeAsync(float volume)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_tts_volume",
                volume = volume < 0f ? 0f : (volume > 1f ? 1f : volume)
            });
        }

        public async Task SetKokoroEarlySpeechAsync(bool enabled)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_kokoro_early_speech",
                early_speech = enabled
            });
        }

        public async Task SetExplicitAvatarCuesAsync(bool enabled)
        {
            await SendCommandAsync(new ClientCommand {
                command = "set_explicit_avatar_cues", explicit_avatar_cues = enabled
            });
        }

        public Task CharacterVoiceAsync(string action, string requestId, CharacterSessionOwner owner,
            string engine = "kokoro", string language = "en", string transcript = "", string reference = "", string revision = "", string operationId = null)
        {
            return SendCommandAsync(new ClientCommand {
                command = "character_voice", action = action, request_id = requestId,
                character_id = owner?.CharacterId, character_session = owner?.Session,
                voice_engine = engine, voice_language = language, voice_transcript = transcript,
                voice_reference = reference, voice_revision = revision,
                voice_operation_id = operationId,
            });
        }

        public async Task SetCompanionPreferencesAsync(string style, bool responsiveSpeech, bool automaticExpressions)
        {
            await SendCommandAsync(new ClientCommand {
                command = "set_companion_preferences", conversation_style = style,
                responsive_speech = responsiveSpeech, automatic_expressions = automaticExpressions,
            });
        }

        public async Task SetProactiveBehaviorAsync(bool enabled)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_proactive_behavior",
                proactive_behavior = enabled
            });
        }

        public async Task SetProactiveIntervalAsync(int intervalSeconds)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_proactive_interval",
                proactive_interval_seconds = intervalSeconds
            });
        }

        public async Task SetModelSettingsAsync(string mode, string provider, string onlineModel,
            string onlineBaseUrl, string localEndpoint, string localModel, string apiKey, string localApiKey = "")
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_model_settings", mode = mode ?? "online", provider = provider ?? "auto",
                online_model = onlineModel ?? "", online_base_url = onlineBaseUrl ?? "",
                local_endpoint = localEndpoint ?? "", local_model = localModel ?? "",
                api_key = apiKey ?? "", local_api_key = localApiKey ?? "",
            });
        }

        public async Task SetModelModeAsync(string mode)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_model_settings", mode = mode == "local" ? "local" : "online"
            });
        }

        public async Task SetOnlineModelSettingsAsync(string apiKey)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_model_settings", mode = "online", api_key = apiKey ?? string.Empty,
            });
        }

        public async Task SetLocalModelSettingsAsync(string endpoint, string model)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "set_model_settings", mode = "local",
                local_endpoint = endpoint ?? string.Empty,
                local_model = model ?? string.Empty,
            });
        }

        public async Task DiscoverLocalModelsAsync(string endpoint, string localApiKey = "")
        {
            await SendCommandAsync(new ClientCommand { command = "discover_local_models", local_endpoint = endpoint ?? "", local_api_key = localApiKey ?? "" });
        }

        public async Task StartLocalModelAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "start_local_model" });
        }

        public async Task StopLocalModelAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "stop_local_model" });
        }

        public async Task ApplyContinuityControlAsync(string commandId, string action,
            string expectedRevision, string actionToken = "", CharacterSessionOwner owner = null)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "continuity_control",
                command_id = commandId ?? string.Empty,
                action = action ?? string.Empty,
                expected_revision = expectedRevision ?? string.Empty,
                action_token = actionToken ?? string.Empty,
                character_id = owner?.CharacterId,
                character_session = owner?.Session,
            });
        }

        public async Task QueryMemoryViewAsync(string requestId, string characterId,
            string lane, string query, string statusFilter, string scopeFilter,
            int limit, int offset)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "memory_view_query",
                request_id = requestId ?? string.Empty,
                character_id = characterId ?? string.Empty,
                memory_lane = lane ?? "v1",
                query = query ?? string.Empty,
                status_filter = statusFilter ?? "current",
                scope_filter = scopeFilter ?? "applicable",
                limit = limit,
                offset = offset,
            });
        }

        public async Task MutateMemoryViewAsync(string requestId, string commandId,
            string characterId, string action, string recordId, string content,
            string category, int importance)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "memory_view_mutate",
                request_id = requestId ?? string.Empty,
                command_id = commandId ?? string.Empty,
                character_id = characterId ?? string.Empty,
                action = action ?? string.Empty,
                record_id = recordId ?? string.Empty,
                content = content ?? string.Empty,
                category = category ?? string.Empty,
                importance = importance,
            });
        }

        public async Task QueryMemoryViewDetailAsync(string requestId, string characterId,
            string lane, string recordId, int limit = 8, int offset = 0)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "memory_view_detail",
                request_id = requestId ?? string.Empty,
                character_id = characterId ?? string.Empty,
                memory_lane = lane ?? string.Empty,
                record_id = recordId ?? string.Empty,
                limit = limit,
                offset = offset,
            });
        }

        public async Task SetLocalAutoStartAsync(bool enabled)
        {
            await SendCommandAsync(new ClientCommand { command = "set_local_auto_start", local_auto_start = enabled });
        }

        public async Task ListCharactersAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "list_characters" });
        }

        public async Task SelectCharacterAsync(string characterId)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "select_character",
                character_id = characterId ?? string.Empty,
            });
        }

        public async Task CreateCharacterAsync(string displayName, string personality, string requestId = null)
        {
            await SendCommandAsync(new ClientCommand
            {
                command = "create_character",
                display_name = displayName ?? string.Empty,
                personality = personality ?? string.Empty,
                request_id = requestId ?? string.Empty,
            });
        }

        public Task PreviewCharacterOperationAsync(string characterId, string action) => SendCommandAsync(new ClientCommand
        { command = "character_operation_preview", character_id = characterId, action = action });

        public Task ConfirmCharacterOperationAsync(string characterId, string token, long revision) => SendCommandAsync(new ClientCommand
        { command = "character_operation_confirm", character_id = characterId, token = token, revision = revision });

        public Task OpenCharacterFolderAsync(string characterId) => SendCommandAsync(new ClientCommand
        { command = "open_character_folder", character_id = characterId });

        public bool TryDequeue(out ServerMessage message)
        {
            // A cancelled receive may finish after reconnect. Retain the actual
            // socket identity on every queued message; equal endpoints are not
            // ownership. Main-thread presentation never sees retired packets.
            while (receivedMessages.TryDequeue(out var received))
            {
                ObserveView("received", received.message);
                if (!ReferenceEquals(received.owner, socket) || socket == null)
                { ObserveView("reject_connection", received.message); continue; }
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                bool dropHistory = DropNextViewResponseForTest == "history" && received.message?.type == "snapshot"
                    && !string.IsNullOrEmpty(received.message.request_id);
                bool dropMemory = DropNextViewResponseForTest == "memory" && received.message?.@event?.type == "memory_view_page";
                if (dropHistory || dropMemory)
                { DropNextViewResponseForTest = null; ObserveView("qa_dropped", received.message); continue; }
#endif
                if (received.message?.type == "snapshot"
                    && received.message.data != null && received.message.data.transport_version < 9)
                {
                    characterSession.InvalidateCurrent();
                    message = new ServerMessage { type = "command_error", error = new CommandError {
                        code = "unsupported_backend_version",
                        message = "The running backend is older than this player. Restart AIFren with the current Development launcher."
                    } };
                    return true;
                }
                if (received.message?.type == "snapshot" && !string.IsNullOrEmpty(received.message.request_id)
                    && received.message.request_id != snapshotRequestId)
                { ObserveView("reject_request", received.message); continue; }
                if (!characterSession.Accept(received.message))
                { ObserveView("reject_" + characterSession.RejectionReason, received.message); continue; }
                if (received.message?.type == "snapshot") RetireSnapshotRequest(received.message.request_id);
                ObserveView("accepted", received.message);
                message = received.message; return true;
            }
            message = null; return false;
        }

        public async Task DisconnectAsync()
        {
            CancellationTokenSource previousCancellation = cancellation;
            ClientWebSocket previousSocket = socket;
            cancellation = null;
            socket = null;
            characterSession.Reset();
            snapshotRequestId = null;

            if (previousCancellation != null)
            {
                previousCancellation.Cancel();
            }

            if (previousSocket != null)
            {
                try
                {
                    if (previousSocket.State == WebSocketState.Open)
                    {
                        await previousSocket.CloseAsync(
                            WebSocketCloseStatus.NormalClosure,
                            "Unity client closing",
                            CancellationToken.None
                        );
                    }
                }
                catch
                {
                    // Closing a local test connection is best effort.
                }
                finally
                {
                    previousSocket.Dispose();
                }
            }

            if (previousCancellation != null)
            {
                previousCancellation.Dispose();
            }

            if (State != ConnectionState.Error)
            {
                State = ConnectionState.Disconnected;
                Debug.Log("[AIFren Transport] Disconnected from local backend.");
            }
        }

        public void Dispose()
        {
            if (cancellation != null)
            {
                cancellation.Cancel();
            }

            if (socket != null)
            {
                socket.Dispose();
            }
        }

        private async Task SendCommandAsync(ClientCommand command)
        {
            // Freeze the binding before the first await. A queued old command
            // must not acquire a newer character or socket while waiting.
            ClientWebSocket ownerSocket = socket;
            if (!BindCharacterCommand(command, characterSession.Current))
            {
                Debug.Log("[AIFren Transport] Discarded a command without the current character binding.");
                return;
            }
            await sendLock.WaitAsync();
            try
            {
                if (!ReferenceEquals(ownerSocket, socket)) return;
                if (socket == null || socket.State != WebSocketState.Open)
                {
                    SetError("Not connected to the AIFren backend.");
                    return;
                }

                // ClientWebSocket permits one outstanding send. Focus changes
                // can make a PTT press and release occur in adjacent frames,
                // especially for Linux mouse buttons, so serialize every
                // command instead of turning that benign sequence into a
                // transport error that disables the input field.
                Debug.Log("[AIFren Transport] Sending command: " + command.command);
                byte[] bytes = Encoding.UTF8.GetBytes(AIFrenProtocol.SerializeCommand(command));
                await socket.SendAsync(
                    new ArraySegment<byte>(bytes),
                    WebSocketMessageType.Text,
                    true,
                    cancellation.Token
                );
            }
            catch (Exception exception)
            {
                SetError("Could not send to the local backend. Reconnect and try again.");
            }
            finally
            {
                sendLock.Release();
            }
        }

        internal static bool BindCharacterCommand(ClientCommand command, CharacterSessionOwner owner)
        {
            if (command == null) return false;
            if (!CharacterSessionFence.IsScopedCommand(command.command)) return true;
            if (owner == null || !owner.IsValid) return false;
            if (!string.IsNullOrEmpty(command.character_id) && command.character_id != owner.CharacterId) return false;
            if (!string.IsNullOrEmpty(command.character_session) && command.character_session != owner.Session) return false;
            command.character_id = owner.CharacterId;
            command.character_session = owner.Session;
            return true;
        }

        internal void EnqueueReceived(ClientWebSocket owner, ServerMessage message) => receivedMessages.Enqueue((owner, message));

        private async Task ReceiveLoopAsync(ClientWebSocket activeSocket, CancellationToken token)
        {
            byte[] buffer = new byte[4096];

            try
            {
                while (!token.IsCancellationRequested && activeSocket.State == WebSocketState.Open)
                {
                    using (MemoryStream stream = new MemoryStream())
                    {
                        WebSocketReceiveResult result;

                        do
                        {
                            result = await activeSocket.ReceiveAsync(
                                new ArraySegment<byte>(buffer),
                                token
                            );

                            if (result.MessageType == WebSocketMessageType.Close)
                            {
                                if (ReferenceEquals(activeSocket, socket) && !token.IsCancellationRequested)
                                {
                                    State = ConnectionState.Disconnected;
                                    LastDisconnectReason = "Backend closed the local connection.";
                                    Debug.LogWarning("[AIFren Transport] " + LastDisconnectReason);
                                }
                                return;
                            }

                            stream.Write(buffer, 0, result.Count);
                        }
                        while (!result.EndOfMessage);

                        string json = Encoding.UTF8.GetString(stream.ToArray());
                        EnqueueReceived(activeSocket, AIFrenProtocol.ParseServerMessage(json));
                    }
                }
            }
            catch (OperationCanceledException)
            {
                // Expected during application shutdown or reconnect.
            }
            catch (Exception exception)
            {
                if (ReferenceEquals(activeSocket, socket) && !token.IsCancellationRequested)
                    SetError("The local backend connection was interrupted. Use Reconnect.");
            }
        }

        private void SetError(string message)
        {
            LastError = message;
            LastDisconnectReason = message;
            State = ConnectionState.Error;
            Debug.LogWarning("[AIFren Transport] Error: " + message);
        }
    }
}
