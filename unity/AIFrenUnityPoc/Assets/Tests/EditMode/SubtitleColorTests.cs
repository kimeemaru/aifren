using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SubtitleColorTests
    {
        private GameObject root;
        private AIFrenPocController controller;
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private T Field<T>(string name) => (T)typeof(AIFrenPocController).GetField(name, Private).GetValue(controller);
        private void Click(string name) => root.GetComponentsInChildren<Button>(true).Single(x => x.name == name).onClick.Invoke();
        [SetUp] public void Setup()
        {
            Assert.IsTrue(PresentationPreferences.IsIsolated);
            PresentationPreferences.DeleteKey(SubtitleTextColor.Preference);
            root = new GameObject("color controls", typeof(RectTransform), typeof(Canvas), typeof(AIFrenPocController));
            root.GetComponent<RectTransform>().sizeDelta = new Vector2(800, 1200);
            controller = root.GetComponent<AIFrenPocController>(); controller.enabled = false;
            typeof(AIFrenPocController).GetField("theme", Private).SetValue(controller, PresentationThemes.Dark);
            typeof(AIFrenPocController).GetField("font", Private).SetValue(controller, TMP_Settings.defaultFontAsset);
            typeof(AIFrenPocController).GetMethod("AddSubtitleColorControls", Private).Invoke(controller, new object[] {root.transform, -20f});
        }
        [TearDown] public void Cleanup()
        {
            Object.DestroyImmediate(root); PresentationPreferences.DeleteKey(SubtitleTextColor.Preference);
            PresentationPreferences.DeleteKey("synthetic-unrelated");
        }
        [Test] public void WhiteDefaultAndExactOldPinkRequireExplicitSave()
        {
            Assert.AreEqual(new Color(.99f,.975f,.95f,1), SubtitleTextColor.Current);
            Click("Subtitle Classic Pink");
            Assert.AreEqual(new Color(.98f,.62f,.78f,1), Field<TMP_Text>("subtitleColorPreview").color);
            Assert.IsFalse(PresentationPreferences.HasKey(SubtitleTextColor.Preference));
            Click("Subtitle Save Color");
            Assert.AreEqual(new Color(.98f,.62f,.78f,1), SubtitleTextColor.Current);
            Assert.AreEqual("pink", PresentationPreferences.GetString(SubtitleTextColor.Preference));
            Click("Subtitle Current White"); Click("Subtitle Cancel Color");
            Assert.AreEqual("pink", Field<TMP_InputField>("subtitleColorInput").text);
        }
        [Test] public void CustomHexPersistsAndReloadsWithoutSavingOtherChoices()
        {
            PresentationPreferences.SetString("synthetic-unrelated", "untouched");
            Field<TMP_InputField>("subtitleColorInput").text = "b8e6ff";
            Click("Subtitle Save Color");
            Assert.AreEqual("#B8E6FF", SubtitleTextColor.Saved);
            typeof(AIFrenPocController).GetMethod("CloseSettingsPanel", Private).Invoke(controller, null);
            Assert.AreEqual("#B8E6FF", Field<TMP_InputField>("subtitleColorInput").text);
            Assert.AreEqual(new Color(184/255f,230/255f,1,1), SubtitleTextColor.Current);
            Click("Subtitle Reset Color"); Assert.AreEqual("#B8E6FF", SubtitleTextColor.Saved);
            Click("Subtitle Save Color"); Assert.IsFalse(PresentationPreferences.HasKey(SubtitleTextColor.Preference));
            Assert.AreEqual("untouched", PresentationPreferences.GetString("synthetic-unrelated"));
        }
        [TestCase("#123")][TestCase("#FFFFFF00")][TestCase("garbage")][TestCase("<red>")][TestCase("")]
        public void InvalidDraftAndPersistedValueFailLocally(string bad)
        {
            SubtitleTextColor.Save("pink");
            Field<TMP_InputField>("subtitleColorInput").SetTextWithoutNotify(bad);
            typeof(AIFrenPocController).GetMethod("RefreshSubtitleColorPreview", Private).Invoke(controller,null);
            Assert.IsFalse(Field<Button>("subtitleColorSave").interactable);
            Click("Subtitle Save Color"); Assert.AreEqual("pink", SubtitleTextColor.Saved);
            PresentationPreferences.SetString(SubtitleTextColor.Preference, bad);
            Assert.AreEqual(SubtitleStyle.Face, SubtitleTextColor.Current);
            Assert.AreEqual(bad, PresentationPreferences.GetString(SubtitleTextColor.Preference), "reading invalid values never rewrites settings");
        }
        [Test] public void QaCopyRetainsColorInIsolatedStore()
        {
            NativeQaSession.ApplyPresentation(new[] {new NativeQaSession.Preference {
                name=SubtitleTextColor.Preference,type="string",value="pink"}});
            Assert.IsTrue(PresentationPreferences.IsIsolated);
            Assert.AreEqual(SubtitleTextColor.ClassicPink, SubtitleTextColor.Current);
        }
        [Test] public void RecreatedControlsLoadSavedChoiceWithoutBorrowingNormalKeys()
        {
            Field<TMP_InputField>("subtitleColorInput").text = "#B8E6FF";
            Click("Subtitle Save Color");
            Object.DestroyImmediate(root);
            root = new GameObject("restarted color controls", typeof(RectTransform), typeof(Canvas), typeof(AIFrenPocController));
            controller = root.GetComponent<AIFrenPocController>(); controller.enabled = false;
            typeof(AIFrenPocController).GetField("font",Private).SetValue(controller,TMP_Settings.defaultFontAsset);
            typeof(AIFrenPocController).GetField("theme",Private).SetValue(controller,PresentationThemes.Dark);
            typeof(AIFrenPocController).GetMethod("AddSubtitleColorControls",Private).Invoke(controller,new object[]{root.transform,-20f});
            Assert.AreEqual("#B8E6FF",Field<TMP_InputField>("subtitleColorInput").text);
            Assert.AreEqual(SubtitleTextColor.Current,Field<TMP_Text>("subtitleColorPreview").color);
            Assert.IsTrue(PresentationPreferences.IsIsolated);
        }
    }
}
