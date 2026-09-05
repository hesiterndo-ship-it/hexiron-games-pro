"""
Integration with HEXIRON SALES.

IMPORTANT: HEXIRON SALES licenses are stored per (product_id, group_id) —
never per individual user, and never per individual game. One purchase of
the "hexiron-games" product unlocks every Premium game for the chat
(group or private DM) that bought it, exactly like the guard/music bots.

Real endpoint (see hexiron-sales/api.py):
    GET  {SALES_API_URL}/api/v1/license?product=<id>&group_id=<chat_id>
    headers: X-API-Key: <SALES_API_KEY>
    -> {"active": true, "expires_at": <unix ts>, "plan": "...", "user_id": ...}
    -> {"active": false}

An admin must create a product with id == SALES_PRODUCT_ID inside the
HEXIRON SALES admin panel before this will ever return active=true.
"""
import logging
import time

import httpx

from config import SALES_API_URL, SALES_API_KEY, SALES_PRODUCT_ID, SALES_BOT_USERNAME, LICENSE_CACHE_TTL_SECONDS
from db import license_cached, license_cache_fresh, cache_license

log = logging.getLogger("hexiron-games.sales")


async def has_license(chat_id: int) -> bool:
    """Is HEXIRON GAMES Premium active for this chat (group or private)?"""
    if not SALES_API_URL or not SALES_API_KEY:
        # Sales integration not configured -> fall back to whatever an admin
        # granted manually via the /admin panel.
        return license_cached(chat_id)

    # Avoid hammering the Sales API on every single button tap.
    if license_cache_fresh(chat_id, LICENSE_CACHE_TTL_SECONDS):
        return license_cached(chat_id)

    try:
        async with httpx.AsyncClient(timeout=7) as client:
            r = await client.get(
                f"{SALES_API_URL}/api/v1/license",
                params={"product": SALES_PRODUCT_ID, "group_id": str(chat_id)},
                headers={"X-API-Key": SALES_API_KEY},
            )
        if r.status_code == 200:
            data = r.json()
            active = bool(data.get("active"))
            cache_license(chat_id, active, data.get("expires_at"), data.get("plan") or "")
            return active
        if r.status_code == 401:
            log.error("SALES_API_KEY رد شد (401) - کلید API با hexiron-sales یکی نیست.")
        else:
            log.warning("hexiron-sales پاسخ غیرمنتظره داد: %s", r.status_code)
    except httpx.HTTPError as e:
        log.warning("اتصال به hexiron-sales ناموفق بود، استفاده از کش: %s", e)

    return license_cached(chat_id)


def purchase_link(chat_id: int) -> str:
    """Deep-link into the HEXIRON SALES telegram bot (it's a bot, not a website)."""
    if SALES_BOT_USERNAME:
        return f"https://t.me/{SALES_BOT_USERNAME}?start=buy_{SALES_PRODUCT_ID}_{chat_id}"
    return ""
