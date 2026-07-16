"""Referral program: refer a friend → both get 1 month free when they pay.

Flow:
  1. Every signed-in user gets a deterministic 8-char referral code derived
     from their user_id (uppercased hex slice). It never changes.
  2. New visitor lands on `/?ref=CODE`, front-end stashes the code in
     localStorage, then submits it to /api/referral/track after Google auth
     completes. We record a pending row in `referrals`.
  3. When the referred user's first checkout succeeds (detected inside the
     Stripe sync path), we call `credit_referral_if_pending()` — it flips the
     row to `paid` and adds 30 days of `bonus_end` to BOTH users' subscription
     docs. `_get_active_pass` treats bonus_end as a valid access grant.
  4. Bonus days stack — if a user has 3 referrals paid, they get 90 days
     tacked on top of their real Stripe period.

Design notes:
  - Bonuses live on the LOCAL sub doc only. Stripe still charges normally.
    This is deliberate: applying real Stripe coupons requires signed-in
    Stripe accounts + more complex webhook handling. The +30 days here means
    the terminal stays UNLOCKED for an extra month even if the user cancels
    Stripe billing after their next charge.
  - Self-referral is blocked. Referral is only counted once per referred user.
"""
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("referrals")

REFERRAL_BONUS_DAYS = 30
CODE_LEN = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def code_from_user_id(user_id: str) -> str:
    """Deterministic uppercase code from the user_id (hex slice)."""
    if not user_id:
        return ""
    # user_id looks like "user_4d27f884583f" — slice out the hex portion
    hex_part = user_id.split("_", 1)[-1] if "_" in user_id else user_id
    return hex_part.upper().replace("-", "")[:CODE_LEN]


async def user_id_from_code(db, code: str) -> str | None:
    """Reverse a referral code back to a user_id by scanning users. Small
    users collection so a linear scan is fine — cached upstream if needed."""
    code = (code or "").strip().upper()
    if not code or len(code) < 4:
        return None
    async for u in db.users.find({}, {"user_id": 1}):
        if code_from_user_id(u["user_id"]) == code:
            return u["user_id"]
    return None


async def track_referral(db, referred_user, referrer_code: str) -> dict:
    """Record that `referred_user` came in via `referrer_code`. Idempotent —
    a user can only be attributed to a single referrer, and only if they
    haven't paid yet."""
    referrer_code = (referrer_code or "").strip().upper()
    if not referrer_code:
        return {"tracked": False, "reason": "no_code"}

    referrer_id = await user_id_from_code(db, referrer_code)
    if not referrer_id:
        return {"tracked": False, "reason": "code_not_found"}
    if referrer_id == referred_user["user_id"]:
        return {"tracked": False, "reason": "self_referral"}

    # Already tracked?
    existing = await db.referrals.find_one({"referred_user_id": referred_user["user_id"]})
    if existing:
        return {"tracked": False, "reason": "already_tracked", "status": existing.get("status")}

    await db.referrals.insert_one({
        "referrer_user_id": referrer_id,
        "referrer_code": referrer_code,
        "referred_user_id": referred_user["user_id"],
        "referred_email": referred_user.get("email"),
        "status": "pending",
        "created_at": _now().isoformat(),
    })
    log.info("referral tracked: %s → %s", referrer_id, referred_user["user_id"])
    return {"tracked": True, "referrer_id": referrer_id}


async def _extend_bonus(db, user_id: str, days: int) -> str:
    """Add `days` to the user's local bonus_end. Returns the new bonus_end ISO."""
    sub = await db.subscriptions.find_one({"user_id": user_id})
    base = _now()
    if sub and sub.get("bonus_end"):
        try:
            cur = datetime.fromisoformat(sub["bonus_end"])
            if cur.tzinfo is None:
                cur = cur.replace(tzinfo=timezone.utc)
            if cur > base:
                base = cur
        except Exception:
            pass
    new_end = base + timedelta(days=days)
    new_iso = new_end.isoformat()
    await db.subscriptions.update_one(
        {"user_id": user_id},
        {"$set": {"bonus_end": new_iso, "updated_at": _now().isoformat()},
         "$inc": {"bonus_days_total": days},
         "$setOnInsert": {"user_id": user_id, "created_at": _now().isoformat()}},
        upsert=True,
    )
    return new_iso


async def credit_referral_if_pending(db, referred_user_id: str) -> dict | None:
    """Called from the payment sync path. If the referred user has a pending
    referral row, flip it to `paid` and grant +30 days to BOTH users. Fully
    idempotent — safe to invoke every sync."""
    row = await db.referrals.find_one({
        "referred_user_id": referred_user_id,
        "status": "pending",
    })
    if not row:
        return None
    referrer_id = row["referrer_user_id"]

    referred_end = await _extend_bonus(db, referred_user_id, REFERRAL_BONUS_DAYS)
    referrer_end = await _extend_bonus(db, referrer_id, REFERRAL_BONUS_DAYS)

    await db.referrals.update_one(
        {"_id": row["_id"]},
        {"$set": {"status": "paid", "rewarded_at": _now().isoformat(),
                  "referred_bonus_end": referred_end,
                  "referrer_bonus_end": referrer_end}},
    )
    log.info("referral credited: %s (+30d, end=%s) & %s (+30d, end=%s)",
             referrer_id, referrer_end, referred_user_id, referred_end)
    return {"referrer_id": referrer_id, "referred_id": referred_user_id, "bonus_days": REFERRAL_BONUS_DAYS}


async def get_referral_stats(db, user) -> dict:
    """Return referral link, code, and paid referral count for the widget."""
    code = code_from_user_id(user["user_id"])
    total = await db.referrals.count_documents({"referrer_user_id": user["user_id"]})
    paid = await db.referrals.count_documents({"referrer_user_id": user["user_id"], "status": "paid"})
    pending = total - paid

    # Bonus already stacked onto this user (either from being referred, or
    # from having referrals of their own).
    sub = await db.subscriptions.find_one({"user_id": user["user_id"]}, {"_id": 0})
    bonus_end = (sub or {}).get("bonus_end")
    bonus_days_total = (sub or {}).get("bonus_days_total", 0)

    # Was this user referred by someone?
    referred_by_row = await db.referrals.find_one({"referred_user_id": user["user_id"]})

    return {
        "code": code,
        "referrals_total": total,
        "referrals_paid": paid,
        "referrals_pending": pending,
        "bonus_days_earned": bonus_days_total,
        "bonus_end": bonus_end,
        "bonus_days_per_referral": REFERRAL_BONUS_DAYS,
        "was_referred": bool(referred_by_row),
        "was_referred_status": (referred_by_row or {}).get("status"),
    }
