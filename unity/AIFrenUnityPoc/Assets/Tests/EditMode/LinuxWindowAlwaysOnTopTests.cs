using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class LinuxWindowAlwaysOnTopTests
    {
        [Test]
        public void RecognizesX11AndRejectsWaylandSessions()
        {
            Assert.IsTrue(LinuxWindowAlwaysOnTop.IsX11Session(":0", "x11"));
            Assert.IsFalse(LinuxWindowAlwaysOnTop.IsX11Session(":0", "wayland"));
            Assert.IsFalse(LinuxWindowAlwaysOnTop.IsX11Session(string.Empty, "x11"));
        }
    }
}
