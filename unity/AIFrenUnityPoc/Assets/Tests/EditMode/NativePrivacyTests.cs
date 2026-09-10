using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class NativePrivacyTests
    {
        [Test]
        public void MatchedEmoteProducesOnlySemanticIntentInNativeLog()
        {
            const string sentinel = "PRIVATE_DIALOGUE_SENTINEL";
            var messages = new List<string>();
            Application.LogCallback observe = (line, stack, type) => messages.Add(line);
            var root = new GameObject("log fixture");
            var controller = root.AddComponent<AIFrenPocController>(); controller.enabled = false;
            Application.logMessageReceived += observe;
            try
            {
                controller.ApplyLegacyAvatarGesture("*nods while thinking about " + sentinel + "* Hello.");
                Assert.That(messages.Exists(line => line.Contains("mapped intent=")), Is.True);
                Assert.That(string.Join("\n", messages), Does.Not.Contain(sentinel));
                Assert.That(string.Join("\n", messages), Does.Not.Contain("nods while"));
            }
            finally { Application.logMessageReceived -= observe; Object.DestroyImmediate(root); }
        }

        [UnityTest]
        public IEnumerator FailedTransportDoesNotPublishExceptionInput()
        {
            const string sentinel = "PRIVATE_PATH_CREDENTIAL_SENTINEL";
            using (var client = new AIFrenWebSocketClient())
            {
                var task = client.ConnectAsync(sentinel + "://bad uri");
                float deadline = Time.realtimeSinceStartup + 5;
                while (!task.IsCompleted && Time.realtimeSinceStartup < deadline) yield return null;
                Assert.That(task.IsCompleted, Is.True);
                Assert.That(client.State, Is.EqualTo(ConnectionState.Error));
                Assert.That(client.LastError, Does.Not.Contain(sentinel));
                Assert.That(client.LastDisconnectReason, Does.Not.Contain(sentinel));
                Assert.That(client.LastError, Does.Contain("Reconnect"));
            }
        }

        [Test]
        public void NativeHelperPipesDrainBothStreamsAfterTheirCaptureLimit()
        {
            if (Application.platform != RuntimePlatform.LinuxEditor) Assert.Ignore("Linux native helper fixture");
            var info = new ProcessStartInfo("/usr/bin/python3") {
                UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true,
            };
            info.ArgumentList.Add("-c");
            info.ArgumentList.Add("import sys; sys.stdout.write('x'*5000000); sys.stdout.flush(); " +
                "sys.stderr.write('PRIVATE_PATH_CREDENTIAL_SENTINEL'*200000); sys.stderr.flush()");
            using (var process = Process.Start(info))
            {
                NativeProcessOutput.Result result = NativeProcessOutput.Collect(process, 8000, 4096);
                Assert.That(process.ExitCode, Is.Zero, "Continued draining must let the helper exit.");
                Assert.That(result.Output.Length, Is.EqualTo(4096));
                Assert.That(result.Truncated, Is.True);
                Assert.That(result.Output, Does.Not.Contain("PRIVATE_PATH_CREDENTIAL_SENTINEL"));
            }
        }

        [Test]
        public void NativeHelperTimeoutRetiresOnlyTheOwnedProcess()
        {
            if (Application.platform != RuntimePlatform.LinuxEditor) Assert.Ignore("Linux native helper fixture");
            var info = new ProcessStartInfo("/usr/bin/python3") {
                UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true,
            };
            info.ArgumentList.Add("-c"); info.ArgumentList.Add("import time; time.sleep(10)");
            using (var process = Process.Start(info))
            {
                Assert.Throws<System.TimeoutException>(() => NativeProcessOutput.Collect(process, 100, 0));
                Assert.That(process.HasExited, Is.True);
            }
        }
    }
}
