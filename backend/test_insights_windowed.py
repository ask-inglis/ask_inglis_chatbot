import json
import unittest
from datetime import date

from insights_windowed_reporting import prepare_windowed_report
from insights_publication import publish_insights


class WindowedInsightsTests(unittest.TestCase):

    def setUp(self):
        self.today = date(2026, 11, 23)
        self.usage = {
            "weeks": {
                "2026-W47": {
                    "answers": 100,
                    "modes": {
                        "chat": 70,
                        "quiz": 20,
                        "dissection": 10,
                    },
                    "lectures": {
                        "Private Test Topic": 80,
                    },
                    "quiz": {
                        "Private Test Topic": [30, 12],
                    },
                },
                "2026-W48": {
                    "answers": 9999,
                    "lectures": {
                        "Incomplete Week": 9999,
                    },
                },
            }
        }

    def test_completed_weeks_only(self):
        result = prepare_windowed_report(
            self.usage, weeks=4, today=self.today
        )

        self.assertEqual(
            result["weekly_trends"][-1]["week"],
            "2026-W47",
        )
        self.assertEqual(
            result["weekly_trends"][-1]["answers"],
            100,
        )
        self.assertEqual(
            len(result["reporting_windows"]), 4
        )

    def test_current_week_excluded(self):
        result = prepare_windowed_report(
            self.usage, weeks=4, today=self.today
        )

        self.assertNotIn(
            "Incomplete Week",
            json.dumps(result),
        )

    def test_all_reporting_periods_stay_private(self):
        for weeks in (4, 8, 12, 26):
            with self.subTest(weeks=weeks):
                internal = prepare_windowed_report(
                    self.usage,
                    weeks=weeks,
                    today=self.today,
                )

                public = publish_insights(
                    internal_aggregates=internal,
                    weeks=weeks,
                )

                self.assertFalse(
                    internal["privacy_approved"]
                )
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
                self.assertNotIn(
                    "Private Test Topic",
                    json.dumps(public),
                )

    def test_invalid_periods_rejected(self):
        for weeks in (0, 27, "4", True):
            with self.subTest(weeks=weeks):
                with self.assertRaises(ValueError):
                    prepare_windowed_report(
                        self.usage,
                        weeks=weeks,
                        today=self.today,
                    )


if __name__ == "__main__":
    unittest.main()
