"""Minimal HTTP health-check endpoint for CloudDump."""

import json
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from clouddump import log

# Module-level state, updated by __main__ after each run completes.
_state = {"last_run": None}


def update_last_run(started, finished, succeeded, failed, total):
    """Record the result of the most recent backup run."""
    _state["last_run"] = {
        "started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jobs": total,
        "succeeded": succeeded,
        "failed": failed,
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = json.dumps({"status": "ok", "last_run": _state["last_run"]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # Delegate to Python logging instead of stderr.
        log.debug("health: %s", fmt % args)


def start_health_server(port=8080):
    """Start the health HTTP server in a daemon thread."""
    server = HTTPServer(("", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health endpoint listening on port %d", port)
