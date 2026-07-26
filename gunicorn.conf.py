# gunicorn.conf.py — Üretim WSGI sunucusu yapılandırması
workers = 2
bind = "0.0.0.0:5000"
worker_class = "sync"
loglevel = "info"
accesslog = "-"
errorlog = "-"

# ── Request queue depth ────────────────────────────────────────────────────────
# backlog: maximum number of pending TCP connections the OS will queue while
# both sync workers are busy. Connections beyond this limit are refused
# immediately (ECONNREFUSED) instead of silently waiting. This bounds
# worst-case latency for legitimate users under burst load.
# Default is 2048 — far too deep for a 2-worker dashboard.
backlog = 64

# ── Timeouts ──────────────────────────────────────────────────────────────────
# timeout: a worker silent for longer than this is killed and replaced.
# 30 s is ample for every route in this app; no route holds a worker for
# more than a few seconds under normal conditions.
timeout = 30

# graceful_timeout: after SIGTERM, gunicorn waits this long for in-flight
# requests to complete before sending SIGKILL. Keeps active dashboard
# requests from being cut off during a rolling restart.
graceful_timeout = 20

# keepalive: seconds to wait for the next request on a keep-alive connection.
keepalive = 5

# ── Worker recycling ──────────────────────────────────────────────────────────
# Recycle workers periodically to avoid memory leaks.
# Each worker will restart after handling between 500 and 650 requests.
max_requests = 500
max_requests_jitter = 150


def post_fork(server, worker):
    """Her worker fork'landıktan sonra başlangıç güvenlik kontrollerini ve
    arka plan döngülerini başlat. Threadler fork'tan sağ çıkamadığı için
    bu hook burada zorunludur."""
    import app as _app

    _app.validate_startup_config()
    _app.enforce_paper_mode_lock()
    _app.um.start_auto_loop(_app._get_main_config)
    cfg0 = _app._get_main_config()
    if cfg0.get("adaptive_system", {}).get("enabled", False):
        _app.ac.start_controller_loop()


def worker_exit(server, worker):
    """Log worker exits so crashes are visible instead of silently timing out."""
    server.log.warning(
        "[gunicorn] Worker exited: pid=%s exitcode=%s",
        worker.pid,
        getattr(worker, "exitcode", "?"),
    )


def worker_abort(worker):
    """Log worker aborts (SIGABRT, e.g. timeout kill)."""
    import logging
    logging.getLogger("gunicorn.error").critical(
        "[gunicorn] Worker aborted (SIGABRT): pid=%s — likely timed out or OOM",
        worker.pid,
    )
