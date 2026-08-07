from datetime import datetime, timedelta, timezone

from courseforge.models import ContentDraft, CourseModule, Offer, SourceDocument
from courseforge.quality import deterministic_review


def test_deterministic_review_catches_guaranteed_income_claim() -> None:
    now = datetime.now(timezone.utc)
    offer = Offer(
        slug="x",
        title="X",
        subtitle="S",
        audience="A",
        promise="P",
        outcomes=["O"],
        modules=[CourseModule(number=1, title="M", outcome="O", deliverable="D")],
        price_jpy=1000,
        regular_price_jpy=2000,
        capacity=5,
        presale_opens=now,
        presale_closes=now + timedelta(days=1),
        delivery_starts=now + timedelta(days=2),
        delivery_format="online",
        support="email",
        refund_policy="開始前は返金",
    )
    draft = ContentDraft(
        release_id="r",
        slug="x",
        title="X",
        audience="A",
        body_markdown=(
            "誰でも必ず稼げます。 [S1] [S2]\n"
            "予約販売です。提供開始は明日です。返金条件を表示します。\n" + "説明" * 600
        ),
    )
    sources = [
        SourceDocument(id="1", source_type="local", title="A", text="source a"),
        SourceDocument(id="2", source_type="local", title="B", text="source b"),
    ]
    findings = deterministic_review(draft, sources, offer)
    assert any(item.code == "guaranteed_income" for item in findings)
