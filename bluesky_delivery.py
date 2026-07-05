from typing import Any, Callable, Dict, Optional

from atproto import Client

from credentials_loader import CredentialMap, select_credentials
from scheduled_delivery import (
    DELIVERED,
    PERMANENT_FAILURE,
    RETRYABLE_FAILURE,
    DeliveryAttempt,
    Payload,
)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


class BlueskyDeliveryAdapter:
    def __init__(
        self,
        credentials: CredentialMap,
        *,
        client_factory: Callable[[], Client] = Client,
    ) -> None:
        self.credentials = credentials
        self.client_factory = client_factory

    def deliver(self, payload: Payload, target_account: str) -> DeliveryAttempt:
        handle, password = select_credentials(self.credentials, target_account)

        try:
            client = self.client_factory()
            client.login(handle, password)
            embed = self._build_embed(client, payload)
            response = client.send_post(text=payload.text, embed=embed)
        except Exception as exc:
            return DeliveryAttempt(
                outcome=self._classify_exception(exc),
                error_message=self._describe_exception(exc),
            )

        receipt = self._extract_receipt(response)
        return DeliveryAttempt(outcome=DELIVERED, receipt=receipt)

    def _build_embed(self, client: Client, payload: Payload) -> Optional[dict]:
        if not payload.images:
            return None

        uploaded_images = []
        for image in payload.images:
            with open(image.path, "rb") as file_handle:
                blob = client.upload_blob(file_handle.read())
            uploaded_images.append(
                {
                    "$type": "app.bsky.embed.images#image",
                    "image": blob["blob"],
                    "alt": image.alt_text or "",
                }
            )

        return {
            "$type": "app.bsky.embed.images",
            "images": uploaded_images,
        }

    def _extract_receipt(self, response: Any) -> Dict[str, Any]:
        remote_uri = None
        remote_cid = None

        if isinstance(response, dict):
            remote_uri = response.get("uri")
            remote_cid = response.get("cid")
        else:
            remote_uri = getattr(response, "uri", None)
            remote_cid = getattr(response, "cid", None)

        return {
            "remote_uri": str(remote_uri) if remote_uri is not None else None,
            "remote_cid": str(remote_cid) if remote_cid is not None else None,
            "raw": _jsonable(response),
        }

    def _classify_exception(self, exc: Exception) -> str:
        if isinstance(exc, (FileNotFoundError, PermissionError, KeyError, ValueError)):
            return PERMANENT_FAILURE

        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        if status_code is not None:
            status_code = int(status_code)
            if 400 <= status_code < 500 and status_code != 429:
                return PERMANENT_FAILURE

        return RETRYABLE_FAILURE

    def _describe_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return f"{type(exc).__name__}: {repr(exc)}"
