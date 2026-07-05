import unittest

from bluesky_delivery import BlueskyDeliveryAdapter
from scheduled_delivery import RETRYABLE_FAILURE, create_payload


class BlankFailureClient:
    def login(self, handle, password):
        raise Exception()


class BlueskyDeliveryAdapterTests(unittest.TestCase):
    def test_blank_exception_still_returns_diagnostic_message(self):
        adapter = BlueskyDeliveryAdapter(
            {"default": ("handle", "password")},
            client_factory=BlankFailureClient,
        )

        attempt = adapter.deliver(create_payload("hello"), "default")

        self.assertEqual(attempt.outcome, RETRYABLE_FAILURE)
        self.assertIn("Exception", attempt.error_message)


if __name__ == "__main__":
    unittest.main()
