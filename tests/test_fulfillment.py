import hashlib
import hmac
import json
from pathlib import Path

import pytest

from courseforge.fulfillment import (
    StripeSignatureError,
    accept_stripe_event,
    verify_stripe_signature,
)
from courseforge.state import StateStore


def _header(payload: bytes, secret: str, timestamp: int) -> str:
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def test_stripe_signature_is_verified() -> None:
    payload = b'{"id":"evt_1"}'
    secret = "whsec_test"
    verify_stripe_signature(
        payload=payload,
        header=_header(payload, secret, 1000),
        secret=secret,
        tolerance_seconds=300,
        now=1000,
    )


def test_stale_stripe_signature_is_rejected() -> None:
    payload = b'{"id":"evt_1"}'
    secret = "whsec_test"
    with pytest.raises(StripeSignatureError, match="outside tolerance"):
        verify_stripe_signature(
            payload=payload,
            header=_header(payload, secret, 1000),
            secret=secret,
            tolerance_seconds=300,
            now=1400,
        )


def test_checkout_event_is_queued_once(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    event = {
        "id": "evt_paid_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "payment_status": "paid",
                "metadata": {"release_id": "release-1"},
                "customer_details": {"email": "Buyer@Example.com"},
            }
        },
    }
    first = accept_stripe_event(json.loads(json.dumps(event)), state)
    second = accept_stripe_event(json.loads(json.dumps(event)), state)
    assert first["queued"] is True
    assert second["duplicate"] is True
    pending = state.list_pending_enrollments(10)
    assert len(pending) == 1
    assert pending[0]["email"] == "buyer@example.com"
