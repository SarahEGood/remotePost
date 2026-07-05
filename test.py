import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bluesky_delivery import BlueskyDeliveryAdapter
from credentials_loader import load_credentials
from scheduled_delivery import (
    DELIVERED,
    Payload,
    ScheduledDeliveryModule,
    create_payload,
)

Post = Payload


def create_post(text, image_paths=None, alt_text=None):
    return create_payload(text=text, image_paths=image_paths, alt_text=alt_text)


def schedulePost(
    lifecycle: ScheduledDeliveryModule,
    post: Post,
    platform: str,
    run_at: datetime,
    target_account: str = "default",
):
    return lifecycle.schedule_delivery(
        post,
        platform,
        run_at,
        target_account=target_account,
    )


def workerLoop(lifecycle: ScheduledDeliveryModule, creds, poll_interval: int = 10):
    adapters = {
        "bluesky": BlueskyDeliveryAdapter(creds),
    }

    while True:
        summary = lifecycle.run_due_deliveries(
            limit=10,
            adapters=adapters,
            now=datetime.now(timezone.utc),
        )
        print(summary)
        time.sleep(poll_interval)

def getCredentials():
    return load_credentials()


def postToBluesky(creds, post: Post, account: str = "default"):
    attempt = BlueskyDeliveryAdapter(creds).deliver(post, account)
    if attempt.outcome != DELIVERED:
        raise RuntimeError(attempt.error_message or f"Delivery failed: {attempt.outcome}")
    return attempt.receipt

if __name__ == '__main__':
    conn = sqlite3.connect('scheduler.db')
    lifecycle = ScheduledDeliveryModule(conn)

    # Year, Month, Day, Hour, Minute, Second
    local_time = datetime(2026, 4, 26, 8, tzinfo=ZoneInfo("America/Los_Angeles"))
    print(local_time)
    run_at = local_time.astimezone(ZoneInfo("Etc/UTC"))
    print(run_at)

    post = create_post(
        "Jojos but like, what if horse girl #Jjba #umamusume",
        ['images/uma_kakyoin01.png'],
        alt_text=['']
    )

    creds = getCredentials()
    schedulePost(lifecycle, post, 'bluesky', run_at, target_account='default')
    workerLoop(lifecycle, creds)
