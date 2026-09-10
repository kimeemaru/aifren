using System.Collections.Generic;
using System.Linq;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SubtitleVisualSliceTests
    {
        private GameObject root;
        private TMP_Text visible;
        private TmpHiddenSubtitleRenderTarget target;
        private HiddenSubtitlePresenter presenter;
        private readonly List<int> shown = new List<int>();
        private TMP_FontAsset font;
        private Material material;
        [SetUp] public void SetUp()
        {
            root = new GameObject("synthetic subtitle", typeof(RectTransform), typeof(Canvas), typeof(CanvasGroup));
            root.GetComponent<RectTransform>().sizeDelta = new Vector2(900, 250);
            visible = Text("visible"); var measure = Text("measurement");
            font = SubtitleStyle.CreateFont(TMP_Settings.defaultFontAsset);
            Assert.IsNotNull(font, "approved subtitle font must import");
            visible.font = measure.font = font;
            material = new Material(font.material); visible.fontSharedMaterial = material;
            SubtitleStyle.Apply(visible, material); SubtitleStyle.Apply(measure, null);
            measure.gameObject.SetActive(false);
            target = new TmpHiddenSubtitleRenderTarget(root, root.GetComponent<CanvasGroup>(),
                root.GetComponent<RectTransform>(), visible, measure);
            presenter = new HiddenSubtitlePresenter(target); shown.Clear(); presenter.WordPresented += shown.Add;
        }
        private TMP_Text Text(string name)
        {
            var obj = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            obj.transform.SetParent(root.transform, false);
            obj.GetComponent<RectTransform>().sizeDelta = new Vector2(860, 230);
            return obj.GetComponent<TMP_Text>();
        }
        [TearDown] public void TearDown()
        {
            target?.Dispose(); Object.DestroyImmediate(root); Object.DestroyImmediate(material);
            if (font != null) { foreach (var atlas in font.atlasTextures) Object.DestroyImmediate(atlas); Object.DestroyImmediate(font.material); Object.DestroyImmediate(font); }
        }
        private void Begin(string[] pages, float[] times)
        {
            presenter.Begin(new SubtitleSession(pages.ToList(), SubtitleTimingPlan.BuildPageWordRanges(pages,
                DialoguePresentationParser.SpokenText), times.ToList(), 1, 0));
            presenter.OnPlaybackStarted(1, 7, times.ToList(), 0);
        }
        private void Tick(float time) => presenter.Tick(time, true, true);
        private void Until(float start, float end) { for (float t = start; t <= end; t += .01f) Tick(t); }

        [Test] public void EachWordHasIntermediateVertexAlphaWithoutRefadingEarlierWords()
        {
            Begin(new[] { "One **gentle** thought." }, new[] { 0f, .4f, .8f });
            Tick(0); Assert.IsEmpty(shown); Assert.AreEqual(0, target.RenderedWordOpacity(0));
            Tick(.09f); Assert.That(target.RenderedWordOpacity(0), Is.InRange(.4f,.6f));
            Tick(.2f); Assert.AreEqual(1, target.RenderedWordOpacity(0));
            Tick(.4f); Tick(.49f);
            Assert.AreEqual(1, target.RenderedWordOpacity(0));
            Assert.That(target.RenderedWordOpacity(1), Is.InRange(.4f,.6f));
            Assert.AreEqual(0, target.RenderedWordOpacity(2));
            CollectionAssert.AreEqual(new[] { 0,1 }, shown);
            Assert.AreEqual(2, root.GetComponentsInChildren<TMP_Text>(true).Length);
            Assert.AreEqual(SubtitleStyle.Face, visible.color);
            Assert.AreEqual(.18f, material.GetFloat(ShaderUtilities.ID_OutlineWidth));
            Assert.AreEqual(material.GetFloat(ShaderUtilities.ID_OutlineWidth),
                material.GetFloat(ShaderUtilities.ID_FaceDilate), "outline must preserve thin face interiors");
            Assert.AreEqual(.38f, material.GetColor(ShaderUtilities.ID_UnderlayColor).a);
        }
        [TestCase("pink")][TestCase("#B8E6FF")][TestCase("white")]
        public void BaseColorSurvivesActiveFadePageRebuildAndPeek(string choice)
        {
            Begin(new[] {"One two.", "Three four."},new[]{0f,.4f,2f,2.4f}); Tick(0);Tick(.09f);
            SubtitleTextColor.TryParse(choice,out Color color);
            SubtitleStyle.Apply(visible,material,color); visible.ForceMeshUpdate();
            Assert.That(target.RenderedWordOpacity(0), Is.InRange(.4f,.6f));
            var ch=visible.textInfo.characterInfo[0];
            Color32 vertex=visible.textInfo.meshInfo[ch.materialReferenceIndex].colors32[ch.vertexIndex];
            Color32 expected=color;
            Assert.AreEqual(expected.r,vertex.r);Assert.AreEqual(expected.g,vertex.g);Assert.AreEqual(expected.b,vertex.b);
            Tick(.2f);presenter.SetSuppressed(true,.2f);presenter.SetSuppressed(false,.3f);Tick(.3f);
            Assert.AreEqual(1,target.RenderedWordOpacity(0));
            Until(.31f,2.25f);Assert.AreEqual(color,visible.color);
            ch=visible.textInfo.characterInfo[0]; vertex=visible.textInfo.meshInfo[ch.materialReferenceIndex].colors32[ch.vertexIndex];
            Assert.AreEqual(expected.r,vertex.r);Assert.AreEqual(expected.g,vertex.g);Assert.AreEqual(expected.b,vertex.b);
            CollectionAssert.AreEqual(new[]{0,1,2},shown);
        }
        [Test] public void FadeUpdatesDoNotRebuildLayoutAndResizePreservesOpacityAndOwnership()
        {
            Begin(new[] { "One two three." }, new[] { 0f,.5f,1f });
            Tick(0); Tick(.1f); int layouts=target.LayoutPreparationCount, meshes=target.MeshPreparationCount;
            Tick(.12f); Tick(.18f);
            Assert.AreEqual(layouts,target.LayoutPreparationCount); Assert.AreEqual(meshes,target.MeshPreparationCount);
            root.GetComponent<RectTransform>().sizeDelta=new Vector2(1500,220);
            Tick(.25f); Assert.AreEqual(1,target.RenderedWordOpacity(0));
            CollectionAssert.AreEqual(new[] {0},shown);
            Assert.AreEqual(layouts+1,target.LayoutPreparationCount);
            Assert.AreEqual(meshes+1,target.MeshPreparationCount);
        }
        [Test] public void PeekPreservesFadeAndDoesNotConsumeDueWords()
        {
            Begin(new[] { "One two three." },new[] {0f,.4f,.8f}); Tick(0);Tick(.09f);
            float alpha=target.RenderedWordOpacity(0);
            presenter.SetSuppressed(true,.09f);Tick(4);Assert.AreEqual(1,shown.Count);
            presenter.SetSuppressed(false,4);Assert.That(target.RenderedWordOpacity(0), Is.EqualTo(alpha).Within(1f/255f + 0.000001f));
            Tick(4);Tick(4.09f);Assert.AreEqual(1,target.RenderedWordOpacity(0));
            Assert.LessOrEqual(shown.Count,2);
            presenter.Cancel();presenter.SetSuppressed(false,5);Tick(5);
            Assert.IsFalse(visible.enabled);Assert.IsFalse(presenter.IsActive);
        }
        [Test] public void PageDwellUsesExistingGapWithoutShiftingFollowingAudioWords()
        {
            Begin(new[] {"First page.","Next page.","Last page."},new[] {0f,.4f,2f,2.4f,4f,4.4f});
            Until(0,.9f);Assert.AreEqual(HiddenSubtitleState.ShowingPage,presenter.State);
            Assert.AreEqual(1,target.RenderedWordOpacity(1));
            Until(.91f,2.10f);CollectionAssert.AreEqual(new[]{0,1,2},shown);
            Until(2.11f,4.10f);CollectionAssert.AreEqual(new[]{0,1,2,3,4},shown);
        }
        [Test] public void NaturalStopCompletesAllPagesAtUserSpeedAndKeepsFinalWordOpaque()
        {
            presenter.ConfigureReveal(2,false);
            Begin(new[]{"First page.","Next page."},new[]{0f,.05f,.1f,.15f});
            Tick(0);presenter.OnPlaybackStopped(7,.2f);
            Until(.01f,2.3f);CollectionAssert.AreEqual(new[]{0,1,2,3},shown);
            Assert.AreEqual(1,target.RenderedWordOpacity(1));Assert.IsTrue(visible.enabled);
            Until(2.31f,4f);Assert.IsFalse(presenter.IsActive);Assert.IsFalse(visible.enabled);
        }
        [Test] public void ExplicitInterruptionBypassesFadesAndLateCallbacksCannotRevive()
        {
            Begin(new[]{"One two."},new[]{0f,.4f});Tick(0);Tick(.1f);
            presenter.OnPlaybackStopped(7,.1f,true); Assert.IsFalse(visible.enabled);
            presenter.OnAudioUnavailable(1,.2f);presenter.OnPlaybackStarted(1,7,new List<float>{0,.4f},.3f);Tick(1);
            Assert.IsFalse(presenter.IsActive);Assert.AreEqual(1,shown.Count);
            Begin(new[]{"Replacement."},new[]{0f});Tick(0);Tick(.1f);
            presenter.OnPlaybackStopped(6,.1f,true);Assert.IsTrue(presenter.IsActive);
        }
        [Test] public void InstantTextStillWaitsForAudioThenAppearsWithoutWordFades()
        {
            presenter.ConfigureReveal(2,true);
            presenter.Begin(new SubtitleSession(new List<string>{"One two."},SubtitleTimingPlan.BuildPageWordRanges(new[]{"One two."}),new List<float>{0,.5f},1,0));
            Tick(10);Assert.IsEmpty(shown);Assert.IsFalse(visible.enabled);
            presenter.OnAudioUnavailable(1,10);Tick(10);
            CollectionAssert.AreEqual(new[]{0,1},shown);Assert.AreEqual(1,target.RenderedWordOpacity(1));
            Until(10.01f,12);Assert.IsFalse(presenter.IsActive);
        }
        [Test] public void DisabledPresentationAndRepeatedNoAudioDoNotCreateOrRestartTimers()
        {
            presenter.Begin(new SubtitleSession(new List<string>{"One word."},SubtitleTimingPlan.BuildPageWordRanges(new[]{"One word."}),new List<float>{0,.2f},1,0));
            presenter.OnAudioUnavailable(1,10);Tick(10);Tick(10.2f);
            presenter.OnAudioUnavailable(1,10.3f);Until(10.31f,12);
            CollectionAssert.AreEqual(new[]{0,1},shown);Assert.IsFalse(presenter.IsActive);
            Begin(new[]{"Disabled."},new[]{0f});presenter.Tick(0,true,false);Assert.IsFalse(visible.enabled);
        }
        [Test] public void LateTimingRefinementDoesNotRestartPlaybackClockOrShownWords()
        {
            Begin(new[]{"One two three."},new[]{0f,.4f,.8f});Until(0,.3f);
            presenter.OnPlaybackStarted(1,7,new List<float>{0,.4f,.8f},.3f);
            Until(.31f,.6f);CollectionAssert.AreEqual(new[]{0,1},shown);
            Assert.AreEqual(1,target.RenderedWordOpacity(1));
        }
        [Test] public void FallbackMaterialSubmeshUsesTheSameWordOpacity()
        {
            // Remove one glyph only from this owned test font; force the existing
            // fallback material without mutating the shared/default font.
            font.atlasPopulationMode = AtlasPopulationMode.Static;
            font.characterTable.RemoveAll(character => character.unicode == (uint)'X');
            font.ReadFontAssetDefinition();
            Begin(new[] { "One X." }, new[] { 0f, .4f });
            Tick(0);Tick(.2f);Tick(.4f);Tick(.49f);
            Assert.Greater(visible.textInfo.materialCount, 1);
            Assert.AreEqual(1f,target.RenderedWordOpacity(0));
            Assert.That(target.RenderedWordOpacity(1), Is.InRange(.4f,.6f));
        }

        [Test] public void WhitespacePunctuationAndEmphasisShareTheExactSpokenWordBoundary()
        {
            Begin(new[]{"Well-known **words**... (still spoken)."},new[]{0f,.4f,.8f,1.2f});
            Until(0,2);CollectionAssert.AreEqual(new[]{0,1,2,3},shown);
            for(int w=0;w<4;w++)Assert.AreEqual(1,target.RenderedWordOpacity(w));
        }
    }
}
