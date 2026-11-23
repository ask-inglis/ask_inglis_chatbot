import json
import unittest
from datetime import date

from insights_validated_pipeline import prepare_validated_report
from insights_disclosure_policy import prepare_banded_topics
from insights_publication import publish_insights


class InsightsBandingTests(unittest.TestCase):

    def test_complete_internal_pipeline(self):
        usage = {
            "weeks": {
                "2026-W47": {
                    "answers": 75,
                    "modes": {"chat": 60, "quiz": 15},
                    "lectures": {
                        "Upper Limb": 60,
                        "Rare Topic": 5,
                    },
                    "quiz": {"Upper Limb": [20, 8]},
                }
            }
        }

        candidate = prepare_validated_report(
            usage,
            weeks=4,
            today=date(2026, 11, 23),
        )

        banded = prepare_banded_topics(candidate)

        self.assertEqual(banded["status"], "internal_only")
        self.assertEqual(
            banded["topics"],
            [{
                "lecture": "Upper Limb",
                "activity_level": "high",
            }],
        )

        self.assertNotIn("60", json.dumps(banded))
        self.assertNotIn("Rare Topic", json.dumps(banded))

        public = publish_insights(
            internal_aggregates=banded,
            weeks=4,
        )

        self.assertEqual(
            public["status"],
            "insufficient_anonymous_data",
        )
        self.assertNotIn("Upper Limb", json.dumps(public))

    def test_invalid_counts_rejected(self):
        for count in (-1, True, 2.5, "50"):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    prepare_banded_topics({
                        "privacy_approved": False,
                        "frequently_asked_topics": [{
                            "lecture": "Test Topic",
                            "interactions": count,
                        }],
                    })


if __name__ == "__main__":
    unittest.main()
