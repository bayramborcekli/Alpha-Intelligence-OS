#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Alpha-20 v1 — Yerel yedekleme scripti
# Kullanım: bash backup.sh
#
# Ne yedeklenir:
#   alpha20_v1/config.json      — Strateji ayarları (API anahtarı içermez)
#   alpha20_v1/state.json       — PAPER trading durumu
#   alpha20_v1/*.jsonl          — Karar / risk / öğrenme logları
#   alpha20_v1/alpha20.log      — Bot logları
#   alpha20_v1/smart_config.json — Akıllı seçim yapılandırması
#   alpha20_v1/safety_state.json — Güvenlik durumu
#   security.log                — Güvenlik olayları (hassas değer içermez)
#
# Dahil EDİLMEYEN:
#   .env                        — Gerçek sırlar içerir
#   .git/                       — Versiyon geçmişi zaten burada
#   backups/                    — Özyinelemeli yedek önlenir
#
# Yedekler: backups/YYYYMMDD_HHMMSS/
# Temizlik:  7 günden eski yedekler otomatik silinir
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${SCRIPT_DIR}/backups"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
DEST="${BACKUP_ROOT}/${TIMESTAMP}"

echo "════════════════════════════════════════"
echo " Alpha-20 v1 Yedekleme — ${TIMESTAMP}"
echo "════════════════════════════════════════"
echo "[backup] Hedef: ${DEST}"

mkdir -p "${DEST}"

copy_if_exists() {
    local src="$1"
    local label="$2"
    if [ -f "${src}" ]; then
        cp "${src}" "${DEST}/" && echo "[+] ${label}"
    else
        echo "[-] ${label} bulunamadı, atlandı."
    fi
}

# ── Config
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/config.json"        "config.json"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/smart_config.json"  "smart_config.json"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/safety_state.json"  "safety_state.json"

# ── State
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/state.json"         "state.json"

# ── JSONL logları
shopt -s nullglob
for f in "${SCRIPT_DIR}/alpha20_v1/"*.jsonl; do
    cp "${f}" "${DEST}/" && echo "[+] $(basename "${f}")"
done

# ── Bot logları
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/alpha20.log"        "alpha20.log"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/bot_process.log"    "bot_process.log"

# ── Öğrenme ağırlıkları
for f in "${SCRIPT_DIR}/alpha20_v1/"*weights*.json; do
    cp "${f}" "${DEST}/" && echo "[+] $(basename "${f}")"
done

# ── Güvenlik logu (parola veya sır içermez)
copy_if_exists "${SCRIPT_DIR}/security.log"                  "security.log"

# ── Yedek manifesti
cat > "${DEST}/MANIFEST.txt" << MANIFEST
Alpha-20 v1 Yedeği
Tarih: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Sistem: $(uname -a 2>/dev/null || echo "bilinmiyor")
Python: $(python3 --version 2>&1 || echo "bilinmiyor")
NOT: Bu yedek .env dosyasını IÇERMEZ.
     API anahtarları veya parolalar bu yedekte YOKTUR.
MANIFEST

echo ""
echo "[backup] ✅ Yedek tamamlandı: ${DEST}"
echo ""

# ── 7 günden eski yedekleri temizle
echo "[backup] 7 günden eski yedekler temizleniyor..."
CLEANED=0
if [ -d "${BACKUP_ROOT}" ]; then
    while IFS= read -r old; do
        echo "[backup] Silindi: $(basename "${old}")"
        rm -rf "${old}"
        CLEANED=$((CLEANED + 1))
    done < <(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d -mtime +7 2>/dev/null)
fi

if [ "${CLEANED}" -eq 0 ]; then
    echo "[backup] Temizlenecek eski yedek yok."
fi

echo ""
echo "[backup] Bitti. Yedekler: ${BACKUP_ROOT}/"
echo "════════════════════════════════════════"
