"""
Instagram Business Insights — Streamlit app
Uses the Instagram API with Instagram Login (graph.instagram.com only — no
Facebook Login, no graph.facebook.com anywhere in this file, per your request).

ENV VARS REQUIRED (set these in Streamlit Cloud -> App -> Settings -> Secrets,
or in a local .env file for dev):

    INSTA_APP_ID        Your Instagram app's Client ID
    INSTA_APP_SECRET    Your Instagram app's Client Secret
    INSTA_REDIRECT_URI  The exact redirect URL, e.g.
                         https://developmentflowinstagram.streamlit.app/callback
                         Must match what's registered in the Meta App
                         Dashboard character for character.

Everything else is derived from those three. See README.md for the full
Meta App Dashboard setup steps (redirect URI registration etc.) — code alone
will not work until that side is configured to match.
"""

import html
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")

# Set this to the FULL redirect URL, e.g.:
#   https://developmentflowinstagram.streamlit.app/callback
# It must exactly match a "Valid OAuth Redirect URI" registered in your Meta
# App Dashboard -> Instagram -> API setup with Instagram login, character for
# character, or the authorization request is rejected before your app code
# ever runs. Kept as one env var (not built from a separate base-URL var) so
# there's only one place this can drift out of sync with the dashboard.
REDIRECT_URI = os.getenv("INSTA_REDIRECT_URI", "").strip()

API_VERSION = "v25.0"  # bump this in one place when Meta ships a new version
GRAPH_HOST = "https://graph.instagram.com"

# The permission your previous app was missing. Insights will not work
# without it, full stop — Meta rejects the fields with a permissions error.
SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
    "instagram_business_manage_insights",
]

# `impressions` is deliberately excluded below: Meta deprecated it for any
# media created after 2024-07-02 (error, not just empty data, on v22.0+).
# `views` is already its replacement and is already in this list.
MEDIA_FIELDS = (
    "id,timestamp,permalink,caption,media_type,media_product_type,"
    "media_url,thumbnail_url,like_count,comments_count,"
    "insights.metric(views,reach,saved,shares,total_interactions)"
)

WINDOW_DAYS = 30  # matches the _30d columns; change in one place if you add 7d/90d
TOP_N_POSTS = 3   # how many "top performing" cards to show


# ---------------------------------------------------------------------------
# 2. OAUTH — Instagram Login flow (graph.instagram.com / api.instagram.com only)
# ---------------------------------------------------------------------------

def build_authorize_url() -> str:
    scope_str = ",".join(SCOPES)
    return (
        "https://www.instagram.com/oauth/authorize"
        f"?client_id={INSTA_APP_ID}"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&scope={quote(scope_str, safe=',')}"
        f"&response_type=code"
    )


def exchange_code_for_short_token(code: str) -> dict:
    """POST to api.instagram.com. Returns access_token, user_id, permissions."""
    resp = requests.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": INSTA_APP_ID,
            "client_secret": INSTA_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": code,
        },
        timeout=15,
    )
    data = resp.json()
    # api.instagram.com sometimes wraps the result in {"data": [ {...} ]}
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        data = data["data"][0]
    return data


def exchange_for_long_lived_token(short_token: str) -> dict:
    """GET graph.instagram.com/access_token. Returns access_token, expires_in."""
    resp = requests.get(
        f"{GRAPH_HOST}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": INSTA_APP_SECRET,
            "access_token": short_token,
        },
        timeout=15,
    )
    return resp.json()


# ---------------------------------------------------------------------------
# 3. PROFILE + MEDIA + INSIGHTS  (all graph.instagram.com)
# ---------------------------------------------------------------------------

def fetch_identity(access_token: str) -> dict:
    r = requests.get(
        f"{GRAPH_HOST}/{API_VERSION}/me",
        params={"fields": "id,user_id,username,name", "access_token": access_token},
        timeout=15,
    )
    return r.json()


def fetch_profile(access_token: str, ig_user_id: str) -> dict:
    r = requests.get(
        f"{GRAPH_HOST}/{API_VERSION}/{ig_user_id}",
        params={
            # biography was missing from the old app entirely — that's your
            # `bio` column with nothing feeding it.
            "fields": (
                "account_type,biography,profile_picture_url,"
                "followers_count,follows_count,media_count"
            ),
            "access_token": access_token,
        },
        timeout=15,
    )
    return r.json()


def fetch_media_window(access_token: str, ig_user_id: str, days: int) -> list[dict]:
    """Every post published in the last `days` days, each with its own
    like_count / comments_count / insights (views, reach, saved, shares,
    total_interactions) already attached via field expansion."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    url = f"{GRAPH_HOST}/{API_VERSION}/{ig_user_id}/media"
    params = {"fields": MEDIA_FIELDS, "limit": 50, "access_token": access_token}

    posts = []
    while url:
        r = requests.get(url, params=params, timeout=15).json()
        if "data" not in r:
            break
        for post in r["data"]:
            ts = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if ts < cutoff:
                return posts  # media is returned newest-first; we're done
            posts.append(post)
        next_url = r.get("paging", {}).get("next")
        if not next_url:
            break
        url, params = next_url, {}  # next_url already has all params baked in
    return posts


def fetch_account_insights_total(access_token: str, ig_user_id: str, days: int) -> dict:
    """Account-level reach + total_interactions, aggregated to ONE number for
    the whole window via metric_type=total_value.

    This is deliberately NOT "sum reach across each post" — that double-counts
    any follower who saw more than one post in the window. This call asks
    Meta for the already-deduplicated account-wide figure instead.
    Note: account-level total_interactions includes boosted/ad interactions;
    the per-post total_interactions used elsewhere in this file does not.
    """
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    r = requests.get(
        f"{GRAPH_HOST}/{API_VERSION}/{ig_user_id}/insights",
        params={
            "metric": "reach,total_interactions",
            "period": "day",
            "metric_type": "total_value",
            "since": since,
            "until": until,
            "access_token": access_token,
        },
        timeout=15,
    )
    data = r.json()
    out = {}
    for m in data.get("data", []):
        out[m["name"]] = m.get("total_value", {}).get("value", 0)
    return out


# ---------------------------------------------------------------------------
# 4. METRICS — the five _30d columns
# ---------------------------------------------------------------------------

def _post_insight_value(post: dict, name: str) -> int:
    for m in post.get("insights", {}).get("data", []):
        if m.get("name") == name:
            vals = m.get("values", [])
            return vals[0]["value"] if vals else 0
    return 0


def compute_metrics(posts: list[dict], followers_count: int, account_totals: dict) -> dict:
    post_count = len(posts)
    likes_sum = sum(p.get("like_count", 0) for p in posts)

    # Organic per-post total_interactions (likes+saves+comments+shares, net of
    # unlikes/unsaves/deletions) — Meta already computes this per post, so we
    # use it directly instead of re-deriving it by hand.
    interactions_sum = sum(_post_insight_value(p, "total_interactions") for p in posts)

    per_post_rates = []
    for p in posts:
        reach = _post_insight_value(p, "reach")
        interactions = _post_insight_value(p, "total_interactions")
        if reach > 0:
            per_post_rates.append((interactions / reach) * 100)

    account_reach = account_totals.get("reach", 0)

    return {
        "post_count": post_count,
        "avg_likes_30d": round(likes_sum / post_count, 2) if post_count else 0.0,
        "er_by_followers_30d": (
            round((interactions_sum / followers_count) * 100, 2) if followers_count else 0.0
        ),
        "er_by_reach_30d": (
            round((interactions_sum / account_reach) * 100, 2) if account_reach else 0.0
        ),
        # mean of each individual post's own (interactions / reach) — the
        # "typical post" number, distinct from er_by_followers_30d, which is
        # dominated by whichever posts got the most raw engagement.
        "er_per_post_30d": (
            round(sum(per_post_rates) / len(per_post_rates), 2) if per_post_rates else 0.0
        ),
        "total_reach_30d": account_reach,
    }


def compute_industry_engagement_rate(posts: list[dict], followers_count: int) -> float:
    """(avg likes + avg comments PER POST) / followers x 100.

    This is a different statistic from er_by_followers_30d above, on purpose.
    er_by_followers_30d SUMS engagement across every post in the window, so it
    scales with how often you post. This one AVERAGES per post first, which is
    what Socialinsider, InstaTrack, and most third-party IG analytics tools
    report as "Engagement Rate" -- confirmed by matching their displayed
    numbers against this exact formula. Keep both: this one is what you
    compare against other tools; er_by_followers_30d is what actually belongs
    in your schema.
    """
    if not posts or not followers_count:
        return 0.0
    avg_likes = sum(p.get("like_count", 0) for p in posts) / len(posts)
    avg_comments = sum(p.get("comments_count", 0) for p in posts) / len(posts)
    return round((avg_likes + avg_comments) / followers_count * 100, 2)


def rank_top_posts(posts: list[dict], n: int = TOP_N_POSTS) -> list[dict]:
    """Top N posts by total_interactions (likes+comments+saves+shares)."""
    return sorted(posts, key=lambda p: _post_insight_value(p, "total_interactions"),
                  reverse=True)[:n]


def _post_thumbnail(post: dict) -> str | None:
    """Video/Reel posts expose a static preview via thumbnail_url; images
    expose media_url directly. Fall back gracefully either way."""
    return post.get("thumbnail_url") or post.get("media_url")


# ---------------------------------------------------------------------------
# 5. ROWS SHAPED FOR YOUR SCHEMA
#    (No DB write here on purpose — you haven't told me what you're storing
#    to, e.g. Postgres/Supabase/SQLite. These three dicts match your column
#    names exactly; wire them into whatever client you're using.)
# ---------------------------------------------------------------------------

def build_db_rows(identity, profile, token_meta, metrics) -> dict:
    social_accounts_row = {
        "platform_user_id": identity.get("id"),
        "handle": identity.get("username"),
        "profile_url": f"https://instagram.com/{identity.get('username')}",
        "scopes": token_meta.get("permissions", ""),
        "token_expires_at": token_meta.get("token_expires_at"),
    }
    instagram_accounts_row = {
        "ig_user_id": identity.get("user_id") or identity.get("id"),
        "username": identity.get("username"),
        "name": identity.get("name"),
        "bio": profile.get("biography"),
        "profile_image_url": profile.get("profile_picture_url"),
        "account_type": profile.get("account_type"),
        "follower_count": profile.get("followers_count", 0),
        "follows_count": profile.get("follows_count", 0),
        "media_count": profile.get("media_count", 0),
    }
    metrics_row = {
        "er_by_followers_30d": metrics["er_by_followers_30d"],
        "er_by_reach_30d": metrics["er_by_reach_30d"],
        "er_per_post_30d": metrics["er_per_post_30d"],
        "avg_likes_30d": metrics["avg_likes_30d"],
        "total_reach_30d": metrics["total_reach_30d"],
    }
    return {
        "social_accounts": social_accounts_row,
        "instagram_accounts": instagram_accounts_row,
        "metrics_30d": metrics_row,
    }


# ---------------------------------------------------------------------------
# 5b. PRESENTATION — CSS + card renderers
# ---------------------------------------------------------------------------

CARD_CSS = """
<style>
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
               gap: 14px; margin: 10px 0 26px 0; }
.metric-box { background: linear-gradient(155deg, #1a1c24 0%, #131319 100%);
              border: 1px solid #2a2c36; border-radius: 14px; padding: 18px 20px; }
.metric-box .label { font-size: 12px; color: #9a9cab; text-transform: uppercase;
                      letter-spacing: .04em; margin-bottom: 8px; }
.metric-box .value { font-size: 28px; font-weight: 700; line-height: 1.1;
                      background: linear-gradient(90deg, #f58529, #dd2a7b, #8134af);
                      -webkit-background-clip: text; background-clip: text; color: transparent; }
.metric-box .sub { font-size: 11.5px; color: #6f7180; margin-top: 8px; line-height: 1.35; }

.post-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
             gap: 16px; margin: 10px 0 26px 0; }
.post-card { position: relative; display: block; background: #14151b;
             border: 1px solid #2a2c36; border-radius: 16px; overflow: hidden;
             text-decoration: none; transition: transform .15s ease, border-color .15s ease; }
.post-card:hover { transform: translateY(-3px); border-color: #dd2a7b; }
.post-rank { position: absolute; top: 10px; left: 10px; z-index: 2; color: #fff;
             font-weight: 700; font-size: 12px; padding: 3px 10px; border-radius: 999px;
             background: linear-gradient(90deg, #f58529, #dd2a7b, #8134af); }
.post-media { position: relative; width: 100%; aspect-ratio: 4 / 5; background: #0e0f13; }
.post-thumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.post-thumb-empty { width: 100%; height: 100%; display: flex; align-items: center;
                     justify-content: center; font-size: 40px; }
.post-type-badge { position: absolute; bottom: 8px; right: 8px; background: rgba(0,0,0,.65);
                    color: #fff; font-size: 11px; padding: 2px 9px; border-radius: 999px; }
.post-body { padding: 12px 14px 14px; }
.post-caption { font-size: 13px; color: #d7d8e0; line-height: 1.4; min-height: 36px; margin: 0 0 10px; }
.post-stats { display: flex; gap: 12px; font-size: 12.5px; color: #b9bac6; margin-bottom: 8px; }
.post-footer { display: flex; justify-content: space-between; font-size: 11.5px;
               color: #6f7180; border-top: 1px solid #23242d; padding-top: 8px; }
</style>
"""

_MEDIA_LABELS = {"REELS": "Reel", "VIDEO": "Video", "CAROUSEL_ALBUM": "Carousel", "IMAGE": "Post"}


def render_metric_box(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    return (f'<div class="metric-box"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{value}</div>{sub_html}</div>')


def render_post_card(post: dict, rank: int) -> str:
    thumb = _post_thumbnail(post)
    thumb_html = (f'<img src="{thumb}" class="post-thumb" />' if thumb
                  else '<div class="post-thumb post-thumb-empty">🎬</div>')

    caption = html.escape((post.get("caption") or "").strip())
    if len(caption) > 110:
        caption = caption[:110].rsplit(" ", 1)[0] + "…"

    media_label = _MEDIA_LABELS.get(
        post.get("media_product_type") or post.get("media_type"), "Post"
    )
    likes = post.get("like_count", 0)
    comments = post.get("comments_count", 0)
    reach = _post_insight_value(post, "reach")
    interactions = _post_insight_value(post, "total_interactions")
    permalink = post.get("permalink", "#")
    date_str = (post.get("timestamp") or "")[:10]

    return f"""
    <a href="{permalink}" target="_blank" class="post-card">
      <div class="post-rank">#{rank}</div>
      <div class="post-media">{thumb_html}<span class="post-type-badge">{media_label}</span></div>
      <div class="post-body">
        <p class="post-caption">{caption or '<em>No caption</em>'}</p>
        <div class="post-stats"><span>❤️ {likes:,}</span><span>💬 {comments:,}</span><span>👁️ {reach:,}</span></div>
        <div class="post-footer"><span>{date_str}</span><span>{interactions:,} interactions</span></div>
      </div>
    </a>
    """


# ---------------------------------------------------------------------------
# 6. STREAMLIT UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Instagram Business Insights", page_icon="📊", layout="wide")
st.markdown(CARD_CSS, unsafe_allow_html=True)
st.title("📊 Instagram Business Insights")

missing = [n for n, v in [("INSTA_APP_ID", INSTA_APP_ID),
                           ("INSTA_APP_SECRET", INSTA_APP_SECRET),
                           ("INSTA_REDIRECT_URI", REDIRECT_URI)] if not v]
if missing:
    st.error(f"Missing required environment variable(s): {', '.join(missing)}. "
              f"Set them in Settings -> Secrets, then reload.")
    st.stop()

st.caption(f"Redirect URI in use: `{REDIRECT_URI}` — this must be registered "
           f"exactly in your Meta App Dashboard.")

# session_state holds everything once we've connected, so Streamlit reruns
# (which happen on every widget interaction) don't try to re-exchange an
# already-used auth code, which Instagram will reject the second time.
if "access_token" not in st.session_state:
    code = st.query_params.get("code")

    if not code:
        st.info("Connect an Instagram professional account to see its 30-day insights.")
        st.link_button("Log in with Instagram", build_authorize_url(), use_container_width=True)
        st.stop()

    with st.status("Connecting to Instagram…", expanded=True) as status:
        st.write("Exchanging code for a short-lived token…")
        short = exchange_code_for_short_token(code)
        st.query_params.clear()  # burn the one-time code out of the URL immediately

        if "access_token" not in short:
            status.update(label="Failed", state="error")
            st.error(f"Token exchange failed: {short}")
            st.stop()

        st.write("Upgrading to a long-lived token…")
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

access_token = st.session_state.access_token
token_meta = st.session_state.token_meta

with st.spinner("Loading profile and 30-day insights…"):
    identity = fetch_identity(access_token)
    ig_user_id = identity.get("user_id") or identity.get("id")
    profile = fetch_profile(access_token, ig_user_id)
    posts = fetch_media_window(access_token, ig_user_id, WINDOW_DAYS)
    account_totals = fetch_account_insights_total(access_token, ig_user_id, WINDOW_DAYS)
    metrics = compute_metrics(posts, profile.get("followers_count", 0), account_totals)

col_img, col_info = st.columns([1, 4])
with col_img:
    pic = profile.get("profile_picture_url")
    if pic:
        st.image(pic, width=120)
with col_info:
    st.subheader(f"{identity.get('name', '')} (@{identity.get('username', '')})")
    st.write(f"**IG User ID:** `{ig_user_id}`")
    st.write(f"**Account type:** {profile.get('account_type', '—')}")
    st.write(f"**Bio:** {profile.get('biography') or '—'}")
    st.write(
        f"**Followers:** {profile.get('followers_count', 0):,} · "
        f"**Following:** {profile.get('follows_count', 0):,} · "
        f"**Posts:** {profile.get('media_count', 0):,}"
    )

industry_er = compute_industry_engagement_rate(posts, profile.get("followers_count", 0))

st.divider()
st.markdown(f"### Last {WINDOW_DAYS} days — {metrics['post_count']} posts")

metric_boxes = "".join([
    render_metric_box("Engagement rate", f"{industry_er}%",
                       "avg likes+comments ÷ followers — matches Socialinsider / InstaTrack"),
    render_metric_box("ER by followers (cumulative)", f"{metrics['er_by_followers_30d']}%",
                       "all engagement in the window ÷ followers — scales with posting frequency"),
    render_metric_box("ER by reach (cumulative)", f"{metrics['er_by_reach_30d']}%",
                       "all engagement in the window ÷ true 30d reach"),
    render_metric_box("ER per post (mean)", f"{metrics['er_per_post_30d']}%",
                       "each post's own engagement ÷ its own reach, then averaged"),
    render_metric_box("Avg likes / post", f"{metrics['avg_likes_30d']}"),
    render_metric_box("Total reach (30d, deduped)", f"{metrics['total_reach_30d']:,}"),
])
st.markdown(f'<div class="metric-grid">{metric_boxes}</div>', unsafe_allow_html=True)
st.caption("Four different “engagement rate” numbers on purpose — they answer different "
           "questions and won't match each other or every other tool. See the labels.")

st.markdown("### 🏆 Top performing posts")
top_posts = rank_top_posts(posts, TOP_N_POSTS)
if top_posts:
    cards_html = "".join(render_post_card(p, i + 1) for i, p in enumerate(top_posts))
    st.markdown(f'<div class="post-grid">{cards_html}</div>', unsafe_allow_html=True)
    st.caption("Ranked by total_interactions (likes + comments + saves + shares) in the window.")
else:
    st.info("No posts with insights data in this window yet.")

st.divider()
st.markdown("### Rows shaped for your database")
st.caption("Matches your `social_accounts` / `instagram_accounts` / metrics "
           "column names exactly. Nothing is written to a DB here — plug this "
           "into whatever client you're using.")
st.json(build_db_rows(identity, profile, token_meta, metrics))

if st.button("Disconnect / start over"):
    st.session_state.clear()
    st.rerun()
