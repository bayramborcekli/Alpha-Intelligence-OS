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

# ── REPLIT_DEV_BYPASS koruması ────────────────────────────────────────────────
# Task #70 merge'i .replit'teki [userenv.development] REPLIT_DEV_BYPASS="1"
# satırını sessizce sildi ve login ekranı geri geldi. Bu blok satırı doğrular,
# yoksa geri ekler. Bypass'ı kaldırma kararını YALNIZ OPERATÖR verir; o zaman
# bu blok da birlikte kaldırılmalıdır.
echo "[post-merge] REPLIT_DEV_BYPASS yapılandırması doğrulanıyor..."
if grep -Eq '^\[userenv\.development\]' .replit \
   && grep -Eq '^REPLIT_DEV_BYPASS *= *"1"' .replit; then
  echo "[post-merge] REPLIT_DEV_BYPASS zaten yerinde."
else
  echo "[post-merge] UYARI: REPLIT_DEV_BYPASS satırı eksik — geri ekleniyor."
  # Varsa yarım kalmış bölümü temizlemeden, eksik parçaları sona ekle.
  if ! grep -Eq '^\[userenv\.development\]' .replit; then
    printf '\n[userenv.development]\nREPLIT_DEV_BYPASS = "1"\n' >> .replit
  else
    # Bölüm var ama satır yok: satırı bölüm başlığının hemen altına ekle.
    sed -i '/^\[userenv\.development\]$/a REPLIT_DEV_BYPASS = "1"' .replit
  fi
  echo "[post-merge] REPLIT_DEV_BYPASS = \"1\" geri eklendi."
fi

echo "[post-merge] Tamamlandı."
