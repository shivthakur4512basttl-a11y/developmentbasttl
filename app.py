"""
Instagram Business Insights — Streamlit app (v8)
Instagram API with Instagram Login only (graph.instagram.com / api.instagram.com).
No Facebook Login, no graph.facebook.com anywhere in this file.

WHAT CHANGED vs v7 — hard-blocking step-through OAuth UI (new; replaces the
old auto-run st.status login sequence — everything else in v7 unchanged)
--------------------------------------------------------------------------
1. The Instagram Login flow this file implements is TWO token exchanges, not
   three: (a) authorisation code -> short-lived token, (b) short-lived token
   -> long-lived (~60 day) token. A third function, refresh_long_lived_token,
   exists in section 3 but is never called on this path — it's for later,
   once you persist tokens to a DB and refresh them after ~24h. Anything
   describing "three tokens" for THIS flow is describing a step that doesn't
   run; the step-through below only ever shows two calls because only two
   calls happen.
2. Login now proceeds as an explicit state machine in st.session_state
   ("oauth_step": await_code -> run_step1 -> run_step2 -> done), so each
   token exchange BLOCKS on a "Next" button click instead of both firing
   back-to-back inside one st.status(...) the way v7 did. After each call,
   the exact request (method, full URL, body) and the exact response body
   Meta returned are shown on screen before the Next button appears — same
   masking rule as the Sequential execution tab (access_token/client_secret
   VALUES redacted, nothing else touched).
3. New _render_call_detail() helper (section 6) does this rendering. It
   pulls the just-made call straight off st.session_state.api_call_log[-1]
   — the SAME entry the existing response hook (_log_api_call, section 2)
   already recorded — rather than re-deriving or re-masking anything. That
   keeps the login step-through and the Sequential execution tab showing
   byte-identical data for the same two calls, by construction, not by
   coincidence.
4. The one-time `code` from Instagram's redirect is stashed in
   st.session_state.oauth_code immediately (before query params are
   cleared), because Streamlit reruns on every button click and a query
   param does not survive that on its own. CSRF `state` verification is
   unchanged from v7 and still happens before the code is ever used.
5. Failure handling: if either exchange comes back without access_token,
   the failing response is still shown in full (nothing is hidden on
   error), and a "Restart login" button clears the OAuth state and returns
   to the login link — it does not attempt to reuse a burned one-time code.
6. Nothing else in the v7 file changed: the Sequential execution tab,
   every fetcher, every metric formula, and every other tab are untouched.

(v7 and earlier changelog entries retained below for full history.)

WHAT CHANGED vs v7(orig) — sequential execution log (new tab, purely additive)
--------------------------------------------------------------------------
1. New "Sequential execution" tab: every network call this file makes, in
   the order it actually happened, starting with the OAuth token exchange.
   Per call — method, full endpoint, HTTP status, response time, and the
   COMPLETE response body exactly as Meta returned it (not the filtered /
   computed values the rest of the app derives from it). Plus a running
   call count, a per-endpoint breakdown, a Clear-log button, and a JSON
   download of the whole log.
2. Implementation is a single `requests` response hook attached to the
   existing shared SESSION object (see "Sequential API call log" in
   section 2, directly above _record_error). A hook observes each response
   after it arrives and, by returning None, hands it back completely
   unchanged — so this doesn't alter what any existing function does,
   receives, or returns; it only watches. Because every call in this file —
   the OAuth calls in section 3 included — goes through this one SESSION,
   one hook sees all of it.
3. One deliberate exception to "complete, unfiltered": access_token and
   client_secret VALUES are masked (prefix/suffix + length shown) wherever
   they appear, in requests and in responses (the two OAuth-exchange
   responses return the token as their payload). Reasoning, and how to
   remove this if you want raw values instead: see the comment block above
   _log_api_call.
4. The only touch points in previously-existing code: one new stdlib
   import (json, for the download button) and the st.tabs([...]) call
   gained a sixth tab. No existing function body, return value, cache key,
   formula, or call site changed. No new pip dependency, no new env var.

ENV VARS REQUIRED (Streamlit Cloud -> Settings -> Secrets, or local .env):
    INSTA_APP_ID        Instagram app Client ID
    INSTA_APP_SECRET    Instagram app Client Secret
    INSTA_REDIRECT_URI  Exact redirect URL registered in the Meta App
                        Dashboard, character for character. Use the app's
                        ROOT url (https://yourapp.streamlit.app/) unless you
                        have created a matching Streamlit page for a subpath.

Dependencies: streamlit>=1.41, requests, python-dotenv
              (pandas + altair ship with streamlit — used for charts)
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets as pysecrets
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import altair as alt
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")
REDIRECT_URI = os.getenv("INSTA_REDIRECT_URI", "").strip()

API_VERSION = "v25.0"          # per Meta docs, latest at time of writing
GRAPH_HOST = "https://graph.instagram.com"

# ONLY the two permissions your app holds. Add others back one at a time,
# and only after they show as available in the Meta App Dashboard.
SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_insights",
]

# Metrics valid for BOTH feed posts and reels -> safe to batch via field
# expansion on /media. Type-specific metrics (watch time, skip rate, follows,
# profile_visits) error on the wrong media type, so they are fetched
# per-media in fetch_media_extras() instead.
COMMON_MEDIA_INSIGHTS = "views,reach,saved,shares,reposts,total_interactions"

MEDIA_FIELDS = (
    "id,timestamp,permalink,caption,media_type,media_product_type,"
    "media_url,thumbnail_url,like_count,comments_count,"
    f"insights.metric({COMMON_MEDIA_INSIGHTS})"
)

# Fallback field set: same media data, NO insights expansion. Used when Meta
# rejects the whole expanded /media call (one bad metric fails the request).
BASIC_MEDIA_FIELDS = (
    "id,timestamp,permalink,caption,media_type,media_product_type,"
    "media_url,thumbnail_url,like_count,comments_count"
)

REELS_EXTRA_METRICS = "ig_reels_avg_watch_time,ig_reels_video_view_total_time,reels_skip_rate"
FEED_EXTRA_METRICS = "follows,profile_visits"

WINDOW_DAYS = 30
TOP_N_POSTS = 3
MAX_ENRICHED_MEDIA = 80        # per-media extra-insight calls are capped here
CACHE_TTL = 600                # seconds; "Refresh data" button clears it

SESSION = requests.Session()


# ---------------------------------------------------------------------------
# 2. HTTP CORE — one request path, one error shape
# ---------------------------------------------------------------------------

_SECRET_KEYS = ("access_token", "client_secret")


def _mask_secret(value: str) -> str:
    if len(value) <= 10:
        return "***REDACTED***"
    return f"{value[:6]}…{value[-4:]} (redacted, {len(value)} chars)"


def _redact_secrets(text) -> str:
    """Masks only access_token / client_secret VALUES — as URL/form params
    (key=value) or JSON fields ("key": "value") — everywhere they occur.
    Every other byte of the request or response is left exactly as-is."""
    if not text:
        return text or ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    for key in _SECRET_KEYS:
        text = re.sub(rf"({key}=)([^&\s\"]+)",
                      lambda m: m.group(1) + _mask_secret(m.group(2)), text)
        text = re.sub(rf'("{key}"\s*:\s*")([^"]+)(")',
                      lambda m: m.group(1) + _mask_secret(m.group(2)) + m.group(3), text)
    return text


def _endpoint_key(url: str) -> str:
    """Collapses a full URL to a stable shape for the 'how many times / which
    endpoint' summary — drops the query string and folds Instagram's long
    numeric IDs to '{id}' so e.g. every .../insights call for every media ID
    counts as one endpoint family. The exact full URL is still kept per-call
    for the detail view; this is only for the grouped counts."""
    path = url.split("?", 1)[0]
    return re.sub(r"/\d{6,}", "/{id}", path)


def _log_api_call(response, *args, **kwargs) -> None:
    """requests 'response' hook — fires once per completed HTTP response on
    SESSION, for every call this file makes. Falling off the end (returning
    None) leaves the response requests hands back to the calling code
    completely unmodified; this function only ever reads it."""
    req = response.request
    body = req.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    entry = {
        "seq": len(st.session_state.get("api_call_log", [])) + 1,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z",
        "method": req.method,
        "url": _redact_secrets(req.url),
        "endpoint": _endpoint_key(req.url),
        "request_body": _redact_secrets(body) if body else "",
        "status_code": response.status_code,
        "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 1),
        "response_text": _redact_secrets(response.text),
        "response_headers": dict(response.headers),
    }
    st.session_state.setdefault("api_call_log", []).append(entry)


SESSION.hooks.setdefault("response", []).append(_log_api_call)


def _record_error(context: str, err: dict) -> None:
    st.session_state.setdefault("api_errors", []).append(
        {"context": context, "error": err}
    )


def api_get(path: str, token: str, **params) -> tuple[dict, dict | None]:
    """GET graph.instagram.com/<version>/<path>. Returns (data, error).
    error is Meta's error object ({message, code, ...}) or a transport stub."""
    params["access_token"] = token
    url = f"{GRAPH_HOST}/{API_VERSION}/{path}"
    try:
        r = SESSION.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        return {}, {"message": f"Network error: {exc}", "transport": True}
    try:
        data = r.json()
    except ValueError:
        return {}, {"message": f"Non-JSON response (HTTP {r.status_code})"}
    if isinstance(data, dict) and "error" in data:
        return {}, data["error"]
    return data, None


def api_get_absolute(url: str) -> tuple[dict, dict | None]:
    """For pagination `next` URLs, which already carry all params."""
    try:
        r = SESSION.get(url, timeout=20)
        data = r.json()
    except requests.RequestException as exc:
        return {}, {"message": f"Network error: {exc}", "transport": True}
    except ValueError:
        return {}, {"message": "Non-JSON response"}
    if isinstance(data, dict) and "error" in data:
        return {}, data["error"]
    return data, None


# ---------------------------------------------------------------------------
# 3. OAUTH — Business Login for Instagram
# ---------------------------------------------------------------------------

def build_authorize_url(state: str) -> str:
    scope_str = ",".join(SCOPES)
    return (
        "https://www.instagram.com/oauth/authorize"
        f"?client_id={INSTA_APP_ID}"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&scope={quote(scope_str, safe=',')}"
        f"&response_type=code"
        f"&state={quote(state, safe='')}"
    )


def exchange_code_for_short_token(code: str) -> dict:
    resp = SESSION.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": INSTA_APP_ID,
            "client_secret": INSTA_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
        timeout=20,
    )
    try:
        data = resp.json()
    except ValueError:
        return {"error_message": f"Non-JSON token response (HTTP {resp.status_code})"}
    if isinstance(data, dict) and isinstance(data.get("data"), list) and data["data"]:
        data = data["data"][0]
    return data


def exchange_for_long_lived_token(short_token: str) -> dict:
    resp = SESSION.get(
        f"{GRAPH_HOST}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": INSTA_APP_SECRET,
            "access_token": short_token,
        },
        timeout=20,
    )
    try:
        return resp.json()
    except ValueError:
        return {"error_message": "Non-JSON long-lived token response"}


def refresh_long_lived_token(token: str) -> dict:
    """Long-lived tokens last ~60 days and can be refreshed after 24h.
    Not called automatically here (token lives only in session_state);
    wire this in once you persist tokens to your DB. This is the ONLY
    other token-related call this file defines — it does not run as part
    of the login step-through because login never needs it."""
    resp = SESSION.get(
        f"{GRAPH_HOST}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=20,
    )
    try:
        return resp.json()
    except ValueError:
        return {"error_message": "Non-JSON refresh response"}


# ---------------------------------------------------------------------------
# 4. FETCHERS  (cached; token is part of the cache key, memory-only)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_identity(token: str) -> dict:
    data, err = api_get("me", token, fields="id,user_id,username,name")
    if err:
        _record_error("identity", err)
    return data


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_profile(token: str, ig_user_id: str) -> dict:
    data, err = api_get(
        ig_user_id, token,
        fields=("account_type,biography,website,profile_picture_url,"
                "followers_count,follows_count,media_count"),
    )
    if err:
        _record_error("profile", err)
    return data


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_common_media_insights(token: str, media_id: str) -> dict:
    """Fallback: the common insight set for ONE media item, same shape as the
    field-expansion output. Drops any metric Meta names in its error and
    retries, so one unsupported metric can't zero out the rest."""
    remaining = COMMON_MEDIA_INSIGHTS.split(",")
    for _ in range(3):
        if not remaining:
            return {}
        data, err = api_get(f"{media_id}/insights", token, metric=",".join(remaining))
        if not err:
            return {"data": data.get("data", [])}
        msg = str(err.get("message", "")).lower()
        dropped = [m for m in remaining if m in msg]
        if not dropped:
            _record_error(f"media insights {media_id}", err)
            return {}
        for m in dropped:
            remaining.remove(m)
    return {}


def _parse_ig_timestamp(raw: str) -> datetime | None:
    """IG uses '2026-08-20T12:34:56+0000'. Tolerate fractional seconds and
    ISO variants; assume UTC if no offset survives parsing."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(raw, fmt)
        except (TypeError, ValueError):
            pass
    try:
        ts = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_media_window(token: str, ig_user_id: str, days: int,
                       align: str = "day_floor", tz_h: float = 0.0) -> list[dict]:
    """All media published inside the selected window, with the common
    insight set attached."""
    _ranges = _chunk_ranges(days, align=align, tz_h=tz_h)
    cutoff = datetime.fromtimestamp(_ranges[0][0], tz=timezone.utc)
    upper = datetime.fromtimestamp(_ranges[-1][1], tz=timezone.utc)
    posts: list[dict] = []
    expansion_ok = True
    data, err = api_get(f"{ig_user_id}/media", token, fields=MEDIA_FIELDS, limit=50)
    if err:
        _record_error("media list (insights expansion failed — retrying without it)", err)
        expansion_ok = False
        data, err = api_get(f"{ig_user_id}/media", token,
                            fields=BASIC_MEDIA_FIELDS, limit=50)
    seen = skipped_parse = 0
    while True:
        if err:
            _record_error("media list", err)
            break
        page = data.get("data", [])
        seen += len(page)
        page_has_recent = False
        for post in page:
            ts = _parse_ig_timestamp(post.get("timestamp", ""))
            if ts is None:
                skipped_parse += 1
                continue
            if ts >= cutoff:
                page_has_recent = True
                if ts < upper:
                    posts.append(post)
        if page and not page_has_recent:
            break
        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        data, err = api_get_absolute(next_url)
    if seen and not posts:
        msg = (f"The API returned {seen} media item(s) but none were inside "
               f"the last {days} days")
        if skipped_parse:
            msg += f"; {skipped_parse} timestamp(s) failed to parse"
        _record_error("media window", {"message": msg})
    if not expansion_ok:
        for post in posts:
            post["insights"] = fetch_common_media_insights(token, post["id"])
    return posts


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_media_extras(token: str, media_id: str, product_type: str) -> dict:
    """Type-specific per-media insights."""
    if product_type == "REELS":
        data, err = api_get(f"{media_id}/insights", token, metric=REELS_EXTRA_METRICS)
        if err:
            _record_error(f"media extras {media_id}", err)
            return {}
        out = {}
        for m in data.get("data", []):
            vals = m.get("values", [])
            if vals and isinstance(vals[0], dict):
                out[m.get("name")] = vals[0].get("value", 0)
        return out

    out = {}
    data, err = api_get(f"{media_id}/insights", token, metric=FEED_EXTRA_METRICS)
    if err:
        _record_error(f"media extras {media_id}", err)
    else:
        for m in data.get("data", []):
            vals = m.get("values", [])
            if vals and isinstance(vals[0], dict):
                out[m.get("name")] = vals[0].get("value", 0)

    pa_data, pa_err = api_get(f"{media_id}/insights", token,
                              metric="profile_activity", breakdown="action_type")
    if pa_err:
        _record_error(f"media profile_activity {media_id}", pa_err)
    else:
        for m in pa_data.get("data", []):
            vals = m.get("values", [])
            if vals and isinstance(vals[0], dict):
                out["profile_activity"] = vals[0].get("value", 0)
            by = {}
            for bd in (m.get("total_value", {}) or {}).get("breakdowns", []) or []:
                for res in bd.get("results", []) or []:
                    dims = res.get("dimension_values", []) or ["?"]
                    by[dims[-1]] = by.get(dims[-1], 0) + res.get("value", 0)
            if by:
                out["profile_activity_by_action"] = by
    return out


def _parse_total_value_payload(data: dict) -> dict:
    out: dict = {}
    for m in data.get("data", []):
        name = m.get("name")
        tv = m.get("total_value", {}) or {}
        entry = {"total": tv.get("value", 0), "by": {}, "source": "meta_total"}
        for bd in tv.get("breakdowns", []) or []:
            for res in bd.get("results", []) or []:
                dims = res.get("dimension_values", []) or ["?"]
                entry["by"][dims[-1]] = entry["by"].get(dims[-1], 0) + res.get("value", 0)
        if not entry["total"] and entry["by"]:
            entry["total"] = sum(entry["by"].values())
            entry["source"] = "breakdown_sum"
        out[name] = entry
    return out


WINDOW_ALIGN_MODES = {
    "Last N complete days (excl. today) — matches the app (views verified)": "complete_days",
    "Day-aligned days, incl. today": "day_floor",
    "Rolling — exact now − N days": "rolling",
}


def _chunk_ranges(days: int, max_span: int = 30, align: str = "day_floor",
                  tz_h: float = 0.0) -> list[tuple[int, int]]:
    tz = timezone(timedelta(hours=tz_h))
    now = datetime.now(tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if align == "rolling":
        lower, upper = now - timedelta(days=days), now
    elif align == "complete_days":
        lower, upper = midnight - timedelta(days=days), midnight
    else:  # "day_floor"
        lower = (now - timedelta(days=days)).replace(hour=0, minute=0,
                                                     second=0, microsecond=0)
        upper = now
    out = []
    cur = lower
    while cur < upper:
        nxt = min(cur + timedelta(days=max_span), upper)
        out.append((int(cur.timestamp()), int(nxt.timestamp())))
        cur = nxt
    return out


def window_bounds_label(days: int, align: str = "day_floor",
                        tz_h: float = 0.0) -> str:
    ranges = _chunk_ranges(days, align=align, tz_h=tz_h)
    if not ranges:
        return "—"
    tz = timezone(timedelta(hours=tz_h))
    since_dt = datetime.fromtimestamp(ranges[0][0], tz=tz)
    until_dt = datetime.fromtimestamp(ranges[-1][1], tz=tz)
    off = f"UTC{tz_h:+g}" if tz_h else "UTC"
    return f"{since_dt:%Y-%m-%d %H:%M} \u2192 {until_dt:%Y-%m-%d %H:%M} {off}"


def _totals_single(token: str, ig_user_id: str, metrics: list[str],
                   since: int, until: int, context: str, **extra) -> dict:
    remaining = list(metrics)
    for _ in range(4):
        if not remaining:
            return {}
        data, err = api_get(
            f"{ig_user_id}/insights", token,
            metric=",".join(remaining), period="day",
            metric_type="total_value", since=since, until=until, **extra,
        )
        if not err:
            return _parse_total_value_payload(data)
        msg = str(err.get("message", "")).lower()
        dropped = [m for m in remaining if m.lower() in msg]
        if not dropped:
            _record_error(context, err)
            return {}
        for m in dropped:
            remaining.remove(m)
        _record_error(f"{context} (dropped: {', '.join(dropped)})", err)
    return {}


def _totals_with_metric_dropping(token: str, ig_user_id: str, metrics: list[str],
                                 days: int, context: str, *,
                                 align: str = "day_floor", tz_h: float = 0.0,
                                 **extra) -> dict:
    merged: dict = {}
    for since, until in _chunk_ranges(days, align=align, tz_h=tz_h):
        part = _totals_single(token, ig_user_id, metrics, since, until,
                              context, **extra)
        for name, entry in part.items():
            slot = merged.setdefault(name, {"total": 0, "by": {}, "source": None})
            slot["total"] += entry.get("total", 0)
            src = entry.get("source", "meta_total")
            slot["source"] = src if slot["source"] in (None, src) else "mixed"
            for k, v in (entry.get("by") or {}).items():
                slot["by"][k] = slot["by"].get(k, 0) + v
    return merged


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_account_totals_by_format(token: str, ig_user_id: str, days: int,
                                   align: str = "day_floor",
                                   tz_h: float = 0.0) -> dict:
    return _totals_with_metric_dropping(
        token, ig_user_id,
        ["reach", "views", "likes", "comments", "saves", "shares", "total_interactions"],
        days, "account totals by format", align=align, tz_h=tz_h,
        breakdown="media_product_type",
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_account_totals_plain(token: str, ig_user_id: str, days: int,
                               align: str = "day_floor",
                               tz_h: float = 0.0) -> dict:
    return _totals_with_metric_dropping(
        token, ig_user_id,
        ["accounts_engaged", "replies", "reposts", "profile_links_taps"],
        days, "account totals", align=align, tz_h=tz_h,
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_reach_plain(token: str, ig_user_id: str, days: int,
                      align: str = "day_floor", tz_h: float = 0.0) -> dict:
    return _totals_with_metric_dropping(
        token, ig_user_id, ["reach"], days, "reach (no breakdown)",
        align=align, tz_h=tz_h,
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_profile_links_taps_by_button(token: str, ig_user_id: str, days: int,
                                       align: str = "day_floor",
                                       tz_h: float = 0.0) -> dict:
    return _totals_with_metric_dropping(
        token, ig_user_id, ["profile_links_taps"], days,
        "profile links taps by button", align=align, tz_h=tz_h,
        breakdown="contact_button_type",
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_follows_unfollows(token: str, ig_user_id: str, days: int,
                            align: str = "day_floor",
                            tz_h: float = 0.0) -> dict:
    return _totals_with_metric_dropping(
        token, ig_user_id, ["follows_and_unfollows"], days,
        "follows/unfollows", align=align, tz_h=tz_h, breakdown="follow_type",
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_follower_split(token: str, ig_user_id: str, days: int,
                         align: str = "day_floor", tz_h: float = 0.0) -> dict:
    out: dict = {}
    for metric in ("views", "reach", "total_interactions"):
        for bd in ("follower_type", "follow_type"):
            res = _totals_with_metric_dropping(
                token, ig_user_id, [metric], days,
                f"{metric} follower split ({bd})", align=align, tz_h=tz_h,
                breakdown=bd)
            if (res.get(metric) or {}).get("by"):
                out[metric] = res[metric]
                break
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_timeseries(token: str, ig_user_id: str, metric: str, days: int,
                     align: str = "day_floor", tz_h: float = 0.0) -> list[dict]:
    out: list[dict] = []
    for since, until in _chunk_ranges(days, align=align, tz_h=tz_h):
        data, err = api_get(
            f"{ig_user_id}/insights", token,
            metric=metric, period="day", metric_type="time_series",
            since=since, until=until,
        )
        if err:
            _record_error(f"time series {metric}", err)
            continue
        for m in data.get("data", []):
            if m.get("name") != metric:
                continue
            for v in m.get("values", []):
                end = (v.get("end_time") or "")[:10]
                out.append({"date": end, "value": v.get("value", 0)})
    dedup = {row["date"]: row for row in out}
    return [dedup[d] for d in sorted(dedup)]


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_online_followers_raw(token: str, ig_user_id: str) -> list[tuple[str, dict]]:
    data, err = api_get(
        f"{ig_user_id}/insights", token,
        metric="online_followers", period="lifetime",
    )
    if err:
        _record_error("online followers", err)
        return []
    out = []
    for m in data.get("data", []):
        for v in m.get("values", []):
            val = v.get("value")
            if isinstance(val, dict):
                out.append(((v.get("end_time") or "")[:10], val))
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_demographics(token: str, ig_user_id: str, metric: str,
                       breakdown: str, timeframe: str) -> list[tuple[str, int]]:
    data, err = api_get(
        f"{ig_user_id}/insights", token,
        metric=metric, period="lifetime", timeframe=timeframe,
        metric_type="total_value", breakdown=breakdown,
    )
    if err:
        _record_error(f"demographics {metric}/{breakdown}", err)
        return []
    rows: list[tuple[str, int]] = []
    for m in data.get("data", []):
        tv = m.get("total_value", {}) or {}
        for bd in tv.get("breakdowns", []) or []:
            for res in bd.get("results", []) or []:
                dims = res.get("dimension_values", []) or ["?"]
                rows.append((str(dims[-1]), res.get("value", 0)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# 5. METRICS
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "D2C & E-commerce": ["d2c", "ecommerce", "e-commerce", "shopify", "dropshipping",
                         "online store", "amazon", "flipkart", "meesho",
                         "quick commerce", "brand owner", "cod orders"],
    "Marketing & Growth": ["performance marketing", "digital marketing", "meta ads",
                           "facebook ads", "google ads", "roas", "funnel",
                           "conversion", "seo", "copywriting", "branding",
                           "growth tactics", "agency", "ad creative"],
    "Business & Startups": ["founder", "entrepreneur", "startup", "bootstrapped",
                            "business growth", "b2b", "ceo", "venture", "scaling"],
    "Finance & Investing": ["personal finance", "investing", "stock market",
                            "mutual fund", "trading", "crypto", "wealth", "sip",
                            "tax saving", "financial freedom"],
    "Tech & AI": ["artificial intelligence", " ai ", "saas", "software", "coding",
                  "developer", "automation", "robotics", "gadgets", "no-code"],
    "Fashion": ["fashion", "outfit", "ootd", "streetwear", "saree", "ethnic wear",
                "lookbook", "styling"],
    "Beauty & Skincare": ["beauty", "makeup", "skincare", "cosmetics", "haircare",
                          "lipstick", "glowup"],
    "Fitness": ["fitness", "gym", "workout", "bodybuilding", "yoga", "trainer",
                "calisthenics", "protein", "transformation"],
    "Health & Wellness": ["wellness", "nutrition", "diet", "mental health",
                          "ayurveda", "weight loss", "healthy habits"],
    "Food & Cooking": ["food", "recipe", "cooking", "foodie", "chef", "restaurant",
                       "street food", "baking"],
    "Travel": ["travel", "wanderlust", "itinerary", "backpacking", "roadtrip",
               "tourism", "hidden gems"],
    "Education & Career": ["education", "learning", "study tips", "upsc", "career",
                           "skills", "course", "teacher", "coaching", "exam"],
    "Entertainment & Comedy": ["comedy", "memes", "funny", "sketch", "standup",
                               "entertainment", "prank"],
    "Music & Dance": ["music", "singer", "musician", "dance", "choreography",
                      "cover song", "playlist"],
    "Art & Design": ["artist", "illustration", "graphic design", "ui design",
                     "ux design", "painting", "typography"],
    "Photography & Video": ["photography", "photographer", "videography",
                            "filmmaker", "video editing", "cinematic", "dslr"],
    "Gaming": ["gaming", "gamer", "esports", "streamer", "bgmi", "valorant",
               "minecraft"],
    "Parenting & Family": ["parenting", "momlife", "dadlife", "toddler",
                           "newborn", "kids activities"],
    "Motivation & Lifestyle": ["motivation", "mindset", "self improvement",
                               "productivity", "habits", "discipline",
                               "lifestyle"],
}

CATEGORY_MIN_SCORE = 4.0
_CAPTION_TERM_CAP = 5


def _match_terms_text(text: str, terms: list[str]) -> set[str]:
    found = set()
    for t in terms:
        t_ = t.strip()
        if " " in t_ or "-" in t_:
            if t_ in text:
                found.add(t)
        elif re.search(rf"\b{re.escape(t_)}\b", text):
            found.add(t)
    return found


def _match_terms_glued(text: str, terms: list[str]) -> set[str]:
    found = set()
    squashed = text.replace(" ", "")
    for t in terms:
        t_ = t.strip().replace(" ", "")
        if " " in t or any(ch.isdigit() for ch in t_) or len(t_) >= 4:
            if t_ and t_ in squashed:
                found.add(t)
    return found


def infer_categories(profile: dict, identity: dict, posts: list[dict],
                     top_n: int = 3) -> list[dict]:
    prof_text = " ".join(str(x or "") for x in (
        profile.get("biography"), identity.get("name"))).lower()
    glued_text = " ".join(str(x or "") for x in (
        identity.get("username"), profile.get("website"))).lower()
    captions = [(p.get("caption") or "").lower() for p in posts]
    hashtags = " ".join(tag for c in captions for tag in re.findall(r"#(\w+)", c))

    results = []
    for category, terms in CATEGORY_KEYWORDS.items():
        score = 0.0
        evidence: set[str] = set()
        hits = _match_terms_text(prof_text, terms)
        score += 3.0 * len(hits)
        evidence |= hits
        hits = _match_terms_glued(glued_text, terms)
        score += 2.0 * len(hits)
        evidence |= hits
        hits = _match_terms_glued(hashtags, terms)
        score += 2.0 * len(hits)
        evidence |= hits
        for t in terms:
            n_caps = sum(1 for c in captions if _match_terms_text(c, [t]))
            if n_caps:
                score += 1.0 * min(n_caps, _CAPTION_TERM_CAP)
                evidence.add(t)
        if score > 0:
            results.append({"category": category, "score": round(score, 1),
                            "evidence": sorted(evidence)[:6]})
    results.sort(key=lambda r: r["score"], reverse=True)
    if not results or results[0]["score"] < CATEGORY_MIN_SCORE:
        return []
    total = sum(r["score"] for r in results) or 1.0
    out = results[:top_n]
    for r in out:
        r["share_pct"] = round(r["score"] / total * 100, 1)
    return out


def _post_insight_value(post: dict, name: str) -> int:
    for m in (post.get("insights") or {}).get("data", []):
        if m.get("name") == name:
            vals = m.get("values", [])
            if vals and isinstance(vals[0], dict):
                return vals[0].get("value", 0) or 0
    return 0


def split_by_format(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    reels = [p for p in posts if p.get("media_product_type") == "REELS"]
    feed = [p for p in posts if p.get("media_product_type") != "REELS"]
    return reels, feed


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 2) if xs else 0.0


def group_stats(posts: list[dict], followers: int,
                extras: dict[str, dict]) -> dict:
    n = len(posts)
    sums = {k: 0 for k in ["views", "reach", "saved", "shares", "reposts",
                            "interactions", "likes", "comments"]}
    er_rates, reach_rates = [], []
    watch_avgs_s, skip_rates = [], []
    total_watch_s = 0.0
    follows_sum, visits_sum = 0, 0
    profile_activity_sum = 0
    profile_activity_by_action: dict[str, int] = {}

    for p in posts:
        reach = _post_insight_value(p, "reach")
        inter = _post_insight_value(p, "total_interactions")
        sums["views"] += _post_insight_value(p, "views")
        sums["reach"] += reach
        sums["saved"] += _post_insight_value(p, "saved")
        sums["shares"] += _post_insight_value(p, "shares")
        sums["reposts"] += _post_insight_value(p, "reposts")
        sums["interactions"] += inter
        sums["likes"] += p.get("like_count", 0) or 0
        sums["comments"] += p.get("comments_count", 0) or 0
        if reach > 0:
            er_rates.append(inter / reach * 100)
            if followers:
                reach_rates.append(reach / followers * 100)
        ex = extras.get(p.get("id", ""), {})
        if "ig_reels_avg_watch_time" in ex:
            watch_avgs_s.append(ex["ig_reels_avg_watch_time"] / 1000.0)
        if "ig_reels_video_view_total_time" in ex:
            total_watch_s += ex["ig_reels_video_view_total_time"] / 1000.0
        if "reels_skip_rate" in ex:
            skip_rates.append(float(ex["reels_skip_rate"]))
        follows_sum += ex.get("follows", 0) or 0
        visits_sum += ex.get("profile_visits", 0) or 0
        profile_activity_sum += ex.get("profile_activity", 0) or 0
        for action, cnt in (ex.get("profile_activity_by_action") or {}).items():
            profile_activity_by_action[action] = profile_activity_by_action.get(action, 0) + cnt

    reach_sum = sums["reach"]

    def rate(x: int) -> float:
        return round(x / reach_sum * 100, 2) if reach_sum else 0.0

    out = {
        "count": n,
        **sums,
        "er_reach_median": _median(er_rates),
        "er_reach_mean": round(sum(er_rates) / len(er_rates), 2) if er_rates else 0.0,
        "reach_rate_median": _median(reach_rates),
        "save_rate": rate(sums["saved"]),
        "share_rate": rate(sums["shares"]),
        "comment_rate": rate(sums["comments"]),
        "views_per_reach": round(sums["views"] / reach_sum, 2) if reach_sum else 0.0,
    }
    if watch_avgs_s:
        out["avg_watch_s_median"] = _median(watch_avgs_s)
        out["total_watch_s"] = round(total_watch_s)
    if skip_rates:
        out["skip_rate_median"] = _median(skip_rates)
        out["hook_rate_median"] = round(100 - out["skip_rate_median"], 2)
    if follows_sum or visits_sum:
        out["follows_from_posts"] = follows_sum
        out["profile_visits_from_posts"] = visits_sum
        out["follow_conversion"] = rate(follows_sum)
    if profile_activity_sum:
        out["profile_activity_from_posts"] = profile_activity_sum
    if profile_activity_by_action:
        out["profile_activity_by_action"] = profile_activity_by_action
    return out


def compute_schema_metrics(posts: list[dict], followers: int,
                           account_totals: dict) -> dict:
    n = len(posts)
    likes_sum = sum(p.get("like_count", 0) or 0 for p in posts)
    inter_sum = sum(_post_insight_value(p, "total_interactions") for p in posts)
    per_post = [
        _post_insight_value(p, "total_interactions") / r * 100
        for p in posts
        if (r := _post_insight_value(p, "reach")) > 0
    ]
    account_reach = (account_totals.get("reach") or {}).get("total", 0)
    return {
        "post_count": n,
        "avg_likes_30d": round(likes_sum / n, 2) if n else 0.0,
        "er_by_followers_30d": round(inter_sum / followers * 100, 2) if followers else 0.0,
        "er_by_reach_30d": round(inter_sum / account_reach * 100, 2) if account_reach else 0.0,
        "er_per_post_30d": round(sum(per_post) / len(per_post), 2) if per_post else 0.0,
        "total_reach_30d": account_reach,
    }


def compute_industry_engagement_rate(posts: list[dict], followers: int) -> float:
    if not posts or not followers:
        return 0.0
    avg_likes = sum(p.get("like_count", 0) or 0 for p in posts) / len(posts)
    avg_comments = sum(p.get("comments_count", 0) or 0 for p in posts) / len(posts)
    return round((avg_likes + avg_comments) / followers * 100, 2)


def per_post_er_list(posts: list[dict]) -> list[float]:
    out = []
    for p in posts:
        reach = _post_insight_value(p, "reach")
        if reach > 0:
            out.append(_post_insight_value(p, "total_interactions") / reach * 100)
    return out


def rank_top_posts(posts: list[dict], n: int = TOP_N_POSTS) -> list[dict]:
    return sorted(posts, key=lambda p: _post_insight_value(p, "total_interactions"),
                  reverse=True)[:n]


def build_db_rows(identity, profile, token_meta, schema_metrics) -> dict:
    return {
        "social_accounts": {
            "platform_user_id": identity.get("id"),
            "handle": identity.get("username"),
            "profile_url": f"https://instagram.com/{identity.get('username')}",
            "scopes": token_meta.get("permissions", ""),
            "token_expires_at": token_meta.get("token_expires_at"),
        },
        "instagram_accounts": {
            "ig_user_id": identity.get("user_id") or identity.get("id"),
            "username": identity.get("username"),
            "name": identity.get("name"),
            "bio": profile.get("biography"),
            "profile_image_url": profile.get("profile_picture_url"),
            "account_type": profile.get("account_type"),
            "follower_count": profile.get("followers_count", 0),
            "follows_count": profile.get("follows_count", 0),
            "media_count": profile.get("media_count", 0),
        },
        "metrics_30d": {
            "er_by_followers_30d": schema_metrics["er_by_followers_30d"],
            "er_by_reach_30d": schema_metrics["er_by_reach_30d"],
            "er_per_post_30d": schema_metrics["er_per_post_30d"],
            "avg_likes_30d": schema_metrics["avg_likes_30d"],
            "total_reach_30d": schema_metrics["total_reach_30d"],
        },
    }


# ---------------------------------------------------------------------------
# 6. FORMATTING + PRESENTATION
# ---------------------------------------------------------------------------

def fmt_int(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 10_000:
        return f"{v / 1_000:.1f}K"
    return f"{int(v):,}"


def fmt_secs(s) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


ACCENT_GRADIENT = "linear-gradient(90deg, #f9ce34, #ee2a7b 55%, #6228d7)"

CARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&display=swap');

:root {
  --bg: #0d0e12; --panel: #15161d; --panel-2: #1b1d26;
  --border: #262834; --border-hi: #ee2a7b;
  --t1: #f2f3f7; --t2: #b6b8c6; --t3: #7c7e8e;
  --grad: linear-gradient(90deg, #f9ce34, #ee2a7b 55%, #6228d7);
  --radius: 16px;
}
html, body, [class*="stApp"] { font-family: 'Inter', system-ui, sans-serif; }
h1, h2, h3, .display { font-family: 'Space Grotesk', 'Inter', sans-serif; letter-spacing: -0.01em; }

.section-eyebrow { font-size: 11px; font-weight: 600; letter-spacing: .12em;
  text-transform: uppercase; color: var(--t3); margin: 4px 0 2px; }
.section-eyebrow::before { content: ""; display: inline-block; width: 22px; height: 3px;
  border-radius: 2px; background: var(--grad); margin-right: 8px; vertical-align: 2px; }

.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px; margin: 8px 0 20px; }
.kpi { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px 16px; min-height: 92px; }
.kpi .label { font-size: 11.5px; color: var(--t3); text-transform: uppercase;
  letter-spacing: .05em; margin-bottom: 6px; }
.kpi .value { font-family: 'Space Grotesk', sans-serif; font-size: 26px; font-weight: 700;
  color: var(--t1); line-height: 1.05; }
.kpi .sub { font-size: 11px; color: var(--t3); margin-top: 6px; line-height: 1.4; }
.kpi.hero { grid-column: span 2; background: var(--panel-2);
  border-image: var(--grad) 1; border-width: 1px 1px 3px 1px; border-style: solid; }
.kpi.hero .value { font-size: 34px; }

.split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 8px 0 20px; }
.split .col { background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px 18px; }
.split .col h4 { margin: 0 0 10px; font-family: 'Space Grotesk', sans-serif;
  font-size: 15px; color: var(--t1); }
.split .col h4 .tag { display: inline-block; font-size: 10px; font-weight: 700;
  letter-spacing: .08em; padding: 2px 8px; border-radius: 999px; color: #fff;
  background: var(--grad); margin-left: 8px; vertical-align: 2px; }
.split .row { display: flex; justify-content: space-between; font-size: 13px;
  color: var(--t2); padding: 5px 0; border-bottom: 1px dashed var(--border); }
.split .row:last-child { border-bottom: none; }
.split .row b { color: var(--t1); font-weight: 600; }
@media (max-width: 700px) { .split { grid-template-columns: 1fr; } }

.pct-block { background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 18px; margin: 8px 0 14px; }
.pct-block h4 { margin: 0 0 10px; font-family: 'Space Grotesk', sans-serif;
  font-size: 14px; color: var(--t1); }
.pct-row { display: flex; align-items: center; gap: 12px; padding: 5px 0; }
.pct-label { flex: 0 0 64px; font-size: 12.5px; color: var(--t2); }
.pct-track { flex: 1; height: 8px; background: var(--panel-2);
  border-radius: 999px; overflow: hidden; }
.pct-fill { display: block; height: 100%; border-radius: 999px;
  background: var(--grad); }
.pct-val { flex: 0 0 52px; text-align: right; font-size: 12.5px;
  color: var(--t1); font-weight: 600; }
.pct-sub { font-size: 11px; color: var(--t3); margin-top: 8px; }

.post-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
  gap: 14px; margin: 8px 0 22px; }
.post-card { position: relative; display: block; background: var(--panel);
  border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden;
  text-decoration: none; transition: transform .15s ease, border-color .15s ease; }
.post-card:hover, .post-card:focus-visible { transform: translateY(-3px);
  border-color: var(--border-hi); outline: none; }
@media (prefers-reduced-motion: reduce) { .post-card, .post-card:hover { transform: none; transition: none; } }
.post-rank { position: absolute; top: 10px; left: 10px; z-index: 2; color: #fff;
  font-weight: 700; font-size: 12px; padding: 3px 10px; border-radius: 999px;
  background: var(--grad); }
.post-media { position: relative; width: 100%; aspect-ratio: 4 / 5; background: #0a0b0f; }
.post-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.post-thumb-empty { width: 100%; height: 100%; display: flex; align-items: center;
  justify-content: center; font-size: 40px; }
.post-type-badge { position: absolute; bottom: 8px; right: 8px; background: rgba(0,0,0,.65);
  color: #fff; font-size: 11px; padding: 2px 9px; border-radius: 999px; }
.post-body { padding: 12px 14px 14px; }
.post-caption { font-size: 13px; color: var(--t2); line-height: 1.45; min-height: 36px; margin: 0 0 10px; }
.post-stats { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12.5px;
  color: var(--t2); margin-bottom: 8px; }
.post-chip { background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 999px; padding: 1px 9px; font-size: 11px; color: var(--t2); }
.post-footer { display: flex; justify-content: space-between; font-size: 11.5px;
  color: var(--t3); border-top: 1px solid var(--border); padding-top: 8px; }
</style>
"""

_MEDIA_LABELS = {"REELS": "Reel", "VIDEO": "Video", "CAROUSEL_ALBUM": "Carousel", "IMAGE": "Post"}


def _compact_html(s: str) -> str:
    return "".join(line.strip() for line in s.splitlines() if line.strip())


_FORMAT_LABELS = {"REELS": "Reels", "FEED": "Posts", "STORY": "Stories", "AD": "Ads"}


def render_pct_block(title: str, by: dict, sub: str = "") -> str:
    total = sum(v for v in by.values() if v)
    if not total:
        return ""
    rows = []
    order = ["REELS", "FEED", "STORY", "AD"] + [k for k in by if k not in _FORMAT_LABELS]
    for key in order:
        v = by.get(key)
        if not v:
            continue
        pct = v / total * 100
        rows.append(
            f'<div class="pct-row">'
            f'<span class="pct-label">{html.escape(_FORMAT_LABELS.get(key, key.replace("_", " ").title()))}</span>'
            f'<span class="pct-track"><span class="pct-fill" style="width:{pct:.1f}%"></span></span>'
            f'<span class="pct-val">{pct:.1f}%</span></div>')
    sub_html = f'<div class="pct-sub">{html.escape(sub)}</div>' if sub else ""
    return (f'<div class="pct-block"><h4>{html.escape(title)}</h4>'
            f'{"".join(rows)}{sub_html}</div>')


def follower_split_line(metric_label: str, entry: dict) -> str | None:
    by = (entry or {}).get("by") or {}
    total = sum(v for v in by.values() if v)
    if not total:
        return None
    parts = " · ".join(
        f"{k.replace('_', '-').title()} {v / total * 100:.1f}%"
        for k, v in sorted(by.items(), key=lambda kv: -kv[1]))
    return f"{metric_label} — {parts}"


def render_kpi(label: str, value: str, sub: str = "", hero: bool = False) -> str:
    cls = "kpi hero" if hero else "kpi"
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    return (f'<div class="{cls}"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div>{sub_html}</div>')


def render_split_row(label: str, left: str, right: str) -> tuple[str, str]:
    return (f'<div class="row"><span>{html.escape(label)}</span><b>{html.escape(left)}</b></div>',
            f'<div class="row"><span>{html.escape(label)}</span><b>{html.escape(right)}</b></div>')


def render_post_card(post: dict, rank: int, extras: dict) -> str:
    thumb = post.get("thumbnail_url") or post.get("media_url")
    thumb_html = (f'<img src="{html.escape(thumb, quote=True)}" class="post-thumb" alt="" />'
                  if thumb else '<div class="post-thumb post-thumb-empty">🎬</div>')
    caption = html.escape((post.get("caption") or "").strip())
    if len(caption) > 110:
        caption = caption[:110].rsplit(" ", 1)[0] + "…"
    media_label = _MEDIA_LABELS.get(post.get("media_product_type")
                                     or post.get("media_type"), "Post")
    likes = post.get("like_count", 0) or 0
    comments = post.get("comments_count", 0) or 0
    views = _post_insight_value(post, "views")
    reach = _post_insight_value(post, "reach")
    interactions = _post_insight_value(post, "total_interactions")
    permalink = html.escape(post.get("permalink", "#"), quote=True)
    date_str = (post.get("timestamp") or "")[:10]

    chips = ""
    ex = extras.get(post.get("id", ""), {})
    if "ig_reels_avg_watch_time" in ex:
        chips += f'<span class="post-chip">⏱ {fmt_secs(ex["ig_reels_avg_watch_time"] / 1000)} avg watch</span>'
    if "reels_skip_rate" in ex:
        chips += f'<span class="post-chip">🪝 {round(100 - float(ex["reels_skip_rate"]))}% held 3s</span>'
    if ex.get("follows"):
        chips += f'<span class="post-chip">➕ {fmt_int(ex["follows"])} follows</span>'
    if ex.get("profile_visits"):
        chips += f'<span class="post-chip">👤 {fmt_int(ex["profile_visits"])} profile visits</span>'
    if ex.get("profile_activity"):
        chips += f'<span class="post-chip">🔗 {fmt_int(ex["profile_activity"])} profile actions</span>'

    return _compact_html(f"""
    <a href="{permalink}" target="_blank" rel="noopener" class="post-card">
      <div class="post-rank">#{rank}</div>
      <div class="post-media">{thumb_html}<span class="post-type-badge">{media_label}</span></div>
      <div class="post-body">
        <p class="post-caption">{caption or '<em>No caption</em>'}</p>
        <div class="post-stats"><span>❤️ {fmt_int(likes)}</span><span>💬 {fmt_int(comments)}</span>
          <span>▶️ {fmt_int(views)}</span><span>👁️ {fmt_int(reach)}</span></div>
        <div class="post-stats">{chips}</div>
        <div class="post-footer"><span>{date_str}</span><span>{fmt_int(interactions)} interactions</span></div>
      </div>
    </a>
    """)


def _chart_base(df: pd.DataFrame):
    return alt.Chart(df).properties(height=220, background="transparent")


def area_chart(rows: list[dict], value_label: str):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return (_chart_base(df)
            .mark_area(line={"color": "#ee2a7b"},
                       color=alt.Gradient(
                           gradient="linear",
                           stops=[alt.GradientStop(color="#ee2a7b", offset=0),
                                  alt.GradientStop(color="#15161d", offset=1)],
                           x1=1, x2=1, y1=0, y2=1))
            .encode(x=alt.X("date:T", title=None),
                    y=alt.Y("value:Q", title=value_label),
                    tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", title=value_label)]))


def bar_chart(rows: list[dict], x_field: str, x_title: str, value_label: str,
              sort=None, horizontal: bool = False):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    base = _chart_base(df).mark_bar(color="#ee2a7b", cornerRadiusEnd=3)
    if horizontal:
        return base.encode(
            y=alt.Y(f"{x_field}:N", title=None, sort=sort or "-x"),
            x=alt.X("value:Q", title=value_label),
            tooltip=[x_field, "value"])
    return base.encode(
        x=alt.X(f"{x_field}:O", title=x_title, sort=sort),
        y=alt.Y("value:Q", title=value_label),
        tooltip=[x_field, "value"])


def _render_call_detail(entry: dict) -> None:
    """Shared by the login step-through and can be reused anywhere else a
    single logged call needs showing. Pulls straight from an entry produced
    by _log_api_call (section 2) — same masking, same fields, no re-derivation."""
    st.caption(f"{entry['method']} · {entry['endpoint']} · "
               f"HTTP {entry['status_code']} · {entry['elapsed_ms']} ms · {entry['ts']}")
    st.text_input("Request URL", entry["url"], disabled=True, key=f"oauth_url_{entry['seq']}")
    if entry["request_body"]:
        st.text_area("Request body", entry["request_body"], disabled=True,
                     height=80, key=f"oauth_body_{entry['seq']}")
    st.markdown("**Response body — exactly as Meta sent it (secrets masked)**")
    try:
        st.json(json.loads(entry["response_text"]))
    except (ValueError, TypeError):
        st.code(entry["response_text"] or "(empty body)")


def _latest_log_entry() -> dict | None:
    log = st.session_state.get("api_call_log", [])
    return log[-1] if log else None


def _reset_oauth_state() -> None:
    for k in ("oauth_step", "oauth_code", "oauth_short_result",
              "oauth_long_result", "oauth_state"):
        st.session_state.pop(k, None)


# ---------------------------------------------------------------------------
# 7. STREAMLIT APP
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Instagram Business Insights", page_icon="📊", layout="wide")
st.markdown(CARD_CSS, unsafe_allow_html=True)

missing = [n for n, v in [("INSTA_APP_ID", INSTA_APP_ID),
                           ("INSTA_APP_SECRET", INSTA_APP_SECRET),
                           ("INSTA_REDIRECT_URI", REDIRECT_URI)] if not v]
if missing:
    st.error(f"Missing required environment variable(s): {', '.join(missing)}. "
             f"Set them in Settings -> Secrets, then reload.")
    st.stop()

_redirect_path = urlparse(REDIRECT_URI).path
if _redirect_path not in ("", "/"):
    st.warning(
        f"Your redirect URI has a path (`{_redirect_path}`). Streamlit only serves "
        f"the app at its root URL, so Instagram's redirect will land on a 404 and "
        f"the login code will be lost — unless you've created a Streamlit page "
        f"matching that path. Recommended: register and use the root URL."
    )

# --- OAuth gate — hard-blocking two-step exchange, one Next click each ------
if "access_token" not in st.session_state:
    st.title("📊 Instagram Business Insights")
    st.caption(f"Redirect URI in use: `{REDIRECT_URI}` — must match the Meta App "
               f"Dashboard registration character for character.")

    st.session_state.setdefault("oauth_step", "await_code")

    # --- await_code: no API call yet, just the login link or a fresh redirect ---
    if st.session_state.oauth_step == "await_code":
        code = st.query_params.get("code")
        returned_state = st.query_params.get("state")

        # Canceled/denied authorization: Instagram redirects with error /
        # error_reason / error_description instead of code (doc: "Canceled
        # authorization" — "it is your responsibility to fail gracefully").
        # Previously unhandled: an absent `code` was treated identically to
        # "login not yet started", so a denial silently re-showed the login
        # button with no explanation.
        oauth_error = st.query_params.get("error")
        if oauth_error:
            error_reason = st.query_params.get("error_reason", oauth_error)
            error_description = (st.query_params.get("error_description", "")
                                 .replace("+", " ")) or "No further detail was given."
            st.query_params.clear()
            st.error(f"Instagram login was not completed — {error_reason}: "
                     f"{error_description}")
            st.session_state.oauth_state = pysecrets.token_urlsafe(16)
            st.link_button("Try logging in again",
                           build_authorize_url(st.session_state.oauth_state),
                           use_container_width=True)
            st.stop()

        if not code:
            st.session_state.oauth_state = pysecrets.token_urlsafe(16)
            st.info("Connect an Instagram professional account to see its insights — "
                    "7 / 30 / 90-day windows, account totals, Reels and Feed "
                    "separated, audience data, and best posting hours.")
            st.link_button("Log in with Instagram",
                           build_authorize_url(st.session_state.oauth_state),
                           use_container_width=True)
            st.stop()

        expected_state = st.session_state.get("oauth_state")
        if returned_state and expected_state and returned_state != expected_state:
            st.query_params.clear()
            st.error("Login state mismatch (possible CSRF or a stale login tab). "
                     "Start the login again.")
            if st.button("Restart login"):
                _reset_oauth_state()
                st.rerun()
            st.stop()

        # Stash the one-time code before clearing the URL — it won't survive
        # the reruns the Next buttons below trigger, and it can only be used once.
        st.session_state.oauth_code = code
        st.query_params.clear()
        st.session_state.oauth_step = "run_step1"
        st.rerun()

    # --- step 1 of 2: authorisation code -> short-lived token (~1 hour) --------
    if st.session_state.oauth_step == "run_step1":
        st.markdown("### Step 1 of 2 — exchange the authorisation code for a short-lived token")
        with st.spinner("Calling api.instagram.com/oauth/access_token…"):
            short = exchange_code_for_short_token(st.session_state.oauth_code)
        st.session_state.oauth_short_result = short
        entry = _latest_log_entry()
        if entry:
            _render_call_detail(entry)

        if "access_token" not in short:
            st.error(f"Token exchange failed: {short}")
            if st.button("Restart login", key="restart_step1"):
                _reset_oauth_state()
                st.rerun()
            st.stop()

        st.success("Short-lived token received (valid ~1 hour).")
        if st.button("Next → exchange for the long-lived token",
                     use_container_width=True, key="next_step2"):
            st.session_state.oauth_step = "run_step2"
            st.rerun()
        st.stop()

    # --- step 2 of 2: short-lived token -> long-lived token (~60 days) ---------
    if st.session_state.oauth_step == "run_step2":
        st.markdown("### Step 2 of 2 — exchange the short-lived token for a long-lived token")
        short_token = st.session_state.oauth_short_result["access_token"]
        with st.spinner("Calling graph.instagram.com/access_token…"):
            long = exchange_for_long_lived_token(short_token)
        st.session_state.oauth_long_result = long
        entry = _latest_log_entry()
        if entry:
            _render_call_detail(entry)

        if "access_token" not in long:
            st.error(f"Long-lived token exchange failed: {long}")
            if st.button("Restart login", key="restart_step2"):
                _reset_oauth_state()
                st.rerun()
            st.stop()

        expires_in = long.get("expires_in", 0)
        st.success(f"Long-lived token received — expires in ~{round(expires_in / 86400)} days. "
                   f"This is the only token the rest of the app uses; nothing further is "
                   f"exchanged automatically (refresh_long_lived_token exists in section 3 "
                   f"for when you persist tokens, but login itself needed only these two calls).")
        if st.button("Next → finish login and load the dashboard",
                     use_container_width=True, key="finish_login"):
            st.session_state.access_token = long["access_token"]
            st.session_state.token_meta = {
                "permissions": st.session_state.oauth_short_result.get("permissions", ""),
                "token_expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                ).isoformat(),
            }
            _reset_oauth_state()
            st.rerun()
        st.stop()

# --- Data load --------------------------------------------------------------
token = st.session_state.access_token
token_meta = st.session_state.token_meta
st.session_state.setdefault("api_errors", [])

# --- Window selector: 7 / 30 / 90 days --------------------------------------
_window_opts = [7, 30, 90]
if hasattr(st, "segmented_control"):
    _picked = st.segmented_control("Insights window (days)", _window_opts,
                                   default=WINDOW_DAYS)
else:
    _picked = st.radio("Insights window (days)", _window_opts,
                       index=_window_opts.index(WINDOW_DAYS), horizontal=True)
window_days = _picked or WINDOW_DAYS

if st.session_state.get("win_align") not in WINDOW_ALIGN_MODES:
    st.session_state.pop("win_align", None)
with st.expander("Window alignment — for matching the native app's date range"):
    _align_label = st.selectbox("Day boundary mode", list(WINDOW_ALIGN_MODES),
                                index=0, key="win_align")
    win_tz_h = st.number_input("Day boundary timezone (hours vs UTC)", value=0.0,
                               step=0.5, min_value=-12.0, max_value=14.0,
                               key="win_tz",
                               help="Only moves where midnight falls for the day "
                                    "boundary; API timestamps stay correct either "
                                    "way. IST = 5.5")
    st.caption("Verified for this account: 'Last N complete days' at UTC 0 matched "
               "the app's Views exactly, which pins the app's window convention — "
               "hence it's the default. Additive metrics (views, likes, "
               "interactions) move almost linearly with window width; the queried "
               "range is shown in the summary line below.")
win_align = WINDOW_ALIGN_MODES[_align_label]

with st.spinner("Loading profile and account insights…"):
    identity = fetch_identity(token)
    ig_user_id = identity.get("user_id") or identity.get("id")
    if not ig_user_id:
        st.error("Could not resolve your Instagram user id — token may be "
                 "expired or revoked. Disconnect and log in again.")
        if st.button("Disconnect"):
            st.session_state.clear()
            st.rerun()
        st.stop()
    profile = fetch_profile(token, ig_user_id)
    followers = profile.get("followers_count", 0) or 0
    posts = fetch_media_window(token, ig_user_id, window_days, win_align, win_tz_h)
    fmt_totals = fetch_account_totals_by_format(token, ig_user_id, window_days,
                                                win_align, win_tz_h)
    plain_totals = fetch_account_totals_plain(token, ig_user_id, window_days,
                                              win_align, win_tz_h)
    fu_totals = fetch_follows_unfollows(token, ig_user_id, window_days,
                                        win_align, win_tz_h)
    split_totals = fetch_follower_split(token, ig_user_id, window_days,
                                        win_align, win_tz_h)
    plr_totals = fetch_profile_links_taps_by_button(token, ig_user_id, window_days,
                                                    win_align, win_tz_h)
    reach_plain = fetch_reach_plain(token, ig_user_id, window_days,
                                    win_align, win_tz_h)
    reach_series = fetch_timeseries(token, ig_user_id, "reach", window_days,
                                    win_align, win_tz_h)
    follower_series = fetch_timeseries(token, ig_user_id, "follower_count",
                                       window_days, win_align, win_tz_h)

extras: dict[str, dict] = {}
enrich = posts[:MAX_ENRICHED_MEDIA]
if enrich:
    prog = st.progress(0.0, text="Loading per-post insights (watch time, follows, profile activity)…")
    for i, p in enumerate(enrich):
        extras[p["id"]] = fetch_media_extras(
            token, p["id"], p.get("media_product_type") or "")
        prog.progress((i + 1) / len(enrich))
    prog.empty()
if len(posts) > MAX_ENRICHED_MEDIA:
    st.caption(f"Watch-time / follows details loaded for the {MAX_ENRICHED_MEDIA} "
               f"newest of {len(posts)} posts to keep load time sane; core metrics "
               f"cover all posts.")

reels, feed = split_by_format(posts)
reels_stats = group_stats(reels, followers, extras)
feed_stats = group_stats(feed, followers, extras)
schema_metrics = compute_schema_metrics(posts, followers, fmt_totals)
industry_er = compute_industry_engagement_rate(posts, followers)
category_inferred = infer_categories(profile, identity, posts)
_series_reach_sum = sum(r["value"] for r in reach_series) if reach_series else None


def total_of(name: str, source: dict) -> int:
    return (source.get(name) or {}).get("total", 0)


def by_format(name: str, fmt: str) -> int:
    return ((fmt_totals.get(name) or {}).get("by", {}) or {}).get(fmt, 0)


# --- Header -----------------------------------------------------------------
col_img, col_info, col_actions = st.columns([1, 5, 2])
with col_img:
    pic = profile.get("profile_picture_url")
    if pic:
        st.image(pic, width=110)
with col_info:
    st.markdown(f"## {identity.get('name') or ''} "
                f"<span style='color:var(--t3);font-size:0.6em'>@{identity.get('username','')}</span>",
                unsafe_allow_html=True)
    st.caption(f"{profile.get('account_type', '—')} · "
               f"{fmt_int(followers)} followers · "
               f"{fmt_int(profile.get('follows_count', 0))} following · "
               f"{fmt_int(profile.get('media_count', 0))} posts")
    if profile.get("biography"):
        st.caption(profile["biography"])
    if profile.get("website"):
        st.caption(f"🔗 {profile['website']}")
    if category_inferred:
        _cats = " · ".join(f"{c['category']} {c['share_pct']}%" for c in category_inferred)
        _sig = ", ".join(category_inferred[0]["evidence"])
        st.caption(f"Inferred niche (keyword heuristic — Instagram's API exposes no "
                   f"category field): {_cats} — top signals: {_sig}")
with col_actions:
    if st.button("↻ Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.session_state.api_errors = []
        st.rerun()
    if st.button("Disconnect", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.markdown(f'<div class="section-eyebrow">Last {window_days} days · '
            f'{len(posts)} posts ({len(reels)} reels, {len(feed)} feed) · '
            f'window queried {window_bounds_label(window_days, win_align, win_tz_h)}</div>',
            unsafe_allow_html=True)

_media_errs = [e for e in st.session_state.get("api_errors", [])
               if str(e.get("context", "")).startswith("media list")]
if not posts and _media_errs:
    _first = _media_errs[0].get("error", {})
    st.error("Your posts list could not be loaded, so every post-based metric "
             "(all ER formulas, avg likes, Reels/Feed tabs) is empty for that "
             f"reason — not because you didn't post. Meta's error: "
             f"{_first.get('message', _first)}")
elif not posts:
    st.caption("No posts were published in this window, so the post-based metrics "
               "(ER formulas, avg likes) are zero by definition. The account totals "
               "still move because older posts, reels, and stories keep earning "
               "views, reach, and interactions after publication.")

tab_overview, tab_reels, tab_feed, tab_audience, tab_data, tab_sequence = st.tabs(
    ["Overview", "Reels", "Feed posts", "Audience", "Data", "Sequential execution"])

# --- OVERVIEW ---------------------------------------------------------------
with tab_overview:
    fu_by = (fu_totals.get("follows_and_unfollows") or {}).get("by", {})
    fu_total = total_of("follows_and_unfollows", fu_totals)
    new_follows_gross = sum(r["value"] for r in follower_series) if follower_series else None

    er_median_all = _median(per_post_er_list(posts))
    kpis = [
        render_kpi("Engagement rate (median / post)",
                   f"{er_median_all}%" if posts else "—",
                   "each post's interactions ÷ its own reach — median, so one viral post can't skew it",
                   hero=True),
        render_kpi("Views", fmt_int(total_of("views", fmt_totals)),
                   "account total, all formats (Meta 'in development')"),
        render_kpi("Reach", fmt_int(total_of("reach", fmt_totals)),
                   "Meta's window total, estimated — see reach cross-check below"),
        render_kpi("Accounts engaged", fmt_int(total_of("accounts_engaged", plain_totals)),
                   "unique accounts that interacted (estimated)"),
        render_kpi("Interactions", fmt_int(total_of("total_interactions", fmt_totals)),
                   "account total incl. boosted content"),
        render_kpi("Profile link taps", fmt_int(total_of("profile_links_taps", plain_totals)),
                   "address / call / email / text taps"),
    ]
    if new_follows_gross is not None:
        kpis.append(render_kpi("New followers (gross)", fmt_int(new_follows_gross),
                               "sum of daily follower_count values"))
    if fu_total or fu_by:
        breakdown_txt = " · ".join(f"{k.title()}: {fmt_int(v)}" for k, v in fu_by.items())
        if fu_total:
            fu_display, fu_sub = fmt_int(fu_total), breakdown_txt or "as reported by Meta"
        else:
            fu_display = "—"
            fu_sub = f"{breakdown_txt} (Meta returned no combined total; " \
                     f"breakdown semantics undocumented)"
        kpis.append(render_kpi("Follows & unfollows", fu_display, fu_sub))
    st.markdown(f'<div class="kpi-grid">{"".join(kpis)}</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-eyebrow">Engagement rates — the five metrics '
                'your schema stores</div>', unsafe_allow_html=True)
    v1_cells = "".join([
        render_kpi("Engagement rate", f"{industry_er}%",
                   "avg likes+comments ÷ followers — matches most third-party tools"),
        render_kpi("ER by followers (cumulative)", f"{schema_metrics['er_by_followers_30d']}%",
                   "all engagement in the window ÷ followers — scales with posting frequency"),
        render_kpi("ER by reach (cumulative)", f"{schema_metrics['er_by_reach_30d']}%",
                   "all engagement ÷ Meta's account reach total for the window"),
        render_kpi("ER per post (mean)", f"{schema_metrics['er_per_post_30d']}%",
                   "each post's engagement ÷ its own reach, then averaged"),
        render_kpi("Avg likes / post", f"{schema_metrics['avg_likes_30d']}"),
        render_kpi(f"Total reach ({window_days}d)", fmt_int(schema_metrics['total_reach_30d']),
                   "Meta's window total — see reach cross-check below"),
    ])
    st.markdown(f'<div class="kpi-grid">{v1_cells}</div>', unsafe_allow_html=True)
    st.caption("Four different engagement-rate numbers on purpose — they answer different "
               "questions and won't match each other or every other tool. See the labels."
               + ("" if window_days == 30 else
                  f" Note: computed over your selected {window_days}-day window even "
                  f"though the schema columns are named _30d."))

    _reach_variants = {
        "with content-type breakdown (headline)": total_of("reach", fmt_totals),
        "plain, no breakdown": total_of("reach", reach_plain),
        "with follower-type breakdown": (split_totals.get("reach") or {}).get("total", 0),
        "sum of daily series": _series_reach_sum,
    }
    _variant_txt = " · ".join(
        f"{k}: {fmt_int(v) if v is not None else '—'}"
        for k, v in _reach_variants.items())
    st.caption(
        "Reach variants, all for the exact window above — " + _variant_txt + ". "
        "Compare each against the app's Viewers number: views matching exactly "
        "proves the window is identical, so any residual gap here is measurement "
        "method, not dates. The API has no 'viewers' metric — reach is the closest "
        "analog and Meta documents it as estimated. The daily-series sum "
        "double-counts people seen on multiple days; a breakdown-sum fallback "
        "double-counts across surfaces (per-variant sources are in Data → Window "
        f"debug). Views total_value: {fmt_int(total_of('views', fmt_totals))}."
    )

    _plt_by = (plr_totals.get("profile_links_taps") or {}).get("by", {})
    if _plt_by:
        st.markdown(render_pct_block("Profile link taps by button", _plt_by),
                    unsafe_allow_html=True)

    st.markdown('<div class="section-eyebrow">By content type — like the native '
                'Account insights</div>', unsafe_allow_html=True)
    _views_by = (fmt_totals.get("views") or {}).get("by", {})
    _inter_by = (fmt_totals.get("total_interactions") or {}).get("by", {})
    _bars = (render_pct_block("Views by content type", _views_by,
                              "Stories appear here from the account-level breakdown; "
                              "story-by-story history isn't retrievable (API keeps "
                              "stories only while live, 24h).")
             + render_pct_block("Interactions by content type", _inter_by))
    if _bars:
        st.markdown(_bars, unsafe_allow_html=True)
    else:
        st.info("Meta returned no content-type breakdown for this window.")

    _split_lines = [ln for ln in (
        follower_split_line("Views", split_totals.get("views")),
        follower_split_line("Viewers (reach)", split_totals.get("reach")),
        follower_split_line("Interactions", split_totals.get("total_interactions")),
    ) if ln]
    if _split_lines:
        st.caption("Followers vs non-followers · " + "   |   ".join(_split_lines))
    else:
        st.caption("Followers vs non-followers split: not returned by Meta for this "
                   "account/window — details in Data → API warnings.")

    st.markdown('<div class="section-eyebrow">Reels vs Feed — Meta\'s account-level split</div>',
                unsafe_allow_html=True)
    rows_l, rows_r = [], []
    for label, metric in [("Views", "views"), ("Reach", "reach"),
                           ("Interactions", "total_interactions"),
                           ("Likes", "likes"), ("Comments", "comments"),
                           ("Saves", "saves"), ("Shares", "shares")]:
        l, r = render_split_row(label, fmt_int(by_format(metric, "REELS")),
                                 fmt_int(by_format(metric, "FEED")))
        rows_l.append(l)
        rows_r.append(r)
    st.markdown(
        _compact_html(f'''<div class="split">
              <div class="col"><h4>Reels<span class="tag">{len(reels)} posted</span></h4>{''.join(rows_l)}</div>
              <div class="col"><h4>Feed<span class="tag">{len(feed)} posted</span></h4>{''.join(rows_r)}</div>
            </div>'''),
        unsafe_allow_html=True)
    st.caption("Source: account insights with breakdown=media_product_type — includes STORY/AD "
               "surfaces in the totals above, so the two columns won't sum to the account total.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="section-eyebrow">Daily reach</div>', unsafe_allow_html=True)
        ch = area_chart(reach_series, "reach")
        if ch is not None:
            st.altair_chart(ch, use_container_width=True)
        else:
            st.info("No daily reach series returned.")
    with c2:
        st.markdown('<div class="section-eyebrow">New followers / day</div>', unsafe_allow_html=True)
        ch = bar_chart(follower_series, "date", None, "new followers")
        if ch is not None:
            st.altair_chart(ch, use_container_width=True)
        else:
            st.info("follower_count series unavailable (requires ≥100 followers).")

    st.markdown('<div class="section-eyebrow">Top content</div>', unsafe_allow_html=True)
    if posts:
        _metric_opts = {
            "Views": ("ins", "views"),
            "Viewers (reach)": ("ins", "reach"),
            "Post interactions": ("ins", "total_interactions"),
            "Likes": ("field", "like_count"),
            "Comments": ("field", "comments_count"),
            "Saves": ("ins", "saved"),
            "Shares": ("ins", "shares"),
            "Follows (feed only)": ("extra", "follows"),
            "Profile visits (feed only)": ("extra", "profile_visits"),
            "Profile activity (feed only)": ("extra", "profile_activity"),
        }
        tc1, tc2, tc3, tc4 = st.columns([2, 1, 1, 1])
        sel_metric = tc1.selectbox("Rank by", list(_metric_opts), key="top_metric")
        sel_order = tc2.selectbox("Order", ["Highest", "Lowest", "Newest"], key="top_order")
        sel_type = tc3.selectbox("Type", ["All", "Reels", "Posts"], key="top_type")
        sel_n = tc4.selectbox("Show", [3, 5, 10, "All"], index=0, key="top_n")

        pool = {"All": posts, "Reels": reels, "Posts": feed}[sel_type]

        def _rank_value(p: dict) -> float:
            kind, key = _metric_opts[sel_metric]
            if kind == "ins":
                return _post_insight_value(p, key)
            if kind == "field":
                return p.get(key, 0) or 0
            return extras.get(p.get("id", ""), {}).get(key, 0) or 0

        if sel_order == "Newest":
            ranked = sorted(pool, key=lambda p: p.get("timestamp", ""), reverse=True)
        else:
            ranked = sorted(pool, key=_rank_value, reverse=(sel_order == "Highest"))
        if sel_n != "All":
            ranked = ranked[:int(sel_n)]

        if ranked:
            st.markdown(f'<div class="post-grid">{"".join(render_post_card(p, i + 1, extras) for i, p in enumerate(ranked))}</div>',
                        unsafe_allow_html=True)
        else:
            st.info("Nothing matches this filter in the window.")
        st.caption("Impressions isn't offered — Meta removed it from the API even "
                   "though the native app still shows it. Follows and profile visits "
                   "exist only on feed posts, so reels rank at 0 on those.")
    else:
        st.info("No posts with insights in this window yet.")

# --- REELS ------------------------------------------------------------------
with tab_reels:
    if not reels:
        st.info("No reels published in this window. Post a reel and refresh.")
    else:
        rk = [
            render_kpi("Reels ER (median)", f"{reels_stats['er_reach_median']}%",
                       "interactions ÷ reach per reel, median", hero=True),
            render_kpi("Views", fmt_int(reels_stats["views"]), "sum across reels"),
            render_kpi("Views / reach", f"{reels_stats['views_per_reach']}",
                       ">1 means rewatching"),
        ]
        if "avg_watch_s_median" in reels_stats:
            rk.append(render_kpi("Avg watch time (median)",
                                 fmt_secs(reels_stats["avg_watch_s_median"]),
                                 "per reel; API reports ms — converted"))
        if "hook_rate_median" in reels_stats:
            rk.append(render_kpi("Hook rate (median)", f"{reels_stats['hook_rate_median']}%",
                                 "viewers who did NOT skip in the first 3s (100 − skip rate; Meta: estimated)"))
        if "total_watch_s" in reels_stats:
            rk.append(render_kpi("Total watch time", fmt_secs(reels_stats["total_watch_s"]),
                                 "all reels in window"))
        rk += [
            render_kpi("Save rate", f"{reels_stats['save_rate']}%", "saves ÷ reach"),
            render_kpi("Share rate", f"{reels_stats['share_rate']}%", "shares ÷ reach"),
            render_kpi("Reach rate (median)", f"{reels_stats['reach_rate_median']}%",
                       "typical reel's reach ÷ followers"),
        ]
        st.markdown(f'<div class="kpi-grid">{"".join(rk)}</div>', unsafe_allow_html=True)

        st.markdown('<div class="section-eyebrow">Top reels</div>', unsafe_allow_html=True)
        top_r = rank_top_posts(reels)
        st.markdown(f'<div class="post-grid">{"".join(render_post_card(p, i + 1, extras) for i, p in enumerate(top_r))}</div>',
                    unsafe_allow_html=True)

        with st.expander("Every reel in the window"):
            rows = []
            for p in reels:
                ex = extras.get(p["id"], {})
                reach = _post_insight_value(p, "reach")
                inter = _post_insight_value(p, "total_interactions")
                rows.append({
                    "date": (p.get("timestamp") or "")[:10],
                    "caption": (p.get("caption") or "")[:60],
                    "views": _post_insight_value(p, "views"),
                    "reach": reach,
                    "interactions": inter,
                    "ER %": round(inter / reach * 100, 2) if reach else 0.0,
                    "avg watch (s)": round(ex.get("ig_reels_avg_watch_time", 0) / 1000, 1)
                        if "ig_reels_avg_watch_time" in ex else None,
                    "held past 3s %": round(100 - float(ex["reels_skip_rate"]), 1)
                        if "reels_skip_rate" in ex else None,
                    "saves": _post_insight_value(p, "saved"),
                    "shares": _post_insight_value(p, "shares"),
                    "link": p.get("permalink"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         column_config={"link": st.column_config.LinkColumn("link")})

# --- FEED -------------------------------------------------------------------
with tab_feed:
    if not feed:
        st.info("No feed posts (images, carousels, feed videos) in this window.")
    else:
        fk = [
            render_kpi("Feed ER (median)", f"{feed_stats['er_reach_median']}%",
                       "interactions ÷ reach per post, median", hero=True),
            render_kpi("Views", fmt_int(feed_stats["views"]), "sum across posts"),
            render_kpi("Reach rate (median)", f"{feed_stats['reach_rate_median']}%",
                       "typical post's reach ÷ followers"),
            render_kpi("Save rate", f"{feed_stats['save_rate']}%", "saves ÷ reach"),
            render_kpi("Share rate", f"{feed_stats['share_rate']}%", "shares ÷ reach"),
        ]
        if "follows_from_posts" in feed_stats:
            fk.append(render_kpi("Follows from posts", fmt_int(feed_stats["follows_from_posts"]),
                                 f"{feed_stats['follow_conversion']}% of reached accounts followed"))
        if "profile_visits_from_posts" in feed_stats:
            fk.append(render_kpi("Profile visits from posts",
                                 fmt_int(feed_stats["profile_visits_from_posts"]),
                                 "visits driven by feed posts"))
        if "profile_activity_from_posts" in feed_stats:
            fk.append(render_kpi("Profile actions from posts",
                                 fmt_int(feed_stats["profile_activity_from_posts"]),
                                 "bio-link taps, calls, emails, directions, texts after "
                                 "visiting from a post"))
        st.markdown(f'<div class="kpi-grid">{"".join(fk)}</div>', unsafe_allow_html=True)

        if feed_stats.get("profile_activity_by_action"):
            st.markdown(render_pct_block("Profile actions by type",
                                         feed_stats["profile_activity_by_action"]),
                        unsafe_allow_html=True)

        st.markdown('<div class="section-eyebrow">Top feed posts</div>', unsafe_allow_html=True)
        top_f = rank_top_posts(feed)
        st.markdown(f'<div class="post-grid">{"".join(render_post_card(p, i + 1, extras) for i, p in enumerate(top_f))}</div>',
                    unsafe_allow_html=True)

        with st.expander("Every feed post in the window"):
            rows = []
            for p in feed:
                ex = extras.get(p["id"], {})
                reach = _post_insight_value(p, "reach")
                inter = _post_insight_value(p, "total_interactions")
                rows.append({
                    "date": (p.get("timestamp") or "")[:10],
                    "type": _MEDIA_LABELS.get(p.get("media_type"), p.get("media_type")),
                    "caption": (p.get("caption") or "")[:60],
                    "views": _post_insight_value(p, "views"),
                    "reach": reach,
                    "interactions": inter,
                    "ER %": round(inter / reach * 100, 2) if reach else 0.0,
                    "saves": _post_insight_value(p, "saved"),
                    "follows": ex.get("follows"),
                    "profile visits": ex.get("profile_visits"),
                    "profile activity": ex.get("profile_activity"),
                    "link": p.get("permalink"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True,
                         column_config={"link": st.column_config.LinkColumn("link")})

# --- AUDIENCE ---------------------------------------------------------------
with tab_audience:
    st.markdown('<div class="section-eyebrow">Most active times — when your '
                'followers are online</div>', unsafe_allow_html=True)
    online_raw = fetch_online_followers_raw(token, ig_user_id)
    if online_raw:
        _day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        ac1, ac2 = st.columns([3, 1])
        with ac1:
            if hasattr(st, "segmented_control"):
                day_pick = st.segmented_control("Day", ["All"] + _day_names,
                                                default="All", key="online_day")
            else:
                day_pick = st.radio("Day", ["All"] + _day_names, index=0,
                                    horizontal=True, key="online_day")
            day_pick = day_pick or "All"
        with ac2:
            tz_shift = st.number_input("Shift vs UTC (h)", value=0.0, step=0.5,
                                       min_value=-12.0, max_value=14.0,
                                       help="Meta reports hours in UTC. IST = 5.5",
                                       key="online_tz")

        _buckets: dict[int, list[float]] = {}
        for date_str, hour_map in online_raw:
            try:
                wd = datetime.strptime(date_str, "%Y-%m-%d").weekday()
            except ValueError:
                continue
            if day_pick != "All" and _day_names[wd] != day_pick:
                continue
            for h, c in hour_map.items():
                try:
                    _buckets.setdefault(int(h), []).append(float(c))
                except (TypeError, ValueError):
                    continue

        if _buckets:
            _rows, _order = [], []
            for h in range(24):
                vs = _buckets.get(h)
                if not vs:
                    continue
                local = (h + tz_shift) % 24
                label = f"{int(local):02d}:{'30' if local % 1 else '00'}"
                _rows.append({"sort": local, "hour": label,
                              "value": round(sum(vs) / len(vs), 1)})
            _rows.sort(key=lambda r: r["sort"])
            _order = [r["hour"] for r in _rows]
            ch = bar_chart([{"hour": r["hour"], "value": r["value"]} for r in _rows],
                           "hour", "hour of day", "avg followers online", sort=_order)
            if ch is not None:
                st.altair_chart(ch, use_container_width=True)
            st.caption("Mean per hour over Meta's served window (~last 30 days, "
                       "regardless of the insights window above). Pick a day to "
                       "mirror the native app's M–Su view.")
        else:
            st.info("No online data for that day yet.")
    else:
        st.info("online_followers unavailable — Meta requires ≥100 followers and "
                "only serves the last 30 days.")

    st.markdown('<div class="section-eyebrow">Demographics</div>', unsafe_allow_html=True)
    d1, d2, d3 = st.columns(3)
    with d1:
        who = st.selectbox("Audience", ["Followers", "Engaged audience"])
    with d2:
        breakdown = st.selectbox("Break down by", ["country", "city", "age", "gender"])
    with d3:
        tf = st.selectbox("Timeframe", ["this_month", "this_week"],
                          help="Only these two are supported on current API versions.")
    metric_name = ("follower_demographics" if who == "Followers"
                   else "engaged_audience_demographics")
    demo = fetch_demographics(token, ig_user_id, metric_name, breakdown, tf)
    if demo:
        ch = bar_chart([{"label": k, "value": v} for k, v in demo[:20]],
                       "label", None, "accounts", horizontal=True)
        st.altair_chart(ch, use_container_width=True)
        st.caption("Meta returns only the top 45 rows and only viewers it has "
                   "demographic data for — bars may sum to less than your follower count.")
    else:
        st.info("No demographic data returned. Meta requires ≥100 followers "
                "(or ≥100 engagements for the engaged-audience metric).")

# --- DATA -------------------------------------------------------------------
with tab_data:
    st.markdown('<div class="section-eyebrow">Rows shaped for your database</div>',
                unsafe_allow_html=True)
    st.caption("Matches your social_accounts / instagram_accounts / metrics column "
               "names exactly. Nothing is written to a DB here.")
    st.json(build_db_rows(identity, profile, token_meta, schema_metrics))

    st.markdown('<div class="section-eyebrow">Extended metrics (new — optional columns)</div>',
                unsafe_allow_html=True)
    st.json({
        "profile": profile,
        "category_inferred": category_inferred,
        "reels_30d": reels_stats,
        "feed_30d": feed_stats,
        "account_totals_by_format": fmt_totals,
        "account_totals": plain_totals,
        "follows_and_unfollows": fu_totals,
        "profile_links_taps_by_button": plr_totals,
        "reach_plain": reach_plain,
    })

    st.markdown('<div class="section-eyebrow">Window debug</div>', unsafe_allow_html=True)
    st.json({
        "window_days": window_days,
        "window_align_mode": win_align,
        "window_tz_offset_hours": win_tz_h,
        "window_queried": window_bounds_label(window_days, win_align, win_tz_h),
        "views_total_value": total_of("views", fmt_totals),
        "reach_total_breakdown": total_of("reach", fmt_totals),
        "reach_total_breakdown_source": (fmt_totals.get("reach") or {}).get("source"),
        "reach_total_plain": total_of("reach", reach_plain),
        "reach_total_plain_source": (reach_plain.get("reach") or {}).get("source"),
        "reach_total_followtype": (split_totals.get("reach") or {}).get("total"),
        "reach_series_sum": _series_reach_sum,
    })

    errs = st.session_state.get("api_errors", [])
    with st.expander(f"API warnings this session ({len(errs)})"):
        if errs:
            st.caption("Metrics Meta flags 'in development' or gates behind the "
                       "100-follower minimum land here instead of failing the page.")
            st.json(errs)
        else:
            st.write("None — every call succeeded.")
    st.caption(f"Token expires: {token_meta.get('token_expires_at', '—')} · "
               f"refresh_long_lived_token() is included for when you persist tokens.")

# --- SEQUENTIAL EXECUTION (every network call, in the order it ran) --------
with tab_sequence:
    st.markdown('<div class="section-eyebrow">Every network call this app has made, '
                'in order — starting with the OAuth token exchange</div>',
                unsafe_allow_html=True)
    st.caption(
        "Only calls that actually went over the network show up here. Most "
        "reruns (changing a filter, switching tabs) hit @st.cache_data instead "
        "and add nothing — that's expected, not a gap. New rows appear on "
        "first login, on 'Refresh data', or when a control (like the days "
        "window) changes to a value that hasn't been fetched yet this "
        "session. access_token and client_secret values are masked wherever "
        "they appear, in requests and responses — see the comment above "
        "_log_api_call in the source for exactly what that does and does not "
        "touch. Everything else, including the complete response body, is "
        "exactly what Meta returned — nothing computed or filtered."
    )

    log = st.session_state.get("api_call_log", [])

    total_calls = len(log)
    error_calls = sum(1 for e in log if e["status_code"] >= 400)
    avg_ms = round(sum(e["elapsed_ms"] for e in log) / total_calls, 1) if total_calls else 0.0
    unique_endpoints = len({e["endpoint"] for e in log})

    kpi_html = "".join([
        render_kpi("Total API calls", str(total_calls),
                   "every network hit this browser session, in sequence", hero=True),
        render_kpi("Unique endpoints", str(unique_endpoints)),
        render_kpi("Calls that errored", str(error_calls), "HTTP status ≥ 400"),
        render_kpi("Avg response time", f"{avg_ms} ms" if total_calls else "—"),
    ])
    st.markdown(f'<div class="kpi-grid">{kpi_html}</div>', unsafe_allow_html=True)

    bcol1, bcol2, _bcol3 = st.columns([1, 1, 4])
    with bcol1:
        if st.button("Clear log", key="clear_api_log", use_container_width=True):
            st.session_state.api_call_log = []
            st.rerun()
    with bcol2:
        st.download_button(
            "Download JSON",
            data=json.dumps(log, indent=2, default=str),
            file_name=f"api_call_log_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
            mime="application/json",
            disabled=not log,
            key="download_api_log",
            use_container_width=True,
        )

    if not log:
        st.info("No network calls logged yet this session. If you're testing this "
                "on an account that was already connected before this tab existed, "
                "click Disconnect and log in again — the OAuth exchange calls only "
                "get captured if they happen while this code is running.")
    else:
        st.markdown('<div class="section-eyebrow">Calls per endpoint</div>',
                    unsafe_allow_html=True)
        endpoint_counts: dict[str, int] = {}
        for e in log:
            endpoint_counts[e["endpoint"]] = endpoint_counts.get(e["endpoint"], 0) + 1
        endpoint_df = pd.DataFrame(
            sorted(endpoint_counts.items(), key=lambda kv: -kv[1]),
            columns=["endpoint", "calls"])
        st.dataframe(endpoint_df, use_container_width=True, hide_index=True)

        st.markdown('<div class="section-eyebrow">Calls in order</div>', unsafe_allow_html=True)
        show_filter = st.radio("Show", ["All calls", "Errors only"],
                               horizontal=True, key="api_log_filter")
        filtered = ([e for e in log if e["status_code"] >= 400]
                    if show_filter == "Errors only" else log)
        table_df = pd.DataFrame([{
            "#": e["seq"], "time (UTC)": e["ts"], "method": e["method"],
            "endpoint": e["endpoint"], "status": e["status_code"], "ms": e["elapsed_ms"],
        } for e in filtered])
        st.dataframe(table_df, use_container_width=True, hide_index=True)

        st.markdown('<div class="section-eyebrow">Full request and response for one '
                    'call</div>', unsafe_allow_html=True)
        if filtered:
            seqs = [e["seq"] for e in filtered]
            pick = st.selectbox("Call #", seqs, index=len(seqs) - 1,
                                key=f"api_log_pick_{show_filter}")
            entry = next(e for e in log if e["seq"] == pick)
            st.caption(f"{entry['method']} · {entry['endpoint']} · "
                       f"HTTP {entry['status_code']} · {entry['elapsed_ms']} ms · "
                       f"{entry['ts']}")
            st.text_input("Request URL", entry["url"], disabled=True, key="api_log_url")
            if entry["request_body"]:
                st.text_area("Request body", entry["request_body"], disabled=True,
                             height=100, key="api_log_body")

            usage_headers = {k: v for k, v in entry.get("response_headers", {}).items()
                             if "usage" in k.lower() or "rate" in k.lower()}
            if usage_headers:
                st.caption("Rate/usage headers Meta returned on this call: " +
                          " · ".join(f"{k}: {v}" for k, v in usage_headers.items()))

            st.markdown("**Full response body — exactly as Meta sent it**")
            try:
                st.json(json.loads(entry["response_text"]))
            except (ValueError, TypeError):
                st.code(entry["response_text"] or "(empty body)")

            with st.expander("Raw response text"):
                st.code(entry["response_text"] or "(empty body)")
            with st.expander("All response headers"):
                st.json(entry.get("response_headers", {}))
        else:
            st.info("No calls match this filter.")
