#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Alpha Intelligence OS — Post-merge kurulum scripti
# Her task merge işleminden sonra otomatik çalışır.
#
# Kurallar:
#   - Idempotent: birden fazla çalıştırılabilir
#   - Non-interactive: stdin kapalı, --yes / -q kullan
#   - Hızlı: kullanıcı beklerken çalışır
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

echo "[post-merge] Bağımlılıklar kuruluyor..."
pip install -q -r requirements.txt

echo "[post-merge] Tamamlandı."
