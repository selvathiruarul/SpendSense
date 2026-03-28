"""
Stripe billing helpers for SpendSense.

Endpoints (registered in main.py):
  POST /billing/checkout        → create Stripe Checkout Session (subscribe)
  GET  /billing/portal          → create Stripe Customer Portal session (manage/cancel)
  POST /billing/webhook         → Stripe event handler (must be unauthenticated)

Environment variables required:
  STRIPE_SECRET_KEY
  STRIPE_WEBHOOK_SECRET
  STRIPE_PRICE_ID_PRO           (monthly price ID from Stripe dashboard)
  FRONTEND_URL                  (e.g. https://your-app.streamlit.app)
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY     (admin key to update user_metadata)
"""
from __future__ import annotations

import os

import httpx
import stripe
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from backend.auth import UserClaims, get_current_user

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

router = APIRouter(prefix="/billing", tags=["billing"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_customer(user: UserClaims) -> str:
    """Return existing Stripe customer ID or create a new one."""
    customers = stripe.Customer.list(email=user.email, limit=1)
    if customers.data:
        return customers.data[0].id
    customer = stripe.Customer.create(
        email=user.email,
        metadata={"supabase_user_id": user.id},
    )
    return customer.id


def _set_user_paid(supabase_user_id: str, is_paid: bool) -> None:
    """Update is_paid flag in Supabase user_metadata via Admin API."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    httpx.patch(
        f"{SUPABASE_URL}/auth/v1/admin/users/{supabase_user_id}",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
        json={"user_metadata": {"is_paid": is_paid}},
        timeout=10,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/checkout")
def create_checkout_session(current_user: UserClaims = Depends(get_current_user)):
    """
    Create a Stripe Checkout Session for the Pro subscription.
    Returns a redirect URL the frontend sends the user to.
    """
    if not PRICE_ID_PRO:
        raise HTTPException(status_code=503, detail="Billing not configured.")

    customer_id = _get_or_create_customer(current_user)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": PRICE_ID_PRO, "quantity": 1}],
        success_url=f"{FRONTEND_URL}?billing=success",
        cancel_url=f"{FRONTEND_URL}?billing=cancelled",
        metadata={"supabase_user_id": current_user.id},
        allow_promotion_codes=True,
    )
    return {"checkout_url": session.url}


@router.get("/portal")
def customer_portal(current_user: UserClaims = Depends(get_current_user)):
    """
    Create a Stripe Customer Portal session so users can manage/cancel their subscription.
    """
    customer_id = _get_or_create_customer(current_user)
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=FRONTEND_URL,
    )
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Handle Stripe events. This endpoint must NOT require auth — Stripe calls it directly.
    Verifies the request using the webhook signing secret.
    """
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        supabase_uid = data.get("metadata", {}).get("supabase_user_id")
        if supabase_uid:
            _set_user_paid(supabase_uid, is_paid=True)

    elif event_type in (
        "customer.subscription.deleted",
        "customer.subscription.paused",
    ):
        # Look up user by Stripe customer ID
        customer_id = data.get("customer")
        if customer_id:
            customers = stripe.Customer.list(id=customer_id, limit=1)
            if customers.data:
                supabase_uid = customers.data[0].metadata.get("supabase_user_id")
                if supabase_uid:
                    _set_user_paid(supabase_uid, is_paid=False)

    elif event_type == "customer.subscription.updated":
        # Re-activate if subscription moves back to active
        status = data.get("status")
        customer_id = data.get("customer")
        if customer_id and status:
            customers = stripe.Customer.list(id=customer_id, limit=1)
            if customers.data:
                supabase_uid = customers.data[0].metadata.get("supabase_user_id")
                if supabase_uid:
                    _set_user_paid(supabase_uid, is_paid=(status == "active"))

    return {"received": True}
