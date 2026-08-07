from __future__ import annotations

import csv
import hashlib
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from courseforge.config import Settings, legal_profile_complete, load_legal_profile
from courseforge.models import ReleaseManifest
from courseforge.state import StateStore


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}$")


class MarketingBlocked(RuntimeError):
    pass


class SubscriberImporter:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    def import_csv(self, path: Path) -> dict[str, int]:
        added = 0
        skipped = 0
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"email", "consent_at", "consent_source"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError("CSV requires email, consent_at, and consent_source columns")
            for row in reader:
                email = str(row.get("email", "")).casefold().strip()
                consent_at = str(row.get("consent_at", "")).strip()
                consent_source = str(row.get("consent_source", "")).strip()
                if not EMAIL_PATTERN.fullmatch(email) or not consent_source:
                    skipped += 1
                    continue
                try:
                    parsed = datetime.fromisoformat(consent_at.replace("Z", "+00:00"))
                except ValueError:
                    skipped += 1
                    continue
                if parsed.tzinfo is None:
                    skipped += 1
                    continue
                inserted = self.state.add_subscriber(
                    email=email,
                    consent_at=parsed.isoformat(),
                    consent_source=consent_source,
                    unsubscribe_token=secrets.token_urlsafe(32),
                )
                added += int(inserted)
                skipped += int(not inserted)
        return {"added": added, "skipped": skipped}


class CampaignMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, *, email: str, subject: str, text: str, campaign_id: str) -> str:
        if not self.settings.resend_api_key or not self.settings.from_email:
            raise MarketingBlocked("RESEND_API_KEY and FROM_EMAIL are required")
        digest = hashlib.sha256(f"{campaign_id}:{email}".encode()).hexdigest()
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {self.settings.resend_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"courseforge-campaign-{digest}",
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


class MarketingService:
    def __init__(self, settings: Settings, state: StateStore) -> None:
        self.settings = settings
        self.state = state
        self.mailer = CampaignMailer(settings)

    def _manifest(self, release_id: str) -> ReleaseManifest:
        path = self.settings.artifact_dir / "releases" / release_id / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"Release manifest not found: {path}")
        return ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _template(self, release_id: str) -> tuple[str, str]:
        path = self.settings.artifact_dir / "releases" / release_id / "packages/marketing/email-01.txt"
        if not path.exists():
            raise FileNotFoundError(f"Campaign template not found: {path}")
        content = path.read_text(encoding="utf-8").strip()
        first, _, rest = content.partition("\n")
        subject = first.removeprefix("件名:").strip() if first.startswith("件名:") else first
        return subject, rest.strip()

    def _assert_live(self, release_id: str, manifest: ReleaseManifest) -> None:
        errors: list[str] = []
        if self.settings.marketing_mode != "live":
            errors.append("MARKETING_MODE is not live")
        if self.settings.approved_release_id != release_id:
            errors.append("APPROVED_RELEASE_ID does not match")
        if manifest.status not in {"approved", "published"} or not manifest.gate_passed:
            errors.append("release is not approved by the quality gate")
        if not legal_profile_complete(load_legal_profile(self.settings)):
            errors.append("legal profile is incomplete")
        if not self.settings.marketing_landing_url or not self.settings.unsubscribe_base_url:
            errors.append("marketing and unsubscribe URLs are required")
        if not self.settings.resend_api_key or not self.settings.from_email:
            errors.append("Resend sender configuration is incomplete")
        if errors:
            raise MarketingBlocked("; ".join(errors))

    def execute(self, *, release_id: str, campaign_id: str, live: bool) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(release_id) or not SAFE_ID.fullmatch(campaign_id):
            raise ValueError("release_id and campaign_id must use safe lowercase identifiers")
        manifest = self._manifest(release_id)
        subject, body = self._template(release_id)
        recipients = self.state.list_campaign_recipients(
            campaign_id, self.settings.max_marketing_emails_per_run
        )
        if not live:
            return {
                "mode": "plan",
                "campaign_id": campaign_id,
                "release_id": release_id,
                "eligible_recipient_count": len(recipients),
                "subject": subject,
            }
        self._assert_live(release_id, manifest)
        sent = 0
        failed = 0
        for recipient in recipients:
            email = str(recipient["email"])
            unsubscribe_url = (
                f"{self.settings.unsubscribe_base_url.rstrip('/')}/unsubscribe/"
                f"{recipient['unsubscribe_token']}"
            )
            text = body.replace("{LANDING_URL}", str(self.settings.marketing_landing_url))
            text = text.replace("{UNSUBSCRIBE_URL}", unsubscribe_url)
            try:
                message_id = self.mailer.send(
                    email=email,
                    subject=subject,
                    text=text,
                    campaign_id=campaign_id,
                )
            except Exception as exc:
                self.state.record_campaign_result(
                    campaign_id=campaign_id,
                    email=email,
                    release_id=release_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                failed += 1
                continue
            self.state.record_campaign_result(
                campaign_id=campaign_id,
                email=email,
                release_id=release_id,
                status="sent",
                message_id=message_id,
            )
            sent += 1
        return {
            "mode": "live",
            "campaign_id": campaign_id,
            "release_id": release_id,
            "sent": sent,
            "failed": failed,
        }
