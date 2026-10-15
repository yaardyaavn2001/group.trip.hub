# Group Trip Hub — Streamlit starter

A multi-member trip coordination prototype based on the supplied Streamlit app. Includes:
- Trip creation and invite code joining
- Rider/pillion profiles
- Shared GPS locations (browser permission required)
- Satellite/street map and distance-to-destination leaderboard
- Leader/admin instructions and role-targeted assignments
- Group alert feed with optional location
- Assembly/regroup points and Google Maps navigation links
- Shared expense log and co-admin promotion

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the HTTPS deployment URL on each phone, join with the trip code, and tap **Start sharing location** in the sidebar. Location sharing requires browser permission.

## Deploy on Streamlit Community Cloud

1. Upload `app.py`, `requirements.txt`, and the `gps_component/` folder to a GitHub repository.
2. Create a Streamlit Community Cloud app pointing at `app.py`.
3. Ensure the deployment uses HTTPS (the standard Streamlit URL does).
4. Share the trip code with members.
5. Test using two separate browsers/devices.

## Important limitations — read before a real ride

**This version deliberately uses SQLite/local storage as requested.** On one running server process, all visitors generally read/write the same SQLite file. However, Streamlit Community Cloud's local filesystem is not a dependable permanent database: files may be reset on rebuild/restart, and this setup is not suitable for high-reliability real-time group tracking. For durable data, use a hosted database such as Supabase/Postgres. Do not rely on this prototype for emergency communications.

- Streamlit reruns and browser scheduling mean location updates are not guaranteed to be instantaneous.
- This page does not provide push notifications. Members see new alerts when their page refreshes/reruns.
- Browser GPS can stop when the page is backgrounded, the phone is locked, or the operating system suspends the browser.
- The leaderboard uses straight-line distance to destination, not road distance along a route.
- Invite codes are access codes, not strong identity verification. Anyone with the code may join. Do not store sensitive information.
- The browser component uses the Streamlit component messaging protocol. Test it in your target browsers before group use.
- Use the phone only while stopped safely. Never operate the app while riding.

## Security / storage

No API keys are required by this version. The database path can be changed with `GROUP_TRIP_DB`. Do not expose the SQLite database publicly or commit it to GitHub. Add `trip_hub.sqlite3*` to `.gitignore`.

## Smoke-test checklist

1. Create a trip with a unique destination and note the invitation code.
2. Open a second browser/incognito window and join using the code.
3. Start GPS sharing on both devices; confirm both pins appear after app reruns/refresh.
4. Send a pothole alert; verify it appears in the shared alerts feed on the second device.
5. As leader, publish an instruction to All Riders; verify a rider sees it and a pillion does not.
6. Add an assembly point; verify it appears on the map and has a navigation link.
7. Promote a member to admin; verify the admin can publish instructions and manage assembly points.
8. Add an expense; verify the total updates.
9. Stop sharing and remove the member's last location if needed.

## Notes

This is a practical prototype, not a production-certified tracking system. Before relying on it for a large group, move storage to a managed database, add proper sign-in and row-level access control, add realtime subscriptions/push notifications, and run end-to-end tests on the actual deployment.
