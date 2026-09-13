using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System;
using AIFren.UnityPoc.Avatar;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CharacterFramingTests
    {
        private string first, second;
        private AvatarConfiguration configuration;
        private string asset, another;
        private bool hadLegacy;
        private float oldLegacy;

        [SetUp]
        public void SetUp()
        {
            first = Guid.NewGuid().ToString("D"); second = Guid.NewGuid().ToString("D");
            configuration = new AvatarConfiguration { portraitPresentation = new AvatarPresentationTransform { x = .1f, scale = 1.2f } };
            asset = AvatarPresentationState.ManagedAssetIdentity(new string('a', 64));
            another = AvatarPresentationState.ManagedAssetIdentity(new string('b', 64));
            hadLegacy = PlayerPrefs.HasKey("AIFren.AvatarPresentation.Portrait.X");
            oldLegacy = PlayerPrefs.GetFloat("AIFren.AvatarPresentation.Portrait.X");
        }

        [TearDown]
        public void TearDown()
        {
            CharacterAvatarPreference.Delete(first); CharacterAvatarPreference.Delete(second);
            if (hadLegacy) PlayerPrefs.SetFloat("AIFren.AvatarPresentation.Portrait.X", oldLegacy);
            else PlayerPrefs.DeleteKey("AIFren.AvatarPresentation.Portrait.X");
            PlayerPrefs.Save();
        }

        private AvatarPresentationState Load(string character, string visual = null) =>
            AvatarPresentationState.LoadForCharacter(configuration, character, visual ?? asset);
        private static AvatarPresentationValues Values(float x) => new AvatarPresentationValues { x = x, y = -.2f, scale = 1.5f };
        private static string Key(string character) => "AIFren.CharacterPresentation.v1." + character;

        [Test]
        public void SharedAssetDoesNotShareCharacterOrLayoutValues()
        {
            Load(first).SetValues(true, Values(.3f), true);
            Load(second).SetValues(true, Values(-.4f), true);
            Load(first).SetValues(false, Values(.5f), true);
            Assert.That(Load(first).GetValues(true).x, Is.EqualTo(.3f));
            Assert.That(Load(first).GetValues(false).x, Is.EqualTo(.5f));
            Assert.That(Load(second).GetValues(true).x, Is.EqualTo(-.4f));
            Assert.That(Load(second).GetValues(false).x, Is.Zero);
        }

        [Test]
        public void SameCharacterRestoresFramingPerStableVisualIdentity()
        {
            Load(first).SetValues(true, Values(.3f), true);
            Load(first, another).SetValues(true, Values(.7f), true);
            Assert.That(Load(first).GetValues(true).x, Is.EqualTo(.3f));
            Assert.That(Load(first, another).GetValues(true).x, Is.EqualTo(.7f));
        }

        [Test]
        public void ExistingGlobalValuesRemainUnattributedAndUnchanged()
        {
            PlayerPrefs.SetFloat("AIFren.AvatarPresentation.Portrait.X", .9f);
            Assert.That(Load(first).GetValues(true).x, Is.EqualTo(.1f));
            Load(first).SetValues(true, Values(.3f), true);
            Assert.That(PlayerPrefs.GetFloat("AIFren.AvatarPresentation.Portrait.X"), Is.EqualTo(.9f));
            Assert.That(AvatarPresentationState.Load(configuration).GetValues(true).x, Is.EqualTo(.9f), "Legacy read remains explicit compatibility.");
        }

        [Test]
        public void UnboundStateUsesAuthoredDefaultsAndCannotPersist()
        {
            PlayerPrefs.SetFloat("AIFren.AvatarPresentation.Portrait.X", .9f);
            var state = AvatarPresentationState.CreateUnbound(configuration);
            Assert.That(state.GetValues(true).x, Is.EqualTo(.1f));
            Assert.Throws<InvalidOperationException>(() => state.Commit(true));
        }

        [Test]
        public void SaveRereadsOtherAssetAndLayoutInsteadOfOverwritingOpenDraft()
        {
            var openDraft = Load(first);
            Load(first, another).SetValues(false, Values(.8f), true);
            Load(first).SetValues(false, Values(.4f), true);
            openDraft.SetValues(true, Values(.3f), true);
            Assert.That(Load(first, another).GetValues(false).x, Is.EqualTo(.8f));
            Assert.That(Load(first).GetValues(false).x, Is.EqualTo(.4f));
        }

        [Test]
        public void UnsavedChangesAndResetDoNotRewritePersistedValues()
        {
            var state = Load(first); state.SetValues(true, Values(.3f), true);
            state.SetValues(true, Values(.8f), false); state.Reset(true, false);
            Assert.That(state.GetValues(true).x, Is.EqualTo(.1f));
            Assert.That(Load(first).GetValues(true).x, Is.EqualTo(.3f));
        }

        [Test]
        public void DeleteCharacterRemovesOnlyItsOneOwnedFramingRecord()
        {
            Load(first).SetValues(true, Values(.3f), true);
            Load(first, another).SetValues(false, Values(.7f), true);
            Load(second).SetValues(true, Values(.4f), true);
            PlayerPrefs.SetFloat("AIFren.AvatarPresentation.Portrait.X", .9f);
            CharacterAvatarPreference.Delete(first);
            Assert.That(PlayerPrefs.HasKey(Key(first)), Is.False);
            Assert.That(Load(second).GetValues(true).x, Is.EqualTo(.4f));
            Assert.That(PlayerPrefs.GetFloat("AIFren.AvatarPresentation.Portrait.X"), Is.EqualTo(.9f));
        }

        [TestCase("not json")]
        [TestCase("{\"version\":2,\"avatars\":[]}")]
        [TestCase("{\"version\":1,\"avatars\":[{\"asset\":\"/external/model.vrm\"}]}")]
        public void UnknownOrCorruptRecordIsNotOverwritten(string invalid)
        {
            PlayerPrefs.SetString(Key(first), invalid);
            var state = Load(first);
            Assert.That(state.GetValues(true).x, Is.EqualTo(.1f));
            Assert.Throws<InvalidOperationException>(() => state.Commit(true));
            Assert.That(PlayerPrefs.GetString(Key(first)), Is.EqualTo(invalid));
        }

        [Test]
        public void NonFiniteValuesRemainNormalized()
        {
            var state = Load(first);
            state.SetValues(true, new AvatarPresentationValues { x = float.NaN, y = float.PositiveInfinity, scale = float.NegativeInfinity }, true);
            var reloaded = Load(first).GetValues(true);
            Assert.That(reloaded.x, Is.Zero); Assert.That(reloaded.y, Is.Zero); Assert.That(reloaded.scale, Is.EqualTo(1f));
        }

        [Test]
        public void StableBundledResourceIdentityIsDistinctFromManagedHash()
        {
            string bundled = AvatarPresentationState.BundledAssetIdentity(configuration);
            Assert.That(bundled, Is.EqualTo(AvatarPresentationState.BundledAssetIdentity(new AvatarConfiguration())));
            configuration.avatarResourcePath = "Synthetic/OtherAvatar";
            Assert.That(AvatarPresentationState.BundledAssetIdentity(configuration), Is.Not.EqualTo(bundled));
            Assert.Throws<ArgumentException>(() => AvatarPresentationState.LoadForCharacter(configuration, "display name", asset));
            Assert.Throws<ArgumentException>(() => AvatarPresentationState.LoadForCharacter(configuration, first, "/model.vrm"));
        }
    }
}
