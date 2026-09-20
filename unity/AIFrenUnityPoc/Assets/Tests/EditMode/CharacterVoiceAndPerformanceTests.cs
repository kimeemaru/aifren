using System.Reflection;
using System;
using System.IO;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using NUnit.Framework;
using UnityEngine;
using Object = UnityEngine.Object;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterVoiceAndPerformanceTests
    {
        [Test]
        public void VoiceCommandRequiresCapturedCharacterSession()
        {
            Assert.That(CharacterSessionFence.IsScopedCommand("character_voice"), Is.True);
            var command = new ClientCommand { command = "character_voice", action = "save" };
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, null), Is.False);
            var a = new CharacterSessionOwner("A", "a-one", 1);
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, a), Is.True);
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, new CharacterSessionOwner("B", "b-one", 2)), Is.False);
            Assert.That(AIFrenWebSocketClient.BindCharacterCommand(command, new CharacterSessionOwner("A", "a-two", 3)), Is.False);
        }

        [Test]
        public void VoiceResultCannotEstablishOrBypassBinding()
        {
            var fence = new CharacterSessionFence();
            var response = new ServerMessage { type = "character_voice", character_id = "A", character_session = "a-one", character_generation = 1 };
            Assert.That(fence.Accept(response), Is.False);
            var data = new SnapshotData { character_id = "A", character_session = "a-two", character_generation = 2,
                character = new CharacterIdentity { character_id = "A" } };
            Assert.That(fence.Accept(new ServerMessage { type = "snapshot", character_id = "A", character_session = "a-two", character_generation = 2, data = data }), Is.True);
            Assert.That(fence.Accept(response), Is.False);
            response.character_session = "a-two"; response.character_generation = 2;
            Assert.That(fence.Accept(response), Is.True);
        }

        [Test]
        public void ProceduralWaveWorksWithoutOptionalClipAndReturnsBones()
        {
            var host = new GameObject("Synthetic motion host");
            var arm = new GameObject("Synthetic arm");
            try
            {
                var animation = host.AddComponent<AvatarAnimationController>();
                Set(animation, "rightUpperArm", arm.transform);
                Set(animation, "rightUpperArmBaseRotation", Quaternion.identity);
                Assert.That(animation.PlayGesture(AvatarGestureIntent.Wave), Is.True);
                Assert.That(animation.ActiveGesture, Is.EqualTo(AvatarGestureIntent.Wave));
                Assert.That(Get(animation, "authoredGestureActive"), Is.False);
                Assert.That(animation.PlayGesture(AvatarGestureIntent.Nod), Is.False);
                arm.transform.localRotation = Quaternion.Euler(15,20,30);
                animation.RetireResponseMotion();
                Assert.That(animation.ActiveGesture, Is.EqualTo(AvatarGestureIntent.None));
                Assert.That(Quaternion.Angle(arm.transform.localRotation, Quaternion.identity), Is.LessThan(.001f));
                Assert.That(host.transform.position, Is.EqualTo(Vector3.zero));
            }
            finally { Object.DestroyImmediate(host); Object.DestroyImmediate(arm); }
        }

        [Test]
        public void ReleasingSubtlePerformanceOnlyRestoresItsChestChannel()
        {
            var host = new GameObject("Synthetic performance host");
            var chest = new GameObject("Synthetic chest");
            try
            {
                var animation = host.AddComponent<AvatarAnimationController>();
                Set(animation, "chest", chest.transform);
                var baseline = Quaternion.Euler(2,3,4);
                Set(animation, "chestBaseRotation", baseline);
                animation.SetSubtlePerformance(true, .5f);
                animation.SetAttentivePresentation(true);
                animation.SetSubtlePerformance(false, 1f);
                Assert.That(Quaternion.Angle(chest.transform.localRotation, baseline), Is.LessThan(.001f));
                Assert.That(host.transform.position, Is.EqualTo(Vector3.zero));
            }
            finally { Object.DestroyImmediate(host); Object.DestroyImmediate(chest); }
        }

        private static object Get(object target, string name) => target.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(target);
        private static void Set(object target, string name, object value) => target.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(target, value);

        [Test]
        public void PackagePreferencesSurviveRestartAndDirectoryMoveWithoutHostKeys()
        {
            string root = Path.Combine(Path.GetTempPath(), "Synthetic preferences " + Guid.NewGuid().ToString("N"));
            string moved = root + " moved";
            try
            {
                var store = new PresentationPreferences.PreferenceFile(Path.Combine(root, "prefs.json"));
                store.Values["style"] = "natural"; store.Values["volume"] = .5f; store.Values["enabled"] = 1;
                store.Save();
                Directory.Move(root, moved);
                var reopened = new PresentationPreferences.PreferenceFile(Path.Combine(moved, "prefs.json"));
                Assert.That(reopened.Values["style"], Is.EqualTo("natural"));
                Assert.That(reopened.Values["volume"], Is.EqualTo(.5f));
                Assert.That(reopened.Values["enabled"], Is.EqualTo(1));
                reopened.Values["style"] = "roleplay"; reopened.Save();
                Assert.That(new PresentationPreferences.PreferenceFile(Path.Combine(moved, "prefs.json")).Values["style"], Is.EqualTo("roleplay"));
                Assert.That(new PresentationPreferences.PreferenceFile(Path.Combine(root, "prefs.json")).Values.Count, Is.Zero);
            }
            finally { if (Directory.Exists(root)) Directory.Delete(root, true); if (Directory.Exists(moved)) Directory.Delete(moved, true); }
        }

        [Test]
        public void InvalidPackagePreferencesFailExplicitly()
        {
            string path = Path.Combine(Path.GetTempPath(), "Synthetic preferences " + Guid.NewGuid().ToString("N") + ".json");
            try
            {
                File.WriteAllText(path, "{\"version\":2,\"entries\":[]}");
                Assert.Throws<IOException>(() => new PresentationPreferences.PreferenceFile(path));
                File.WriteAllText(path, "{\"version\":1,\"entries\":[{\"key\":\"x\",\"kind\":\"code\"}]}");
                Assert.Throws<IOException>(() => new PresentationPreferences.PreferenceFile(path));
            }
            finally { File.Delete(path); }
        }
    }
}
