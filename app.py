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

# -------------------------- App configuration --------------------------
st.set_page_config(
    page_title="Group Trip Hub",
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

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.1rem; padding-bottom: 2rem;}
      div.stButton > button {width:100%; min-height:2.7rem; font-weight:600;}
      .small-muted {font-size:0.85rem; opacity:0.78;}
      [data-testid="stMetric"] {border:1px solid rgba(128,128,128,.22); padding:12px; border-radius:12px;}
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------- Database --------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  destination TEXT NOT NULL,
  dest_lat REAL NOT NULL,
  dest_lon REAL NOT NULL,
  trip_date TEXT DEFAULT '',
  invite_code TEXT UNIQUE NOT NULL,
  leader_member_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('Rider','Pillion')),
  bike_name TEXT DEFAULT '',
  member_role TEXT NOT NULL DEFAULT 'member' CHECK(member_role IN ('leader','admin','member')),
  joined_at TEXT NOT NULL,
  UNIQUE(trip_id, name),
  FOREIGN KEY(trip_id) REFERENCES trips(id)
);
CREATE TABLE IF NOT EXISTS locations (
  member_id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  accuracy REAL,
  speed REAL,
  heading REAL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(member_id) REFERENCES members(id)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  member_name TEXT NOT NULL,
  alert_type TEXT NOT NULL,
  message TEXT DEFAULT '',
  lat REAL,
  lon REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instructions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  created_by TEXT NOT NULL,
  target_member_id TEXT,
  target_role TEXT NOT NULL DEFAULT 'Everyone',
  title TEXT NOT NULL,
  details TEXT NOT NULL,
  created_at TEXT NOT NULL,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS assembly_points (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  created_by TEXT NOT NULL,
  name TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  paid_by TEXT NOT NULL,
  amount REAL NOT NULL,
  item TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  item TEXT NOT NULL,
  checked INTEGER NOT NULL DEFAULT 0,
  UNIQUE(trip_id, member_id, item)
);
"""

def db():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def query_all(sql, params=()):
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

def query_one(sql, params=()):
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

def execute(sql, params=()):
    with db() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

def distance_km(lat1, lon1, lat2, lon2):
    earth = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return earth * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))

def fmt_time(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b %H:%M:%S")
    except Exception:
        return value or "Never"

def get_trip_by_code(code):
    return query_one("SELECT * FROM trips WHERE upper(invite_code)=upper(?)", (code.strip(),))

def get_members(trip_id):
    return query_all("SELECT * FROM members WHERE trip_id=? ORDER BY joined_at", (trip_id,))

def current_member():
    if not st.session_state.get("trip_id") or not st.session_state.get("member_id"):
        return None
    return query_one("SELECT * FROM members WHERE id=? AND trip_id=?", (st.session_state.member_id, st.session_state.trip_id))

def is_admin(member):
    return bool(member and member.get("member_role") in ("leader", "admin"))

def rerun():
    st.rerun()

# -------------------------- Entry / trip join --------------------------
if "member_id" not in st.session_state:
    st.session_state.member_id = None
if "trip_id" not in st.session_state:
    st.session_state.trip_id = None

st.title("🏍️ Group Trip Hub")
st.caption("Shared trip coordination • GPS location • alerts • instructions • assembly points")

member = current_member()
if not member:
    st.session_state.member_id = None
    st.session_state.trip_id = None

    create_tab, join_tab = st.tabs(["➕ Create a trip", "🔗 Join a trip"])
    with create_tab:
        st.subheader("Create a new group trip")
        with st.form("create_trip_form"):
            trip_name = st.text_input("Trip name", placeholder="e.g. Thane → Malshej Ghat Ride")
            destination = st.text_input("Destination name", placeholder="e.g. Malshej Ghat Viewpoint")
            c1, c2 = st.columns(2)
            with c1:
                dest_lat = st.number_input("Destination latitude", value=19.2183, format="%.6f")
            with c2:
                dest_lon = st.number_input("Destination longitude", value=73.7289, format="%.6f")
            trip_date = st.text_input("Trip date (optional)", placeholder="e.g. 18 Oct 2026")
            st.markdown("**Your leader profile**")
            leader_name = st.text_input("Your name", placeholder="Name visible to the group")
            leader_role = st.selectbox("Your role", ["Rider", "Pillion"])
            bike_name = st.text_input("Bike / assigned rider (optional)", placeholder="e.g. MH-04 AB 1234")
            create = st.form_submit_button("Create trip and become leader")
        if create:
            if not trip_name.strip() or not destination.strip() or not leader_name.strip():
                st.error("Trip name, destination, and your name are required.")
            elif not (-90 <= dest_lat <= 90 and -180 <= dest_lon <= 180):
                st.error("Please enter valid destination coordinates.")
            else:
                trip_id = secrets.token_urlsafe(12)
                member_id = secrets.token_urlsafe(18)
                invite_code = secrets.token_hex(3).upper()
                with db() as conn:
                    # Avoid the very unlikely chance of a duplicate invitation code.
                    while conn.execute("SELECT 1 FROM trips WHERE invite_code=?", (invite_code,)).fetchone():
                        invite_code = secrets.token_hex(3).upper()
                    conn.execute(
                        "INSERT INTO trips(id,name,destination,dest_lat,dest_lon,trip_date,invite_code,leader_member_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (trip_id, trip_name.strip(), destination.strip(), float(dest_lat), float(dest_lon),
                         trip_date.strip(), invite_code, member_id, now_iso())
                    )
                    conn.execute(
                        "INSERT INTO members(id,trip_id,name,role,bike_name,member_role,joined_at) VALUES(?,?,?,?,?,?,?)",
                        (member_id, trip_id, leader_name.strip(), leader_role, bike_name.strip(), "leader", now_iso())
                    )
                    conn.commit()
                st.session_state.trip_id = trip_id
                st.session_state.member_id = member_id
                st.success(f"Trip created. Invitation code: {invite_code}")
                rerun()

    with join_tab:
        st.subheader("Join an existing trip")
        st.write("Ask the trip leader for the invitation code.")
        with st.form("join_trip_form"):
            invite_code = st.text_input("Trip invitation code", max_chars=12).strip().upper()
            join_name = st.text_input("Your name")
            join_role = st.selectbox("Your role", ["Rider", "Pillion"])
            join_bike = st.text_input("Bike / assigned rider (optional)")
            join = st.form_submit_button("Join trip")
        if join:
            trip = get_trip_by_code(invite_code)
            if not trip:
                st.error("That invitation code was not found. Check the code with your trip leader.")
            elif not join_name.strip():
                st.error("Please enter your name.")
            else:
                existing = query_one("SELECT * FROM members WHERE trip_id=? AND lower(name)=lower(?)",
                                     (trip["id"], join_name.strip()))
                if existing:
                    st.error("A member with that name is already in this trip. Ask the leader if this is your account.")
                else:
                    member_id = secrets.token_urlsafe(18)
                    execute(
                        "INSERT INTO members(id,trip_id,name,role,bike_name,member_role,joined_at) VALUES(?,?,?,?,?,?,?)",
                        (member_id, trip["id"], join_name.strip(), join_role, join_bike.strip(), "member", now_iso())
                    )
                    st.session_state.trip_id = trip["id"]
                    st.session_state.member_id = member_id
                    rerun()
    st.info("Privacy note: locations are shared with everyone in this trip while you use the app. Join only a trip you trust.")
    st.stop()

trip = query_one("SELECT * FROM trips WHERE id=?", (member["trip_id"],))
if not trip:
    st.error("This trip could not be found. Please rejoin with its invitation code.")
    st.stop()

# -------------------------- Sidebar profile and location --------------------------
with st.sidebar:
    st.header("👤 Your profile")
    st.write(f"**{member['name']}**")
    st.caption(f"{member['role']} • {member['member_role'].title()}")
    st.write(f"**Trip:** {trip['name']}")
    st.code(trip["invite_code"], language=None)
    st.caption("Share this invitation code with your group.")
    if st.button("🔄 Refresh trip data"):
        rerun()
    if st.button("🚪 Leave / switch trip"):
        st.session_state.member_id = None
        st.session_state.trip_id = None
        rerun()
    st.divider()
    st.subheader("📡 Share my location")
    st.caption("Allow location access. Keep this page open for updates; phones may pause GPS when the page is in the background.")
    gps_value = GPS_COMPONENT(key="gps", default=None)
    if isinstance(gps_value, dict) and gps_value.get("lat") is not None and gps_value.get("lon") is not None:
        try:
            lat, lon = float(gps_value["lat"]), float(gps_value["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                execute(
                    """INSERT INTO locations(member_id,trip_id,lat,lon,accuracy,speed,heading,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(member_id) DO UPDATE SET trip_id=excluded.trip_id, lat=excluded.lat,
                       lon=excluded.lon, accuracy=excluded.accuracy, speed=excluded.speed,
                       heading=excluded.heading, updated_at=excluded.updated_at""",
                    (member["id"], trip["id"], lat, lon, gps_value.get("accuracy"),
                     gps_value.get("speed"), gps_value.get("heading"), now_iso())
                )
                st.success(f"Location shared: {lat:.5f}, {lon:.5f}")
        except (ValueError, TypeError):
            st.warning("GPS returned invalid coordinates.")
    if st.button("Stop sharing location"):
        execute("DELETE FROM locations WHERE member_id=?", (member["id"],))
        st.info("Your last location was removed.")
        rerun()

# -------------------------- Fetch shared data --------------------------
members = get_members(trip["id"])
locations = query_all(
    """SELECT l.*, m.name, m.role, m.bike_name, m.member_role
       FROM locations l JOIN members m ON m.id=l.member_id
       WHERE l.trip_id=?""", (trip["id"],)
)
alerts = query_all("SELECT * FROM alerts WHERE trip_id=? ORDER BY id DESC LIMIT 50", (trip["id"],))
instructions = query_all(
    """SELECT i.*, m.name AS target_name FROM instructions i
       LEFT JOIN members m ON m.id=i.target_member_id
       WHERE i.trip_id=? ORDER BY i.id DESC LIMIT 100""", (trip["id"],)
)
assembly_points = query_all("SELECT * FROM assembly_points WHERE trip_id=? ORDER BY id DESC", (trip["id"],))
is_manager = is_admin(member)

# -------------------------- Header and tabs --------------------------
head1, head2 = st.columns([4, 1])
with head1:
    st.subheader(f"🧭 {trip['name']}")
    st.caption(f"Destination: {trip['destination']}" + (f" • {trip['trip_date']}" if trip["trip_date"] else ""))
with head2:
    st.metric("Members", len(members))

map_tab, alerts_tab, instructions_tab, assembly_tab, expenses_tab, admin_tab = st.tabs([
    "🗺️ Live map & leaderboard", "🚨 Alerts", "📋 Instructions", "📍 Assembly points", "💰 Expenses", "⚙️ Trip admin"
])

# -------------------------- Map and leaderboard --------------------------
with map_tab:
    col_map, col_rank = st.columns([1.7, 1])
    with col_map:
        st.subheader("Shared location map")
        map_layer = st.radio("Map layer", ["Satellite", "Street", "Light"], horizontal=True, key="map_layer")
        if map_layer == "Satellite":
            fmap = folium.Map(location=[trip["dest_lat"], trip["dest_lon"]], zoom_start=10, tiles=None)
            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                attr="Esri World Imagery", name="Satellite", overlay=False, control=True
            ).add_to(fmap)
        elif map_layer == "Street":
            fmap = folium.Map(location=[trip["dest_lat"], trip["dest_lon"]], zoom_start=10, tiles="OpenStreetMap")
        else:
            fmap = folium.Map(location=[trip["dest_lat"], trip["dest_lon"]], zoom_start=10, tiles="CartoDB positron")
        folium.Marker([trip["dest_lat"], trip["dest_lon"]], tooltip=f"Destination: {trip['destination']}",
                      popup=f"Destination: {trip['destination']}",
                      icon=folium.Icon(color="green", icon="flag")).add_to(fmap)
        for point in assembly_points:
            folium.Marker([point["lat"], point["lon"]], tooltip=f"Assembly: {point['name']}",
                          popup=f"{point['name']} — {point['notes']}",
                          icon=folium.Icon(color="blue", icon="users", prefix="fa")).add_to(fmap)
        for loc in locations:
            dist_dest = distance_km(loc["lat"], loc["lon"], trip["dest_lat"], trip["dest_lon"])
            popup = (f"{loc['name']} ({loc['role']})<br>As-the-crow-flies to destination: {dist_dest:.2f} km"
                     f"<br>Updated: {fmt_time(loc['updated_at'])}")
            folium.Marker([loc["lat"], loc["lon"]], tooltip=loc["name"], popup=popup,
                          icon=folium.Icon(color="red" if loc["member_id"] != member["id"] else "purple",
                                           icon="motorcycle" if loc["role"] == "Rider" else "user",
                                           prefix="fa")).add_to(fmap)
        if locations:
            coords = [[x["lat"], x["lon"]] for x in locations] + [[trip["dest_lat"], trip["dest_lon"]]]
            fmap.fit_bounds(coords, padding=(20, 20))
st_folium(
         fmap,
         use_container_width=True,
         height=520,
         key="shared_trip_map",
         returned_objects=[],
         returned_key=None,
     )
st.caption("Distances shown are straight-line estimates, not road-route distances. Map tiles require internet access.")
    with col_rank:
        st.subheader("🏁 Distance leaderboard")
        if not locations:
            st.info("No member has shared a GPS location yet.")
        else:
            ranking = []
            now_ts = time.time()
            for loc in locations:
                km = distance_km(loc["lat"], loc["lon"], trip["dest_lat"], trip["dest_lon"])
                try:
                    updated = datetime.fromisoformat(loc["updated_at"]).timestamp()
                    age = max(0, int(now_ts - updated))
                except Exception:
                    age = 999999
                ranking.append({**loc, "dist": km, "age": age})
            ranking.sort(key=lambda x: x["dist"])
            for i, item in enumerate(ranking, 1):
                stale = item["age"] > 180
                st.markdown(f"**#{i} {item['name']}**" + (" · ⚠️ stale" if stale else " · 🟢 recent"))
                st.caption(f"{item['dist']:.2f} km straight-line to destination • updated {item['age']//60} min ago")
                st.progress(max(0.0, min(1.0, 1.0 - item["dist"] / max(1.0, max(x["dist"] for x in ranking)))))
        st.divider()
        st.subheader("Members")
        for mbr in members:
            role_tag = "👑" if mbr["member_role"] == "leader" else ("🛡️" if mbr["member_role"] == "admin" else "•")
            st.write(f"{role_tag} {mbr['name']} — {mbr['role']}" + (f" ({mbr['bike_name']})" if mbr["bike_name"] else ""))

# -------------------------- Alerts --------------------------
with alerts_tab:
    st.subheader("Send a group alert")
    st.caption("All active trip members can submit alerts. Use these only when safe to do so; do not handle your phone while riding.")
    alert_types = ["👮 Police checking", "⚠️ Pothole / road hazard", "🚦 Heavy traffic", "🆘 Accident / emergency",
                   "🔧 Breakdown / puncture", "☕ Tea / rest stop", "⛽ Fuel stop", "📍 Assemble at a point", "ℹ️ Other"]
    with st.form("send_alert_form", clear_on_submit=True):
        alert_type = st.selectbox("Alert type", alert_types)
        alert_message = st.text_input("Extra details (optional)", max_chars=250)
        attach_location = st.checkbox("Attach my latest shared GPS location", value=True)
        send_alert = st.form_submit_button("🚨 Send alert to trip")
    if send_alert:
        loc = query_one("SELECT lat,lon FROM locations WHERE member_id=?", (member["id"],)) if attach_location else None
        execute(
            "INSERT INTO alerts(trip_id,member_id,member_name,alert_type,message,lat,lon,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (trip["id"], member["id"], member["name"], alert_type, alert_message.strip(),
             loc["lat"] if loc else None, loc["lon"] if loc else None, now_iso())
        )
        st.success("Alert saved to the shared trip feed. Members can see it when their app refreshes.")
        rerun()
    st.divider()
    st.subheader("Recent group alerts")
    if not alerts:
        st.info("No alerts yet.")
    for alert in alerts:
        with st.container(border=True):
            st.markdown(f"**{alert['alert_type']}**")
            st.write(alert["message"] or "No extra details.")
            st.caption(f"From {alert['member_name']} • {fmt_time(alert['created_at'])}")
            if alert["lat"] is not None and alert["lon"] is not None:
                st.markdown(f"[Open alert location in Google Maps](https://www.google.com/maps?q={alert['lat']},{alert['lon']})")

# -------------------------- Instructions --------------------------
with instructions_tab:
    st.subheader("Trip instructions and duties")
    if is_manager:
        with st.form("instruction_form", clear_on_submit=True):
            target_mode = st.radio("Assign to", ["Everyone", "Specific member", "All Riders", "All Pillions"], horizontal=True)
            target_member_id = None
            if target_mode == "Specific member":
                chosen = st.selectbox("Member", members, format_func=lambda x: f"{x['name']} ({x['role']})")
                target_member_id = chosen["id"]
            title = st.text_input("Instruction title", placeholder="e.g. Keep 100 m gap")
            details = st.text_area("Details", placeholder="Explain what the group/member should do")
            post_instruction = st.form_submit_button("Publish instruction")
        if post_instruction:
            if not title.strip() or not details.strip():
                st.error("Enter both a title and details.")
            else:
                execute(
                    "INSERT INTO instructions(trip_id,created_by,target_member_id,target_role,title,details,created_at) VALUES(?,?,?,?,?,?,?)",
                    (trip["id"], member["id"], target_member_id, target_mode, title.strip(), details.strip(), now_iso())
                )
                st.success("Instruction published.")
                rerun()
    else:
        st.info("Only the trip leader and promoted co-admins can publish instructions.")
    visible_instructions = []
    for item in instructions:
        if item["target_role"] == "Everyone":
            visible_instructions.append(item)
        elif item["target_role"] == "Specific member" and item["target_member_id"] == member["id"]:
            visible_instructions.append(item)
        elif item["target_role"] == "All Riders" and member["role"] == "Rider":
            visible_instructions.append(item)
        elif item["target_role"] == "All Pillions" and member["role"] == "Pillion":
            visible_instructions.append(item)
    if not visible_instructions:
        st.info("No instructions for you yet.")
    for item in visible_instructions:
        with st.container(border=True):
            st.markdown(f"**{item['title']}**")
            st.write(item["details"])
            st.caption(f"Published {fmt_time(item['created_at'])}")
            if item["target_member_id"]:
                st.caption(f"Assigned to: {item['target_name'] or 'member'}")
            if item["created_by"] == member["id"] or is_manager:
                c1, c2 = st.columns(2)
                if c1.button("Mark acknowledged", key=f"ack_{item['id']}"):
                    execute("UPDATE instructions SET acknowledged=1 WHERE id=? AND trip_id=?", (item["id"], trip["id"]))
                    rerun()
                if c2.button("Mark completed", key=f"done_{item['id']}"):
                    execute("UPDATE instructions SET completed=1 WHERE id=? AND trip_id=?", (item["id"], trip["id"]))
                    rerun()
            status = "Completed" if item["completed"] else ("Acknowledged" if item["acknowledged"] else "Not acknowledged")
            st.caption(f"Status: {status}")

# -------------------------- Assembly points --------------------------
with assembly_tab:
    st.subheader("Group assembly / regroup points")
    if is_manager:
        st.markdown("Leader/admin can create a regroup point. Coordinates can be copied from a map app.")
        with st.form("assembly_form", clear_on_submit=True):
            point_name = st.text_input("Point name", placeholder="e.g. Fuel station before the ghat")
            p1, p2 = st.columns(2)
            with p1:
                point_lat = st.number_input("Latitude", value=float(trip["dest_lat"]), format="%.6f", key="point_lat")
            with p2:
                point_lon = st.number_input("Longitude", value=float(trip["dest_lon"]), format="%.6f", key="point_lon")
            point_notes = st.text_input("Instructions / notes", placeholder="Wait here until everyone arrives")
            add_point = st.form_submit_button("Add assembly point")
        if add_point:
            if not point_name.strip():
                st.error("Enter a point name.")
            elif not (-90 <= point_lat <= 90 and -180 <= point_lon <= 180):
                st.error("Enter valid coordinates.")
            else:
                execute("INSERT INTO assembly_points(trip_id,created_by,name,lat,lon,notes,created_at) VALUES(?,?,?,?,?,?,?)",
                        (trip["id"], member["id"], point_name.strip(), float(point_lat), float(point_lon), point_notes.strip(), now_iso()))
                rerun()
    else:
        st.info("Only the leader and co-admins can create assembly points.")
    if not assembly_points:
        st.info("No assembly points added yet.")
    for point in assembly_points:
        with st.container(border=True):
            st.markdown(f"**📍 {point['name']}**")
            st.write(point["notes"] or "No extra instructions.")
            st.caption(f"{point['lat']:.6f}, {point['lon']:.6f}")
            st.markdown(f"[Navigate with Google Maps](https://www.google.com/maps/dir/?api=1&destination={point['lat']},{point['lon']})")
            if is_manager and st.button("Delete point", key=f"del_point_{point['id']}"):
                execute("DELETE FROM assembly_points WHERE id=? AND trip_id=?", (point["id"], trip["id"]))
                rerun()

# -------------------------- Expenses --------------------------
with expenses_tab:
    st.subheader("Group expense log")
    with st.form("expense_form", clear_on_submit=True):
        amount = st.number_input("Amount (₹)", min_value=0.0, step=10.0)
        item = st.text_input("Expense type / details", placeholder="Fuel, toll, food, stay...")
        add_expense = st.form_submit_button("Add expense")
    if add_expense:
        if amount <= 0 or not item.strip():
            st.error("Enter a positive amount and expense details.")
        else:
            execute("INSERT INTO expenses(trip_id,member_id,paid_by,amount,item,created_at) VALUES(?,?,?,?,?,?)",
                    (trip["id"], member["id"], member["name"], float(amount), item.strip(), now_iso()))
            rerun()
    exp = query_all("SELECT paid_by AS 'Paid by', amount AS 'Amount (₹)', item AS 'Item', created_at AS 'Time' FROM expenses WHERE trip_id=? ORDER BY id DESC", (trip["id"],))
    if exp:
        df = pd.DataFrame(exp)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.metric("Total group expenses", f"₹{df['Amount (₹)'].sum():,.2f}")
    else:
        st.info("No expenses recorded yet.")

# -------------------------- Admin controls --------------------------
with admin_tab:
    st.subheader("Trip administration")
    st.write(f"**Invitation code:** `{trip['invite_code']}`")
    st.caption("Anyone who has the code can attempt to join. Share it privately. This starter app does not provide strong verified identities.")
    if is_manager:
        st.markdown("#### Promote or demote co-admins")
        for mbr in members:
            if mbr["member_role"] == "leader":
                st.write(f"👑 **{mbr['name']}** — trip leader")
                continue
            c1, c2 = st.columns([3, 1])
            with c1:
                st.write(f"{mbr['name']} — {mbr['role']} — {mbr['member_role']}")
            with c2:
                if mbr["member_role"] == "admin":
                    if st.button("Remove admin", key=f"demote_{mbr['id']}"):
                        execute("UPDATE members SET member_role='member' WHERE id=? AND trip_id=?", (mbr["id"], trip["id"]))
                        rerun()
                else:
                    if st.button("Make admin", key=f"promote_{mbr['id']}"):
                        execute("UPDATE members SET member_role='admin' WHERE id=? AND trip_id=?", (mbr["id"], trip["id"]))
                        rerun()
        st.divider()
        st.markdown("#### Trip details")
        with st.form("edit_trip_form"):
            new_name = st.text_input("Trip name", value=trip["name"])
            new_destination = st.text_input("Destination", value=trip["destination"])
            e1, e2 = st.columns(2)
            with e1:
                new_lat = st.number_input("Destination latitude", value=float(trip["dest_lat"]), format="%.6f", key="edit_dest_lat")
            with e2:
                new_lon = st.number_input("Destination longitude", value=float(trip["dest_lon"]), format="%.6f", key="edit_dest_lon")
            save_trip = st.form_submit_button("Save trip details")
        if save_trip:
            if not new_name.strip() or not new_destination.strip() or not (-90 <= new_lat <= 90 and -180 <= new_lon <= 180):
                st.error("Please enter valid trip details.")
            else:
                execute("UPDATE trips SET name=?,destination=?,dest_lat=?,dest_lon=? WHERE id=?",
                        (new_name.strip(), new_destination.strip(), float(new_lat), float(new_lon), trip["id"]))
                rerun()
    else:
        st.info("Only the trip leader and co-admins can manage trip settings and co-admins.")

st.divider()
st.caption("Group Trip Hub prototype • Refresh the page to fetch the latest shared data. Automatic push notifications and background tracking are not provided by this Streamlit-only version.")
