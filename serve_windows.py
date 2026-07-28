"""Windows tek-süreç sunucu girişi.

Linux/Replit üretim yolu DEĞİŞMEDİ: orada gunicorn kullanılır
(`gunicorn -c gunicorn.conf.py app:app`). gunicorn ve fcntl Windows'ta
bulunmadığı için masaüstü başlatıcısı (launcher_windows.py) bu girişi
kullanır.

Tek süreç + çoklu iş parçacığı (waitress): worker-arası paylaşımlı
durum sorunları tanım gereği oluşmaz; flock katmanı yine de aktiftir
(ikinci bir kopya başlatılırsa dosya kilitleri korur).

Yalnız yerel makineden erişim: 127.0.0.1'e bağlanır.

Startup logu güvenlidir: Python yolu, proje kökü, port ve config kaynak
ÖZETİ (source/present — asla değer) yazılır.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import local_env

# Tek env yükleyici: Windows'ta proje .env, Replit'te process env kazanır.
# İdempotent — app.py de çağırsa .env yalnız bir kez uygulanır.
local_env.load_project_env()

HOST = "127.0.0.1"
PORT = int(os.environ.get("ALPHA_PORT", "5000"))
ROOT = Path(__file__).resolve().parent

log = logging.getLogger("alpha.serve")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    try:
        from waitress import serve
    except ImportError:
        log.error("waitress kurulu degil; INSTALL_WINDOWS.cmd calistirin "
                  "veya .venv icine 'pip install waitress' yapin.")
        raise SystemExit(3)

    # Güvenli startup özeti — secret DEĞERİ asla yazılmaz.
    log.info("python=%s", sys.executable)
    log.info("project_root=%s port=%d host=%s", ROOT, PORT, HOST)
    for key, meta in local_env.credential_metadata().items():
        log.info("config %s: present=%s source=%s length=%d",
                 key, meta["present"], meta["source"], meta["length"])

    from app import app

    serve(app, host=HOST, port=PORT, threads=8)


if __name__ == "__main__":
    main()
