from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from courseforge.config import Settings, load_legal_profile, load_offer


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or Settings()
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
