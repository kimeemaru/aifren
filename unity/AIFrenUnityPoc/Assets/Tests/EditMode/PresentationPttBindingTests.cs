using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class PresentationPttBindingTests
    {
        [Test]
        public void DefaultAndMouseThumbBindingsRoundTrip()
        {
            Assert.AreEqual(KeyCode.Mouse4, PresentationPttBinding.Load("invalid"));
            Assert.AreEqual(KeyCode.Mouse3, PresentationPttBinding.Load(PresentationPttBinding.Save(KeyCode.Mouse3)));
        }

        [Test]
        public void EscapeIsNotAcceptedAsPushToTalk()
        {
            Assert.IsFalse(PresentationPttBinding.IsValid(KeyCode.Escape));
        }

        [Test]
        public void PressedPttReleasesWhenFocusOrHeldStateIsLost()
        {
            Assert.IsFalse(PresentationPttInputPolicy.ShouldStart(false, true));
            Assert.IsTrue(PresentationPttInputPolicy.ShouldStart(true, true));
            Assert.IsTrue(PresentationPttInputPolicy.ShouldRelease(true, false, true));
            Assert.IsTrue(PresentationPttInputPolicy.ShouldRelease(true, true, false));
            Assert.IsFalse(PresentationPttInputPolicy.ShouldRelease(true, true, true));
            Assert.IsFalse(PresentationPttInputPolicy.ShouldRelease(false, false, false));
        }
    }
}
