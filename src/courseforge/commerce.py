from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

import httpx

from courseforge.config import Settings, legal_profile_complete
from courseforge.models import Offer, ReleaseManifest


class PublishBlocked(RuntimeError):
    pass


class PublishGuard:
    @staticmethod
    def assert_live_allowed(
        *,
        settings: Settings,
        release_id: str,
        manifest: ReleaseManifest,
        offer: Offer,
        legal_profile: dict[str, str],
    ) -> None:
        errors: list[str] = []
        if settings.publish_mode != "live":
            errors.append("PUBLISH_MODE is not live")
        if settings.approved_release_id != release_id:
            errors.append("APPROVED_RELEASE_ID does not match the requested release")
        if manifest.status != "approved":
            errors.append("release manifest is not approved")
        if not manifest.gate_passed:
            errors.append("quality gate did not pass")
        if not legal_profile_complete(legal_profile):
            errors.append("legal profile is incomplete or contains placeholders")
        if not offer.presale_window_is_valid:
            errors.append("presale dates are invalid")
        if any(marker in offer.refund_policy.upper() for marker in ("DRAFT", "TODO")):
            errors.append("refund policy is still a draft")
        if manifest.content_hash.strip() == "":
            errors.append("content hash is missing")
        if errors:
            raise PublishBlocked("; ".join(errors))


class StripePresalePublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://api.stripe.com/v1"

    def plan(self, offer: Offer, release_id: str) -> dict[str, Any]:
        return {
            "channel": "stripe",
            "mode": "plan",
            "release_id": release_id,
            "product": offer.title,
            "unit_amount": offer.price_jpy,
            "currency": offer.currency,
            "fulfillment_starts": offer.delivery_starts.isoformat(),
            "operations": ["create product", "create one-time price", "create payment link"],
        }

    def _post(self, path: str, data: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not self.settings.stripe_secret_key:
            raise PublishBlocked("STRIPE_SECRET_KEY is not configured")
        response = httpx.post(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.settings.stripe_secret_key}",
                "Idempotency-Key": idempotency_key,
            },
            data=data,
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()

    def publish(self, offer: Offer, release_id: str) -> dict[str, Any]:
        product = self._post(
            "/products",
            {
                "name": offer.title,
                "description": offer.subtitle,
                "metadata[release_id]": release_id,
                "metadata[presale]": "true",
                "metadata[delivery_starts]": offer.delivery_starts.isoformat(),
            },
            f"courseforge-{release_id}-product",
        )
        price = self._post(
            "/prices",
            {
                "product": product["id"],
                "unit_amount": offer.price_jpy,
                "currency": offer.currency,
                "metadata[release_id]": release_id,
            },
            f"courseforge-{release_id}-price",
        )
        payment_link = self._post(
            "/payment_links",
            {
                "line_items[0][price]": price["id"],
                "line_items[0][quantity]": 1,
                "after_completion[type]": "redirect",
                "after_completion[redirect][url]": self.settings.checkout_success_url,
                "metadata[release_id]": release_id,
            },
            f"courseforge-{release_id}-link",
        )
        return {
            "channel": "stripe",
            "product_id": product["id"],
            "price_id": price["id"],
            "payment_link_id": payment_link["id"],
            "url": payment_link.get("url"),
        }


class ShopifyPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def plan(self, offer: Offer, release_id: str) -> dict[str, Any]:
        return {
            "channel": "shopify",
            "mode": "plan",
            "release_id": release_id,
            "status": "DRAFT first; ACTIVE only after live gate",
            "product": offer.title,
            "price": offer.price_jpy,
        }

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.shopify_store_domain or not self.settings.shopify_admin_token:
            raise PublishBlocked("Shopify store domain or admin token is not configured")
        endpoint = (
            f"https://{self.settings.shopify_store_domain}/admin/api/"
            f"{self.settings.shopify_api_version}/graphql.json"
        )
        response = httpx.post(
            endpoint,
            headers={
                "X-Shopify-Access-Token": self.settings.shopify_admin_token,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"]))
        return payload["data"]

    def publish(self, offer: Offer, release_id: str) -> dict[str, Any]:
        if not self.settings.shopify_publication_id:
            raise PublishBlocked("SHOPIFY_PUBLICATION_ID is required for live publication")
        mutation = """
        mutation CreateProduct($product: ProductCreateInput!) {
          productCreate(product: $product) {
            product { id title status }
            userErrors { field message }
          }
        }
        """
        description = (
            f"<p>{html.escape(offer.subtitle)}</p>"
            f"<p><strong>提供開始:</strong> {offer.delivery_starts.isoformat()}</p>"
            f"<p><strong>返金条件:</strong> {html.escape(offer.refund_policy)}</p>"
        )
        data = self._graphql(
            mutation,
            {
                "product": {
                    "title": offer.title,
                    "descriptionHtml": description,
                    "status": "ACTIVE",
                    "tags": ["presale", f"release:{release_id}"],
                    "productOptions": [{"name": "受講権", "values": [{"name": "1名"}]}],
                }
            },
        )["productCreate"]
        if data.get("userErrors"):
            raise RuntimeError(str(data["userErrors"]))
        product_id = data["product"]["id"]
        publish_mutation = """
        mutation Publish($id: ID!, $input: [PublicationInput!]!) {
          publishablePublish(id: $id, input: $input) {
            userErrors { field message }
          }
        }
        """
        published = self._graphql(
            publish_mutation,
            {
                "id": product_id,
                "input": [{"publicationId": self.settings.shopify_publication_id}],
            },
        )["publishablePublish"]
        if published.get("userErrors"):
            raise RuntimeError(str(published["userErrors"]))
        return {"channel": "shopify", "product_id": product_id, "status": "ACTIVE"}


class CommerceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def execute(
        self,
        *,
        channels: list[str],
        live: bool,
        release_id: str,
        manifest: ReleaseManifest,
        offer: Offer,
        legal_profile: dict[str, str],
    ) -> list[dict[str, Any]]:
        publishers: dict[str, Any] = {
            "stripe": StripePresalePublisher(self.settings),
            "shopify": ShopifyPublisher(self.settings),
        }
        unknown = [channel for channel in channels if channel not in publishers]
        if unknown:
            raise ValueError(f"Unsupported commerce channels: {', '.join(unknown)}")
        if live:
            PublishGuard.assert_live_allowed(
                settings=self.settings,
                release_id=release_id,
                manifest=manifest,
                offer=offer,
                legal_profile=legal_profile,
            )
        results: list[dict[str, Any]] = []
        for channel in channels:
            publisher = publishers[channel]
            results.append(
                publisher.publish(offer, release_id)
                if live
                else publisher.plan(offer, release_id)
            )
        if live:
            manifest.status = "published"
            manifest.published_at = datetime.now(timezone.utc)
        return results
