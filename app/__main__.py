"""Portable foreground launcher: python -m app --open."""
import argparse
import threading
import time
from urllib.parse import urlencode
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Overseer Network (loopback only)")
    parser.add_argument("--port", type=int, default=2077)
    parser.add_argument("--open", action="store_true", help="Open the authenticated panel in your browser")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    # Construct the application before opening the browser so first-run access exists.
    from app.main import app
    from app.security import access_control
    url = f"http://127.0.0.1:{args.port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False))
    if args.open:
        def open_when_ready():
            for _ in range(150):
                if server.should_exit:
                    return
                if server.started:
                    webbrowser.open(url + "?" + urlencode({"access_token": access_control.access_token}))
                    return
                time.sleep(0.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    print("Overseer: " + url + " (Ctrl+C to stop)")
    server.run()


if __name__ == "__main__":
    main()
