using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UniVRM10;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ActPresentationTests
    {
        [Test] public void ExplicitActChannelsWinAndOmittedChannelsRetainEmoteCompatibility()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Reply(1, "*smiles and nods* Yes.", new PresentationMetadata { origin="act", emotion="sad", intensity=.4f, has_intensity=true });
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.EqualTo(40).Within(.1));
            Assert.That(f.Body.ActiveGesture, Is.EqualTo(AvatarGestureIntent.Nod));
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("act"));
            f.Reply(2, "*smiles* Yes.", new PresentationMetadata { origin="act", gesture="agreement" });
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("explicit_emote"));
        }

        [Test] public void RepeatedTargetAndDuplicateFinalDoNotRestartButNeutralAlwaysClears()
        {
            using var f = new FacialEmoteProjectionTests.Fixture();
            var cue = new PresentationMetadata { origin="act", emotion="happy", intensity=.6f, has_intensity=true, gesture="agreement" };
            f.Reply(1, "Yes.", cue); int revision = f.Face.TargetRevision;
            f.Owner.Begin(1); // duplicate announcement cannot reopen a published turn
            Assert.False(f.Owner.PublishFinal(1, f.Resolver, cue, "Yes.", "Mira"));
            f.Reply(2, "Okay.", cue);
            Assert.AreEqual(revision, f.Face.TargetRevision);
            f.Body.StopSpeech(); f.Body.RetireResponseMotion(); f.Tick(.4f);
            f.Reply(3, "I am listening.");
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.6f).Within(.001));
            f.Reply(4, "Okay.", new PresentationMetadata { origin="act", emotion="neutral" });
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
        }

        [Test] public void RapidRetargetBlendsEverySemanticChannelAndLeavesProceduralWeightsAlone()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Resolver.ApplyReply(new PresentationMetadata { emotion="happy" }, AvatarGestureIntent.None); f.Tick(.05f);
            float first=f.Renderer.GetBlendShapeWeight(0); Assert.That(first, Is.InRange(.1f,69f));
            f.Resolver.ApplyReply(new PresentationMetadata { emotion="sad" }, AvatarGestureIntent.None); f.Tick(.025f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.LessThan(first));
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.GreaterThan(0));
            f.Resolver.ApplyReply(new PresentationMetadata { emotion="angry" }, AvatarGestureIntent.None); f.Tick(.025f);
            Assert.That(f.Renderer.GetBlendShapeWeight(2), Is.GreaterThan(0));
            f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            Assert.That(f.Renderer.GetBlendShapeWeight(1), Is.Zero);
            f.Runtime.SetWeight(new ExpressionKey(ExpressionPreset.aa), .4f);
            f.Runtime.SetWeight(new ExpressionKey(ExpressionPreset.blink), .3f);
            f.Face.ClearSemanticExpression(); f.Tick(.4f);
            Assert.That(f.Runtime.GetWeight(new ExpressionKey(ExpressionPreset.aa)), Is.EqualTo(.4f));
            Assert.That(f.Runtime.GetWeight(new ExpressionKey(ExpressionPreset.blink)), Is.EqualTo(.3f));
            Assert.That(f.Renderer.GetBlendShapeWeight(2), Is.Zero);
        }

        [Test] public void ManualChoiceWinsUntilExplicitResetAndCancelledReplacementCannotDispatch()
        {
            using var f = new FacialEmoteProjectionTests.Fixture(true);
            f.Reply(1, "Yes.", new PresentationMetadata { emotion="happy" });
            f.Face.SetExpression(f.Face.ActiveExpression.Id, .25f); f.Tick(.4f);
            f.Reply(2, "Okay.", new PresentationMetadata { origin="act", emotion="neutral" });
            Assert.True(f.Face.ManualOverride); Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f).Within(.001));
            f.Reply(3, "Okay.", new PresentationMetadata { origin="act", emotion="sad" });
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f).Within(.001));
            f.Face.ClearExpression(); f.Tick(.4f);
            f.Owner.Begin(4); f.Owner.Retire(4); f.Owner.Begin(5);
            Assert.False(f.Owner.PublishFinal(4, f.Resolver, new PresentationMetadata { origin="act", emotion="angry" }, "Late.", "Mira"));
            Assert.True(f.Owner.PublishFinal(5, f.Resolver, new PresentationMetadata { origin="act", emotion="happy" }, "Current.", "Mira"));
            f.Tick(.4f); Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.7f).Within(.001));
        }

        [Test] public void SettingIsOffUntilSaveAcknowledgementAndCancelDoesNotWritePreferences()
        {
            Assert.True(PresentationPreferences.IsIsolated);
            var root=new GameObject("synthetic cue controls", typeof(RectTransform), typeof(Canvas), typeof(AIFrenPocController));
            const BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
            try
            {
                var c=root.GetComponent<AIFrenPocController>(); c.enabled=false;
                typeof(AIFrenPocController).GetField("theme",flags).SetValue(c,PresentationThemes.Dark);
                typeof(AIFrenPocController).GetField("font",flags).SetValue(c,TMP_Settings.defaultFontAsset);
                typeof(AIFrenPocController).GetMethod("AddAvatarCueControls",flags).Invoke(c,new object[]{root.transform,-20f});
                var toggle=root.GetComponentsInChildren<Toggle>().Single(x=>x.name=="Explicit Avatar Cues");
                Assert.False(toggle.isOn);
                toggle.isOn=true;
                root.GetComponentsInChildren<Button>().Single(x=>x.name=="Cancel Avatar Cues").onClick.Invoke();
                Assert.False(toggle.isOn);
                root.GetComponentsInChildren<Button>().Single(x=>x.name=="Save Avatar Cues").onClick.Invoke();
                Assert.False((bool)typeof(AIFrenPocController).GetField("avatarCuesSaving",flags).GetValue(c), "Disconnected save must not strand the controls.");
                typeof(AIFrenPocController).GetMethod("ReceiveAvatarCuesSetting",flags).Invoke(c,new object[]{true,true});
                Assert.True(toggle.isOn);
                toggle.isOn=false;
                // Unsolicited snapshot cannot replace an unsaved draft.
                typeof(AIFrenPocController).GetMethod("ReceiveAvatarCuesSetting",flags).Invoke(c,new object[]{true,false});
                Assert.False(toggle.isOn);
                root.GetComponentsInChildren<Button>().Single(x=>x.name=="Cancel Avatar Cues").onClick.Invoke();
                Assert.True(toggle.isOn);
                Assert.False(PresentationPreferences.HasKey("explicit_avatar_cues"));
                var parsed=AIFrenProtocol.ParseServerMessage("{\"type\":\"snapshot\",\"data\":{\"companion\":{\"explicit_avatar_cues\":true}}}");
                Assert.True(parsed.data.companion.explicit_avatar_cues);
                StringAssert.Contains("\"explicit_avatar_cues\":true",AIFrenProtocol.SerializeCommand(new ClientCommand {command="set_explicit_avatar_cues",explicit_avatar_cues=true}));
            }
            finally { Object.DestroyImmediate(root); }
        }
    }
}
