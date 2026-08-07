from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from courseforge.config import Settings, load_offer
from courseforge.state import StateStore


class StripeSignatureError(ValueError):
    pass


class FulfillmentBlocked(RuntimeError):
    pass


def verify_stripe_signature(*, payload: bytes, header: str, secret: str, tolerance_seconds: int = 300, now: int | None = None) -> None:
    values: dict[str, list[str]] = {}
    for part in header.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values.setdefault(key.strip(), []).append(value.strip())
    timestamps = values.get("t", [])
    signatures = values.get("v1", [])
    if not timestamps or not signatures:
        raise StripeSignatureError("Stripe-Signature is missing t or v1")
    try:
        timestamp = int(timestamps[0])
    except ValueError as exc:
        raise StripeSignatureError("Stripe signature timestamp is invalid") from exc
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise StripeSignatureError("Stripe signature timestamp is outside tolerance")
    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise StripeSignatureError("Stripe signature does not match")


def accept_stripe_event(event: dict[str, Any], state: StateStore) -> dict[str, Any]:
    event_id = str(event.get("id", "")).strip()
    event_type = str(event.get("type", "")).strip()
    if not event_id or not event_type:
        raise ValueError("Stripe event id and type are required")
    if event_type != "checkout.session.completed":
        return {"accepted": True, "queued": False, "reason": "event type ignored"}
    session = event.get("data", {}).get("object", {})
    if session.get("payment_status") not in {"paid", "no_payment_required"}:
        return {"accepted": True, "queued": False, "reason": "payment is not complete"}
    metadata = session.get("metadata") or {}
    release_id = str(metadata.get("release_id", "")).strip()
    customer_details = session.get("customer_details") or {}
    email = str(customer_details.get("email") or session.get("customer_email") or "").strip()
    if not release_id or not email:
        raise ValueError("Paid checkout is missing release_id or customer email")
    inserted = state.record_paid_checkout(
        event_id=event_id,
        event_type=event_type,
        email=email,
        release_id=release_id,
    )
    return {"accepted": True, "queued": inserted, "duplicate": not inserted}


class ResendMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_onboarding(self, *, email: str, release_id: str) -> str:
        if not self.settings.resend_api_key or not self.settings.from_email or not self.settings.course_portal_url:
            raise FulfillmentBlocked("Resend key, FROM_EMAIL, and COURSE_PORTAL_URL are required")
        offer = load_offer(self.settings.offer_config_path)
        subject = f"【予約確認】{offer.title}"
        text = (
            f"{offer.title}をご予約いただき、ありがとうございます。\n\n"
            f"提供開始: {offer.delivery_starts.isoformat()}\n"
            f"受講案内: {self.settings.course_portal_url}\n"
            f"リリースID: {release_id}\n\n"
            "日程や提供内容に変更がある場合は、登録メールへご案内します。"
        )
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self.settings.resend_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"courseforge-{hashlib.sha256(f'{email}:{release_id}'.encode()).hexdigest()}",
            },
            json={
                "from": self.settings.from_email,
                "to": [email],
                "subject": subject,
                "text": text,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        message_id = str(response.json().get("id", ""))
        if not message_id:
            raise RuntimeError("Resend did not return a message id")
        return message_id


class FulfillmentService:
    def __init__(self, settings: Settings, state: StateStore) -> None:
        self.settings = settings
        self.state = state
        self.mailer = ResendMailer(settings)

    def process_pending(self, *, live: bool) -> list[dict[str, Any]]:
        rows = self.state.list_pending_enrollments(self.settings.max_fulfillments_per_run)
        if not live:
            return [
                {
                    "mode": "plan",
                    "enrollment_id": row["id"],
                    "release_id": row["release_id"],
                    "recipient": "configured purchaser email",
                }
                for row in rows
            ]
        if self.settings.fulfillment_mode != "live":
            raise FulfillmentBlocked("FULFILLMENT_MODE is not live")
        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                message_id = self.mailer.send_onboarding(
                    email=str(row["email"]), release_id=str(row["release_id"])
                )
            except Exception as exc:
                self.state.mark_enrollment_failed(int(row["id"]), f"{type(exc).__name__}: {exc}")
                results.append({
                    "enrollment_id": row["id"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            self.state.mark_enrollment_sent(int(row["id"]))
            results.append({
                "enrollment_id": row["id"],
                "release_id": row["release_id"],
                "status": "sent",
                "message_id": message_id,
            })
        return results


def parse_event(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("Stripe event payload must be an object")
    return value
