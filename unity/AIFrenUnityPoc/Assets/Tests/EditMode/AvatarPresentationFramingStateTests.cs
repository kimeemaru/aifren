using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarPresentationFramingStateTests
    {
        [SetUp]
        public void SetUp()
        {
            AvatarPresentationFramingState.DeleteAllPersisted();
            PlayerPrefs.Save();
        }

        [TearDown]
        public void TearDown()
        {
            AvatarPresentationFramingState.DeleteAllPersisted();
            PlayerPrefs.Save();
        }

        [Test]
        public void FreshStateUsesIndependentAuthoredDefaults()
        {
            AvatarConfiguration configuration = ConfigurationWithDistinctDefaults();
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(configuration);

            AvatarPresentationFramingValues portrait = state.GetValues(true);
            AvatarPresentationFramingValues landscape = state.GetValues(false);

            Assert.AreEqual(1.25f, portrait.zoom);
            Assert.AreEqual(1.10f, landscape.zoom);
            Assert.AreEqual(0f, portrait.panX);
            Assert.AreEqual(0f, portrait.panY);
            Assert.AreEqual(0f, landscape.panX);
            Assert.AreEqual(0f, landscape.panY);
        }

        [Test]
        public void SaveLoadRoundTripPreservesPortraitAndLandscapeSeparately()
        {
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.1f);
            state.SetValue(true, AvatarPresentationFramingField.HorizontalPan, -.35f);
            state.SetValue(true, AvatarPresentationFramingField.VerticalPan, .20f);
            state.SetValue(false, AvatarPresentationFramingField.Zoom, 1.7f);
            state.SetValue(false, AvatarPresentationFramingField.HorizontalPan, .40f);
            state.SetValue(false, AvatarPresentationFramingField.VerticalPan, -.15f);

            AvatarPresentationFramingState loaded = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());

            Assert.AreEqual(2.1f, loaded.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(-.35f, loaded.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(.20f, loaded.GetValue(true, AvatarPresentationFramingField.VerticalPan));
            Assert.AreEqual(1.7f, loaded.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(.40f, loaded.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(-.15f, loaded.GetValue(false, AvatarPresentationFramingField.VerticalPan));
        }

        [Test]
        public void ChangingOneOrientationDoesNotMutateTheOther()
        {
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.3f);
            state.SetValue(true, AvatarPresentationFramingField.HorizontalPan, .6f);

            Assert.AreEqual(1.10f, state.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, state.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(0f, state.GetValue(false, AvatarPresentationFramingField.VerticalPan));
        }

        [Test]
        public void ResetPortraitRestoresAuthoredZoomAndZeroUserPan()
        {
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.3f);
            state.SetValue(true, AvatarPresentationFramingField.HorizontalPan, .6f);
            state.SetValue(true, AvatarPresentationFramingField.VerticalPan, -.4f);
            state.Reset(true);

            Assert.AreEqual(1.25f, state.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, state.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(0f, state.GetValue(true, AvatarPresentationFramingField.VerticalPan));
        }

        [Test]
        public void ResetLandscapeDoesNotChangePortraitAndRestoresItsOwnDefaults()
        {
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            state.SetValue(true, AvatarPresentationFramingField.HorizontalPan, -.3f);
            state.SetValue(false, AvatarPresentationFramingField.Zoom, 2.2f);
            state.SetValue(false, AvatarPresentationFramingField.VerticalPan, .5f);
            state.Reset(false);

            Assert.AreEqual(1.10f, state.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, state.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(0f, state.GetValue(false, AvatarPresentationFramingField.VerticalPan));
            Assert.AreEqual(-.3f, state.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
        }

        [Test]
        public void ResetAllMatchesFreshStateAndDoesNotTreatAlignmentAsUserPan()
        {
            AvatarConfiguration configuration = ConfigurationWithDistinctDefaults();
            configuration.portraitAlignmentOffset = new AvatarVector2 { x = .12f, y = .08f };
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(configuration);
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.5f);
            state.SetValue(true, AvatarPresentationFramingField.HorizontalPan, -.5f);
            state.SetValue(false, AvatarPresentationFramingField.VerticalPan, .5f);
            state.ResetAll();

            AvatarPresentationFramingState reloaded = AvatarPresentationFramingState.Load(configuration);
            Assert.AreEqual(1.25f, reloaded.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(1.10f, reloaded.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, reloaded.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(0f, reloaded.GetValue(false, AvatarPresentationFramingField.VerticalPan));

            Rect resolved = reloaded.Resolve(true);
            Assert.AreEqual(.62f, resolved.center.x, .0001f);
            Assert.AreEqual(.58f, resolved.center.y, .0001f);
        }

        [Test]
        public void ResetUiPersistenceDeletionRestoresFreshDefaultsForBothOrientations()
        {
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.2f);
            state.SetValue(false, AvatarPresentationFramingField.HorizontalPan, -.75f);
            AvatarPresentationFramingState.DeleteAllPersisted();
            PlayerPrefs.Save();

            AvatarPresentationFramingState fresh = AvatarPresentationFramingState.Load(ConfigurationWithDistinctDefaults());
            Assert.AreEqual(1.25f, fresh.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(1.10f, fresh.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, fresh.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(0f, fresh.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
        }

        [Test]
        public void TransientSessionEditsDoNotPersistUntilCommit()
        {
            AvatarConfiguration configuration = ConfigurationWithDistinctDefaults();
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(configuration);
            state.SetValues(true, new AvatarPresentationFramingValues
            {
                zoom = 2.15f,
                panX = .42f,
                panY = -.18f
            }, false);

            AvatarPresentationFramingState uncommittedReload = AvatarPresentationFramingState.Load(configuration);
            Assert.AreEqual(1.25f, uncommittedReload.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(0f, uncommittedReload.GetValue(true, AvatarPresentationFramingField.HorizontalPan));

            state.Commit(true);
            AvatarPresentationFramingState committedReload = AvatarPresentationFramingState.Load(configuration);
            Assert.AreEqual(2.15f, committedReload.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(.42f, committedReload.GetValue(true, AvatarPresentationFramingField.HorizontalPan));
            Assert.AreEqual(-.18f, committedReload.GetValue(true, AvatarPresentationFramingField.VerticalPan));
        }

        [Test]
        public void CancelCanRestoreSnapshotWithoutChangingPersistedState()
        {
            AvatarConfiguration configuration = ConfigurationWithDistinctDefaults();
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(configuration);
            state.SetValue(false, AvatarPresentationFramingField.Zoom, 1.7f);
            state.SetValue(false, AvatarPresentationFramingField.HorizontalPan, -.25f);
            AvatarPresentationFramingValues snapshot = state.GetValues(false);

            state.SetValue(false, AvatarPresentationFramingField.Zoom, 2.8f, false);
            state.SetValue(false, AvatarPresentationFramingField.HorizontalPan, .8f, false);
            state.SetValues(false, snapshot, false);

            Assert.AreEqual(1.7f, state.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(-.25f, state.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
            AvatarPresentationFramingState reloaded = AvatarPresentationFramingState.Load(configuration);
            Assert.AreEqual(1.7f, reloaded.GetValue(false, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(-.25f, reloaded.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
        }

        [Test]
        public void PortraitSessionSnapshotRestoresWithoutCrossWritingLandscape()
        {
            AvatarConfiguration configuration = ConfigurationWithDistinctDefaults();
            AvatarPresentationFramingState state = AvatarPresentationFramingState.Load(configuration);
            state.SetValue(true, AvatarPresentationFramingField.Zoom, 1.9f);
            state.SetValue(true, AvatarPresentationFramingField.VerticalPan, .3f);
            state.SetValue(false, AvatarPresentationFramingField.HorizontalPan, -.4f);
            AvatarPresentationFramingValues portraitSnapshot = state.GetValues(true);

            state.SetValue(true, AvatarPresentationFramingField.Zoom, 2.7f, false);
            state.SetValue(true, AvatarPresentationFramingField.VerticalPan, -.6f, false);
            state.SetValues(true, portraitSnapshot, false);

            Assert.AreEqual(1.9f, state.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(.3f, state.GetValue(true, AvatarPresentationFramingField.VerticalPan));
            Assert.AreEqual(-.4f, state.GetValue(false, AvatarPresentationFramingField.HorizontalPan));
            AvatarPresentationFramingState reloaded = AvatarPresentationFramingState.Load(configuration);
            Assert.AreEqual(1.9f, reloaded.GetValue(true, AvatarPresentationFramingField.Zoom));
            Assert.AreEqual(.3f, reloaded.GetValue(true, AvatarPresentationFramingField.VerticalPan));
        }

        private static AvatarConfiguration ConfigurationWithDistinctDefaults()
        {
            return new AvatarConfiguration
            {
                portraitDefaultZoom = 1.25f,
                landscapeDefaultZoom = 1.10f
            };
        }
    }
}
