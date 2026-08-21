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
        private readonly ConcurrentQueue<ServerMessage> receivedMessages =
            new ConcurrentQueue<ServerMessage>();
        private ClientWebSocket socket;
        private CancellationTokenSource cancellation;
        private Task receiveTask;
        private readonly SemaphoreSlim sendLock = new SemaphoreSlim(1, 1);

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
                SetError(exception.Message);
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

        public async Task RequestSnapshotAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "get_snapshot" });
        }

        public async Task RequestConsoleLogAsync()
        {
            await SendCommandAsync(new ClientCommand { command = "get_console_log" });
        }

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

        public async Task SetGeminiApiKeyAsync(string apiKey)
        {
            await SendCommandAsync(new ClientCommand { command = "set_gemini_api_key", api_key = apiKey ?? string.Empty });
        }

        public bool TryDequeue(out ServerMessage message)
        {
            return receivedMessages.TryDequeue(out message);
        }

        public async Task DisconnectAsync()
        {
            CancellationTokenSource previousCancellation = cancellation;
            ClientWebSocket previousSocket = socket;
            cancellation = null;
            socket = null;

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
            await sendLock.WaitAsync();
            try
            {
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
                SetError(exception.Message);
            }
            finally
            {
                sendLock.Release();
            }
        }

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
                                State = ConnectionState.Disconnected;
                                LastDisconnectReason = string.IsNullOrWhiteSpace(result.CloseStatusDescription)
                                    ? "Backend closed the local connection."
                                    : result.CloseStatusDescription;
                                Debug.LogWarning("[AIFren Transport] " + LastDisconnectReason);
                                return;
                            }

                            stream.Write(buffer, 0, result.Count);
                        }
                        while (!result.EndOfMessage);

                        string json = Encoding.UTF8.GetString(stream.ToArray());
                        receivedMessages.Enqueue(AIFrenProtocol.ParseServerMessage(json));
                    }
                }
            }
            catch (OperationCanceledException)
            {
                // Expected during application shutdown or reconnect.
            }
            catch (Exception exception)
            {
                SetError(exception.Message);
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
