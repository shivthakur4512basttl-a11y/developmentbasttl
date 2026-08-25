"""
Instagram Business Insights — Streamlit app (v6)
Instagram API with Instagram Login only (graph.instagram.com / api.instagram.com).
No Facebook Login, no graph.facebook.com anywhere in this file.

WHAT CHANGED vs v5 — profile field coverage completed; a permission declined
-----------------------------------------------------------------------------
1. NOT added, on purpose: public_profile / "default public profile fields"
   (graph-api/reference/user). That page is the FACEBOOK User node — it
   requires a Facebook User access token via Facebook Login and returns the
   Facebook person's name parts and picture. This app uses Business Login
   for Instagram exclusively: there is no Facebook user, no Facebook token,
   and no graph.facebook.com call anywhere, so that permission has no
   exercise path here. It sits on virtually every Meta app by default,
   which is why it appears "granted" in the App Dashboard. Wiring it in
   would mean adding a second, unrelated login flow to fetch worse
   duplicates of data already pulled from the IG profile.
2. Added instead, under instagram_business_basic (already held): the one
   IG User profile field the app wasn't fetching — `website` (link-in-bio
   URL). Now fetched in fetch_profile, shown in the header, and the full
   profile snapshot is dumped in Data -> Extended metrics so nothing
   fetched is invisible. build_db_rows shapes remain byte-identical.

WHAT CHANGED vs v4 — reach-variants diagnostic; verified default window
------------------------------------------------------------------------
1. VERIFIED on live data (2026-08-25): "Last N complete days" at UTC 0
   reproduced the native app's Views EXACTLY (4,457 = 4,457). An exact
   match on an additive metric pins the app's window convention: 30
   complete days, today excluded, UTC midnights — so that mode is now the
   DEFAULT. All three modes remain selectable; a stale-session guard
   clears old widget state after the label rename.
2. With identical windows, the remaining reach gap (API 2,135 vs the app's
   "Viewers" 2,226) is measurement method, not dates. The API exposes no
   "viewers" metric; reach is the closest analog and Meta documents it as
   estimated. New diagnostic: reach is fetched FOUR ways for the same
   window — with media_product_type breakdown (the headline), plain with
   no breakdown (new fetch_reach_plain, +1 API call per load), with
   follower-type breakdown (already fetched for the split), and the summed
   daily series — all shown side by side in the Overview caption and the
   Data-tab window debug. Whichever tracks the app's Viewers is what the
   app uses; if none do, the residual is Meta-side and no parameter we
   pass will close it.
3. _parse_total_value_payload now records WHERE each total came from:
   "meta_total" (Meta's own total_value.value) vs "breakdown_sum" (the
   fallback that sums breakdown rows — which, for reach, double-counts
   accounts appearing under more than one surface), or "mixed" across
   chunks. Diagnostic only; no math consumes it.
4. Deliberately UNCHANGED: the headline Reach KPI and the stored
   total_reach_30d still come from the breakdown call, same as v2-v4. If
   the variants show plain reach tracking the app's Viewers better, that
   switch is a one-line change made on evidence — not another guess.

WHAT CHANGED vs v3 — window convention is now selectable, nothing removed
--------------------------------------------------------------------------
1. The day-boundary convention and its timezone are now UI controls (an
   expander under the 7/30/90 selector): "Day-aligned incl. today" (the v3
   behavior, still the default), "Last N complete days (excl. today)", or
   "Rolling — exact now − N days" (the v2 behavior), plus an hours-vs-UTC
   offset for where midnight falls (IST = 5.5). Every account-level
   total_value call, both time series, AND the posts-in-window cutoff follow
   the same convention, and the settings are part of every cache key, so
   switching modes can't serve stale numbers.
   WHY: v3's UTC day-flooring narrowed the reach gap vs the native app
   (77 low -> 52 low) but pushed views ~51 HIGH — views is additive, so a
   wider window reads strictly higher. That result is evidence the app's
   own "Last 30 days" is narrower and/or aligned to a different midnight
   (likely the account's local timezone). Meta doesn't document the app's
   convention, so it's now empirically testable instead of guessed.
2. The cross-check caption now includes the views total with a note that
   views moves ~linearly with window width; the Data-tab window-debug JSON
   now records the active mode, offset, and views total alongside reach.
3. In "complete days" mode, posts published today are also excluded from
   the post-based metrics (consistent window everywhere); in the other two
   modes post handling is byte-identical to before.

WHAT CHANGED vs v2 — nothing removed, all additive/corrective
---------------------------------------------------------------
1. _chunk_ranges() now floors `since` to the start of its UTC day instead of
   the exact instant the code ran. Every account-level total_value call goes
   through this (reach, views, likes, comments, saves, shares,
   total_interactions, accounts_engaged, replies, reposts,
   profile_links_taps, follows_and_unfollows, the new
   profile_links_taps-by-button call, and both time series) — so this shifts
   MOST account-level numbers slightly, not just reach, and it shifts them
   up (the window gets up to ~24h wider, never narrower). Rationale: reach
   and friends are aggregated by whole days server-side; a `since` landing
   mid-day risked Meta rounding away part of that first day, which is one
   documented, named cause of API-vs-native-app number mismatches. This is a
   reasoned improvement, not a confirmed fix — Meta doesn't publicly
   document its own day-boundary/timezone convention for "last N days" in
   the app, so treat this as the best available default and validate against
   the app using the two additions below, not as a guarantee of an exact
   match.
2. window_bounds_label() + a caption in Overview surface the exact UTC
   since/until every account-level call used, so you can compare it directly
   against whatever date range Instagram's own app shows for its "last N
   days" — the fastest way to confirm or rule out point 1 empirically.
3. A visible reach cross-check in Overview + Data: account total_value reach
   vs. the sum of the daily reach time-series for the same window. Meta
   documents daily reach as deduplicated within each day only, not across
   the window, so these are not expected to match — shown so you can see the
   gap yourself instead of taking a comment's word for it. Two KPI sub-labels
   that flatly asserted "sum of daily values" (unconfirmed) now point here
   instead.
4. Two more insights extracted, previously available but unused:
     - Media-level `profile_activity`, broken down by action_type (bio-link
       tap, call, email, direction, text) — what someone did after visiting
       your profile from a specific post. Distinct from profile_visits,
       which only says a visit happened. New: Feed tab KPI + breakdown bars,
       a post-card chip, a "Top content" rank-by option, and a column in the
       feed data table.
     - Account-level `profile_links_taps`, now also fetched WITH its
       contact_button_type breakdown (call/email/direction/text/book-now),
       alongside the existing flat total — new breakdown bars in Overview.
   Cost: one extra API call per feed post during per-post enrichment
   (profile_activity needs its own call — Meta errors a batched request if
   one metric in it doesn't support the requested breakdown). Feed-heavy
   accounts near MAX_ENRICHED_MEDIA will feel this; nothing else changed
   about that cap.

WHAT CHANGED vs v1 (audit summary)
----------------------------------
1. SCOPES trimmed to the TWO permissions your app actually holds:
       instagram_business_basic
       instagram_business_manage_insights
   v1 requested five (messages / comments / content_publish included). Asking
   for scopes your app doesn't have kills the authorize step before your code
   ever runs. Nothing in this file ever used those endpoints anyway.

2. Reels and Feed are now SEPARATE everywhere: per-post metrics are split by
   media_product_type, and account-level reach/views/interactions are fetched
   with breakdown=media_product_type so you get Meta's own REELS vs FEED vs
   STORY split — not a hand-rolled sum of posts.

3. Granted-but-unused insights now implemented (all covered by your two scopes):
   Account:  views, accounts_engaged, likes/comments/saves/shares/replies/
             reposts totals, profile_links_taps, follows_and_unfollows,
             reach time-series, follower_count time-series, online_followers,
             follower_demographics, engaged_audience_demographics
   Media:    reposts (all), and per-type extras —
             REELS: ig_reels_avg_watch_time, ig_reels_video_view_total_time,
                    reels_skip_rate
             FEED:  follows (followers gained from a post), profile_visits

4. Formula upgrades: median-based per-post ER (robust to one viral outlier),
   rate decomposition (save/share/comment rate per reach), views-per-reach
   (rewatch signal), reach rate vs followers, reels hook rate (100 − skip
   rate), feed follow-conversion. The five columns your schema stores are
   unchanged; everything new lives in a separate "extended" dict.

5. Corrected an overclaim from v1: account reach total_value was labeled
   "deduplicated across 30 days". Meta's response docs describe total_value
   as the SUM of the period's values, so cross-day dedup is NOT guaranteed.
   Labels now say what the number actually is.

6. Hardening: shared session, one error path for every call, OAuth `state`
   CSRF check, retry-that-drops-unavailable-metrics (several account metrics
   are flagged "in development" by Meta and can vanish per-account), warning
   if the redirect URI has a path (Streamlit only serves the root URL).

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
import os
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
    wire this in once you persist tokens to your DB."""
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
    insight set attached. The window bounds come from _chunk_ranges() with
    the SAME align/tz convention as the account totals, so "posts in
    window" and "account totals window" can't silently diverge. The upper
    bound only bites in complete-days mode (today's posts drop out along
    with today's totals); in day-aligned and rolling modes it equals "now",
    which changes nothing versus before.

    Resilience: if the insights field expansion makes the whole /media call
    fail (Meta rejects the entire request when one expanded metric is
    unavailable), retry WITHOUT the expansion and fetch each post's insights
    individually. A failed expansion must never masquerade as '0 posts'.

    Ordering: does NOT assume strict newest-first (pinned posts could break
    that). Items are filtered by timestamp; pagination stops only when an
    entire page falls entirely OLDER than the window. If items came back but
    none landed in the window, a diagnostic is recorded instead of a silent
    zero."""
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
                page_has_recent = True  # not yet older than the window
                if ts < upper:
                    posts.append(post)
        if page and not page_has_recent:
            break  # whole page older than the window — done
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
    """Type-specific per-media insights. REELS: watch time + skip rate (one
    call, unchanged). FEED: follows + profile_visits, PLUS profile_activity
    broken down by action_type — what someone did after visiting your
    profile from this post (bio-link tap, call, email, direction, text),
    which is a different signal than profile_visits (a visit happened) or
    follows (they followed). profile_activity needs a separate call: it's
    the only metric here that takes a breakdown, and Meta errors the whole
    request if one metric in a batch doesn't support the breakdown given to
    it. The two FEED calls are independent, so one failing still returns
    whatever the other got — strictly more resilient than a single
    all-or-nothing call. Returns {} keys are simply omitted on error (some
    metrics are flagged 'in development' by Meta and can be absent per
    account)."""
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
    """-> {metric: {"total": int, "by": {DIMENSION: int}, "source": str}}
    "source" records where the total came from: "meta_total" = Meta's own
    total_value.value; "breakdown_sum" = the fallback below, which for
    reach DOUBLE-COUNTS accounts that appear under more than one breakdown
    dimension (someone who saw both a reel and a post lands in both
    buckets). Diagnostic only — nothing downstream uses it for math."""
    out: dict = {}
    for m in data.get("data", []):
        name = m.get("name")
        tv = m.get("total_value", {}) or {}
        entry = {"total": tv.get("value", 0), "by": {}, "source": "meta_total"}
        for bd in tv.get("breakdowns", []) or []:
            for res in bd.get("results", []) or []:
                dims = res.get("dimension_values", []) or ["?"]
                entry["by"][dims[-1]] = entry["by"].get(dims[-1], 0) + res.get("value", 0)
        # Meta omits the top-level value on some breakdown responses -> 0 total
        # alongside a non-zero breakdown. Prefer the breakdown sum in that case.
        if not entry["total"] and entry["by"]:
            entry["total"] = sum(entry["by"].values())
            entry["source"] = "breakdown_sum"
        out[name] = entry
    return out


# Window alignment modes. Verified 2026-08-25: complete_days at UTC 0
# reproduced the native app's Views exactly, so it leads the list (selectbox
# index 0 = default). The UI expander is the source of truth for the active
# mode; the per-function signature defaults are inert on the main path.
#   complete_days  — last N complete local days: [midnight − N days, midnight
#                    today]. Today's still-accumulating data excluded.
#   day_floor      — since floored to local midnight of (now − N days),
#                    until = this exact instant. v3 behavior at tz 0.
#   rolling        — exact now − N days to now, to the second. v2 behavior.
WINDOW_ALIGN_MODES = {
    "Last N complete days (excl. today) — matches the app (views verified)": "complete_days",
    "Day-aligned days, incl. today": "day_floor",
    "Rolling — exact now − N days": "rolling",
}


def _chunk_ranges(days: int, max_span: int = 30, align: str = "day_floor",
                  tz_h: float = 0.0) -> list[tuple[int, int]]:
    """Split the window into <=max_span-day (since, until) unix pairs — Meta
    serves short insight ranges, so 90d becomes three 30d calls.

    `align` and `tz_h` pick the window convention (see WINDOW_ALIGN_MODES).
    Rationale: reach/views/interactions are aggregated by whole days
    server-side, and additive metrics move almost linearly with window
    width — so WHERE the boundary falls, and in WHICH timezone, is exactly
    what decides whether these totals line up with the native app's
    "Last N days". Meta doesn't publish the app's convention; use the UI
    expander + window_bounds_label() to find the one that matches instead
    of trusting any single guess. Unix timestamps are timezone-correct
    regardless of tz_h (aware datetimes)."""
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
    """Human-readable (since, until) for the full window, rendered in the
    chosen boundary timezone — for comparing against whatever date range
    Instagram's app shows for the same nominal 'last N days' period."""
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
    """One since/until range. Meta 400s the WHOLE call if one metric is
    unavailable for this account or was deprecated since this file was
    written. Drop the offending metric (when the error names it) and retry."""
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
    """Window totals, chunked into <=30d ranges and summed. Additive metrics
    (views, likes, interactions…) sum exactly; reach sums each chunk's value,
    consistent with the sum-of-daily-values caveat used everywhere else.
    align/tz_h are keyword-only so they can never fall into **extra and leak
    into the API query string."""
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
    """Account totals WITH breakdown=media_product_type — Meta's own
    REELS vs FEED vs STORY vs AD split. All listed metrics support this
    breakdown per the IG User Insights reference."""
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
    """Metrics that don't take the media_product_type breakdown."""
    return _totals_with_metric_dropping(
        token, ig_user_id,
        ["accounts_engaged", "replies", "reposts", "profile_links_taps"],
        days, "account totals", align=align, tz_h=tz_h,
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_reach_plain(token: str, ig_user_id: str, days: int,
                      align: str = "day_floor", tz_h: float = 0.0) -> dict:
    """Account reach total_value with NO breakdown — the control arm of the
    reach-variants diagnostic. Attaching a breakdown can change what Meta
    returns as the top-level total, and the breakdown-sum fallback
    double-counts cross-surface viewers, so this is the cleanest single
    number the API offers for window-unique reach. One extra call per
    load."""
    return _totals_with_metric_dropping(
        token, ig_user_id, ["reach"], days, "reach (no breakdown)",
        align=align, tz_h=tz_h,
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_profile_links_taps_by_button(token: str, ig_user_id: str, days: int,
                                       align: str = "day_floor",
                                       tz_h: float = 0.0) -> dict:
    """profile_links_taps broken down by contact_button_type (call, email,
    direction, text, book-now, instant-experience) — which specific button
    people tap, not just the combined total already covered by
    fetch_account_totals_plain. Separate call: accounts_engaged/replies/
    reposts in that batch don't support this breakdown, so it can't ride
    along with them."""
    return _totals_with_metric_dropping(
        token, ig_user_id, ["profile_links_taps"], days,
        "profile links taps by button", align=align, tz_h=tz_h,
        breakdown="contact_button_type",
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_follows_unfollows(token: str, ig_user_id: str, days: int,
                            align: str = "day_floor",
                            tz_h: float = 0.0) -> dict:
    """follows_and_unfollows with breakdown=follow_type. Requires >=100
    followers; returns {} below that."""
    return _totals_with_metric_dropping(
        token, ig_user_id, ["follows_and_unfollows"], days,
        "follows/unfollows", align=align, tz_h=tz_h, breakdown="follow_type",
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_follower_split(token: str, ig_user_id: str, days: int,
                         align: str = "day_floor", tz_h: float = 0.0) -> dict:
    """Followers vs non-followers split for views / reach / interactions —
    what Instagram's native 'Account insights' shows as 30.3% / 69.7%.
    Meta's docs name this breakdown inconsistently (follower_type for views,
    follow_type for reach), so both spellings are tried per metric; metrics
    that reject both are simply absent from the result."""
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
    """Daily time series -> [{"date": ..., "value": ...}], chunked into <=30d
    calls for longer windows. follower_count needs >=100 followers and Meta
    serves only ~30 days of it — older chunks fail quietly into the log.
    Uses the same window convention as the totals, so the reach cross-check
    compares like for like."""
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
    """Per-day hour buckets for the last ~30 days (Meta's limit) ->
    [(YYYY-MM-DD, {hour_str: count})]. Requires >=100 followers.
    Kept per-day so the UI can filter by weekday like the native app."""
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
    """follower_demographics / engaged_audience_demographics.
    timeframe must be this_month or this_week on v20+ (older values were
    removed). Needs >=100 followers (or >=100 engagements for the engaged
    metric); Meta returns only the top 45 rows."""
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
    """Per-format metric block. Median-based ER is the headline: with a
    typical 30-day sample one viral post drags any mean; the median is what
    a *typical* post did."""
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
        # ig_reels_* watch times arrive in MILLISECONDS (not in Meta's docs;
        # widely confirmed by third-party integrations) — converted here.
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
        "reach_rate_median": _median(reach_rates),      # % of followers a typical post reaches
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
        out["follow_conversion"] = rate(follows_sum)     # follows per 100 reached
    if profile_activity_sum:
        out["profile_activity_from_posts"] = profile_activity_sum
    if profile_activity_by_action:
        out["profile_activity_by_action"] = profile_activity_by_action
    return out


def compute_schema_metrics(posts: list[dict], followers: int,
                           account_totals: dict) -> dict:
    """The five columns your schema stores — definitions unchanged from v1,
    labels corrected. Account reach here is Meta's total_value for the
    window; Meta documents total_value as the sum of the period's values, so
    treat it as Meta's reported window total, NOT guaranteed cross-day
    unique."""
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
    """(avg likes + avg comments per post) / followers x 100 — the per-post
    averaged formula most third-party IG tools display as 'Engagement Rate'.
    Kept for cross-tool comparison; your schema stores the cumulative ones."""
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
    """Unchanged shape — matches your social_accounts / instagram_accounts /
    metrics column names exactly. New metrics are returned separately as
    metrics_extended_30d; add columns for those only if you want them."""
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
    """Markdown ends an HTML block at a blank line and renders 4-space-indented
    text as a code block — so multi-line HTML with indentation leaks raw source
    into the page from the second element onward. Collapse to one line."""
    return "".join(line.strip() for line in s.splitlines() if line.strip())


_FORMAT_LABELS = {"REELS": "Reels", "FEED": "Posts", "STORY": "Stories", "AD": "Ads"}


def render_pct_block(title: str, by: dict, sub: str = "") -> str:
    """Native-style 'By content type' percentage bars from a breakdown map."""
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
    """'Views — Followers 30.3% · Non-followers 69.7%' from a follower-type
    breakdown. Labels come straight from Meta; nothing is renamed."""
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

# --- OAuth gate -------------------------------------------------------------
if "access_token" not in st.session_state:
    st.title("📊 Instagram Business Insights")
    st.caption(f"Redirect URI in use: `{REDIRECT_URI}` — must match the Meta App "
               f"Dashboard registration character for character.")
    code = st.query_params.get("code")
    returned_state = st.query_params.get("state")

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
            st.rerun()
        st.stop()

    with st.status("Connecting to Instagram…", expanded=True) as status:
        st.write("Exchanging code for a short-lived token…")
        short = exchange_code_for_short_token(code)
        st.query_params.clear()  # burn the one-time code immediately
        if "access_token" not in short:
            status.update(label="Failed", state="error")
            st.error(f"Token exchange failed: {short}")
            st.stop()
        st.write("Upgrading to a long-lived token (≈60 days)…")
        long = exchange_for_long_lived_token(short["access_token"])
        if "access_token" not in long:
            status.update(label="Failed", state="error")
            st.error(f"Long-lived token exchange failed: {long}")
            st.stop()
        expires_in = long.get("expires_in", 0)
        st.session_state.access_token = long["access_token"]
        st.session_state.token_meta = {
            "permissions": short.get("permissions", ""),
            "token_expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            ).isoformat(),
        }
        status.update(label="Connected", state="complete")
    st.rerun()

# --- Data load --------------------------------------------------------------
token = st.session_state.access_token
token_meta = st.session_state.token_meta
st.session_state.setdefault("api_errors", [])

# --- Window selector: 7 / 30 / 90 days --------------------------------------
_window_opts = [7, 30, 90]
if hasattr(st, "segmented_control"):
    _picked = st.segmented_control("Insights window (days)", _window_opts,
                                   default=WINDOW_DAYS)
else:  # older Streamlit fallback
    _picked = st.radio("Insights window (days)", _window_opts,
                       index=_window_opts.index(WINDOW_DAYS), horizontal=True)
window_days = _picked or WINDOW_DAYS

# --- Window alignment: which "last N days" convention to use -----------------
if st.session_state.get("win_align") not in WINDOW_ALIGN_MODES:
    st.session_state.pop("win_align", None)  # labels changed in v5 — drop stale state
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

# Per-media type-specific insights (watch time, skip rate, follows, visits)
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

tab_overview, tab_reels, tab_feed, tab_audience, tab_data = st.tabs(
    ["Overview", "Reels", "Feed posts", "Audience", "Data"])

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
            # Meta returned breakdown rows but no combined total — showing 0
            # would be a lie, so show a dash and let the breakdown speak.
            fu_display = "—"
            fu_sub = f"{breakdown_txt} (Meta returned no combined total; " \
                     f"breakdown semantics undocumented)"
        kpis.append(render_kpi("Follows & unfollows", fu_display, fu_sub))
    st.markdown(f'<div class="kpi-grid">{"".join(kpis)}</div>', unsafe_allow_html=True)

    # --- The v1 metric cells: same numbers, same formulas, always visible ---
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

    # --- Profile link taps by button (new — was only a flat total before) ---
    _plt_by = (plr_totals.get("profile_links_taps") or {}).get("by", {})
    if _plt_by:
        st.markdown(render_pct_block("Profile link taps by button", _plt_by),
                    unsafe_allow_html=True)

    # --- Native-style content-type split (matches the in-app Account insights) ---
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

    # Signature element: the format split, from Meta's own account-level breakdown
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
