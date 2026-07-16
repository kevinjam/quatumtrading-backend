"""Stripe-backed monthly access pass.

Model: $7.99 one-time Stripe Checkout charge → 30-day `pro` access. When the
pass expires the user pays again. Cancel = simply don't pay next month
(`cancel_at_period_end` flag flips so UI hides the "Manage / Cancel" button
and shows "Resume" — no actual auto-renew here since the test environment
uses a shared key without subscription objects).

This module exposes:
  • create_checkout_session(db, user, origin_url, payment_methods=['card']) -> {url, session_id}
  • get_checkout_status(db, session_id, user_id) -> {payment_status, status, pro: bool}
  • handle_webhook(db, body_bytes, signature) -> {event_type, session_id, payment_status}
  • get_billing_status(db, user) -> {plan, status, current_period_end, cancel_at_period_end}
  • set_cancel_at_period_end(db, user, cancel: bool)
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import stripe
from emergentintegrations.payments.stripe.checkout import (
    CheckoutSessionRequest,
    StripeCheckout,
)
from fastapi import HTTPException
from pymongo import ReturnDocument

log = logging.getLogger("billing")

# Fixed package — defined ONLY server-side to prevent client-side tampering.
MONTHLY_ACCESS_PASS = {
    "id": "monthly_799",
    "amount": 7.99,
    "currency": "usd",
    "days": 30,
    "label": "Quant Terminal Pro — 30 days",
}

WEBHOOK_PATH = "/api/webhook/stripe"


def _client(host_url: str) -> StripeCheckout:
    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="STRIPE_API_KEY not configured")
    return StripeCheckout(
        api_key=api_key,
        webhook_url=f"{host_url.rstrip('/')}{WEBHOOK_PATH}",
    )


async def _sync_user_pro_from_stripe(db, user_id: str) -> bool:
    """Lazily sync a user's local Pro state with their actual Stripe Subscription.

    Used because the user opted NOT to set up webhooks — we instead refresh
    the period_end from Stripe whenever the user opens the app. Returns True if
    we successfully synced (local state may or may not have changed), False on
    any error (local state left untouched).
    """
    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    if not api_key:
        return False
    doc = await db.subscriptions.find_one({"user_id": user_id})
    sub_id = doc and doc.get("stripe_subscription_id")
    if not sub_id:
        return False
    try:
        stripe.api_key = api_key
        sub = stripe.Subscription.retrieve(sub_id)
    except Exception as e:
        log.warning("Could not sync Stripe sub %s: %s", sub_id, e)
        return False

    status = (sub.get("status") or "").lower()
    cpe_unix = _extract_current_period_end(sub)
    cancel_at_end = bool(sub.get("cancel_at_period_end", False))
    if not cpe_unix:
        return False
    new_end = datetime.fromtimestamp(int(cpe_unix), tz=timezone.utc).isoformat()
    plan = "pro" if status in ("active", "trialing", "past_due") else "free"
    await db.subscriptions.update_one(
        {"user_id": user_id},
        {"$set": {
            "plan": plan,
            "status": "active" if plan == "pro" else "inactive",
            "current_period_end": new_end,
            "cancel_at_period_end": cancel_at_end,
            "stripe_status": status,
            "updated_at": _now().isoformat(),
        }},
    )
    return True


def _extract_current_period_end(sub) -> int | None:
    """Stripe moved current_period_end off the subscription root onto
    subscription.items.data[0] in mid-2024. Support both shapes."""
    cpe = sub.get("current_period_end") if isinstance(sub, dict) else getattr(sub, "current_period_end", None)
    if cpe:
        return int(cpe)
    items = sub.get("items") if isinstance(sub, dict) else getattr(sub, "items", None)
    data = None
    if items is not None:
        data = items.get("data") if isinstance(items, dict) else getattr(items, "data", None)
    if data:
        first = data[0]
        cpe2 = first.get("current_period_end") if isinstance(first, dict) else getattr(first, "current_period_end", None)
        if cpe2:
            return int(cpe2)
    return None


MONTHLY_PRO_GENERATIONS = 7  # legacy — kept for backwards-compat callers

# Tier catalog — single source of truth for pricing + monthly limits
TIERS = {
    "free":  {"name": "Free",  "price": 0.00,  "monthly_limit": 0},
    "plus":  {"name": "Plus",  "price": 14.99, "monthly_limit": 15},
    "ultra": {"name": "Ultra", "price": 23.99, "monthly_limit": 30},
}


def tier_from_price_id(price_id: str | None) -> str:
    """Map a Stripe price ID to our tier name."""
    if not price_id:
        return "free"
    if price_id == (os.environ.get("STRIPE_PRICE_ULTRA") or "").strip():
        return "ultra"
    if price_id == (os.environ.get("STRIPE_PRICE_PLUS") or "").strip():
        return "plus"
    # Legacy $7.99 or unknown price → grandfather as Plus so old subs stay working
    return "plus"


def price_id_for_tier(tier: str) -> str:
    if tier == "ultra":
        return (os.environ.get("STRIPE_PRICE_ULTRA") or "").strip()
    return (os.environ.get("STRIPE_PRICE_PLUS") or "").strip()


def paywall_enabled() -> bool:
    """Master kill-switch for the paywall. Set PAYWALL_ENABLED=false in .env to
    let every signed-in user use every feature (useful while iterating on
    pricing / waiting for ramp-up). All paywall infrastructure stays wired up
    behind this flag so flipping it back to true restores enforcement."""
    return (os.environ.get("PAYWALL_ENABLED") or "true").strip().lower() not in ("false", "0", "no", "off")


# ───────────────────────── helpers ─────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_active_pass(db, user_id: str) -> dict | None:
    """Return the active subscription doc for the user, or None.
    Lazily syncs with Stripe if the local period seems expired.

    A user is considered "active" if EITHER:
      • current_period_end (from Stripe) is in the future, OR
      • bonus_end (referral rewards) is in the future.
    """
    doc = await db.subscriptions.find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return None

    def _parse_end(d, key="current_period_end"):
        cpe = d.get(key)
        if not cpe:
            return None
        try:
            end = datetime.fromisoformat(cpe) if isinstance(cpe, str) else cpe
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return end
        except Exception:
            return None

    end = _parse_end(doc)
    bonus_end = _parse_end(doc, "bonus_end")

    # If Stripe period expired locally but we have a subscription on file,
    # refresh from Stripe (does nothing for pure-referral-bonus users).
    if (end is None or end < _now()) and doc.get("stripe_subscription_id"):
        if await _sync_user_pro_from_stripe(db, user_id):
            doc = await db.subscriptions.find_one({"user_id": user_id}, {"_id": 0})
            end = _parse_end(doc) if doc else None
            bonus_end = _parse_end(doc, "bonus_end") if doc else None

    # Active if EITHER Stripe period OR bonus period is still in the future.
    stripe_active = bool(end and end >= _now())
    bonus_active = bool(bonus_end and bonus_end >= _now())
    if not stripe_active and not bonus_active:
        return None
    return doc


async def get_user_tier(db, user) -> str:
    """Returns 'free' | 'plus' | 'ultra' based on the user's active subscription."""
    if not paywall_enabled():
        return "ultra"
    doc = await _get_active_pass(db, user["user_id"])
    if not doc:
        return "free"
    return doc.get("tier") or tier_from_price_id(doc.get("stripe_price_id"))


async def is_pro(db, user) -> bool:
    return (await get_user_tier(db, user)) in ("plus", "ultra")


def _utc_month_key() -> str:
    now = _now()
    return f"{now.year:04d}-{now.month:02d}"


async def get_monthly_generations_used(db, user_id: str) -> int:
    """How many monthly Pro generations the user has consumed in the current UTC calendar month."""
    key = f"{user_id}:pro_monthly:{_utc_month_key()}"
    doc = await db.daily_quota.find_one({"key": key})
    return int(doc.get("count", 0)) if doc else 0


async def consume_monthly_pro_quota(db, user_id: str, limit: int | None = None) -> bool:
    """Legacy shim — kept because server.py imports it. Uses the user's tier limit."""
    if not paywall_enabled():
        return True
    user_doc = await db.subscriptions.find_one({"user_id": user_id})
    tier = "free"
    if user_doc and (user_doc.get("plan") == "pro"):
        tier = user_doc.get("tier") or tier_from_price_id(user_doc.get("stripe_price_id"))
    real_limit = limit if limit is not None else TIERS[tier]["monthly_limit"]
    if real_limit == 0:
        return False
    key = f"{user_id}:pro_monthly:{_utc_month_key()}"
    doc = await db.daily_quota.find_one_and_update(
        {"key": key},
        {"$inc": {"count": 1},
         "$setOnInsert": {"key": key, "user_id": user_id, "feature": "pro_monthly", "month": _utc_month_key(), "created_at": _now().isoformat()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return (doc.get("count", 0) if doc else 1) <= real_limit


async def get_billing_status(db, user) -> dict:
    used = await get_monthly_generations_used(db, user["user_id"])
    tier = await get_user_tier(db, user)
    tier_info = TIERS[tier]

    # If paywall is disabled, everyone is Ultra
    if not paywall_enabled():
        return {
            "plan": "pro",
            "tier": "ultra",
            "tier_label": TIERS["ultra"]["name"],
            "status": "active",
            "current_period_end": None,
            "cancel_at_period_end": False,
            "price": TIERS["ultra"]["price"],
            "currency": "usd",
            "label": "Ultra",
            "monthly_generations_used": used,
            "monthly_generations_limit": TIERS["ultra"]["monthly_limit"],
            "paywall_enabled": False,
            "tiers": TIERS,
        }

    doc = await _get_active_pass(db, user["user_id"])
    if not doc:
        last = await db.subscriptions.find_one({"user_id": user["user_id"]}, {"_id": 0})
        return {
            "plan": "free",
            "tier": "free",
            "tier_label": "Free",
            "status": "inactive",
            "current_period_end": (last or {}).get("current_period_end"),
            "cancel_at_period_end": True,
            "price": 0,
            "currency": "usd",
            "label": "Free",
            "monthly_generations_used": used,
            "monthly_generations_limit": 0,
            "paywall_enabled": True,
            "tiers": TIERS,
            "bonus_end": (last or {}).get("bonus_end"),
            "bonus_days_total": (last or {}).get("bonus_days_total", 0),
        }
    # Pick the later of Stripe period end and bonus end so the UI shows the
    # true expiry when referral bonuses are stacked.
    stripe_end = doc.get("current_period_end")
    bonus_end = doc.get("bonus_end")
    effective_end = stripe_end
    if bonus_end and (not stripe_end or bonus_end > stripe_end):
        effective_end = bonus_end
    return {
        "plan": "pro",
        "tier": tier,
        "tier_label": tier_info["name"],
        "status": "active",
        "current_period_end": effective_end,
        "stripe_period_end": stripe_end,
        "bonus_end": bonus_end,
        "bonus_days_total": doc.get("bonus_days_total", 0),
        "cancel_at_period_end": bool(doc.get("cancel_at_period_end", True)),
        "price": tier_info["price"],
        "currency": "usd",
        "label": tier_info["name"],
        "monthly_generations_used": used,
        "monthly_generations_limit": tier_info["monthly_limit"],
        "paywall_enabled": True,
        "tiers": TIERS,
    }


# ───────────────────────── checkout flow ─────────────────────────
async def create_checkout_session(db, user, origin_url: str, host_url: str) -> dict:
    """Create a Stripe Checkout session for the monthly access pass."""
    if not origin_url:
        raise HTTPException(status_code=400, detail="origin_url required")
    pkg = MONTHLY_ACCESS_PASS
    success_url = f"{origin_url.rstrip('/')}/billing/return?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin_url.rstrip('/')}/dashboard"

    client = _client(host_url)
    req = CheckoutSessionRequest(
        amount=pkg["amount"],
        currency=pkg["currency"],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": user["user_id"],
            "email": user["email"],
            "package_id": pkg["id"],
            "source": "quant_terminal_paywall",
        },
    )
    session = await client.create_checkout_session(req)

    # MANDATORY: insert payment_transactions row BEFORE returning URL
    await db.payment_transactions.insert_one({
        "session_id": session.session_id,
        "user_id": user["user_id"],
        "email": user["email"],
        "amount": pkg["amount"],
        "currency": pkg["currency"],
        "package_id": pkg["id"],
        "metadata": {"source": "quant_terminal_paywall"},
        "payment_status": "pending",
        "status": "initiated",
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
    })
    return {"url": session.url, "session_id": session.session_id}


async def get_checkout_status(db, session_id: str, user_id: str) -> dict:
    """Poll Stripe for status and, on `paid`, idempotently grant 30 days pro.

    Supports BOTH:
      • Internal Checkout API sessions (have a payment_transactions row).
      • External Stripe Payment Link sessions (no internal row) — verified via
        direct Stripe API call using STRIPE_API_KEY. Requires the success URL
        on the Payment Link to be configured as
        `https://<domain>/billing/return?session_id={CHECKOUT_SESSION_ID}`.
    """
    txn = await db.payment_transactions.find_one({"session_id": session_id})

    # ───────── External Payment Link path ─────────
    if not txn:
        api_key = os.environ.get("STRIPE_API_KEY", "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="STRIPE_API_KEY not configured")
        try:
            stripe.api_key = api_key
            sess = stripe.checkout.Session.retrieve(session_id)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Stripe session not found: {e}")

        pay_status = (sess.get("payment_status") or "").lower()
        sess_status = (sess.get("status") or "").lower()
        client_ref = sess.get("client_reference_id")
        cust_email = (sess.get("customer_details") or {}).get("email") or ""

        if pay_status == "paid":
            # Security: if Stripe knows whose payment this is (we passed
            # client_reference_id in the URL), require it to match the logged-in
            # user. Without that match, anyone could paste someone else's
            # session_id and steal their grant.
            if client_ref and client_ref != user_id:
                raise HTTPException(status_code=403, detail="Payment belongs to a different user")

            grant_user_id = client_ref or user_id
            already = await db.payment_transactions.find_one({"session_id": session_id})

            # If this Checkout session created a real Stripe Subscription (your
            # Payment Link is set to recurring $7.99/mo), use the subscription's
            # actual current_period_end and customer/subscription IDs. Otherwise
            # fall back to the +30-day access-pass model.
            sub_id = sess.get("subscription")
            cust_id = sess.get("customer")
            cpe_end_iso = None
            cancel_at_end = True  # default for one-time pass
            if sub_id:
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    cpe_unix = _extract_current_period_end(sub)
                    if cpe_unix:
                        cpe_end_iso = datetime.fromtimestamp(int(cpe_unix), tz=timezone.utc).isoformat()
                    cancel_at_end = bool(sub.get("cancel_at_period_end", False))
                except Exception as e:
                    log.warning("Could not fetch subscription %s: %s", sub_id, e)

            if not already or not already.get("processed"):
                if cpe_end_iso:
                    # Real subscription path — store sub IDs and the exact period end
                    await db.subscriptions.update_one(
                        {"user_id": grant_user_id},
                        {"$set": {
                            "user_id": grant_user_id,
                            "email": cust_email,
                            "plan": "pro",
                            "status": "active",
                            "current_period_end": cpe_end_iso,
                            "cancel_at_period_end": cancel_at_end,
                            "stripe_customer_id": cust_id,
                            "stripe_subscription_id": sub_id,
                            "last_session_id": session_id,
                            "updated_at": _now().isoformat(),
                        },
                         "$setOnInsert": {"created_at": _now().isoformat()}},
                        upsert=True,
                    )
                else:
                    await _grant_pass(db, grant_user_id, cust_email, session_id)
                await db.payment_transactions.update_one(
                    {"session_id": session_id},
                    {"$set": {
                        "session_id": session_id,
                        "user_id": grant_user_id,
                        "email": cust_email,
                        "amount": (sess.get("amount_total") or 0) / 100.0,
                        "currency": sess.get("currency", "usd"),
                        "package_id": "stripe_subscription" if sub_id else "payment_link",
                        "stripe_subscription_id": sub_id,
                        "stripe_customer_id": cust_id,
                        "payment_status": "paid",
                        "status": "complete",
                        "processed": True,
                        "updated_at": _now().isoformat(),
                    },
                     "$setOnInsert": {"created_at": _now().isoformat()}},
                    upsert=True,
                )
        return {"payment_status": pay_status, "status": sess_status, "pro": pay_status == "paid"}

    # ───────── Internal Checkout API path (existing behavior) ─────────
    # Already settled → return cached state, no double-grant
    if txn.get("payment_status") in ("paid", "expired", "failed") and txn.get("processed"):
        return {
            "payment_status": txn["payment_status"],
            "status": txn["status"],
            "pro": txn["payment_status"] == "paid",
        }
    if txn.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Payment belongs to a different user")

    client = StripeCheckout(
        api_key=os.environ.get("STRIPE_API_KEY", ""),
        webhook_url="https://placeholder/api/webhook/stripe",
    )
    status = await client.get_checkout_status(session_id)
    pay_status = (status.payment_status or "").lower()
    sess_status = (status.status or "").lower()

    update = {
        "payment_status": pay_status,
        "status": sess_status,
        "updated_at": _now().isoformat(),
    }

    if pay_status == "paid":
        update["processed"] = True
        await _grant_pass(db, user_id, txn.get("email", ""), session_id)

    await db.payment_transactions.update_one(
        {"session_id": session_id, "user_id": user_id},
        {"$set": update},
    )
    return {
        "payment_status": pay_status,
        "status": sess_status,
        "pro": pay_status == "paid",
    }


async def _grant_pass(db, user_id: str, email: str, session_id: str) -> None:
    """Idempotently extend the user's pro pass by 30 days from now (or current end)."""
    existing = await db.subscriptions.find_one({"user_id": user_id})
    now = _now()
    base_end = now
    if existing and existing.get("current_period_end"):
        try:
            cur = datetime.fromisoformat(existing["current_period_end"])
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=timezone.utc)
            if cur > now:
                base_end = cur  # stack — don't shortchange the user
        except Exception:
            pass

    new_end = base_end + timedelta(days=MONTHLY_ACCESS_PASS["days"])
    await db.subscriptions.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id,
            "email": email,
            "plan": "pro",
            "status": "active",
            "current_period_end": new_end.isoformat(),
            # Access-pass model: each $7.99 grants 30 days. We surface this as
            # "Auto-renew OFF" by default so users aren't surprised; they can
            # always extend with another $7.99 from the Manage modal.
            "cancel_at_period_end": True,
            "last_session_id": session_id,
            "updated_at": now.isoformat(),
        },
         "$setOnInsert": {"created_at": now.isoformat()}},
        upsert=True,
    )


async def set_cancel_at_period_end(db, user, cancel: bool) -> dict:
    """Toggle cancel_at_period_end. For real Stripe Subscriptions, this calls
    the Stripe API so Stripe actually stops auto-charging. For the one-time
    access-pass fallback, it's informational only (pass auto-expires anyway)."""
    doc = await db.subscriptions.find_one({"user_id": user["user_id"]})
    sub_id = doc and doc.get("stripe_subscription_id")
    api_key = os.environ.get("STRIPE_API_KEY", "").strip()

    if sub_id and api_key:
        try:
            stripe.api_key = api_key
            stripe.Subscription.modify(sub_id, cancel_at_period_end=bool(cancel))
        except Exception as e:
            log.exception("Stripe cancel toggle failed")
            raise HTTPException(status_code=502, detail=f"Stripe rejected update: {e}")

    await db.subscriptions.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"cancel_at_period_end": bool(cancel), "updated_at": _now().isoformat()}},
    )
    return await get_billing_status(db, user)


# ───────────────────────── webhook ─────────────────────────
async def handle_webhook(db, host_url: str, body: bytes, signature: str) -> dict:
    """Process a Stripe webhook. Handles BOTH:
      1. Internal Checkout API sessions (have payment_transactions row) — granted
         to txn.user_id.
      2. External Payment Link sessions (no internal row) — granted to the
         `client_reference_id` Stripe attaches to the session (we pass it in
         the redirect URL when the user clicks Unlock).
    """
    # Verify + parse the event. Prefer the official SDK so we can read
    # `client_reference_id` (the emergentintegrations wrapper exposes a slim
    # WebhookEventResponse without that field).
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    event = None
    if secret and api_key:
        try:
            stripe.api_key = api_key
            event = stripe.Webhook.construct_event(payload=body, sig_header=signature, secret=secret)
        except Exception as e:
            log.warning("stripe signature verify failed (%s) — falling back to emergentintegrations parse", e)

    if event is None:
        # Fallback path — when no webhook secret is configured (or verify failed),
        # use the emergent wrapper which is lenient with the shared test key.
        client = _client(host_url)
        evt = await client.handle_webhook(body, signature)
        if evt.payment_status and evt.payment_status.lower() == "paid" and evt.session_id:
            await _grant_for_session_id(db, evt.session_id, api_key)
        return {"event_type": evt.event_type, "session_id": evt.session_id, "payment_status": evt.payment_status}

    # Verified path — branch on event type
    etype = event["type"]
    obj = event["data"]["object"]
    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session_id = obj.get("id")
        pay_status = (obj.get("payment_status") or "").lower()
        if session_id and pay_status == "paid":
            client_ref = obj.get("client_reference_id")
            cust_email = (obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or ""
            await _grant_for_session_id(db, session_id, api_key, client_ref=client_ref, email=cust_email, session_obj=obj)
        return {"event_type": etype, "session_id": session_id, "payment_status": pay_status}

    if etype == "customer.subscription.deleted":
        # For real subscriptions: drop the subscription doc when canceled at end
        sub_id = obj.get("id")
        await db.subscriptions.update_one(
            {"stripe_subscription_id": sub_id},
            {"$set": {"status": "canceled", "cancel_at_period_end": True, "updated_at": _now().isoformat()}},
        )
        return {"event_type": etype, "subscription_id": sub_id}

    return {"event_type": etype, "ignored": True}


async def _grant_for_session_id(
    db,
    session_id: str,
    api_key: str,
    client_ref: str | None = None,
    email: str = "",
    session_obj: dict | None = None,
) -> None:
    """Grant 30-day pass keyed off either an internal payment_transactions row
    OR (for external Payment Links) the client_reference_id on the Stripe session."""
    # 1. Internal Checkout path — find by session_id
    txn = await db.payment_transactions.find_one({"session_id": session_id})
    if txn and not txn.get("processed"):
        await _grant_pass(db, txn["user_id"], txn.get("email", email), session_id)
        await db.payment_transactions.update_one(
            {"session_id": session_id},
            {"$set": {"payment_status": "paid", "status": "complete", "processed": True, "updated_at": _now().isoformat()}},
        )
        return

    # 2. External Payment Link path — use client_reference_id
    if client_ref is None and api_key and not session_obj:
        # Fetch session from Stripe to extract client_reference_id + email
        try:
            stripe.api_key = api_key
            session_obj = stripe.checkout.Session.retrieve(session_id)
            client_ref = session_obj.get("client_reference_id")
            if not email:
                email = (session_obj.get("customer_details") or {}).get("email") or ""
        except Exception as e:
            log.warning("could not retrieve stripe session %s: %s", session_id, e)

    if client_ref:
        await _grant_pass(db, client_ref, email, session_id)
        return

    log.warning("webhook session %s paid but no user could be identified (no internal txn, no client_reference_id)", session_id)


# ───────────────────────── daily free quota ─────────────────────────
async def consume_daily_free_quota(db, user_id: str, feature: str, limit: int = 1) -> bool:
    """Atomically check + increment the per-day usage counter for `feature`.
    Returns True if the user was allowed to consume (i.e. under the limit), False if rate-limited.
    """
    today = _now().date().isoformat()  # UTC day key
    key = f"{user_id}:{feature}:{today}"
    doc = await db.daily_quota.find_one_and_update(
        {"key": key},
        {"$inc": {"count": 1},
         "$setOnInsert": {"key": key, "user_id": user_id, "feature": feature, "day": today, "created_at": _now().isoformat()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    # After upsert+inc, doc.count is the post-increment value
    return (doc.get("count", 0) if doc else 1) <= limit
