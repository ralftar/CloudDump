"""Dead-man-switch pings around each run, in the Healthchecks.io protocol.

The endpoint expects a ping at the start of a run and one at its end, and
alerts when the end ping is late or reports failure. It watches from outside:
it raises the alarm when this process, its host or its mail is gone, which
nothing inside can report.

    <url>/start   the run began; the endpoint times the run from here
    <url>         the run finished with every job successful
    <url>/fail    the run finished with failures

The request body carries a short summary the endpoint shows with the ping.
A failed ping is logged and never fails the run.
"""

import time
import urllib.error
import urllib.request

from clouddump import log

_TIMEOUT_SECONDS = 10
_ATTEMPTS = 3
# Healthchecks.io keeps the first 100 kB of a ping's body.
_BODY_LIMIT = 100_000


def ping(base_url, signal="", body=""):
    """POST a ping to base_url/signal. Returns True once the endpoint accepts it."""
    if not base_url:
        return None
    url = base_url.rstrip("/") + (f"/{signal}" if signal else "")
    data = body.encode("utf-8")[:_BODY_LIMIT]
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={"Content-Type": "text/plain; charset=utf-8"})
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                resp.read()
            log.info("Heartbeat ping sent (%s)", signal or "success")
            return True
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # The URL itself is the endpoint's credential: the error names
            # what went wrong, never the URL.
            reason = exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            if attempt == _ATTEMPTS:
                log.warning("Heartbeat ping (%s) failed after %d attempts: %s",
                            signal or "success", _ATTEMPTS, reason)
                return False
            time.sleep(2 * attempt)
    return False
