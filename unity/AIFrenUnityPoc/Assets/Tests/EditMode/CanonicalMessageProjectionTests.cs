using System.Collections.Generic;
using NUnit.Framework;
using AIFren.UnityPoc.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class CanonicalMessageProjectionTests
    {
        [Test]
        public void SnapshotThenEchoAndEchoThenSnapshotRemainIdentityStable()
        {
            var snapshotFirst = new HashSet<string> { "41:timestamp" };
            Assert.That(CanonicalMessageProjection.TryAdmit(snapshotFirst, "41:timestamp"), Is.False);

            var echoFirst = new HashSet<string>();
            Assert.That(CanonicalMessageProjection.TryAdmit(echoFirst, "41:timestamp"), Is.True);
            echoFirst.Clear();
            echoFirst.Add("41:timestamp");
            Assert.That(CanonicalMessageProjection.TryAdmit(echoFirst, "41:timestamp"), Is.False);
        }

        [Test]
        public void IdenticalTextIsNotAKeyAndDistinctCanonicalIdsRemainDistinct()
        {
            var ids = new HashSet<string>();
            Assert.That(CanonicalMessageProjection.TryAdmit(ids, "10:t"), Is.True);
            Assert.That(CanonicalMessageProjection.TryAdmit(ids, "11:t"), Is.True);
        }
    }
}
