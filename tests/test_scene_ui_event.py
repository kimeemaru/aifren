import unittest
from types import SimpleNamespace

from scene_ui_event import scene_ui_clear_event, scene_ui_response_requirement


class SceneUiEventTests(unittest.TestCase):
    @staticmethod
    def event(**fields):
        relation = SimpleNamespace(
            target=fields.get("target", "companion"),
            facet=fields.get("facet"), side=fields.get("side"),
            predicate=fields.get("predicate", "covered_by"),
            cause_kind=fields.get("cause_kind", "scene"),
            cause=fields.get("cause", "item"),
            semantic_family=fields.get("semantic_family"),
        )
        scope = SimpleNamespace(kind="real_world", label="")
        return scene_ui_clear_event(relation, scope)

    def test_supported_relation_families_render_natural_user_side_events(self):
        cases = (
            (dict(facet="eyes", cause="blindfold"), "*I take off your blindfold.*"),
            (dict(facet="eyes", cause_kind="actor_part", cause="user hands"),
             "*I move my hands away from your eyes.*"),
            (dict(facet="mouth", cause_kind="actor_part", cause="user hand"),
             "*I move my hand away from your mouth.*"),
            (dict(facet="ears", predicate="wearing", cause="earplugs"),
             "*I remove the earplugs from your ears.*"),
            (dict(facet="wrists", predicate="wearing", cause="sparkly scrunchie"),
             "*I take off your sparkly scrunchie.*"),
            (dict(facet="wrists", side="left", predicate="tethered_to", cause="pole",
                  cause_kind="environment", semantic_family="restraint"),
             "*I release your left wrist from the restraint.*"),
            (dict(facet="ears", predicate="unavailable_due_to", cause="loud music",
                  cause_kind="environment", semantic_family="hearing_obstruction"),
             "*The loud music stops.*"),
            (dict(predicate="wearing", cause="red scarf"), "*I take off your red scarf.*"),
            (dict(predicate="holding", facet="hands", cause="cup"),
             "*I take the cup from you and set it down.*"),
            (dict(predicate="riding", cause="bicycle"), "*I help you stop using the bicycle.*"),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                event = self.event(**fields)
                self.assertEqual(expected, event.model_text)
                self.assertNotRegex(event.model_text, r"relation:|[0-9a-f]{8}-[0-9a-f-]{27}")
                requirement = scene_ui_response_requirement(event)
                self.assertEqual("must_respect", requirement.mode)
                self.assertEqual("scene_ui_clear", requirement.intent)

    def test_unsafe_database_like_labels_are_not_forwarded(self):
        event = self.event(predicate="wearing", cause="scene-123/../../private")
        self.assertNotIn("scene-123", event.model_text)
        self.assertNotIn("private", event.model_text)


if __name__ == "__main__":
    unittest.main()
