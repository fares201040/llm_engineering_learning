import unittest

from week5.new_implementation import query


class QueryFacadeTests(unittest.TestCase):
    def test_decision_boundary_is_public_without_exposing_provider_proposals(self):
        self.assertIn("decide_planning_needs", query.__all__)
        self.assertIn("build_planning_draft", query.__all__)
        self.assertIn("assemble_grounded_proposal", query.__all__)
        self.assertIsNotNone(query.PlannerDecision)


if __name__ == "__main__":
    unittest.main()
