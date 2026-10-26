import json
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Group Trip & Ride Manager",
    page_icon="🏍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("GROUP_TRIP_DB", str(APP_DIR / "trip_hub.sqlite3")))
GPS_COMPONENT = components.declare_component(
    "group_trip_gps",
    path=str(APP_DIR / "gps_component"),
)

# Screen Wake-Lock to keep mobile screens awake on handlebars
components.html("""
    <script>
        async function requestWakeLock() {
            try {
                if ('wakeLock' in navigator) {
                    const wakeLock = await navigator.wakeLock.request('screen');
                    console.log('Screen Wake Lock is active');
                }
            } catch (err) {
                console.error(`${err.name}, ${err.message}`);
            }
        }
        requestWakeLock();
        document.addEventListener('visibilitychange', async () => {
            if (document.visibilityState === 'visible') {
                requestWakeLock();
            }
        });
    </script>
""", height=0)

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                dest_lat REAL NOT NULL,
                dest_lon REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                bike_name TEXT,
                member_role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                FOREIGN KEY(trip_id) REFERENCES trips(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                trip_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                accuracy REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(trip_id, member_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                title TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instructions (
                id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

init_db()

def query_all(sql, params=()):
    with get_db() as conn:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

def query_one(sql, params=()):
    with get_db() as conn:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

def execute(sql, params=()):
    with get_db() as conn:
        conn.execute(sql, params)
        conn.commit()

def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_trip_by_code(code):
    return query_one("SELECT * FROM trips WHERE invite_code = ?", (code.strip().upper(),))

def get_trip_by_id(trip_id):
    return query_one("SELECT * FROM trips WHERE id = ?", (trip_id,))

def get_members(trip_id):
    return query_all("SELECT * FROM members WHERE trip_id = ?", (trip_id,))

def current_member():
    if "member_id" not in st.session_state or "trip_id" not in st.session_state:
        return None
    return query_one("SELECT * FROM members WHERE id = ? AND trip_id = ?", (st.session_state.member_id, st.session_state.trip_id))

# Sidebar UI
st.sidebar.title("🏍️ Group Trip & Ride Manager")

mem = current_member()
trip = get_trip_by_id(st.session_state.get("trip_id")) if st.session_state.get("trip_id") else None

if not mem or not trip:
    st.sidebar.subheader("Trip Access")
    tab_create, tab_join = st.sidebar.tabs(["Create Trip", "Join Trip"])
    
    with tab_create:
        with st.form("create_trip_form"):
            trip_name = st.text_input("Trip Name", value="Sahyadri Range Ride")
            dest_lat = st.number_input("Destination Latitude", value=19.3248, format="%.4f")
            dest_lon = st.number_input("Destination Longitude", value=73.7578, format="%.4f")
            leader_name = st.text_input("Your Name", value="Leader")
            bike_name = st.text_input("Your Vehicle / Bike", value="Suzuki Access 125")
            submitted = st.form_submit_button("Create Trip")
            if submitted:
                trip_id = secrets.token_urlsafe(12)
                invite_code = secrets.token_hex(3).upper()
                member_id = secrets.token_urlsafe(18)
                execute("INSERT INTO trips (id, name, invite_code, dest_lat, dest_lon, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (trip_id, trip_name, invite_code, dest_lat, dest_lon, now_iso()))
                execute("INSERT INTO members (id, trip_id, name, role, bike_name, member_role, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (member_id, trip_id, leader_name, "Rider", bike_name, "Leader", now_iso()))
                st.session_state.trip_id = trip_id
                st.session_state.member_id = member_id
                st.rerun()

    with tab_join:
        with st.form("join_trip_form"):
            invite_code = st.text_input("Invitation Code", max_chars=12).strip().upper()
            join_name = st.text_input("Your Name")
            join_role = st.selectbox("Your Role", ["Rider", "Pillion"])
            join_bike = st.text_input("Vehicle / Bike Name (optional)")
            join_submitted = st.form_submit_button("Join Trip")
            if join_submitted:
                t = get_trip_by_code(invite_code)
                if not t:
                    st.error("Invitation code not found.")
                elif not join_name.strip():
                    st.error("Please enter your name.")
                else:
                    existing = query_one("SELECT * FROM members WHERE trip_id = ? AND lower(name) = lower(?)", (t["id"], join_name.strip()))
                    if existing:
                        st.error("A member with that name already exists in this trip.")
                    else:
                        member_id = secrets.token_urlsafe(18)
                        execute("INSERT INTO members (id, trip_id, name, role, bike_name, member_role, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (member_id, t["id"], join_name.strip(), join_role, join_bike, "Member", now_iso()))
                        st.session_state.trip_id = t["id"]
                        st.session_state.member_id = member_id
                        st.rerun()
    st.stop()

# Sidebar UI when logged in
st.sidebar.markdown(f"**Your Profile**\n\n{mem['name']}\n\n*{mem['role']} · {mem['member_role']}*")

if st.sidebar.button("🚨 EMERGENCY SOS / BREAKDOWN", type="primary"):
    execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (secrets.token_urlsafe(12), trip["id"], mem["id"], f"🚨 EMERGENCY SOS TRIGGERED by {mem['name']}! Immediate assistance required.", now_iso()))
    st.sidebar.error("SOS Alert Broadcasted to Convoy!")

st.sidebar.markdown(f"**Trip:** {trip['name']}")
st.sidebar.code(trip["invite_code"], language="text")
st.sidebar.caption("Share this invitation code with riders.")

if st.sidebar.button("🔄 Refresh trip data"):
    st.rerun()

if st.sidebar.button("🚪 Leave / switch trip"):
    st.session_state.pop("trip_id", None)
    st.session_state.pop("member_id", None)
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 📍 Share My Location")
st.sidebar.caption("Keep this page active to broadcast live GPS coordinates.")

# GPS component invocation
gps_data = GPS_COMPONENT(key=f"gps_{mem['id']}")
if gps_data and isinstance(gps_data, dict):
    lat = gps_data.get("lat")
    lon = gps_data.get("lon")
    acc = gps_data.get("accuracy")
    if lat and lon:
        execute("""
            INSERT INTO locations (trip_id, member_id, lat, lon, accuracy, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(trip_id, member_id) DO UPDATE SET
                lat = excluded.lat,
                lon = excluded.lon,
                accuracy = excluded.accuracy,
                updated_at = excluded.updated_at
        """, (trip["id"], mem["id"], lat, lon, acc, time.time()))

# Main Dashboard Navigation Tabs
tab_live, tab_rules, tab_budget, tab_alerts = st.tabs([
    "🗺️ Live Map & Leaderboard", 
    "📋 Rider & Pillion Instructions", 
    "💰 Budget & Expenses", 
    "🚨 Convoy Alerts"
])

with tab_live:
    col_map, col_rank = st.columns([2, 1])

    with col_map:
        st.markdown("### Map Layer")
        map_style = st.radio("Select layer", ["Satellite", "Street", "Light"], horizontal=True, label_visibility="collapsed")
        
        tile_url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        attr = "Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP"
        if map_style == "Street":
            tile_url = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attr = "&copy; OpenStreetMap contributors"
        elif map_style == "Light":
            tile_url = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
            attr = "&copy; CARTO"

        fmap = folium.Map(location=[trip["dest_lat"], trip["dest_lon"]], zoom_start=11, tiles=tile_url, attr=attr)
        
        # Destination marker
        folium.Marker(
            [trip["dest_lat"], trip["dest_lon"]],
            popup="Destination",
            tooltip="Destination",
            icon=folium.Icon(color="green", icon="flag", prefix="fa")
        ).add_to(fmap)

        locations = query_all("SELECT * FROM locations WHERE trip_id = ?", (trip["id"],))
        members = {m["id"]: m for m in get_members(trip["id"])}

        my_current_loc = None
        for loc in locations:
            m_info = members.get(loc["member_id"])
            if not m_info:
                continue
            if loc["member_id"] == mem["id"]:
                my_current_loc = (loc["lat"], loc["lon"])
            popup_text = f"<b>{m_info['name']}</b><br>{m_info['role']} ({m_info.get('bike_name', '')})"
            icon_color = "red" if loc["member_id"] == mem["id"] else "purple"
            icon_glyph = "motorcycle" if m_info["role"] == "Rider" else "user"
            folium.Marker(
                [loc["lat"], loc["lon"]],
                tooltip=m_info["name"],
                popup=popup_text,
                icon=folium.Icon(color=icon_color, icon=icon_glyph, prefix="fa")
            ).add_to(fmap)

        if my_current_loc:
            folium.PolyLine(
                [my_current_loc, [trip["dest_lat"], trip["dest_lon"]]],
                color="#2563eb",
                weight=4,
                opacity=0.8,
                tooltip="Direct Route to Destination"
            ).add_to(fmap)

        if locations:
            coords = [[x["lat"], x["lon"]] for x in locations] + [[trip["dest_lat"], trip["dest_lon"]]]
            fmap.fit_bounds(coords, padding=(20, 20))

        st_folium(
            fmap,
            use_container_width=True,
            height=520,
            key="shared_trip_map",
            returned_objects=[],
        )

        gmaps_url = f"https://www.google.com/maps/dir/?api=1&destination={trip['dest_lat']},{trip['dest_lon']}&travelmode=driving"
        st.markdown(f"""
            <div style="text-align: center; margin: 12px 0;">
                <a href="{gmaps_url}" target="_blank" style="background-color: #1a73e8; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 16px; display: inline-block; box-shadow: 0 4px 6px rgba(0,0,0,0.2);">
                    🧭 Open Turn-by-Turn Navigation in Google Maps
                </a>
            </div>
        """, unsafe_allow_html=True)

        st.caption("Distances shown are straight-line estimates to destination. Click above for real-time voice & road navigation.")

    with col_rank:
        st.subheader("🏁 Distance Leaderboard")
        if not locations:
            st.info("No members have shared GPS coordinates yet.")
        else:
            ranking = []
            now_ts = time.time()
            for loc in locations:
                m_info = members.get(loc["member_id"])
                if not m_info:
                    continue
                dist = distance_km(loc["lat"], loc["lon"], trip["dest_lat"], trip["dest_lon"])
                age = int(now_ts - loc["updated_at"])
                ranking.append((dist, age, m_info, loc))
            
            ranking.sort(key=lambda x: x[0])
            for idx, (dist, age, m_info, loc) in enumerate(ranking, 1):
                status_color = "🟢" if age < 60 else ("🟡" if age < 300 else "🔴")
                st.markdown(f"**#{idx} {m_info['name']}** · {status_color} {'recent' if age < 60 else f'{age}s ago'}")
                st.caption(f"{dist:.2f} km straight-line · updated {int(age/60)} min ago")
                st.markdown("---")

    st.markdown("### Trip Members")
    for mid, m_info in members.items():
        crown = "👑 " if m_info["member_role"] == "Leader" else "• "
        st.markdown(f"{crown}**{m_info['name']}** — {m_info['role']} ({m_info.get('bike_name', 'No vehicle specified')})")

with tab_rules:
    st.subheader("📋 Standard Operating Procedures & Instructions")
    
    col_r, col_p = st.columns(2)
    with col_r:
        st.markdown("### 🏍️ For Riders (Default SOP)")
        st.markdown("""
        * **Maintain Formation:** Keep a staggered riding formation on highways; single file on mountain switchbacks.
        * **Lead & Sweep:** Never overtake the designated ride leader unless instructed. The sweep rider stays at the rear.
        * **Fuel Checks:** Ensure fuel tanks are topped up before group departure.
        * **Mirrors & Signals:** Check mirrors frequently and use clear hand/indicator signals for potholes, livestock, or stops.
        """)
    with col_p:
        st.markdown("### 👥 For Pillions (Default SOP)")
        st.markdown("""
        * **Body Posture:** Lean with the rider smoothly into corners; avoid sudden shifting of weight.
        * **Hold Onto Rider / Grab Rails:** Maintain secure contact, especially on uneven terrain or unpaved roads.
        * **Communication:** Agree on a simple tap signal on the rider's shoulder or helmet for breaks or discomfort.
        * **Gear Up:** Always wear a proper helmet, jacket, and sturdy footwear.
        """)

    st.markdown("---")
    st.subheader("📝 Custom & Individual Instructions Board")
    
    # Form to add custom instruction
    with st.form("add_instruction_form"):
        st.markdown("#### Add Custom Instruction / Note")
        all_members = get_members(trip["id"])
        member_options = {"🌐 ALL MEMBERS (General)": "ALL"}
        for m in all_members:
            member_options[f"{m['name']} ({m['role']})"] = m['id']
            
        target_choice = st.selectbox("Assign To", options=list(member_options.keys()))
        inst_title = st.text_input("Instruction Title (e.g., Carry Extra Water, Lead Pacing)")
        inst_content = st.text_area("Instruction Details")
        
        submitted_inst = st.form_submit_button("Post Instruction")
        if submitted_inst:
            if inst_title.strip() and inst_content.strip():
                target_id = member_options[target_choice]
                execute("""
                    INSERT INTO instructions (id, trip_id, target_id, title, content, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (secrets.token_urlsafe(12), trip["id"], target_id, inst_title.strip(), inst_content.strip(), mem["name"], now_iso()))
                st.success("Instruction posted successfully!")
                st.rerun()
            else:
                st.error("Please fill in both the title and instruction details.")

    # Display custom instructions
    custom_instructions = query_all("SELECT * FROM instructions WHERE trip_id = ?", (trip["id"],))
    members_map = {m["id"]: f"{m['name']} ({m['role']})" for m in all_members}
    members_map["ALL"] = "🌐 All Members (General)"

    if custom_instructions:
        st.markdown("#### Posted Instructions & Notes")
        for inst in custom_instructions:
            assigned_to = members_map.get(inst["target_id"], "Unknown")
            st.info(f"**📌 {inst['title']}** (Assigned to: *{assigned_to}* by **{inst['created_by']}**)\n\n{inst['content']}")
    else:
        st.info("No custom instructions posted yet. Use the form above to assign notes for everyone or specific members!")

with tab_budget:
    st.subheader("💰 Trip Budget & Expense Splitting")
    
    with st.form("add_expense_form"):
        st.markdown("#### Add New Expense")
        exp_title = st.text_input("Expense Description (e.g., Breakfast at Wada, Fuel)")
        exp_amount = st.number_input("Amount (₹)", min_value=0.0, step=50.0)
        submitted_exp = st.form_submit_button("Add Expense")
        if submitted_exp:
            if exp_title.strip() and exp_amount > 0:
                execute("INSERT INTO expenses (id, trip_id, member_id, title, amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (secrets.token_urlsafe(12), trip["id"], mem["id"], exp_title.strip(), exp_amount, now_iso()))
                st.success("Expense added successfully!")
                st.rerun()
            else:
                st.error("Please enter a valid title and amount.")

    expenses = query_all("SELECT * FROM expenses WHERE trip_id = ?", (trip["id"],))
    members_map = {m["id"]: m["name"] for m in get_members(trip["id"])}

    if expenses:
        total_spent = sum(e["amount"] for e in expenses)
        total_members = len(members_map)
        per_person = total_spent / total_members if total_members > 0 else 0

        st.metric("Total Trip Expenditure", f"₹{total_spent:,.2f}", f"₹{per_person:,.2f} per person ({total_members} members)")
        
        st.markdown("#### Expense Log")
        for e in expenses:
            payer_name = members_map.get(e["member_id"], "Unknown")
            st.markdown(f"* **{e['title']}**: ₹{e['amount']:,.2f} (Paid by **{payer_name}**)")
    else:
        st.info("No expenses recorded yet. Add your fuel, toll, or food expenses above!")

with tab_alerts:
    st.subheader("🚨 Convoy Alerts & Broadcasts")
    
    st.markdown("#### Quick Broadcast Buttons")
    col_q1, col_q2, col_q3, col_q4 = st.columns(4)
    
    with col_q1:
        if st.button("☕ Tea / Rest Stop"):
            execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(12), trip["id"], mem["id"], "☕ Requesting a tea / rest stop ahead.", now_iso()))
            st.rerun()
    with col_q2:
        if st.button("⛽ Low Fuel"):
            execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(12), trip["id"], mem["id"], "⛽ Low fuel warning / Need fuel station stop.", now_iso()))
            st.rerun()
    with col_q3:
        if st.button("🔧 Puncture / Issue"):
            execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(12), trip["id"], mem["id"], "🔧 Mechanical breakdown / Puncture. Stop & assist.", now_iso()))
            st.rerun()
    with col_q4:
        if st.button("🐢 Regroup / Slow"):
            execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (secrets.token_urlsafe(12), trip["id"], mem["id"], "🐢 Slow down / Need to regroup convoy.", now_iso()))
            st.rerun()

    st.markdown("---")
    with st.form("send_alert_form"):
        alert_msg = st.text_input("Custom Broadcast Message")
        submitted_alert = st.form_submit_button("Broadcast Custom Alert")
        if submitted_alert:
            if alert_msg.strip():
                execute("INSERT INTO alerts (id, trip_id, member_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                        (secrets.token_urlsafe(12), trip["id"], mem["id"], alert_msg.strip(), now_iso()))
                st.success("Alert broadcasted to all members!")
                st.rerun()
            else:
                st.error("Alert message cannot be empty.")

    alerts = query_all("SELECT * FROM alerts WHERE trip_id = ? ORDER BY created_at DESC", (trip["id"],))
    members_map = {m["id"]: m["name"] for m in get_members(trip["id"])}

    if alerts:
        st.markdown("#### Recent Broadcasts & SOS Alerts")
        for a in alerts:
            sender = members_map.get(a["member_id"], "Unknown")
            if "EMERGENCY" in a["message"]:
                st.error(f"**{sender}**: {a['message']}")
            else:
                st.warning(f"**{sender}**: {a['message']}")
    else:
        st.info("No active emergency or convoy alerts.")
