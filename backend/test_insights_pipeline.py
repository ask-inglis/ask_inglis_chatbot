import unittest
from datetime import date

from insights_aggregates import prepare_weekly_aggregates
from insights_contributors import verify_contributors
from insights_privacy import build_private_insights


class InsightsPipelineTests(unittest.TestCase):

    def test_large_usage_stays_private(self):
        usage = {
            "weeks": {
                "2026-W47": {
                    "answers": 10000,
                    "modes": {
                        "chat": 8000,
                        "quiz": 1500,
                        "dissection": 500,
                    },
                    "lectures": {
                        "Upper Limb": 6000,
                        "Heart": 4000,
                    },
                    "quiz": {
                        "Upper Limb": [1000, 450],
                    },
                }
            }
        }

        internal = prepare_weekly_aggregates(
            usage,
            weeks=4,
            today=date(2026, 11, 18),
        )

        self.assertEqual(
            internal["frequently_asked_topics"][0]["interactions"],
            6000,
        )

        self.assertFalse(internal["privacy_approved"])

        for source in (
            "browser_sessions",
            "chat_interactions",
            "quiz_attempts",
        ):
            self.assertFalse(
                verify_contributors(
                    10000,
                    source,
                    independently_verified=True,
                )["verified"]
            )

        public = build_private_insights(4)

        self.assertEqual(
            public["status"],
            "insufficient_anonymous_data",
        )

        self.assertEqual(
            public["frequently_asked_topics"]["items"],
            [],
        )

        self.assertEqual(
            public["quiz_performance"]["items"],
            [],
        )

        self.assertEqual(
            public["weekly_trends"]["items"],
            [],
        )

        self.assertEqual(public["recommendations"], [])

    def test_unverified_counts_rejected(self):
        for count in (0, 1, 9, 10, 100, 10000):
            with self.subTest(count=count):
                result = verify_contributors(
                    count,
                    "approved_course_aggregate",
                    independently_verified=False,
                )
                self.assertFalse(result["verified"])

    def test_invalid_contributor_counts_rejected(self):
        for count in (True, None, -1, 10.5, "10"):
            with self.subTest(count=count):
                result = verify_contributors(
                    count,
                    "approved_course_aggregate",
                    independently_verified=True,
                )
                self.assertFalse(result["verified"])


if __name__ == "__main__":
    unittest.main()
