from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from courseforge.config import Settings, load_legal_profile, load_offer
from courseforge.fulfillment import (
    StripeSignatureError,
    accept_stripe_event,
    parse_event,
    verify_stripe_signature,
)
from courseforge.state import StateStore


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or Settings()
    state = StateStore(runtime.state_db_path)
    app = FastAPI(title="CourseForge Presale", version="0.1.0")
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        offer = load_offer(runtime.offer_config_path)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"offer": offer, "checkout_url": runtime.stripe_payment_link_url},
        )

    @app.get("/legal", response_class=HTMLResponse)
    def legal(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="legal.html",
            context={"profile": load_legal_profile(runtime)},
        )

    @app.get("/checkout")
    def checkout() -> RedirectResponse:
        if not runtime.stripe_payment_link_url:
            raise HTTPException(
                status_code=503,
                detail="Checkout is not active. A reviewed payment link has not been configured.",
            )
        return RedirectResponse(runtime.stripe_payment_link_url, status_code=302)

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request) -> dict[str, object]:
        if not runtime.stripe_webhook_secret:
            raise HTTPException(status_code=503, detail="Stripe webhook is not configured")
        payload = await request.body()
        signature = request.headers.get("stripe-signature", "")
        try:
            verify_stripe_signature(
                payload=payload,
                header=signature,
                secret=runtime.stripe_webhook_secret,
                tolerance_seconds=runtime.stripe_webhook_tolerance_seconds,
            )
            return accept_stripe_event(parse_event(payload), state)
        except StripeSignatureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/unsubscribe/{token}", response_class=HTMLResponse)
    def unsubscribe_form(token: str) -> HTMLResponse:
        safe_token = html.escape(token, quote=True)
        return HTMLResponse(
            "<main style='max-width:720px;margin:80px auto;font-family:sans-serif'>"
            "<h1>メール配信の停止</h1>"
            "<p>今後のマーケティングメールを停止します。購入に必要な取引メールは対象外です。</p>"
            f"<form method='post' action='/unsubscribe/{safe_token}'>"
            "<button type='submit'>配信を停止する</button></form></main>"
        )

    @app.post("/unsubscribe/{token}", response_class=HTMLResponse)
    def unsubscribe(token: str) -> HTMLResponse:
        state.unsubscribe(token)
        return HTMLResponse(
            "<main style='max-width:720px;margin:80px auto;font-family:sans-serif'>"
            "<h1>配信停止を受け付けました</h1>"
            "<p>今後のマーケティングメール配信対象から除外します。</p></main>"
        )

    @app.get("/thanks", response_class=HTMLResponse)
    def thanks() -> HTMLResponse:
        return HTMLResponse(
            "<main style='max-width:720px;margin:80px auto;font-family:sans-serif'>"
            "<h1>ご予約ありがとうございます</h1>"
            "<p>決済確認後、提供開始日と受講案内を登録メールへお送りします。</p>"
            "</main>"
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "publish_mode": runtime.publish_mode}

    return app


app = create_app()
