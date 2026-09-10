using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using System.Reflection;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class ContinuityPanelStateTests
    {
        [Test]
        public void ScopeTextShowsRealWorldAndSafeScenarioLabel()
        {
            Assert.That(ContinuityPanelState.ScopeText(new TruthScopeSnapshot { kind = "real_world" }), Is.EqualTo("Real world"));
            Assert.That(ContinuityPanelState.ScopeText(new TruthScopeSnapshot { kind = "scenario", label = "Silvervale" }), Is.EqualTo("RP: Silvervale"));
            Assert.That(ContinuityPanelState.ScopeText(new TruthScopeSnapshot { kind = "scenario", label = "bad\nlabel" }), Is.EqualTo("RP: bad label"));
        }

        [Test]
        public void ThreadsAreBoundedAndNeverRenderActionTokens()
        {
            ContinuityThread[] rows = new ContinuityThread[9];
            for (int index = 0; index < rows.Length; index++)
            {
                rows[index] = new ContinuityThread {
                    kind = "waiting", description = "GPU delivery " + index,
                    action_token = "thread-private-" + index,
                };
            }
            ContinuityThread[] bounded = ContinuityPanelState.BoundedThreads(rows);
            Assert.That(bounded.Length, Is.EqualTo(ContinuityPanelState.MaximumThreads));
            Assert.That(ContinuityPanelState.ThreadPrefix(bounded[0].kind) + ": " + bounded[0].description,
                Does.Not.Contain("thread-private"));
        }

        [Test]
        public void ProtocolKeepsActorActivitiesAndSceneSubjectsDistinct()
        {
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"continuity\":{" +
                "\"activity\":{\"value\":\"cooking dinner\",\"can_clear\":true}," +
                "\"companion_activity\":{\"value\":\"sleeping\",\"can_clear\":false}," +
                "\"scene_subjects\":[{\"kind\":\"hoodie\",\"scope\":\"Real world\"," +
                "\"summary\":\"color: white, stain: coffee, wet: true, worn by: user\"," +
                "\"confirmed\":\"2026-08-27T14:30Z\"}]," +
                "\"scene_relations\":[{\"target\":\"companion\",\"facet\":\"eyes\"," +
                "\"predicate\":\"covered_by\",\"cause\":\"blindfold\",\"scope\":\"Real world\"}]," +
                "\"capability_effects\":[{\"target\":\"companion\",\"capability\":\"vision\"," +
                "\"state\":\"obstructed\",\"cause\":\"blindfold\"}]," +
                "\"profile_baseline\":[{\"relation\":\"normally_worn\",\"item\":\"black dress\"," +
                "\"region\":\"full_outfit\",\"provenance\":\"character_profile\"}]}}}"
            );

            Assert.That(ContinuityPanelState.ActivityText(snapshot.data.continuity.activity),
                Is.EqualTo("cooking dinner"));
            Assert.That(ContinuityPanelState.ActivityText(snapshot.data.continuity.companion_activity),
                Is.EqualTo("sleeping"));
            Assert.That(snapshot.data.continuity.scene_subjects.Length, Is.EqualTo(1));
            Assert.That(snapshot.data.continuity.scene_subjects[0].kind, Is.EqualTo("hoodie"));
            Assert.That(snapshot.data.continuity.scene_subjects[0].summary, Does.Not.Contain("scene_subject_id"));
            string scene = ContinuityPanelState.SceneText(snapshot.data.continuity.scene_subjects,
                snapshot.data.continuity.scene_relations, snapshot.data.continuity.capability_effects,
                snapshot.data.continuity.profile_baseline);
            Assert.That(scene, Does.Contain("hoodie [Real world]"));
            Assert.That(scene, Does.Contain("stain: coffee"));
            Assert.That(scene, Does.Contain("wet: true"));
            Assert.That(scene, Does.Contain("Confirmed 2026-08-27T14:30Z"));
            Assert.That(scene, Does.Contain("companion eyes: covered by — blindfold"));
            Assert.That(scene, Does.Contain("<b>Companion</b>"));
            Assert.That(scene, Does.Contain("• Vision: obstructed"));
            Assert.That(scene, Does.Contain("  - blindfold"));
            Assert.That(scene, Does.Contain("Profile default: black dress [full outfit]"));
        }

        [Test]
        public void ProactiveSnapshotHasIntervalBackoffAndDevelopmentReasonText()
        {
            ServerMessage snapshot = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"snapshot\",\"data\":{\"companion\":{" +
                "\"proactive_behavior\":true,\"proactive_interval_seconds\":30," +
                "\"proactive_eligibility\":\"backed_off_prior_checkin_unanswered\"," +
                "\"proactive_ignored_streak\":1,\"proactive_next_opportunity_seconds\":18}}}"
            );
            Assert.That(snapshot.data.companion.proactive_behavior, Is.True);
            Assert.That(snapshot.data.companion.proactive_interval_seconds, Is.EqualTo(30));
            Assert.That(ContinuityPanelState.ProactiveEligibilityText(
                snapshot.data.companion.proactive_eligibility,
                snapshot.data.companion.proactive_next_opportunity_seconds,
                snapshot.data.companion.proactive_ignored_streak),
                Does.Contain("Backed off: prior check-in unanswered"));
            Assert.That(ContinuityPanelState.ProactiveEligibilityText(
                "eligible_open_thread"), Is.EqualTo("Eligible: open thread"));
        }

        [Test]
        public void ProactiveGenerationIdentitySurvivesProtocolAndDoesNotUseUserThinkingPolicy()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"event\",\"event\":{\"type\":\"turn_started\",\"data\":{" +
                "\"turn_id\":7,\"generation_origin\":\"proactive\",\"proactive\":true}}}"
            );
            Assert.That(message.@event.data.generation_origin, Is.EqualTo("proactive"));
            Assert.That(message.@event.data.proactive, Is.True);
            Assert.That(AIFrenPocController.IsProactiveGeneration(message.@event.data), Is.True);
            Assert.That(AIFrenPocController.IsProactiveGeneration(new BackendEventData {
                generation_origin = "user",
            }), Is.False);
        }

        [Test]
        public void ProactiveTurnStartedPreservesExistingDialogueAndReadyPresentation()
        {
            GameObject root = new GameObject("proactive-controller", typeof(AIFrenPocController));
            try
            {
                AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
                SetField(controller, "visibleState", "Ready");
                SetField(controller, "detail", "Previous status");
                SetField(controller, "currentAssistantPresentationText", "Previous dialogue");
                TMP_InputField input = new GameObject(
                    "input", typeof(RectTransform), typeof(TMP_InputField)
                ).GetComponent<TMP_InputField>();
                input.transform.SetParent(root.transform, false);
                Button send = new GameObject(
                    "send", typeof(RectTransform), typeof(Button)
                ).GetComponent<Button>();
                send.transform.SetParent(root.transform, false);
                SetField(controller, "messageInput", input);
                SetField(controller, "sendButton", send);
                ServerMessage message = AIFrenProtocol.ParseServerMessage(
                    "{\"type\":\"event\",\"event\":{\"type\":\"turn_started\",\"data\":{" +
                    "\"turn_id\":7,\"generation_origin\":\"proactive\",\"proactive\":true}}}"
                );

                typeof(AIFrenPocController).GetMethod(
                    "HandleServerMessage", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, new object[] { message });

                Assert.That(Field(controller, "visibleState"), Is.EqualTo("Ready"));
                Assert.That(Field(controller, "detail"), Is.EqualTo("Previous status"));
                Assert.That(Field(controller, "currentAssistantPresentationText"),
                    Is.EqualTo("Previous dialogue"));
                Assert.That(Field(controller, "activeTurnIsProactive"), Is.EqualTo(true));
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void SceneTextRendersEveryBackendBoundedSectionWithoutLocalCaps()
        {
            ContinuitySceneSubject[] subjects = new ContinuitySceneSubject[12];
            ContinuitySceneRelation[] relations = new ContinuitySceneRelation[16];
            ContinuityCapabilityEffect[] effects = new ContinuityCapabilityEffect[5];
            for (int index = 0; index < subjects.Length; index++)
                subjects[index] = new ContinuitySceneSubject { kind = "subject " + index, scope = "Real world" };
            for (int index = 0; index < relations.Length; index++)
                relations[index] = new ContinuitySceneRelation {
                    target = "companion", facet = "hands", predicate = "holding",
                    cause = "object " + index, scope = "Real world",
                };
            for (int index = 0; index < effects.Length; index++)
                effects[index] = new ContinuityCapabilityEffect {
                    target = "companion", capability = "capability " + index,
                    state = "constrained", cause = "cause " + index,
                };

            string scene = ContinuityPanelState.SceneText(subjects, relations, effects);

            Assert.That(scene, Does.Contain("SCENE SUBJECTS"));
            Assert.That(scene, Does.Contain("subject 11"));
            Assert.That(scene, Does.Contain("RELATIONS"));
            Assert.That(scene, Does.Contain("object 15"));
            Assert.That(scene, Does.Contain("DERIVED CAPABILITIES"));
            Assert.That(scene, Does.Contain("Capability 4"));
        }

        [Test]
        public void SceneTextGroupsCapabilityCausesForCompanionAndUser()
        {
            ContinuityCapabilityEffect[] effects = {
                new ContinuityCapabilityEffect {
                    target = "companion", capability = "vision", state = "unavailable",
                    cause = "blindfold, user hands",
                },
                new ContinuityCapabilityEffect {
                    target = "companion", capability = "speech", state = "constrained",
                    cause = "user hand",
                },
                new ContinuityCapabilityEffect {
                    target = "user", capability = "hands", state = "partially_occupied",
                    cause = "cup",
                },
            };

            string scene = ContinuityPanelState.SceneText(null, null, effects);

            Assert.That(scene, Does.Contain("<b>Companion</b>"));
            Assert.That(scene, Does.Contain("• Vision: unavailable\n  - blindfold\n  - user hands"));
            Assert.That(scene, Does.Contain("• Speech: constrained\n  - user hand"));
            Assert.That(scene, Does.Contain("<b>User</b>"));
            Assert.That(scene, Does.Contain("• Hands: partially occupied\n  - cup"));
        }

        [Test]
        public void SceneDetailsUsesDedicatedClampedScrollWithVisibleScrollbar()
        {
            GameObject root = new GameObject("scene-details-controller", typeof(AIFrenPocController));
            GameObject parent = new GameObject("context-page", typeof(RectTransform));
            parent.transform.SetParent(root.transform, false);
            try
            {
                AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
                SetField(controller, "theme", PresentationThemes.Dark);
                SetField(controller, "font", TMP_Settings.defaultFontAsset);
                object[] arguments = { parent.transform, -20f, 230f };
                TMP_Text value = (TMP_Text)typeof(AIFrenPocController).GetMethod(
                    "AddSceneDetailsView", BindingFlags.Instance | BindingFlags.NonPublic
                ).Invoke(controller, arguments);
                ScrollRect scroll = (ScrollRect)Field(controller, "continuitySceneScroll");

                Assert.That(scroll, Is.Not.Null);
                Assert.That(scroll.viewport, Is.Not.Null);
                Assert.That(scroll.content, Is.SameAs(value.rectTransform));
                Assert.That(scroll.horizontal, Is.False);
                Assert.That(scroll.vertical, Is.True);
                Assert.That(scroll.movementType, Is.EqualTo(ScrollRect.MovementType.Clamped));
                Assert.That(scroll.verticalScrollbar, Is.Not.Null);
                Assert.That(value.enableWordWrapping, Is.True);
                Assert.That(value.overflowMode, Is.EqualTo(TextOverflowModes.Overflow));
            }
            finally
            {
                Object.DestroyImmediate(parent);
                Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void ContinuityControlPendingAndAcknowledgementPreserveConversationPresentation()
        {
            GameObject root = new GameObject("continuity-controller", typeof(AIFrenPocController));
            try
            {
                AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
                SetField(controller, "visibleState", "Ready");
                SetField(controller, "detail", "Previous conversational status");
                SetField(controller, "currentAssistantPresentationText", "Previous assistant message");
                SetField(controller, "submitInFlight", true);

                controller.BeginPendingContinuityControl(
                    "command-1", "resolve_thread", "0", "revision-1");
                AssertConversationPresentationUnchanged(controller);

                controller.CompletePendingContinuityControl(new ContinuitySnapshot {
                    scope = new TruthScopeSnapshot { kind = "real_world", label = "Real world" },
                    open_threads = System.Array.Empty<ContinuityThread>(),
                    revision = "revision-2",
                });
                AssertConversationPresentationUnchanged(controller);
                Assert.That(Field(controller, "pendingContinuityCommandId"), Is.Null);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void InWorldSceneMutationSnapshotReleasesPendingUiBeforeReactionCompletes()
        {
            GameObject root = new GameObject("scene-interaction-controller", typeof(AIFrenPocController));
            try
            {
                AIFrenPocController controller = root.GetComponent<AIFrenPocController>();
                controller.BeginPendingContinuityControl(
                    "scene-command", "interact_scene_relation", "relation:0", "revision-1");
                var updated = new ContinuitySnapshot {
                    scope = new TruthScopeSnapshot { kind = "real_world", label = "Real world" },
                    revision = "revision-2",
                };
                controller.ApplyAuthoritativeContinuityChange("scene-command", updated);
                Assert.That(Field(controller, "pendingContinuityCommandId"), Is.Null);
                Assert.That(Field(controller, "authoritativeContinuity"), Is.SameAs(updated));

                controller.BeginPendingContinuityControl(
                    "new-command", "interact_scene_relation", "relation:1", "revision-2");
                controller.ApplyAuthoritativeContinuityChange("old-command", updated);
                Assert.That(Field(controller, "pendingContinuityCommandId"), Is.EqualTo("new-command"));
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        private static void AssertConversationPresentationUnchanged(AIFrenPocController controller)
        {
            Assert.That(Field(controller, "visibleState"), Is.EqualTo("Ready"));
            Assert.That(Field(controller, "detail"), Is.EqualTo("Previous conversational status"));
            Assert.That(Field(controller, "currentAssistantPresentationText"), Is.EqualTo("Previous assistant message"));
            Assert.That(Field(controller, "submitInFlight"), Is.EqualTo(true));
        }

        private static object Field(AIFrenPocController controller, string name)
        {
            return typeof(AIFrenPocController).GetField(
                name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(controller);
        }

        private static void SetField(AIFrenPocController controller, string name, object value)
        {
            typeof(AIFrenPocController).GetField(
                name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(controller, value);
        }
    }
}
