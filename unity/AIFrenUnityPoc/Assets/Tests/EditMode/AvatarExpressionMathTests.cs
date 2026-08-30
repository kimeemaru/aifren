using AIFren.UnityPoc.Avatar;
using NUnit.Framework;
using UniVRM10;
using UnityEngine;
using System.Reflection;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AvatarExpressionMathTests
    {
        [TestCase(ExpressionPreset.happy, AvatarExpressionCategory.Emotion)]
        [TestCase(ExpressionPreset.angry, AvatarExpressionCategory.Emotion)]
        [TestCase(ExpressionPreset.aa, AvatarExpressionCategory.Mouth)]
        [TestCase(ExpressionPreset.blinkLeft, AvatarExpressionCategory.Blink)]
        [TestCase(ExpressionPreset.lookRight, AvatarExpressionCategory.LookAt)]
        [TestCase(ExpressionPreset.neutral, AvatarExpressionCategory.Neutral)]
        [TestCase(ExpressionPreset.custom, AvatarExpressionCategory.Custom)]
        public void CategoriesPreserveUniVrmChannelMeaning(ExpressionPreset preset, AvatarExpressionCategory expected)
        {
            Assert.AreEqual(expected, AvatarExpressionMath.CategoryFor(preset));
        }

        [Test]
        public void BlendWeightMovesContinuouslyTowardTarget()
        {
            Assert.AreEqual(.25f, AvatarExpressionMath.BlendWeight(0f, 1f, .05f, .20f), .0001f);
            Assert.AreEqual(.75f, AvatarExpressionMath.BlendWeight(1f, 0f, .05f, .20f), .0001f);
        }

        [Test]
        public void BlendWeightDoesNotAutoResetWithoutAnExplicitTargetChange()
        {
            Assert.AreEqual(.6f, AvatarExpressionMath.BlendWeight(.6f, .6f, 10f, .20f), .0001f);
        }

        [TestCase(ExpressionPreset.happy, true)]
        [TestCase(ExpressionPreset.custom, true)]
        [TestCase(ExpressionPreset.aa, false)]
        [TestCase(ExpressionPreset.blink, false)]
        [TestCase(ExpressionPreset.lookLeft, false)]
        public void ProceduralPresetsAreNotPersistentExpressionTargets(ExpressionPreset preset, bool expected)
        {
            Assert.AreEqual(expected, AvatarExpressionMath.IsPersistentExpressionPreset(preset));
        }

        [Test]
        public void BundledStyleVrmRequestsControlRigBeforeRuntimeCreation()
        {
            GameObject root = new GameObject("vrm-control-rig-test");
            try
            {
                Vrm10Instance instance = root.AddComponent<Vrm10Instance>();
                MethodInfo setup = typeof(AvatarLoader).GetMethod("EnsurePresentationControlRig", BindingFlags.Static | BindingFlags.NonPublic);
                FieldInfo runtime = typeof(Vrm10Instance).GetField("m_runtime", BindingFlags.Instance | BindingFlags.NonPublic);
                FieldInfo useControlRig = typeof(Vrm10Instance).GetField("m_useControlRig", BindingFlags.Instance | BindingFlags.NonPublic);
                Assert.NotNull(setup);
                Assert.IsNull(runtime.GetValue(instance));

                setup.Invoke(null, new object[] { instance });

                Assert.IsTrue((bool)useControlRig.GetValue(instance));
                Assert.IsNull(runtime.GetValue(instance));
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }
    }
}
