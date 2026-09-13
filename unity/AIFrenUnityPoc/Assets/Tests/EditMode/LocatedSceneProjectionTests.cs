using System.Linq;
using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class LocatedSceneProjectionTests
    {
        // Exact generic backend projection shape from the synthetic player
        // reproduction. Cause already owns its literal location description.
        internal const string SnapshotJson = @"{
            ""revision"":""synthetic-location-revision"",
            ""scene_relations"":[
                {""target"":""companion"",""facet"":"""",""side"":"""",""predicate"":""located_on"",
                 ""cause"":""blue paint on right middle fingernail"",""locus"":""right middle fingernail"",
                 ""effect"":"""",""quantity"":0,""scope"":""Real world"",""can_clear"":true,""clear_token"":""relation:0""},
                {""target"":""companion"",""facet"":"""",""side"":"""",""predicate"":""located_on"",
                 ""cause"":""silver ring on right ring finger"",""locus"":""right ring finger"",
                 ""effect"":"""",""quantity"":0,""scope"":""Real world"",""can_clear"":true,""clear_token"":""relation:1""},
                {""target"":""companion"",""facet"":"""",""side"":"""",""predicate"":""located_on"",
                 ""cause"":""blue glove on left hand"",""locus"":""left hand"",
                 ""effect"":"""",""quantity"":0,""scope"":""Real world"",""can_clear"":true,""clear_token"":""relation:2""}
            ],
            ""scene_subjects"":[{""kind"":""blindfold"",""label"":""blindfold"",""lifecycle"":""dormant"",
                ""can_remove"":true,""remove_token"":""subject:0"",""conditions"":[]}],
            ""capability_effects"":[],""open_threads"":[]
        }";

        [Test]
        public void JsonDecodeRetainsExactTypedLocusAlongsideOwnedDescription()
        {
            var snapshot = JsonUtility.FromJson<ContinuitySnapshot>(SnapshotJson);
            var locus = typeof(ContinuitySceneRelation).GetField("locus");
            Assert.That(locus, Is.Not.Null, "Transport must not discard the typed location from the backend snapshot.");
            CollectionAssert.AreEqual(new[] { "right middle fingernail", "right ring finger", "left hand" },
                snapshot.scene_relations.Select(relation => (string)locus.GetValue(relation)));
        }

        [Test]
        public void LocatedRelationsAndDormantRemovalAllAppearWithoutCapabilityInference()
        {
            var snapshot = JsonUtility.FromJson<ContinuitySnapshot>(SnapshotJson);
            var rows = SceneOverlayState.Rows(snapshot);
            CollectionAssert.AreEqual(new[] {
                "Companion — blue paint on right middle fingernail",
                "Companion — silver ring on right ring finger",
                "Companion — blue glove on left hand",
                "Blindfold (not active)",
            }, rows.Select(row => row.text));
            CollectionAssert.AreEqual(new[] { "relation:0", "relation:1", "relation:2", "subject:0" }, rows.Select(row => row.actionToken));
            Assert.That(rows.Take(3).All(row => row.action == "interact_scene_relation"), Is.True);
            Assert.That(rows[3].action, Is.EqualTo("retire_scene_subject"));
            Assert.That(string.Join(" ", rows.Select(row => row.text)), Does.Not.Contain("constrained"));
        }

        [TestCase("user", "You")]
        [TestCase("companion", "Companion")]
        [TestCase("wooden figure", "Wooden figure")]
        public void GenericLocatedDescriptionPreservesTargetWithoutDuplicatingLocus(string target, string label)
        {
            var relation = new ContinuitySceneRelation {
                target = target, predicate = "located_on", cause = "adhesive patch on upper sleeve",
                locus = "upper sleeve", can_clear = false, clear_token = "unused",
            };
            var row = SceneOverlayState.Rows(new ContinuitySnapshot { scene_relations = new[] { relation } }).Single();
            Assert.That(row.text, Is.EqualTo(label + " — adhesive patch on upper sleeve"));
            Assert.That(row.action, Is.Empty);
            Assert.That(row.actionToken, Is.Empty);
        }

        [Test]
        public void LocatedRowDoesNotHideAnAuthoritativeCapabilityEffectOrInventMissingDescription()
        {
            var relation = new ContinuitySceneRelation {
                target = "companion", predicate = "located_on", cause = "opaque cloth on eyes", locus = "eyes",
                effect = "vision obstruction", can_clear = true, clear_token = "relation:0",
            };
            var snapshot = new ContinuitySnapshot { scene_relations = new[] { relation },
                capability_effects = new[] { new ContinuityCapabilityEffect { target = "companion", capability = "vision", state = "unavailable" } }
            };
            Assert.That(SceneOverlayState.Rows(snapshot).Single().text, Is.EqualTo("Companion Vision unavailable — opaque cloth on eyes"));
            relation.cause = "";
            Assert.That(SceneOverlayState.Rows(snapshot), Is.Empty, "A location alone must not manufacture an object description.");
        }
    }
}
