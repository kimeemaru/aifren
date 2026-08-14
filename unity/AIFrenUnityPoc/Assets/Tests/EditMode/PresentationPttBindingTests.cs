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
    }
}
