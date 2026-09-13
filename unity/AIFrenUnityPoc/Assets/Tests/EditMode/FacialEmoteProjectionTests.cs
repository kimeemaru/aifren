using System;
using System.Collections.Generic;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UniGLTF.Extensions.VRMC_vrm;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class FacialEmoteProjectionTests
    {
        [TestCase("*smiles warmly* Hello.", "happy")]
        [TestCase("*I smile softly* Hello.", "happy")]
        [TestCase("*I am smiling gently.* Hello.", "happy")]
        [TestCase("*Mira smiles brightly at you* Hello.", "happy")]
        [TestCase("*beams.* Wonderful.", "happy")]
        [TestCase("*returns to a neutral expression*", "neutral")]
        [TestCase("*I return to a neutral face.* Understood.", "neutral")]
        [TestCase("*Mira returns to a neutral expression.* Okay.", "neutral")]
        [TestCase("*smiles warmly and nods* Hello.", "happy")]
        [TestCase("*I smile and nod* Hello.", "happy")]
        [TestCase("*nods and smiles gently* Hello.", "happy")]
        [TestCase("*smiles* *nods* Hello.", "happy")]
        [TestCase("*Mira smiles warmly, a soft look of encouraging pride on her face.* Well done.", "happy")]
        [TestCase("*Mira shifts slightly on the bench, offering a warm smile* Hello.", "happy")]
        [TestCase("*I offer a gentle smile.* Hello.", "happy")]
        [TestCase("*Mira brightens, a gentle smile spreading across her face.* Wonderful.", "happy")]
        [TestCase("*brightens, a soft smile spreading across my face.* Hello.", "happy")]
        public void TypedCurrentSelfActionsChooseOneFacialRequest(string text, string expected)
        {
            var document = DialoguePresentationParser.ParseDocument(text);
            Assert.That(FacialEmoteProjection.Select(document, "Mira", out var reason), Is.EqualTo(expected));
            Assert.That(reason, Is.EqualTo("eligible"));
            Assert.That(document.SpokenText, Is.EqualTo(DialoguePresentationParser.SpokenText(text)));
        }

        [TestCase("You make me smile.")]
        [TestCase("I **smile warmly** at that phrase.")]
        [TestCase("*Mira brightens.* That is good news.")]
        [TestCase("*Mira brightens, a sad smile spreading across her face.*")]
        [TestCase("*Mira brightens, a gentle smile spreading across Rowan's face.*")]
        [TestCase("*Mira imagines a gentle smile spreading across her face.*")]
        [TestCase("*Mira might brighten, a gentle smile spreading across her face.*")]
        [TestCase("*She brightens, a gentle smile spreading across her face.*")]
        [TestCase("For example: *Mira brightens, a gentle smile spreading across her face.*")]
        [TestCase("*watches you smile*")]
        [TestCase("*Rowan smiles warmly*")]
        [TestCase("*She smiles warmly*")]
        [TestCase("*you smile warmly*")]
        [TestCase("*Mira watches Rowan smile*")]
        [TestCase("*tries not to smile*")]
        [TestCase("*does not smile*")]
        [TestCase("*I don't smile*")]
        [TestCase("*Mira will smile*")]
        [TestCase("*might smile*")]
        [TestCase("*would smile if you asked*")]
        [TestCase("*I smiled yesterday*")]
        [TestCase("*recalls smiling*")]
        [TestCase("\"*smiles warmly*\"")]
        [TestCase("'*smiles warmly*'")]
        [TestCase("*\"smiles warmly\"*")]
        [TestCase("For example: *smiles warmly*")]
        [TestCase("If we meet: *smiles warmly*")]
        [TestCase("Yesterday, *Mira smiles warmly*")]
        [TestCase("*smiles warmly* is an example.")]
        [TestCase("*smiles warmly* would be a stage direction.")]
        [TestCase("*smiles sadly*")]
        [TestCase("*gives a sad smile*")]
        [TestCase("*frowns*")]
        [TestCase("*smiles and frowns*")]
        [TestCase("*smiles* *returns to a neutral expression*")]
        [TestCase("*smiles* *frowns*")]
        [TestCase("*smiles* Hello. *returns to a neutral expression*")]
        [TestCase("*smiles, a sad look on her face*")]
        [TestCase("*smiles warmly if you agree*")]
        [TestCase("*smiles while remembering yesterday*")]
        [TestCase("*Mira smiles warmly, but Rowan is sad*")]
        [TestCase("*Mira shifts if you smile, offering a warm smile*")]
        [TestCase("*Mira watches you, offering a warm smile*")]
        [TestCase("*Mira's face lights up with interest.* Hello.")]
        [TestCase("*Mira's smile softens sympathetically.* Oh.")]
        [TestCase("*beams* Hello.")] // Existing parser treats this unrecognized inline form as emphasis.
        [TestCase("*smiles **warmly*** Hello.")] // No marker stripping to expand the grammar.
        public void UnsupportedOrNoncurrentSyntaxDoesNotSelect(string text)
        {
            Assert.That(FacialEmoteProjection.Select(DialoguePresentationParser.ParseDocument(text), "Mira", out _), Is.Null);
        }

        [Test]
        public void NoNameAliasesOrUnboundedActionWork()
        {
            Assert.That(FacialEmoteProjection.Select(DialoguePresentationParser.ParseDocument("*Mira smiles*"), "QA Mira", out _), Is.Null);
            Assert.That(FacialEmoteProjection.Select(DialoguePresentationParser.ParseDocument("*smiles " + new string('x', 257) + "*"), "Mira", out var reason), Is.Null);
            Assert.That(reason, Is.EqualTo("bound"));
        }

        // Actual UniVRM validator/merger and a generated blendshape mesh; no private VRM.
        internal sealed class Fixture : IDisposable
        {
            internal readonly GameObject Root = new GameObject("synthetic facial projection");
            internal readonly AvatarPresentationResolver Resolver;
            internal readonly AvatarExpressionController Face;
            internal readonly AvatarAnimationController Body;
            internal readonly ResponsePresentationTurn Owner = new ResponsePresentationTurn();
            internal readonly SkinnedMeshRenderer Renderer;
            internal readonly Vrm10RuntimeExpression Runtime;
            internal readonly VRM10Expression Happy, Mouth, Blink, Sad, Angry;
            private readonly Mesh mesh;
            private readonly VRM10Object vrm;
            internal Fixture(bool extra = false)
            {
                var model = new GameObject("model"); model.transform.SetParent(Root.transform);
                var instance = model.AddComponent<Vrm10Instance>(); instance.enabled = false;
                vrm = ScriptableObject.CreateInstance<VRM10Object>(); instance.Vrm = vrm;
                mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
                mesh.AddBlendShapeFrame("synthetic-smile", 100, new[] { Vector3.up, Vector3.zero, Vector3.zero }, new Vector3[3], new Vector3[3]);
                Renderer = model.AddComponent<SkinnedMeshRenderer>(); Renderer.sharedMesh = mesh;
                Happy = ScriptableObject.CreateInstance<VRM10Expression>();
                Happy.MorphTargetBindings = new[] { new MorphTargetBinding { RelativePath = "", Index = 0, Weight = 1 } };
                Mouth = ScriptableObject.CreateInstance<VRM10Expression>();
                Blink = ScriptableObject.CreateInstance<VRM10Expression>();
                vrm.Expression.Happy = Happy; vrm.Expression.Aa = Mouth; vrm.Expression.Blink = Blink;
                if (extra)
                {
                    mesh.AddBlendShapeFrame("synthetic-sad", 100, new[] { Vector3.down, Vector3.zero, Vector3.zero }, new Vector3[3], new Vector3[3]);
                    mesh.AddBlendShapeFrame("synthetic-angry", 100, new[] { Vector3.left, Vector3.zero, Vector3.zero }, new Vector3[3], new Vector3[3]);
                    Sad = ScriptableObject.CreateInstance<VRM10Expression>();
                    Angry = ScriptableObject.CreateInstance<VRM10Expression>();
                    Sad.MorphTargetBindings = new[] { new MorphTargetBinding { RelativePath="", Index=1, Weight=1 } };
                    Angry.MorphTargetBindings = new[] { new MorphTargetBinding { RelativePath="", Index=2, Weight=1 } };
                    vrm.Expression.Sad = Sad; vrm.Expression.Angry = Angry;
                }
                Runtime = (Vrm10RuntimeExpression)Activator.CreateInstance(typeof(Vrm10RuntimeExpression),
                    BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { instance, null, false }, null);
                Face = Root.AddComponent<AvatarExpressionController>();
                Set(Face, "runtime", Runtime);
                var capabilities = (List<AvatarExpressionCapability>)Get(Face, "capabilities");
                capabilities.Add(new AvatarExpressionCapability(new ExpressionKey(ExpressionPreset.happy), Happy));
                if (extra) { capabilities.Add(new AvatarExpressionCapability(new ExpressionKey(ExpressionPreset.sad), Sad)); capabilities.Add(new AvatarExpressionCapability(new ExpressionKey(ExpressionPreset.angry), Angry)); }
                Body = Root.AddComponent<AvatarAnimationController>(); Set(Body, "head", Root.transform);
                Resolver = Root.AddComponent<AvatarPresentationResolver>(); Resolver.Configure();
            }
            internal void Reply(int id, string text, PresentationMetadata metadata = null)
            { Owner.Begin(id); Assert.That(Owner.PublishFinal(id, Resolver, metadata, text, "Mira"), Is.True); Tick(.4f); }
            internal void Tick(float delta)
            {
                Invoke(Face, "TickBlend", delta);
                Invoke(Runtime, "Process", default(LookAtEyeDirection));
            }
            public void Dispose()
            {
                Runtime.Dispose(); UnityEngine.Object.DestroyImmediate(Root);
                foreach (var x in new UnityEngine.Object[] { vrm, Happy, Mouth, Blink, Sad, Angry, mesh }) UnityEngine.Object.DestroyImmediate(x);
            }
        }
        private static object Get(object x, string name) => x.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(x);
        private static void Set(object x, string name, object value) => x.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(x, value);
        private static object Invoke(object x, string name, params object[] args) => x.GetType().GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(x, args);

        [Test]
        public void AcceptedFinalChangesActualMeshAndPreservesSpokenProjectionAndRetirement()
        {
            using var f = new Fixture();
            const string text = "*I smile and nod* I **really** appreciate it.";
            f.Reply(1, text, new PresentationMetadata { speech_mode = "normal" });
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("explicit_emote"));
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.7f).Within(.001));
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            Assert.That(f.Body.ActiveGesture, Is.EqualTo(AvatarGestureIntent.Nod));
            Assert.That(DialoguePresentationParser.SpokenText(text), Is.EqualTo("I really appreciate it."));
            f.Body.StopSpeech(); f.Body.RetireResponseMotion(); f.Tick(.4f);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            f.Reply(2, "I am listening.");
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("no_change"));
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            f.Reply(3, "*returns to a neutral expression*");
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
        }

        [Test]
        public void ExplicitMetadataOwnsFaceEvenWhenUnsupportedAndNeutralWins()
        {
            using var f = new Fixture();
            f.Reply(1, "*smiles warmly*", new PresentationMetadata { emotion = "sad" });
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("model_metadata"));
            Assert.That(f.Resolver.LastFaceRequest, Is.EqualTo("sad"));
            Assert.That(f.Resolver.LastFaceApplied, Is.False);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
            f.Reply(2, "*smiles warmly*", new PresentationMetadata { gesture = "agreement", speech_mode = "unavailable" });
            Assert.That(f.Resolver.AllowsLipSync, Is.False);
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            f.Reply(3, "*smiles warmly*", new PresentationMetadata { emotion = "neutral" });
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("model_metadata"));
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.Zero);
        }

        [Test]
        public void RecordedPresentNounSmileReachesTheExistingNativeBlendOwner()
        {
            using var f = new Fixture();
            const string text = "*Mira brightens, a gentle smile spreading across her face.* That is wonderful.";
            f.Reply(1, text);
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("explicit_emote"));
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
            Assert.That(DialoguePresentationParser.SpokenText(text), Is.EqualTo("That is wonderful."));
        }

        [Test]
        public void AuthoritativeSnapshotIsNotReportedAsAModelFacialChoice()
        {
            using var f = new Fixture();
            f.Resolver.Apply(new PresentationMetadata { emotion = "neutral" });
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("state_update"));
            f.Reply(1, "I am listening.", new PresentationMetadata { emotion = "happy" });
            Assert.That(f.Resolver.LastFaceOrigin, Is.EqualTo("model_metadata"));
            Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
        }

        [Test]
        public void DuplicateCancelledReplacedAndRetiredOwnersCannotApplyLateFacialText()
        {
            using var f = new Fixture();
            f.Reply(1, "*smiles warmly*");
            Assert.That(f.Owner.PublishFinal(1, f.Resolver, null, "*returns to a neutral expression*", "Mira"), Is.False);
            f.Owner.Begin(2); f.Owner.Retire(2);
            Assert.That(f.Owner.PublishFinal(2, f.Resolver, null, "*returns to a neutral expression*", "Mira"), Is.False);
            f.Owner.Begin(3); f.Owner.Begin(4);
            Assert.That(f.Owner.PublishFinal(3, f.Resolver, null, "*returns to a neutral expression*", "Mira"), Is.False);
            f.Owner.Reset(); // same owner reset used by reconnect/character/asset retirement
            Assert.That(f.Owner.PublishFinal(4, f.Resolver, null, "*returns to a neutral expression*", "Mira"), Is.False);
            f.Tick(.4f); Assert.That(f.Renderer.GetBlendShapeWeight(0), Is.EqualTo(70).Within(.1));
        }

        [Test]
        public void AuthorOverrideAndManualWeightRemainOwnedByExistingExpressionRuntime()
        {
            using var f = new Fixture();
            f.Happy.OverrideMouth = ExpressionOverrideType.block;
            f.Happy.OverrideBlink = ExpressionOverrideType.block;
            // Recreate the installed validator after authored fixture configuration changes.
            var validator = Vrm10RuntimeExpression.ExpressionValidatorFactory.Create(
                new VRM10ObjectExpression { Happy = f.Happy, Aa = f.Mouth, Blink = f.Blink });
            Set(f.Runtime, "_validator", validator);
            f.Runtime.SetWeight(new ExpressionKey(ExpressionPreset.aa), .8f);
            f.Runtime.SetWeight(new ExpressionKey(ExpressionPreset.blink), .8f);
            f.Reply(1, "*smiles warmly*");
            Assert.That(f.Runtime.ActualWeights[new ExpressionKey(ExpressionPreset.aa)], Is.Zero);
            Assert.That(f.Runtime.ActualWeights[new ExpressionKey(ExpressionPreset.blink)], Is.Zero);
            f.Face.SetExpression(f.Face.ActiveExpression.Id, .25f); f.Tick(.4f); // same manual control API
            f.Reply(2, "No new facial action.");
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f).Within(.001));
            f.Body.StopSpeech(); f.Tick(.4f);
            Assert.That(f.Face.ActiveIntensity, Is.EqualTo(.25f).Within(.001));
        }
    }
}
