# Deploying on a VPS

This puts the app on an Ubuntu VPS. The Python server runs as a service and serves HTTPS itself on port 8443, with a self-signed certificate.
There is no web server in front, so ports 80 and 443 stay free for whatever else runs on the machine.
It uses the demo pack (simulated data and the already built app), so there is no multi-hour build and no Node.js. It is not set up for real
patient data; see "Running it for real users" in the main README first.

You can also run the app without this folder (`python server.py`). The folder only saves you the typing.

## Steps

1. On the VPS, as root, get the code. If the repository is public:

   ```
   git clone https://github.com/Smarttechk/Hack2Skill_Precision-Prevention.git /opt/hack2skill/app
   ```

   If it is private, add a read-only deploy key first (GitHub, Settings, Deploy keys) and clone with the SSH address.

2. Run the setup with the VPS address (or your domain). A second argument changes the port:

   ```
   cd /opt/hack2skill/app
   sh deployment/setup.sh YOUR_VPS_IP
   ```

   It asks for the name and password of the first admin account. It will not go on without one, because with no accounts the app opens to
   everyone as an admin. It stops if the port is already in use.

3. Open `https://YOUR_VPS_IP:8443`. With only an IP the certificate is self-signed, so the browser warns once. If the page does not load,
   allow the port in the provider's firewall panel.

4. Check it: `curl -k https://YOUR_VPS_IP:8443/api/health`. It says "not ready" in the demo, because the hospital records database
   (842 MB) is not in the repository. Everything else works.
## Afterwards

- Update: `sh /opt/hack2skill/app/deployment/update.sh`
- Logs: `journalctl -u hack2skill -n 100 --no-pager`
- Care Assistant: `venv/bin/pip install -r requirements-assistant.txt`, put `ANTHROPIC_API_KEY` in `/etc/hack2skill.env`, then `systemctl restart hack2skill`.
  Leave the key empty and it stays off.
- Audit key: set `PP_AUDIT_KEY` in `/etc/hack2skill.env` (`openssl rand -hex 32`) and note the log's head hash from time to time (`scripts/audit_head.py`).
- A real certificate, if you have a domain: put the certificate and key files in /etc/ssl/hack2skill/ as cert.pem and key.pem (owned by hack2skill) and restart the service.
- Demo accounts for the other roles: `venv/bin/python scripts/add_user.py --demo` (passwords are written outside the project; remove these accounts before real use).

## What this does not cover

- The 842 MB hospital records database is not in the repository, so the FHIR API, patient names and past visits need the full build (`run_all.py`).
- Run one server process only. Sessions, sign-in limits and the access log are files under `outputs/`.
- We wrote these scripts without a VPS to try them on. Run them once on a throwaway server first and read the output.
