import json
import unittest

from insights_publication import publish_insights
from insights_reporting_policy import (
    validate_aggregate_group,
    build_reporting_status,
)


class InsightsReportingTests(unittest.TestCase):

    def test_untrusted_metadata_cannot_publish(self):
        """Caller-supplied verification must never unlock statistics."""

        untrusted = {
            "verified_contributors": 10000,
            "independently_verified": True,
            "source": "approved_course_aggregate",
            "privacy_approved": True,
            "frequently_asked_topics": [
                {
                    "lecture": "Sensitive Test Lecture",
                    "interactions": 5000,
                }
            ],
            "quiz_performance": [
                {
                    "lecture": "Sensitive Test Lecture",
                    "answered": 1000,
                    "missed": 900,
                }
            ],
        }

        # Structural validation alone does not establish trust.
        self.assertTrue(validate_aggregate_group(untrusted))

        result = publish_insights(
            internal_aggregates=untrusted,
            weeks=4,
        )

        serialized = json.dumps(result)

        self.assertEqual(
            result["status"],
            "insufficient_anonymous_data",
        )
        self.assertNotIn("Sensitive Test Lecture", serialized)
        self.assertNotIn("5000", serialized)
        self.assertEqual(
            result["frequently_asked_topics"]["items"],
            [],
        )
        self.assertEqual(
            result["quiz_performance"]["items"],
            [],
        )

    def test_verified_flag_cannot_override_privacy(self):
        data = {
            "privacy_approved": True,
            "verified_contributors": 50,
            "independently_verified": True,
            "source": "approved_course_aggregate",
        }

        result = publish_insights(data, weeks=8)

        self.assertFalse(
            result["privacy"]["individual_data_exposed"]
        )
        self.assertEqual(
            result["privacy"]["contributor_verification"],
            "unavailable",
        )
        self.assertEqual(result["recommendations"], [])

    def test_reporting_status_does_not_enable_statistics(self):
        result = build_reporting_status(12)

        self.assertFalse(result["statistics_available"])
        self.assertEqual(
            result["source_verification"],
            "not_configured",
        )


if __name__ == "__main__":
    unittest.main()
