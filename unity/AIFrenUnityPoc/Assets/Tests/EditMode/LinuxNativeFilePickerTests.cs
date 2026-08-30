using System;
using System.IO;
using System.Reflection;
using NUnit.Framework;
using AIFren.UnityPoc.UI;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class LinuxNativeFilePickerTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Application.temporaryCachePath, "AIFrenPickerTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }

        [Test]
        public void CancelDoesNotProduceASelectionOrAnError()
        {
            var result = Interpret(1, string.Empty, string.Empty);
            Assert.IsEmpty(SelectedPath(result));
            Assert.IsEmpty(Error(result));
        }

        [Test]
        public void SuccessfulSelectionReturnsTheExistingPath()
        {
            string selected = Path.Combine(root, "avatar.vrm");
            File.WriteAllText(selected, "fixture");

            var result = Interpret(0, selected, string.Empty);
            Assert.AreEqual(selected, SelectedPath(result));
            Assert.IsEmpty(Error(result));
        }

        [Test]
        public void PickerFailureReturnsOneConciseWorkerError()
        {
            var result = Interpret(2, string.Empty, "zenity detail");
            Assert.IsEmpty(SelectedPath(result));
            StringAssert.Contains("zenity failed (exit 2): zenity detail", Error(result));
        }

        [Test]
        public void AvatarFiltersExposeOnlyTheCombinedSupportedFormats()
        {
            FieldInfo field = typeof(AIFrenPocController).Assembly
                .GetType("AIFren.UnityPoc.UI.LinuxNativeFilePicker")
                .GetField("AvatarModelFilters", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            string[] filters = (string[])field.GetValue(null);

            CollectionAssert.AreEqual(new[] { "Compatible VRM and .glb Avatars | *.vrm *.glb" }, filters);
        }

        private static object Interpret(int exitCode, string selected, string standardError)
        {
            MethodInfo method = typeof(AIFrenPocController).Assembly
                .GetType("AIFren.UnityPoc.UI.LinuxNativeFilePicker")
                .GetMethod("InterpretProcessResult", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            return method.Invoke(null, new object[] { exitCode, selected, standardError });
        }

        private static string SelectedPath(object result)
        {
            return (string)result.GetType().GetField("path", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public).GetValue(result);
        }

        private static string Error(object result)
        {
            return (string)result.GetType().GetField("error", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public).GetValue(result);
        }
    }
}
