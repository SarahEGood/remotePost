import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol


PENDING = "pending"
DELIVERING = "delivering"
DELIVERED = "delivered"
FAILED = "failed"
UNKNOWN_OUTCOME = "unknown_outcome"

RETRYABLE_FAILURE = "retryable_failure"
PERMANENT_FAILURE = "permanent_failure"


@dataclass
class Image:
    path: str
    alt_text: Optional[str] = None


@dataclass
class Payload:
    text: str
    images: List[Image] = field(default_factory=list)


@dataclass
class ScheduledDelivery:
    id: int
    platform: str
    target_account: str
    payload: Payload
    run_at: datetime
    attempt_count: int
    delivery_state: str
    next_attempt_at: datetime


@dataclass
class DeliveryAttempt:
    outcome: str
    error_message: Optional[str] = None
    receipt: Optional[dict] = None


@dataclass
class RunSummary:
    claimed: int = 0
    delivered: int = 0
    failed: int = 0
    unknown_outcome: int = 0
    requeued: int = 0


class PlatformAdapter(Protocol):
    def deliver(self, payload: Payload, target_account: str) -> DeliveryAttempt:
        ...


def create_payload(
    text: str, image_paths: Optional[List[str]] = None, alt_text: Optional[List[str]] = None
) -> Payload:
    images: List[Image] = []
    image_paths = image_paths or []
    alt_text = alt_text or []

    for index, path in enumerate(image_paths):
        image_alt_text = alt_text[index] if index < len(alt_text) else None
        images.append(Image(path=path, alt_text=image_alt_text))

    return Payload(text=text, images=images)


def serialize_payload(payload: Payload) -> str:
    return json.dumps(asdict(payload))


def deserialize_payload(payload_json: str) -> Payload:
    data = json.loads(payload_json)
    images = [Image(**image_data) for image_data in (data.get("images") or [])]
    return Payload(text=data["text"], images=images)


class ScheduledDeliveryModule:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        lease_duration: timedelta = timedelta(minutes=5),
        retry_delays: Optional[List[timedelta]] = None,
        max_attempts: int = 3,
    ) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.lease_duration = lease_duration
        self.retry_delays = retry_delays or [
            timedelta(minutes=1),
            timedelta(minutes=5),
            timedelta(minutes=15),
        ]
        self.max_attempts = max_attempts
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY,
                platform TEXT,
                post_json TEXT,
                run_at DATETIME,
                status TEXT
            )
            """
        )

        required_columns = {
            "target_account": "TEXT",
            "payload_json": "TEXT",
            "delivery_state": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "TEXT",
            "leased_until": "TEXT",
            "last_error": "TEXT",
            "delivered_at": "TEXT",
            "receipt_json": "TEXT",
        }

        existing_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(scheduled_posts)").fetchall()
        }

        for column_name, column_definition in required_columns.items():
            if column_name not in existing_columns:
                self.conn.execute(
                    f"ALTER TABLE scheduled_posts ADD COLUMN {column_name} {column_definition}"
                )

        self.conn.execute(
            """
            UPDATE scheduled_posts
            SET payload_json = COALESCE(payload_json, post_json),
                target_account = COALESCE(target_account, 'default'),
                delivery_state = COALESCE(
                    delivery_state,
                    CASE status
                        WHEN 'done' THEN 'delivered'
                        WHEN 'failed' THEN 'failed'
                        WHEN 'delivering' THEN 'delivering'
                        WHEN 'unknown_outcome' THEN 'unknown_outcome'
                        ELSE 'pending'
                    END
                ),
                next_attempt_at = COALESCE(next_attempt_at, run_at),
                delivered_at = CASE
                    WHEN delivery_state = 'delivered' AND delivered_at IS NULL THEN run_at
                    ELSE delivered_at
                END
            """
        )
        self.conn.commit()

    def schedule_delivery(
        self,
        payload: Payload,
        platform: str,
        run_at: datetime,
        *,
        target_account: str = "default",
    ) -> int:
        run_at_utc = _coerce_utc(run_at)
        payload_json = serialize_payload(payload)
        legacy_state = _legacy_status(PENDING)

        cursor = self.conn.execute(
            """
            INSERT INTO scheduled_posts (
                platform,
                post_json,
                payload_json,
                run_at,
                status,
                delivery_state,
                target_account,
                attempt_count,
                next_attempt_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform,
                payload_json,
                payload_json,
                _to_storage(run_at_utc),
                legacy_state,
                PENDING,
                target_account,
                0,
                _to_storage(run_at_utc),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def run_due_deliveries(
        self,
        limit: int,
        adapters: Dict[str, PlatformAdapter],
        *,
        now: Optional[datetime] = None,
    ) -> RunSummary:
        current_time = _coerce_utc(now or datetime.now(timezone.utc))
        summary = RunSummary()
        summary.unknown_outcome += self._mark_expired_leases_unknown(current_time)

        deliveries = self._claim_due_deliveries(limit, current_time)
        summary.claimed = len(deliveries)

        for delivery in deliveries:
            adapter = adapters.get(delivery.platform)
            if adapter is None:
                self._mark_failed(delivery.id, "No platform adapter configured.", current_time)
                summary.failed += 1
                continue

            try:
                attempt = adapter.deliver(delivery.payload, delivery.target_account)
            except Exception as exc:
                self._mark_unknown_outcome(
                    delivery.id,
                    f"Unhandled adapter exception: {exc}",
                    current_time,
                )
                summary.unknown_outcome += 1
                continue

            if attempt.outcome == DELIVERED:
                self._mark_delivered(delivery.id, attempt.receipt or {}, current_time)
                summary.delivered += 1
                continue

            if attempt.outcome == RETRYABLE_FAILURE:
                if delivery.attempt_count >= self.max_attempts:
                    self._mark_failed(
                        delivery.id,
                        attempt.error_message or "Retry attempts exhausted.",
                        current_time,
                    )
                    summary.failed += 1
                else:
                    self._requeue_delivery(delivery, attempt.error_message, current_time)
                    summary.requeued += 1
                continue

            if attempt.outcome == PERMANENT_FAILURE:
                self._mark_failed(
                    delivery.id,
                    attempt.error_message or "Permanent delivery failure.",
                    current_time,
                )
                summary.failed += 1
                continue

            self._mark_unknown_outcome(
                delivery.id,
                f"Unknown adapter outcome: {attempt.outcome}",
                current_time,
            )
            summary.unknown_outcome += 1

        return summary

    def mark_unknown_outcome_delivered(
        self, delivery_id: int, receipt: dict, *, now: Optional[datetime] = None
    ) -> None:
        current_time = _coerce_utc(now or datetime.now(timezone.utc))
        self._update_state(
            delivery_id,
            delivery_state=DELIVERED,
            delivered_at=current_time,
            receipt_json=json.dumps(receipt),
            leased_until=None,
            last_error=None,
            next_attempt_at=None,
        )

    def resolve_unknown_outcome_as_failed(
        self, delivery_id: int, *, requeue: bool = False, now: Optional[datetime] = None
    ) -> None:
        current_time = _coerce_utc(now or datetime.now(timezone.utc))
        if requeue:
            self._update_state(
                delivery_id,
                delivery_state=PENDING,
                next_attempt_at=current_time,
                leased_until=None,
                last_error="Requeued after manual unknown_outcome review.",
            )
            return

        self._mark_failed(
            delivery_id,
            "Marked failed after manual unknown_outcome review.",
            current_time,
        )

    def _claim_due_deliveries(
        self, limit: int, now: datetime
    ) -> List[ScheduledDelivery]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM scheduled_posts
            WHERE delivery_state = ?
              AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, id ASC
            LIMIT ?
            """,
            (PENDING, _to_storage(now), limit),
        ).fetchall()

        deliveries: List[ScheduledDelivery] = []
        lease_until = now + self.lease_duration

        for row in rows:
            updated = self.conn.execute(
                """
                UPDATE scheduled_posts
                SET delivery_state = ?,
                    status = ?,
                    attempt_count = COALESCE(attempt_count, 0) + 1,
                    leased_until = ?,
                    last_error = NULL
                WHERE id = ?
                  AND delivery_state = ?
                """,
                (
                    DELIVERING,
                    _legacy_status(DELIVERING),
                    _to_storage(lease_until),
                    row["id"],
                    PENDING,
                ),
            )
            if updated.rowcount != 1:
                continue

            deliveries.append(
                ScheduledDelivery(
                    id=row["id"],
                    platform=row["platform"],
                    target_account=row["target_account"] or "default",
                    payload=deserialize_payload(row["payload_json"] or row["post_json"]),
                    run_at=_from_storage(row["run_at"]),
                    attempt_count=int(row["attempt_count"] or 0) + 1,
                    delivery_state=DELIVERING,
                    next_attempt_at=_from_storage(row["next_attempt_at"] or row["run_at"]),
                )
            )

        self.conn.commit()
        return deliveries

    def _mark_expired_leases_unknown(self, now: datetime) -> int:
        cursor = self.conn.execute(
            """
            UPDATE scheduled_posts
            SET delivery_state = ?,
                status = ?,
                leased_until = NULL,
                last_error = ?
            WHERE delivery_state = ?
              AND leased_until IS NOT NULL
              AND leased_until < ?
            """,
            (
                UNKNOWN_OUTCOME,
                _legacy_status(UNKNOWN_OUTCOME),
                "Delivery lease expired before a receipt was recorded.",
                DELIVERING,
                _to_storage(now),
            ),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def _mark_delivered(self, delivery_id: int, receipt: dict, now: datetime) -> None:
        self._update_state(
            delivery_id,
            delivery_state=DELIVERED,
            delivered_at=now,
            receipt_json=json.dumps(receipt),
            leased_until=None,
            last_error=None,
            next_attempt_at=None,
        )

    def _mark_failed(self, delivery_id: int, error_message: str, now: datetime) -> None:
        self._update_state(
            delivery_id,
            delivery_state=FAILED,
            leased_until=None,
            last_error=error_message,
            next_attempt_at=None,
            delivered_at=None,
            receipt_json=None,
        )

    def _mark_unknown_outcome(self, delivery_id: int, error_message: str, now: datetime) -> None:
        self._update_state(
            delivery_id,
            delivery_state=UNKNOWN_OUTCOME,
            leased_until=None,
            last_error=error_message,
            next_attempt_at=None,
        )

    def _requeue_delivery(
        self, delivery: ScheduledDelivery, error_message: Optional[str], now: datetime
    ) -> None:
        retry_delay = self.retry_delays[min(delivery.attempt_count - 1, len(self.retry_delays) - 1)]
        next_attempt_at = now + retry_delay
        self._update_state(
            delivery.id,
            delivery_state=PENDING,
            leased_until=None,
            next_attempt_at=next_attempt_at,
            last_error=error_message,
        )

    def _update_state(
        self,
        delivery_id: int,
        *,
        delivery_state: str,
        leased_until: Optional[datetime] = None,
        next_attempt_at: Optional[datetime] = None,
        last_error: Optional[str] = None,
        delivered_at: Optional[datetime] = None,
        receipt_json: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE scheduled_posts
            SET delivery_state = ?,
                status = ?,
                leased_until = ?,
                next_attempt_at = ?,
                last_error = ?,
                delivered_at = ?,
                receipt_json = ?
            WHERE id = ?
            """,
            (
                delivery_state,
                _legacy_status(delivery_state),
                _to_storage(leased_until) if leased_until else None,
                _to_storage(next_attempt_at) if next_attempt_at else None,
                last_error,
                _to_storage(delivered_at) if delivered_at else None,
                receipt_json,
                delivery_id,
            ),
        )
        self.conn.commit()


def _legacy_status(delivery_state: str) -> str:
    if delivery_state == DELIVERED:
        return "done"
    return delivery_state


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_storage(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _coerce_utc(value).isoformat()


def _from_storage(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
