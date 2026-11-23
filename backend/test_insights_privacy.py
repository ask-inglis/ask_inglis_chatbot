import unittest
from datetime import date

from insights_aggregates import prepare_weekly_aggregates
from insights_privacy import (
    MIN_CONTRIBUTORS,
    SECTIONS,
    build_private_insights,
)


class InsightsPrivacyTests(unittest.TestCase):

    def test_contributor_threshold(self):
        self.assertEqual(MIN_CONTRIBUTORS, 10)

    def test_all_sections_are_suppressed(self):
        result = build_private_insights(4)

        self.assertEqual(
            result["status"],
            "insufficient_anonymous_data",
        )
        self.assertEqual(
            result["privacy"]["contributor_verification"],
            "unavailable",
        )
        self.assertFalse(
            result["privacy"]["individual_data_exposed"]
        )

        for section in SECTIONS:
            self.assertEqual(result[section]["items"], [])
            self.assertEqual(
                result[section]["status"],
                "insufficient_anonymous_data",
            )

        self.assertEqual(result["recommendations"], [])

    def test_invalid_time_ranges_rejected(self):
        for weeks in [0, 27, -1, "4", True, None]:
            with self.subTest(weeks=weeks):
                with self.assertRaises(ValueError):
                    build_private_insights(weeks)

    def test_internal_counts_are_not_approved(self):
        sample = {
            "weeks": {
                "2026-W47": {
                    "answers": 500,
                    "modes": {"chat": 400, "quiz": 100},
                    "lectures": {"Upper Limb": 500},
                    "quiz": {"Upper Limb": [100, 40]},
                }
            }
        }

        internal = prepare_weekly_aggregates(
            sample,
            weeks=4,
            today=date(2026, 11, 18),
        )

        self.assertFalse(internal["privacy_approved"])
        self.assertEqual(
            internal["data_type"],
            "interaction_counts_only",
        )

        # Even 500 interactions do not prove 10 students.
        public = build_private_insights(4)

        self.assertEqual(
            public["frequently_asked_topics"]["items"], []
        )
        self.assertEqual(
            public["quiz_performance"]["items"], []
        )

    def test_weekly_calculations(self):
        sample = {
            "weeks": {
                "2026-W47": {
                    "answers": 20,
                    "modes": {
                        "chat": 12,
                        "quiz": 5,
                        "dissection": 3,
                    },
                    "lectures": {
                        "Upper Limb": 12,
                        "Heart": 8,
                    },
                    "quiz": {
                        "Upper Limb": [10, 4],
                    },
                }
            }
        }

        result = prepare_weekly_aggregates(
            sample,
            weeks=4,
            today=date(2026, 11, 18),
        )

        self.assertEqual(len(result["weekly_trends"]), 4)
        self.assertEqual(
            result["weekly_trends"][-1]["answers"], 20
        )
        self.assertEqual(
            result["frequently_asked_topics"][0]["lecture"],
            "Upper Limb",
        )
        self.assertEqual(
            result["quiz_performance"][0]["miss_rate"],
            0.4,
        )

    def test_negative_and_invalid_counts(self):
        sample = {
            "weeks": {
                "2026-W47": {
                    "answers": -10,
                    "modes": {"chat": True},
                    "lectures": {"Upper Limb": -5},
                    "quiz": {"Upper Limb": [5, 9]},
                }
            }
        }

        result = prepare_weekly_aggregates(
            sample,
            weeks=4,
            today=date(2026, 11, 18),
        )

        self.assertEqual(result["weekly_trends"][-1]["answers"], 0)
        self.assertEqual(result["weekly_trends"][-1]["chat"], 0)
        self.assertEqual(result["frequently_asked_topics"], [])
        self.assertEqual(result["quiz_performance"], [])


if __name__ == "__main__":
    unittest.main()
