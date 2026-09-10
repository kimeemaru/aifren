using PlayerPrefs = AIFren.UnityPoc.PresentationPreferences;
using System.Linq;
using System.Reflection;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class SceneOverlayStateTests
    {
        [Test]
        public void RowsGroupImportantRelationsWithAuthoritativeEffectsAndClearTokens()
        {
            ContinuitySnapshot snapshot = new ContinuitySnapshot {
                scene_relations = new[] {
                    new ContinuitySceneRelation {
                        target = "companion", facet = "eyes", predicate = "covered_by",
                        cause = "blindfold", effect = "vision obstruction", can_clear = true,
                        clear_token = "relation:0",
                    },
                    new ContinuitySceneRelation {
                        target = "companion", facet = "eyes", predicate = "covered_by",
                        cause = "user hands", effect = "vision obstruction", can_clear = true,
                        clear_token = "relation:1",
                    },
                    new ContinuitySceneRelation {
                        target = "user", facet = "hands", predicate = "holding",
                        cause = "cup", effect = "hand occupancy", can_clear = true,
                        clear_token = "relation:2",
                    },
                },
                capability_effects = new[] {
                    new ContinuityCapabilityEffect {
                        target = "companion", capability = "vision", state = "unavailable",
                    },
                    new ContinuityCapabilityEffect {
                        target = "user", capability = "hands", state = "partially_occupied",
                    },
                },
            };

            SceneOverlayRow[] rows = SceneOverlayState.Rows(snapshot);
            Assert.That(rows.Select(row => row.text), Does.Contain("Companion Vision unavailable — blindfold"));
            Assert.That(rows.Select(row => row.text), Does.Contain("Companion Vision unavailable — user hands"));
            Assert.That(rows.Select(row => row.text), Does.Contain("You holding cup"));
            Assert.That(rows[0].action, Is.EqualTo("interact_scene_relation"));
            Assert.That(rows[0].actionToken, Does.StartWith("relation:"));
            Assert.That(string.Join(" ", rows.Select(row => row.text)), Does.Not.Contain("scene-"));
        }

        [Test]
        public void DormantSubjectsAreRemovableAndCurrentSubjectsAreNotDuplicated()
        {
            ContinuitySnapshot snapshot = new ContinuitySnapshot {
                scene_subjects = new[] {
                    new ContinuitySceneSubject {
                        kind = "cup", lifecycle = "dormant", can_remove = true,
                        remove_token = "subject:0",
                    },
                    new ContinuitySceneSubject {
                        kind = "hat", lifecycle = "current", can_remove = false,
                    },
                },
                scene_relations = new[] {
                    new ContinuitySceneRelation {
                        target = "companion", predicate = "wearing", cause = "red hat",
                        can_clear = true, clear_token = "relation:0",
                    },
                },
            };

            SceneOverlayRow[] rows = SceneOverlayState.Rows(snapshot);
            Assert.That(rows.Select(row => row.text), Does.Contain("red hat"));
            SceneOverlayRow dormant = rows.Single(row => row.text == "Cup (not active)");
            Assert.That(dormant.action, Is.EqualTo("retire_scene_subject"));
            Assert.That(rows.Any(row => row.text == "Hat (not active)"), Is.False);
        }

        [Test]
        public void CurrentConditionsUseTypedProjectionWithoutInventingDormantFacts()
        {
            ContinuitySnapshot snapshot = new ContinuitySnapshot {
                scene_subjects = new[] {
                    new ContinuitySceneSubject {
                        kind = "scarf", label = "red scarf", lifecycle = "current",
                        conditions = new[] { "wet: true", "stain: mud" },
                    },
                    new ContinuitySceneSubject {
                        kind = "cup", label = "blue cup", lifecycle = "dormant",
                        conditions = new[] { "condition: cracked" }, can_remove = true,
                        remove_token = "subject:1",
                    },
                },
            };

            SceneOverlayRow[] rows = SceneOverlayState.Rows(snapshot);
            Assert.That(rows.Select(row => row.text), Does.Contain("Red scarf — wet: true"));
            Assert.That(rows.Select(row => row.text), Does.Contain("Red scarf — stain: mud"));
            Assert.That(rows.Any(row => row.text.Contains("cracked")), Is.False);
            Assert.That(rows.Select(row => row.text), Does.Contain("Cup (not active)"));
        }

        [Test]
        public void ProjectionIsBoundedAndContainsNoOpaqueIdentifiers()
        {
            ContinuitySceneRelation[] relations = Enumerable.Range(0, 40).Select(index =>
                new ContinuitySceneRelation {
                    target = "companion", predicate = "holding", cause = "item " + index,
                    can_clear = true, clear_token = "relation:" + index,
                }).ToArray();
            SceneOverlayRow[] rows = SceneOverlayState.Rows(new ContinuitySnapshot {
                scene_relations = relations,
            });
            Assert.That(rows.Length, Is.EqualTo(SceneOverlayState.MaximumRows));
            Assert.That(rows.All(row => !row.text.Contains("relation:")), Is.True);
        }

        [Test]
        public void OverlayIsCompactScrollableAndObeysGlobalUiVisibility()
        {
            Assert.That(SceneOverlayState.PanelAnchorMin(true).x, Is.EqualTo(.03f));
            Assert.That(SceneOverlayState.PanelAnchorMax(true).x, Is.EqualTo(.47f));
            Assert.That(SceneOverlayState.PanelAnchorMax(false).x, Is.EqualTo(.285f));
            Assert.That(SceneOverlayState.PanelAnchorMin(true).y, Is.EqualTo(.82f));
            Assert.That(SceneOverlayState.PanelHeight(1, 1920f), Is.LessThan(110f));
            Assert.That(SceneOverlayState.PanelHeight(3, 1920f), Is.LessThan(SceneOverlayState.PanelHeight(10, 1920f)));
            Assert.That(SceneOverlayState.PanelHeight(10, 1920f), Is.LessThanOrEqualTo(1920f * .34f));
            Assert.That(SceneOverlayState.PanelHeight(24, 1920f), Is.LessThanOrEqualTo(1920f * .34f));
            Assert.That(SceneOverlayState.ShouldShow(true, 1, true), Is.True);
            Assert.That(SceneOverlayState.ShouldShow(true, 1, false), Is.False);

            const string preference = "AIFren.SceneOverlay";
            bool hadPreference = PlayerPrefs.HasKey(preference);
            int oldPreference = PlayerPrefs.GetInt(preference, 0);
            GameObject root = new GameObject("scene-overlay-controller", typeof(AIFrenPocController), typeof(RectTransform));
            try
            {
                AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
                root.GetComponent<RectTransform>().sizeDelta = new Vector2(900f, 1600f);
                SetField(controller, "theme", PresentationThemes.Dark);
                SetField(controller, "font", TMP_Settings.defaultFontAsset);
                typeof(AIFrenPocController).GetMethod(
                    "BuildSceneOverlay", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, new object[] { root.GetComponent<RectTransform>() });
                SetField(controller, "authoritativeContinuity", new ContinuitySnapshot {
                    scene_relations = new[] { new ContinuitySceneRelation {
                        target = "companion", predicate = "holding", cause = "cup",
                        can_clear = true, clear_token = "relation:0",
                    }},
                });
                typeof(AIFrenPocController).GetMethod(
                    "SetSceneOverlayVisible", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, new object[] { true });

                ScrollRect scroll = (ScrollRect)Field(controller, "sceneOverlayScroll");
                GameObject panel = (GameObject)Field(controller, "sceneOverlayPanel");
                Assert.That(scroll, Is.Not.Null);
                Assert.That(scroll.vertical, Is.True);
                Assert.That(scroll.horizontal, Is.False);
                Assert.That(scroll.verticalScrollbar, Is.Null);
                Assert.That(panel.GetComponent<Image>().color.a, Is.EqualTo(.96f).Within(.001f));
                Assert.That(panel.activeSelf, Is.False, "Enabled drawer starts collapsed.");
                var drawer = (SceneDrawerPresenter)Field(controller, "sceneDrawer");
                drawer.Pointer(true, true, 0); drawer.Tick(0, .2f, false);
                Assert.That(panel.activeSelf, Is.True);
                TMP_Text title = panel.GetComponentsInChildren<TMP_Text>().Single(x => x.text == "CURRENT SCENE");
                title.ForceMeshUpdate();
                Assert.That(title.rectTransform.rect.height, Is.GreaterThanOrEqualTo(title.preferredHeight),
                    "A one-row scene must leave enough height for the actual title glyphs.");
                Bounds header = RectTransformUtility.CalculateRelativeRectTransformBounds(panel.transform, title.transform);
                Bounds body = RectTransformUtility.CalculateRelativeRectTransformBounds(panel.transform, scroll.viewport);
                Assert.That(header.min.y, Is.GreaterThan(body.max.y), "The scroll mask must remain below the title.");
                Assert.That(PlayerPrefs.GetInt(preference), Is.EqualTo(1));
                SetField(controller, "interfaceHidden", true);
                typeof(AIFrenPocController).GetMethod(
                    "RefreshSceneOverlay", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, new object[] { Field(controller, "authoritativeContinuity") });
                Assert.That(panel.activeSelf, Is.False);
                SetField(controller, "interfaceHidden", false);
                typeof(AIFrenPocController).GetMethod(
                    "RefreshSceneOverlay", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, new object[] { Field(controller, "authoritativeContinuity") });
                Assert.That(panel.activeSelf, Is.False, "Show restores the affordance, not an obstructive pinned panel.");
                Assert.That(((Button)Field(controller, "sceneDrawerTab")).gameObject.activeSelf, Is.True);
                GameObject scrim = new GameObject("synthetic modal scrim");
                scrim.transform.SetParent(root.transform);
                SetField(controller, "modalScrim", scrim);
                typeof(AIFrenPocController).GetMethod("RefreshSceneDrawerAvailability", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(controller, null);
                Assert.That(((Button)Field(controller, "sceneDrawerTab")).gameObject.activeSelf, Is.False,
                    "Modal navigation must not reach a drawer behind the scrim.");
                scrim.SetActive(false);
                typeof(AIFrenPocController).GetMethod("RefreshSceneDrawerAvailability", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(controller, null);
                Assert.That(((Button)Field(controller, "sceneDrawerTab")).gameObject.activeSelf, Is.True);
                Assert.That(panel.activeSelf, Is.False);
            }
            finally
            {
                if (hadPreference) PlayerPrefs.SetInt(preference, oldPreference);
                else PlayerPrefs.DeleteKey(preference);
                PlayerPrefs.Save();
                Object.DestroyImmediate(root);
            }
        }

        private static object Field(object target, string name)
        {
            return target.GetType().GetField(
                name, BindingFlags.Instance | BindingFlags.NonPublic
            ).GetValue(target);
        }

        private static void SetField(object target, string name, object value)
        {
            target.GetType().GetField(
                name, BindingFlags.Instance | BindingFlags.NonPublic
            ).SetValue(target, value);
        }
    }
}
