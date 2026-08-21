using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarPresentationStateTests
    {
        private static readonly string[] Keys =
        {
            "AIFren.AvatarPresentation.Portrait.X", "AIFren.AvatarPresentation.Portrait.Y", "AIFren.AvatarPresentation.Portrait.Scale",
            "AIFren.AvatarPresentation.Landscape.X", "AIFren.AvatarPresentation.Landscape.Y", "AIFren.AvatarPresentation.Landscape.Scale"
        };
        private readonly bool[] hadValues = new bool[6];
        private readonly float[] savedValues = new float[6];

        [SetUp]
        public void SetUp()
        {
            for (int index = 0; index < Keys.Length; index++)
            {
                hadValues[index] = PlayerPrefs.HasKey(Keys[index]);
                savedValues[index] = PlayerPrefs.GetFloat(Keys[index]);
            }
            AvatarPresentationState.DeletePersistedValues();
        }

        [TearDown]
        public void TearDown()
        {
            AvatarPresentationState.DeletePersistedValues();
            for (int index = 0; index < Keys.Length; index++)
                if (hadValues[index]) PlayerPrefs.SetFloat(Keys[index], savedValues[index]);
            PlayerPrefs.Save();
        }

        [Test]
        public void PortraitAndLandscapePersistIndependently()
        {
            AvatarPresentationState state = AvatarPresentationState.Load(new AvatarConfiguration());
            state.SetValues(true, new AvatarPresentationValues { x = .2f, y = -.3f, scale = 1.4f }, true);
            state.SetValues(false, new AvatarPresentationValues { x = -.4f, y = .1f, scale = 1.2f }, true);
            AvatarPresentationState reloaded = AvatarPresentationState.Load(new AvatarConfiguration());
            Assert.AreEqual(.2f, reloaded.GetValues(true).x);
            Assert.AreEqual(-.4f, reloaded.GetValues(false).x);
        }

        [Test]
        public void ResetRestoresTheAuthoredOrientationDefault()
        {
            AvatarConfiguration configuration = new AvatarConfiguration
            {
                portraitPresentation = new AvatarPresentationTransform { x = .1f, y = -.2f, scale = 1.3f }
            };
            AvatarPresentationState state = AvatarPresentationState.Load(configuration);
            state.SetValues(true, new AvatarPresentationValues { x = 1f, y = 1f, scale = 2f }, false);
            state.Reset(true, false);
            AvatarPresentationValues restored = state.GetValues(true);
            Assert.AreEqual(.1f, restored.x); Assert.AreEqual(-.2f, restored.y); Assert.AreEqual(1.3f, restored.scale);
        }

        [Test]
        public void RestoringAnUnsavedSnapshotLeavesSavedValuesUnchanged()
        {
            AvatarPresentationState state = AvatarPresentationState.Load(new AvatarConfiguration());
            AvatarPresentationValues saved = new AvatarPresentationValues { x = .2f, y = -.1f, scale = 1.25f };
            state.SetValues(true, saved, true);

            state.SetValues(true, new AvatarPresentationValues { x = .8f, y = .7f, scale = 2f }, false);
            state.SetValues(true, saved, false);

            AvatarPresentationValues reloaded = AvatarPresentationState.Load(new AvatarConfiguration()).GetValues(true);
            Assert.AreEqual(saved.x, reloaded.x);
            Assert.AreEqual(saved.y, reloaded.y);
            Assert.AreEqual(saved.scale, reloaded.scale);
        }

        [Test]
        public void ScaleSupportsTheDirectViewerCloseZoomRange()
        {
            AvatarPresentationState state = AvatarPresentationState.Load(new AvatarConfiguration());
            state.SetValues(true, new AvatarPresentationValues { scale = 99f }, false);

            Assert.AreEqual(AvatarPresentationTransform.MaximumScale, state.GetValues(true).scale);
        }

        [Test]
        public void TranslationCoversTheFullDirectViewRangeAndRejectsNonFiniteValues()
        {
            AvatarPresentationState state = AvatarPresentationState.Load(new AvatarConfiguration());
            state.SetValues(true, new AvatarPresentationValues { x = 99f, y = -99f, scale = float.NaN }, false);
            AvatarPresentationValues clamped = state.GetValues(true);

            Assert.AreEqual(AvatarPresentationTransform.MaximumTranslation, clamped.x);
            Assert.AreEqual(-AvatarPresentationTransform.MaximumTranslation, clamped.y);
            Assert.AreEqual(1f, clamped.scale);
        }
    }
}
