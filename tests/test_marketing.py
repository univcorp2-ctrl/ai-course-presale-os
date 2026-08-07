from pathlib import Path

from courseforge.marketing import SubscriberImporter
from courseforge.state import StateStore


def test_only_explicit_timezone_aware_consent_is_imported_and_unsubscribe_works(
    tmp_path: Path,
) -> None:
    state = StateStore(tmp_path / "state.db")
    csv_path = tmp_path / "subscribers.csv"
    csv_path.write_text(
        "email,consent_at,consent_source\n"
        "valid@example.com,2026-08-07T10:00:00+09:00,web-form\n"
        "invalid@example.com,2026-08-07T10:00:00,unknown\n",
        encoding="utf-8",
    )
    result = SubscriberImporter(state).import_csv(csv_path)
    assert result == {"added": 1, "skipped": 1}

    recipients = state.list_campaign_recipients("launch-01", 10)
    assert len(recipients) == 1
    assert state.unsubscribe(str(recipients[0]["unsubscribe_token"])) is True
    assert state.list_campaign_recipients("launch-01", 10) == []
