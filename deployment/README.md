# Deploying on a VPS

This puts the app on an Ubuntu VPS: the Python server runs as a service on 127.0.0.1:8000, and nginx serves it over HTTPS on ports 80 and 443.
It uses the demo pack (simulated data and the already built app), so there is no multi-hour build and no Node.js. It is not set up for real
patient data; see "Running it for real users" in the main README first.

You can also run the app without this folder (`python server.py` behind any proxy). The folder only saves you the typing.

## Steps

1. On the VPS, as root, get the code. If the repository is public:

   ```
   git clone https://github.com/Smarttechk/Hack2Skill_Precision-Prevention.git /opt/hack2skill/app
   ```

   If it is private, add a read-only deploy key first (GitHub, Settings, Deploy keys) and clone with the SSH address.

2. Run the setup, giving the VPS address (or your domain):

   ```
   cd /opt/hack2skill/app
   sh deployment/setup.sh YOUR_VPS_IP
   ```

   It asks for the name and password of the first admin account. It will not go on without one, because with no accounts the app opens to
   everyone as an admin.

3. Open `https://YOUR_VPS_IP`. With only an IP the certificate is self-signed, so the browser warns once. Passwords travel over HTTPS either way.

4. Check it: `curl -k https://YOUR_VPS_IP/api/health` (200 when ready).

## Afterwards

- Update: `sh /opt/hack2skill/app/deployment/update.sh`
- Logs: `journalctl -u hack2skill -n 100 --no-pager`
- Care Assistant: `venv/bin/pip install -r requirements-assistant.txt`, put `ANTHROPIC_API_KEY` in `/etc/hack2skill.env`, then `systemctl restart hack2skill`.
  Leave the key empty and it stays off.
- Audit key: set `PP_AUDIT_KEY` in `/etc/hack2skill.env` (`openssl rand -hex 32`) and note the log's head hash from time to time (`scripts/audit_head.py`).
- A real certificate, if you have a domain pointing at the VPS: `apt install certbot python3-certbot-nginx`, then `certbot --nginx -d YOUR_DOMAIN`.
- Demo accounts for the other roles: `venv/bin/python scripts/add_user.py --demo` (passwords are written outside the project; remove these accounts before real use).

## What this does not cover

- The 842 MB hospital records database is not in the repository, so the FHIR API, patient names and past visits need the full build (`run_all.py`).
- Run one server process only. Sessions, sign-in limits and the access log are files under `outputs/`.
- We wrote these scripts without a VPS to try them on. Run them once on a throwaway server first and read the output.
