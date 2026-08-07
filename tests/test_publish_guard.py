from datetime import datetime, timedelta, timezone

import pytest

from courseforge.commerce import PublishBlocked, PublishGuard
from courseforge.config import Settings
from courseforge.models import CourseModule, Offer, ReleaseManifest


def _offer() -> Offer:
    now = datetime.now(timezone.utc)
    return Offer(
        slug="offer",
        title="Offer",
        subtitle="Subtitle",
        audience="Audience",
        promise="Promise",
        outcomes=["Outcome"],
        modules=[CourseModule(number=1, title="M1", outcome="O", deliverable="D")],
        price_jpy=1000,
        regular_price_jpy=2000,
        capacity=10,
        presale_opens=now,
        presale_closes=now + timedelta(days=1),
        delivery_starts=now + timedelta(days=2),
        delivery_format="online",
        support="email",
        refund_policy="講座開始前は全額返金",
    )


def test_live_publish_is_blocked_when_legal_profile_is_incomplete() -> None:
    settings = Settings(publish_mode="live", approved_release_id="release-1")
    manifest = ReleaseManifest(
        release_id="release-1",
        slug="offer",
        content_hash="abc",
        status="approved",
        gate_passed=True,
    )
    with pytest.raises(PublishBlocked, match="legal profile"):
        PublishGuard.assert_live_allowed(
            settings=settings,
            release_id="release-1",
            manifest=manifest,
            offer=_offer(),
            legal_profile={"seller_name": ""},
        )
