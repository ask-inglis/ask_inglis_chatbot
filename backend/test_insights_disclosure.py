import json
import unittest
from datetime import date

from insights_validated_pipeline import prepare_validated_report
from insights_publication import publish_insights


class InsightsDisclosureTests(unittest.TestCase):

    def test_many_interactions_do_not_prove_many_students(self):
        usage = {
            "weeks": {
                "2026-W47": {
                    "answers": 10000,
                    "modes": {"chat": 10000},
                    "lectures": {"Private Anatomy Topic": 10000},
                    "quiz": {},
                }
            }
        }

        candidate = prepare_validated_report(
            usage, weeks=4, today=date(2026, 11, 23)
        )

        self.assertEqual(
            candidate["frequently_asked_topics"][0]["interactions"],
            10000,
        )

        public = publish_insights(candidate, weeks=4)

        self.assertEqual(
            public["frequently_asked_topics"]["items"], []
        )
        self.assertNotIn(
            "Private Anatomy Topic", json.dumps(public)
        )

    def test_overlapping_periods_cannot_expose_hidden_counts(self):
        usage = {
            "weeks": {
                "2026-W43": {
                    "answers": 3,
                    "modes": {"chat": 3},
                    "lectures": {"Rare Anatomy Topic": 3},
                    "quiz": {},
                },
                "2026-W47": {
                    "answers": 100,
                    "modes": {"chat": 100},
                    "lectures": {"Rare Anatomy Topic": 100},
                    "quiz": {},
                },
            }
        }

        candidate_4 = prepare_validated_report(
            usage, weeks=4, today=date(2026, 11, 23)
        )
        candidate_8 = prepare_validated_report(
            usage, weeks=8, today=date(2026, 11, 23)
        )

        # The internal reports differ, so releasing both
        # could allow subtraction to reveal smaller groups.
        self.assertNotEqual(
            candidate_4["frequently_asked_topics"],
            candidate_8["frequently_asked_topics"],
        )

        public_4 = publish_insights(candidate_4, weeks=4)
        public_8 = publish_insights(candidate_8, weeks=8)

        self.assertEqual(
            public_4["frequently_asked_topics"]["items"], []
        )
        self.assertEqual(
            public_8["frequently_asked_topics"]["items"], []
        )

    def test_untrusted_approval_flags_do_not_publish(self):
        candidate = {
            "privacy_approved": True,
            "verified_contributors": 1000,
            "independently_verified": True,
            "source": "approved_course_aggregate",
            "frequently_asked_topics": [
                {"lecture": "Sensitive Topic", "interactions": 50}
            ],
        }

        public = publish_insights(candidate, weeks=4)

        self.assertEqual(
            public["status"], "insufficient_anonymous_data"
        )
        self.assertEqual(
            public["frequently_asked_topics"]["items"], []
        )


if __name__ == "__main__":
    unittest.main()
