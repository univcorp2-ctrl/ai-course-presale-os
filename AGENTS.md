# Agent operating contract

## Goal

Deliver useful, evidence-linked AI course material and a safe presale operation. Volume is never a success metric by itself.

## Non-negotiable rules

- Never commit secrets, customer lists, payment data, cookies, or private Notion content.
- Never use unofficial posting endpoints, scraped session cookies, or browser impersonation.
- Never publish a draft that lacks two independent review providers.
- Never invent testimonials, revenue, enrollments, endorsements, scarcity, or guaranteed outcomes.
- Preserve source URL, retrieval time, allowed use, model trace, content hash, and reviewer decision.
- Treat `config/legal.yaml` placeholders and `config/offer.yaml` DRAFT terms as hard live blockers.
- Use official APIs for Stripe, Shopify, Notion, OpenAI, Anthropic, Google, Ollama, and Resend.
- Keep external effects behind explicit `--live` plus matching environment gates.

## Change workflow

1. Read only the files needed for the change.
2. Update tests with behavior changes.
3. Run `ruff check .` and `pytest -q`.
4. Do not weaken a quality or live-publish gate to make a test pass.
5. Document platform/API assumptions that can expire.
