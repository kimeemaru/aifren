using NUnit.Framework;
using AIFren.UnityPoc.Avatar;
using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;

namespace AIFren.UnityPoc.Tests.EditMode
{
    [SetUpFixture]
    public sealed class NativePreferenceBoundary
    {
        [OneTimeSetUp]
        public void RequireIsolatedPreferencesBeforeAnyNativeFixture()
        {
            Assert.That(PlayerPrefs.IsIsolated, Is.True,
                "Run native tests with the reviewed -runTests route; never borrow normal PlayerPrefs.");
        }
    }

    public sealed class PreferenceIsolationTests
    {
        [Test]
        public void ExistingBackgroundFixturePreservesEverySavedField()
        {
            const string portrait = "AIFren.AvatarViewerBackground.CustomPath.Portrait";
            const string landscape = "AIFren.AvatarViewerBackground.CustomPath.Landscape";
            PlayerPrefs.SetString(portrait, "/synthetic/portrait.png");
            PlayerPrefs.SetString(landscape, "/synthetic/landscape.png");
            try
            {
                var fixture = new AvatarViewerBackgroundStateTests();
                fixture.SetUp();
                try { fixture.PersistsCustomImagePathsIndependently(); }
                finally { fixture.TearDown(); }
                Assert.That(PlayerPrefs.GetString(portrait), Is.EqualTo("/synthetic/portrait.png"));
                Assert.That(PlayerPrefs.GetString(landscape), Is.EqualTo("/synthetic/landscape.png"));
            }
            finally { PlayerPrefs.DeleteKey(portrait); PlayerPrefs.DeleteKey(landscape); }
        }

        [Test]
        public void SaveResetAndFailedFixtureStayInsideProcessLocalStorage()
        {
            const string key = "AIFren.TestOnly.Sentinel";
            Assert.That(PlayerPrefs.IsIsolated, Is.True);
            PlayerPrefs.SetString(key, "synthetic sentinel"); PlayerPrefs.Save();
            try
            {
                try { AvatarPresentationState.DeletePersistedValues(); throw new System.Exception("synthetic failure"); }
                catch (System.Exception) { }
                Assert.That(PlayerPrefs.GetString(key), Is.EqualTo("synthetic sentinel"));
            }
            finally { PlayerPrefs.DeleteKey(key); PlayerPrefs.Save(); }
        }
    }
}
