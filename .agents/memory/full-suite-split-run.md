---
name: Tam test paketi bölünmüş koşucu ile koşulur
description: 13.6k+ testlik paket tek koşuda summary basmadan sessizce ölüyor (OOM); tools/run_full_suite.py otomatik böler ve summary'siz koşuyu FAIL sayar
---
Tam paket ~13.7k test; tek `pytest` koşusu summary basmadan sessizce ölebiliyor (muhtemel OOM — hata/iz yok). "0 FAILED" görünümü yanıltıcı; yeşil sanılabilir.

**Çözüm (otomatik):** `python tools/run_full_suite.py` — collect-only ile dosya başına test sayısını çıkarır, kümülatif sayıya göre parçalara böler (parça başına ≤8000, en az 2), her parçayı ayrı pytest alt sürecinde koşar, summary satırlarını birleştirir. Summary'siz parça = FAIL; koşulan < toplanan = FAIL. Windows CI: `tools\windows\run_full_suite.cmd`.

**How to apply:** Tam paket kanıtı gerekince asla çıplak `python -m pytest tests/` koşma; koşucuyu kullan. Replit shell'in 300 sn sınırında `--parallel` bayrağı iki parçayı eşzamanlı koşar (~170 sn) — bellek dar makinede parallel KULLANMA (seri varsayılan). Arka plan (nohup/setsid) süreçleri bu ortamda ShellExec oturumları arasında yaşamıyor; uzun koşuları tek foreground komuta sığdır.
