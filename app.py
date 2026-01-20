import streamlit as st
import os
import requests
import urllib.parse
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# --- 1. Configuration ---
INSTA_APP_ID = os.getenv("INSTA_APP_ID")
INSTA_APP_SECRET = os.getenv("INSTA_APP_SECRET")
EMBED_URL = os.getenv("INSTA_EMBED_URL")
API_VERSION = "v24.0"
INSTA_REDIRECT_URI = "https://facebookflowbasttl.streamlit.app/redirect"

# --- 2. Metrics Logic ---
def fetch_instagram_metrics(access_token, ig_user_id, days, followers):
    print(f"\n--- TERMINAL: Starting Data Fetch for {days} Days ---")
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    fields = "id,timestamp,like_count,comments_count,insights.metric(views,impressions,reach,saved,shares,total_interactions)"
    url = f"https://graph.instagram.com/{API_VERSION}/{ig_user_id}/media?fields={fields}&limit=50&access_token={access_token}"
    
    totals = {"likes": 0, "comments": 0, "shares": 0, "saves": 0, "reach": 0, "total_interactions": 0, "post_count": 0}
    
    while url:
        response = requests.get(url, timeout=10).json()
        if "data" not in response: break
        
        for post in response['data']:
            post_date = datetime.strptime(post['timestamp'], "%Y-%m-%dT%H:%M:%S%z")
            if post_date < cutoff_date:
                url = None 
                break
            
            totals["likes"] += post.get('like_count', 0)
            totals["comments"] += post.get('comments_count', 0)
            totals["post_count"] += 1
            
            if 'insights' in post:
                for metric in post['insights']['data']:
                    val = metric['values'][0]['value'] if metric['values'] else 0
                    m_name = metric['name']
                    if m_name == 'shares': totals["shares"] += val
                    elif m_name == 'saved': totals["saves"] += val
                    elif m_name == 'reach': totals["reach"] += val
                    elif m_name == 'total_interactions': totals["total_interactions"] += val
        
        url = response.get('paging', {}).get('next') if url else None
    
    engagement = totals["likes"] + totals["comments"] + totals["shares"] + totals["saves"]
    er = (engagement / followers * 100) if followers > 0 else 0
    print(f"TERMINAL: {days}-Day Summary -> Posts: {totals['post_count']}, ER: {round(er, 2)}%")
    return {"ER": round(er, 2), "posts": totals["post_count"], "totals": totals}

# --- 3. Streamlit UI ---
st.set_page_config(page_title="Instagram Pro Insights", page_icon="📊", layout="wide")
st.title("📊 Instagram Business Data Automator")

query_params = st.query_params

if "code" in query_params:
    auth_code = query_params["code"].split("#_")[0]
    print(f"\n[!] TERMINAL: NEW AUTH CODE RECEIVED: {auth_code[:15]}...")

    with st.status("🔗 Connecting to Instagram & Fetching Data...", expanded=True) as status:
        # STEP 1: Exchange Code for Short Token
        token_url = "https://api.instagram.com/oauth/access_token"
        payload = {
            "client_id": INSTA_APP_ID,
            "client_secret": INSTA_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": INSTA_REDIRECT_URI,
            "code": auth_code
        }
        token_res = requests.post(token_url, data=payload).json()
        short_token = token_res.get("access_token")  # AUTH TOKEN

        if short_token:
            # STEP 2: Upgrade to Long-Lived Token
            ll_url = "https://graph.instagram.com/access_token"
            ll_params = {
                "grant_type": "ig_exchange_token",
                "client_secret": INSTA_APP_SECRET,
                "access_token": short_token
            }
            ll_res = requests.get(ll_url, params=ll_params).json()
            access_token = ll_res.get("access_token")  # ACCESS TOKEN
            
            # STEP 3: Fetch Basic Identity
            st.write("👤 Fetching Profile Details...")
            me_url = (
                f"https://graph.instagram.com/{API_VERSION}/me"
                f"?fields=id,user_id,username,name"
                f"&access_token={access_token}"
            )
            me_data = requests.get(me_url).json()
            app_id = me_data.get("id")
            user_id = me_data.get("user_id")
            username = me_data.get("username")
            name = me_data.get("name", "Instagram User")

            # STEP 4: Fetch Professional Account Data
            prof_url = (
                f"https://graph.instagram.com/{API_VERSION}/{user_id}"
                f"?fields=account_type,profile_picture_url,followers_count,follows_count,media_count"
                f"&access_token={access_token}"
            )
            prof = requests.get(prof_url).json()

            account_type = prof.get("account_type")
            profile_pic = prof.get("profile_picture_url")
            followers = prof.get("followers_count", 0)
            follows = prof.get("follows_count", 0)
            media_count = prof.get("media_count", 0)

            print(f"--- TERMINAL: USER CONNECTED ---")
            print(f"Name: {name} | Username: @{username}")
            print(f"IG User ID: {user_id} | Followers: {followers} | Media: {media_count}")

            # STEP 5: Run Multi-Range Reports
            report_7 = fetch_instagram_metrics(access_token, user_id, 7, followers)
            report_30 = fetch_instagram_metrics(access_token, user_id, 30, followers)
            report_90 = fetch_instagram_metrics(access_token, user_id, 90, followers)

            status.update(label="✅ Success! Data Processed.", state="complete")

            # --- DISPLAY DASHBOARD ---
            st.divider()
            
            col_img, col_info = st.columns([1, 4])
            with col_img:
                st.image(profile_pic, width=120) if profile_pic else st.write("👤 (No Profile Image)")
            
            with col_info:
                st.subheader(f"{name} (@{username})")
                st.write(f"**App ID:** `{app_id}`")
                st.write(f"**IG User ID:** `{user_id}`")
                st.write(f"**Account Type:** `{account_type}`")
                st.write(f"**Followers:** {followers:,}")
                st.write(f"**Following:** {follows:,}")
                st.write(f"**Media Count:** {media_count:,}")

            st.divider()

            # Engagement Metrics
            st.markdown("### 📈 Engagement Performance")
            m1, m2, m3 = st.columns(3)
            m1.metric("7-Day ER", f"{report_7['ER']}%", f"{report_7['posts']} posts")
            m2.metric("30-Day ER", f"{report_30['ER']}%", f"{report_30['posts']} posts")
            m3.metric("90-Day ER", f"{report_90['ER']}%", f"{report_90['posts']} posts")

            # Data Tables
            st.divider()
            tab1, tab2 = st.tabs(["30-Day Raw Data", "90-Day Raw Data"])
            with tab1:
                st.json(report_30['totals'])
            with tab2:
                st.json(report_90['totals'])

            # CoShot Deep Link Stuff
            refresh_url = f"https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token={access_token}"
            from urllib.parse import quote
            deep_link = (
                "coshot://callback?"
                f"access_token={quote(access_token)}&"
                f"auth_token={quote(short_token)}&"
                f"refresh_url={quote(refresh_url)}"
            )

            st.divider()
            st.markdown("### 🔗 Export to CoShot App")
            st.link_button("📲 Send to CoShot App", deep_link, use_container_width=True)

        else:
            st.error("❌ Token exchange failed. Please check your App ID/Secret.")
            print(f"TERMINAL ERROR: {token_res}")

else:
    st.info("👋 Welcome! Please authorize your Instagram account to view professional insights.")
    st.link_button("🚀 Login & Authorize Instagram", url=EMBED_URL, use_container_width=True)

print("\n--- TERMINAL: App Loop Finished ---\n")
