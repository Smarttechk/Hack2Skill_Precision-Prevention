"""
Runs the web app: the API at /api and the React frontend at /.

    python server.py                 then open http://127.0.0.1:8000   (not "localhost": on Windows that waits about 2 seconds to reach the server)
    python server.py --port 9000
    python server.py --host 0.0.0.0 --certfile config/tls/cert.pem --keyfile config/tls/key.pem   (HTTPS on the network)
Sign-in: python scripts/add_user.py NAME ROLE creates accounts (see api/auth.py).

Build the frontend once first:  cd frontend && npm install && npm run build
For frontend development with live reload:  cd frontend && npm run dev   (proxies /api to this server)
"""
import argparse
import os
import sys

import uvicorn
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.app import app

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Every non-API path returns the React app, which handles its own pages."""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Unknown API address")   # never answer an API call with the web page
        # only files inside dist: a path such as "C:\Windows\win.ini" or "../outputs/x.csv" would otherwise escape it
        file = os.path.realpath(os.path.join(DIST, path))
        if path and file.startswith(os.path.realpath(DIST) + os.sep) and os.path.isfile(file):
            return FileResponse(file)
        # the page itself is always re-checked, so a new build (and its security headers) reaches every browser at
        # once; the script and style files it names carry a fingerprint in their name and can be kept
        return FileResponse(os.path.join(DIST, "index.html"), headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--certfile", help="HTTPS certificate (make one with python scripts/make_dev_cert.py)")
    ap.add_argument("--keyfile", help="HTTPS private key")
    a = ap.parse_args()
    from api import auth
    if not os.path.isdir(DIST):
        print("Frontend not built yet: cd frontend && npm install && npm run build  (the API still runs)")
    public = a.host not in ("127.0.0.1", "localhost", "::1")
    if public and not auth.sign_in_required():
        # never serve patient records to a network with nobody having to sign in
        sys.exit("Refusing to start: other computers could reach this server and nobody would have to sign in.\n"
                 "Create the hospital admin account first (python scripts/add_user.py NAME admin), then start again.")
    if bool(a.certfile) != bool(a.keyfile):
        sys.exit("HTTPS needs both --certfile and --keyfile.")
    if public and not a.certfile:
        print("Warning: this server is not using HTTPS. Use --certfile and --keyfile, or put it behind a reverse proxy "
              "that does (nginx, IIS, a load balancer); never send passwords over plain HTTP.")
    scheme = "https" if a.certfile else "http"
    # the server listens on IPv4; on Windows the name "localhost" tries IPv6 first and waits about two seconds before it falls back, so a
    # page or sign-in opened through it feels slow. The address 127.0.0.1 goes straight there.
    where = "127.0.0.1" if a.host in ("127.0.0.1", "localhost") else a.host
    print(f"Open {scheme}://{where}:{a.port}   (use this address, not 'localhost': on Windows 'localhost' adds about 2 seconds to every new connection)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", ssl_certfile=a.certfile, ssl_keyfile=a.keyfile)
