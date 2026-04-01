"""Minimal HTTP health-check endpoint for CloudDump."""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from clouddump import log

# Module-level state, updated by __main__ after each run completes.
_state = {
    "last_run": {"jobs": 0, "succeeded": 0, "failed": 0, "has_run": False},
    "jobs": {},
    "log_requests": False,
}


def update_last_run(started, finished, succeeded, failed, total):
    """Record the result of the most recent backup run."""
    _state["last_run"] = {
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "finished_epoch": int(finished.timestamp()),
        "jobs": total,
        "succeeded": succeeded,
        "failed": failed,
        "has_run": True,
    }


def update_job_metric(job_id, job_type, status, elapsed, rx=None, tx=None):
    """Record metrics for a single job execution."""
    entry = {
        "type": job_type,
        "status": status,
        "elapsed_seconds": elapsed,
    }
    if rx is not None:
        entry["rx_bytes"] = rx
    if tx is not None:
        entry["tx_bytes"] = tx
    _state["jobs"][job_id] = entry


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = json.dumps({
                "status": "ok",
                "last_run": _state["last_run"],
                "jobs": _state["jobs"],
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        if _state["log_requests"]:
            log.debug("health: %s", fmt % args)


def start_health_server(port=8080, log_requests=False):
    """Start the health HTTP server in a daemon thread."""
    _state["log_requests"] = log_requests
    try:
        server = HTTPServer(("", port), _Handler)
    except OSError as exc:
        log.warning("Could not start health endpoint on port %d: %s", port, exc)
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health endpoint listening on port %d", port)
