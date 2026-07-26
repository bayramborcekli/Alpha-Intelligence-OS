# gunicorn.conf.py — Üretim WSGI sunucusu yapılandırması
workers = 2
bind = "0.0.0.0:5000"
timeout = 120
keepalive = 5
worker_class = "sync"
loglevel = "info"
accesslog = "-"
errorlog = "-"


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
