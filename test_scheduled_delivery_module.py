import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from scheduled_delivery import (
    DELIVERED,
    FAILED,
    PENDING,
    RETRYABLE_FAILURE,
    UNKNOWN_OUTCOME,
    DeliveryAttempt,
    ScheduledDeliveryModule,
    create_payload,
)


class FakeAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def deliver(self, payload, target_account):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ScheduledDeliveryModuleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.module = ScheduledDeliveryModule(
            self.conn,
            lease_duration=timedelta(minutes=1),
            retry_delays=[timedelta(minutes=2)],
            max_attempts=2,
        )

    def tearDown(self):
        self.conn.close()

    def test_delivered_receipt_is_persisted(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        payload = create_payload("hello world")
        self.module.schedule_delivery(payload, "bluesky", now)

        summary = self.module.run_due_deliveries(
            5,
            {
                "bluesky": FakeAdapter(
                    [
                        DeliveryAttempt(
                            outcome=DELIVERED,
                            receipt={"remote_uri": "at://example/post/1"},
                        )
                    ]
                )
            },
            now=now,
        )

        row = self.conn.execute(
            """
            SELECT delivery_state, receipt_json, delivered_at
            FROM scheduled_posts
            """
        ).fetchone()

        self.assertEqual(summary.claimed, 1)
        self.assertEqual(summary.delivered, 1)
        self.assertEqual(row[0], DELIVERED)
        self.assertIn("at://example/post/1", row[1])
        self.assertIsNotNone(row[2])

    def test_retryable_failure_is_requeued_before_terminal_failure(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        payload = create_payload("retry me")
        self.module.schedule_delivery(payload, "bluesky", now)

        first_summary = self.module.run_due_deliveries(
            5,
            {
                "bluesky": FakeAdapter(
                    [
                        DeliveryAttempt(
                            outcome=RETRYABLE_FAILURE,
                            error_message="temporary outage",
                        )
                    ]
                )
            },
            now=now,
        )

        row = self.conn.execute(
            """
            SELECT delivery_state, attempt_count, next_attempt_at, last_error
            FROM scheduled_posts
            """
        ).fetchone()

        self.assertEqual(first_summary.requeued, 1)
        self.assertEqual(row[0], PENDING)
        self.assertEqual(row[1], 1)
        self.assertIn("temporary outage", row[3])
        self.assertGreater(datetime.fromisoformat(row[2]), now)

        second_summary = self.module.run_due_deliveries(
            5,
            {
                "bluesky": FakeAdapter(
                    [
                        DeliveryAttempt(
                            outcome=RETRYABLE_FAILURE,
                            error_message="still down",
                        )
                    ]
                )
            },
            now=now + timedelta(minutes=3),
        )

        row = self.conn.execute(
            """
            SELECT delivery_state, attempt_count, last_error
            FROM scheduled_posts
            """
        ).fetchone()

        self.assertEqual(second_summary.failed, 1)
        self.assertEqual(row[0], FAILED)
        self.assertEqual(row[1], 2)
        self.assertIn("still down", row[2])

    def test_expired_lease_becomes_unknown_outcome(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        payload = create_payload("lease me")
        self.module.schedule_delivery(payload, "bluesky", now)

        self.module.run_due_deliveries(
            5,
            {"bluesky": FakeAdapter([RuntimeError("boom")])},
            now=now,
        )

        row = self.conn.execute(
            """
            SELECT delivery_state
            FROM scheduled_posts
            """
        ).fetchone()
        self.assertEqual(row[0], UNKNOWN_OUTCOME)

        self.conn.execute(
            """
            UPDATE scheduled_posts
            SET delivery_state = ?, leased_until = ?, status = ?
            """,
            ("delivering", (now - timedelta(minutes=2)).isoformat(), "delivering"),
        )
        self.conn.commit()

        summary = self.module.run_due_deliveries(5, {"bluesky": FakeAdapter([])}, now=now)
        row = self.conn.execute(
            """
            SELECT delivery_state, last_error
            FROM scheduled_posts
            """
        ).fetchone()

        self.assertEqual(summary.unknown_outcome, 1)
        self.assertEqual(row[0], UNKNOWN_OUTCOME)
        self.assertIn("lease expired", row[1].lower())


if __name__ == "__main__":
    unittest.main()
