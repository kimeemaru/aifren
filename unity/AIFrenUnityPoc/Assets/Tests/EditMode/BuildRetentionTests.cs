using System;
using System.IO;
using System.Linq;
using System.Reflection;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class BuildRetentionTests
    {
        [TestCase(true)]
        [TestCase(false)]
        public void ActualBuildOwnerPreservesLastCompletedTargetUntilSuccess(bool fail)
        {
            string root = Path.Combine(Path.GetTempPath(), "aifren-build-retention-" + Guid.NewGuid().ToString("N"));
            string target = Path.Combine(root, "LinuxDevelopment"); Directory.CreateDirectory(target);
            File.WriteAllText(Path.Combine(target, "player"), "last usable build");
            try
            {
                Type owner = AppDomain.CurrentDomain.GetAssemblies().Select(assembly => assembly.GetType(
                    "AIFren.UnityPoc.Editor.BuildAIFrenPoc")).Single(type => type != null);
                MethodInfo build = owner.GetMethod("BuildWithLastGoodRetention");
                Action<string> injected = staging => {
                    Assert.That(File.ReadAllText(Path.Combine(target, "player")), Is.EqualTo("last usable build"));
                    File.WriteAllText(Path.Combine(staging, "player"), "new build");
                    if (fail) throw new IOException("synthetic build failure");
                };
                if (fail) Assert.Throws<TargetInvocationException>(() => build.Invoke(null, new object[] { target, "player", injected }));
                else build.Invoke(null, new object[] { target, "player", injected });
                Assert.That(File.ReadAllText(Path.Combine(target, "player")), Is.EqualTo(fail ? "last usable build" : "new build"));
                Assert.That(Directory.GetDirectories(root), Has.Length.EqualTo(1));
            }
            finally { Directory.Delete(root, true); }
        }
    }
}
