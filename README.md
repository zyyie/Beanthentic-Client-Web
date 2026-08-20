# Flask Website (Guimaras Style)

Simple Flask website inspired by your reference design.

## Setup

1. Create a virtual environment (optional but recommended):
   - Windows PowerShell:
     - `python -m venv .venv`
     - `.venv\Scripts\Activate.ps1`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Run the Flask app:
   - **While coding (recommended):** double-click `run.bat` or `scripts\run-dev.bat`
   - **Phone on same Wi-Fi:** `scripts\run-for-phone.bat`
   - Or: `python web.py` (auto-reload enabled by default)
4. Open on this computer:
   - [http://127.0.0.1:5001](http://127.0.0.1:5001)

**If changes do not show:** use `run.bat` (not an old terminal), open `127.0.0.1:5001` (not `settings.json` `app_server_base` port 8080), then refresh.

## Access from your phone (same Wi-Fi as the laptop)

1. **Once (admin):** double-click `scripts\allow-lan-access.bat` → click **Yes** (opens firewall + sets Wi-Fi to Private).
2. **Every time:** double-click `scripts\run-for-phone.bat` (or `python web.py`).
3. On the phone (same Wi-Fi, **mobile data off**), open the `http://192.168.x.x:5001/` URL from the terminal.
4. **Test first:** `http://192.168.x.x:5001/api/lan-ping` — if you see JSON `"ok": true`, the phone reached the laptop.

**Common mistake:** laptop on `192.168.0.x` but phone/PC/XAMPP on `192.168.100.x` — different Wi-Fi networks. Use the **laptop** IP, not `settings.json` `app_db_host`.

**Router (TP-LINK):** turn off **AP Isolation** / **Client Isolation** if the phone still cannot connect.