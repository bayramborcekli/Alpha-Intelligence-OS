#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Alpha Intelligence OS — Yedekleme scripti
# Sürüm: ownership-baseline-v1
#
# Kullanım: bash backup.sh
#
# Ne yedeklenir:
#   alpha20_v1/config.json       — Strateji ayarları
#   alpha20_v1/smart_config.json — Akıllı seçim yapılandırması
#   alpha20_v1/safety_state.json — Kill switch durumu
#   alpha20_v1/state.json        — PAPER pozisyon / bakiye
#   alpha20_v1/*.jsonl           — Karar / risk / öğrenme logları
#   alpha20_v1/alpha20.log       — Bot logu
#   alpha20_v1/bot_process.log   — Süreç logu
#   security.log                 — Güvenlik olayları
#
# Dahil EDİLMEYEN:
#   .env            — Gerçek sırlar içerir — asla yedeklenmez
#   .git/           — Sürüm kontrolü zaten burada
#   backups/        — Özyinelemeli yedek önlenir
#
# Yedek yapısı:  backups/YYYYMMDD_HHMMSS/
#   MANIFEST.txt      — Tarih, sürüm, metadata
#   CHECKSUMS.sha256  — SHA-256 bütünlük denetimi
#
# Retention politikası:
#   Günlük:   Son 7 gün
#   Haftalık: Son 4 hafta  (Pazar yedekleri korunur)
#   Aylık:    Son 6 ay     (Ayın 1'i yedekleri korunur)
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_ROOT="${SCRIPT_DIR}/backups"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
DEST="${BACKUP_ROOT}/${TIMESTAMP}"
VERSION_FILE="${SCRIPT_DIR}/VERSION"
APP_VERSION="$(cat "${VERSION_FILE}" 2>/dev/null | tr -d '[:space:]' || echo "unknown")"

echo "════════════════════════════════════════"
echo " Alpha Intelligence OS Yedekleme"
echo " Sürüm : ${APP_VERSION}"
echo " Zaman : ${TIMESTAMP}"
echo "════════════════════════════════════════"
echo "[backup] Hedef: ${DEST}"

mkdir -p "${DEST}"

# ── Dosya kopyalama yardımcısı ───────────────────────────────────────────────
copy_if_exists() {
    local src="$1"
    local label="$2"
    if [ -f "${src}" ]; then
        cp "${src}" "${DEST}/" && echo "[+] ${label}"
    else
        echo "[-] ${label} bulunamadı, atlandı."
    fi
}

# ── Yapılandırma ─────────────────────────────────────────────────────────────
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/config.json"        "config.json"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/smart_config.json"  "smart_config.json"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/safety_state.json"  "safety_state.json"

# ── Durum ────────────────────────────────────────────────────────────────────
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/state.json"         "state.json"

# ── JSONL logları ────────────────────────────────────────────────────────────
shopt -s nullglob
for f in "${SCRIPT_DIR}/alpha20_v1/"*.jsonl; do
    cp "${f}" "${DEST}/" && echo "[+] $(basename "${f}")"
done

# ── Öğrenme ağırlıkları ──────────────────────────────────────────────────────
for f in "${SCRIPT_DIR}/alpha20_v1/"*weights*.json; do
    cp "${f}" "${DEST}/" && echo "[+] $(basename "${f}")"
done

# ── Bot logları ──────────────────────────────────────────────────────────────
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/alpha20.log"        "alpha20.log"
copy_if_exists "${SCRIPT_DIR}/alpha20_v1/bot_process.log"    "bot_process.log"

# ── Güvenlik logu ────────────────────────────────────────────────────────────
copy_if_exists "${SCRIPT_DIR}/security.log"                  "security.log"

# ── Manifes ──────────────────────────────────────────────────────────────────
cat > "${DEST}/MANIFEST.txt" << MANIFEST
Alpha Intelligence OS Yedeği
============================
Tarih     : $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Sürüm     : ${APP_VERSION}
Checkpoint: ${TIMESTAMP}
Sistem    : $(uname -a 2>/dev/null || echo "bilinmiyor")
Python    : $(python3 --version 2>&1 || echo "bilinmiyor")

UYARI: Bu yedek .env dosyasını İÇERMEZ.
       API anahtarları veya parolalar bu yedekte YOKTUR.
       Geri yüklemek için: bash restore.sh backups/${TIMESTAMP}
MANIFEST

# ── SHA-256 bütünlük özeti ───────────────────────────────────────────────────
echo ""
echo "[backup] SHA-256 bütünlük özeti oluşturuluyor..."
(
    cd "${DEST}"
    # CHECKSUMS dosyasının kendisi hariç tüm dosyaları hashle
    find . -maxdepth 1 -type f ! -name "CHECKSUMS.sha256" \
        | sort | xargs sha256sum > CHECKSUMS.sha256
)
echo "[backup] ✅ CHECKSUMS.sha256 oluşturuldu"

# ── Bütünlük doğrulaması (kendi kendini test et) ─────────────────────────────
echo "[backup] Bütünlük doğrulanıyor..."
if (cd "${DEST}" && sha256sum -c CHECKSUMS.sha256 --quiet 2>&1); then
    echo "[backup] ✅ Bütünlük doğrulaması başarılı"
else
    echo "[backup] ❌ HATA: Bütünlük doğrulaması başarısız! Yedek bozuk olabilir."
    exit 1
fi

echo ""
echo "[backup] ✅ Yedek tamamlandı: ${DEST}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# Retention politikası
# Günlük  : Son 7 gün — hepsi korunur
# Haftalık: Son 4 hafta — sadece Pazar (0) yedekleri korunur
# Aylık   : Son 6 ay — sadece ayın 1'i yedekleri korunur
# ═══════════════════════════════════════════════════════════════════════════════

echo "[backup] Retention politikası uygulanıyor..."

TODAY_DOW="$(date +"%u")"   # 1=Pzt ... 7=Paz
TODAY_DAY="$(date +"%d")"   # 01-31
CLEANED=0

if [ -d "${BACKUP_ROOT}" ]; then
    while IFS= read -r old; do
        DIR_NAME="$(basename "${old}")"
        # Tarih: YYYYMMDD_HHMMSS — ilk 8 karakter YYYYMMDD
        DIR_DATE="${DIR_NAME:0:8}"

        # Yaşı gün olarak hesapla
        if date --version >/dev/null 2>&1; then
            # GNU date
            DIR_EPOCH="$(date -d "${DIR_DATE}" +%s 2>/dev/null || echo 0)"
        else
            # BSD date (macOS)
            DIR_EPOCH="$(date -j -f "%Y%m%d" "${DIR_DATE}" +%s 2>/dev/null || echo 0)"
        fi
        NOW_EPOCH="$(date +%s)"
        AGE_DAYS=$(( (NOW_EPOCH - DIR_EPOCH) / 86400 ))

        # 7 günden kısa — sakla
        [ "${AGE_DAYS}" -lt 7 ] && continue

        # 7–28 gün arası — yalnızca Pazar yedeklerini sakla
        if [ "${AGE_DAYS}" -lt 28 ]; then
            # Yedek gününün haftanın kaçıncı günü olduğunu bul
            if date --version >/dev/null 2>&1; then
                BACKUP_DOW="$(date -d "${DIR_DATE}" +"%u" 2>/dev/null || echo 0)"
            else
                BACKUP_DOW="$(date -j -f "%Y%m%d" "${DIR_DATE}" +"%u" 2>/dev/null || echo 0)"
            fi
            [ "${BACKUP_DOW}" = "7" ] && continue  # Pazar — sakla
        fi

        # 28–180 gün arası — yalnızca ayın 1'ini sakla
        if [ "${AGE_DAYS}" -lt 180 ]; then
            BACKUP_DAY="${DIR_DATE:6:2}"
            [ "${BACKUP_DAY}" = "01" ] && continue  # Ayın 1'i — sakla
        fi

        # 180 günden eski — her şeyi sil
        echo "[backup] Silindi (retention): $(basename "${old}")"
        rm -rf "${old}"
        CLEANED=$((CLEANED + 1))

    done < <(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d \
                 ! -name "$(basename "${DEST}")" 2>/dev/null | sort)
fi

if [ "${CLEANED}" -eq 0 ]; then
    echo "[backup] Retention: Silinecek eski yedek yok."
else
    echo "[backup] Retention: ${CLEANED} yedek silindi."
fi

echo ""
echo "[backup] Bitti. Yedekler: ${BACKUP_ROOT}/"
echo "════════════════════════════════════════"
