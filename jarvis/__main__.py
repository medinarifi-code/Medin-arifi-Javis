"""Start: python -m jarvis  (lokal)   |   python -m jarvis --cloud  (Render)"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS – dein KI-Assistent")
    parser.add_argument("--cloud", action="store_true", help="Betrieb auf einem Server (Render): Port aus $PORT")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    from .server import app, cfg

    host = "127.0.0.1"
    if args.cloud:
        if not cfg.web_password:
            raise SystemExit("Bitte JARVIS_WEB_PASSWORD setzen - sonst kann jeder deinen JARVIS und deine Mails benutzen.")
        host = "0.0.0.0"
    elif not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    print(f"\n  J.A.R.V.I.S. ist online: http://localhost:{args.port}\n")
    uvicorn.run(app, host=host, port=args.port, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
