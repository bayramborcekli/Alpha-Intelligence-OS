#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Alpha Intelligence OS — Geri yükleme scripti
# Sürüm: ownership-baseline-v1
#
# Kullanım:
#   bash restore.sh --dry-run backups/YYYYMMDD_HHMMSS   # Sadece kontrol
#   bash restore.sh backups/YYYYMMDD_HHMMSS              # Gerçek geri yükleme
#
# Geri yükleme adımları:
#   1. Bütünlük denetimi (CHECKSUMS.sha256)
#   2. Mevcut verinin ön-yedeği (üzerine yazmadan önce)
#   3. Dosyaları geri yükle (atomic)
#   4. Audit log'a yaz
#
# Güvenlik:
#   - Bütünlük başarısız → DURUR
#   - MANIFEST.txt eksik → DURUR
#   - CHECKSUMS.sha256 eksik → DURUR
#   - .env asla geri yüklenmez
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT_LOG="${SCRIPT_DIR}/restore_audit.log"
DRY_RUN=false
BACKUP_DIR=""

# ── Argüman ayrıştırma ───────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)
            DRY_RUN=true
            shift
            ;;
        *)
            BACKUP_DIR="$1"
            shift
            ;;
    esac
done

if [ -z "${BACKUP_DIR}" ]; then
    echo "Kullanım: bash restore.sh [--dry-run] <yedek-dizini>"
    echo "Örnek   : bash restore.sh backups/20250126_120000"
    exit 1
fi

# Mutlak yol
if [[ "${BACKUP_DIR}" != /* ]]; then
    BACKUP_DIR="${SCRIPT_DIR}/${BACKUP_DIR}"
fi

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
MODE_LABEL="DRY-RUN"
${DRY_RUN} || MODE_LABEL="RESTORE"

echo "════════════════════════════════════════"
echo " Alpha Intelligence OS Geri Yükleme"
echo " Mod   : ${MODE_LABEL}"
echo " Kaynak: ${BACKUP_DIR}"
echo " Zaman : ${TIMESTAMP}"
echo "════════════════════════════════════════"

# ── Audit log yardımcısı ─────────────────────────────────────────────────────
audit() {
    local level="$1"
    local msg="$2"
    echo "[${TIMESTAMP}] [${level}] [${MODE_LABEL}] ${msg}" >> "${AUDIT_LOG}"
}

# ── Doğrulama: dizin mevcut mu? ──────────────────────────────────────────────
if [ ! -d "${BACKUP_DIR}" ]; then
    echo "❌ HATA: Yedek dizini bulunamadı: ${BACKUP_DIR}"
    audit "ERROR" "Yedek dizini bulunamadı: ${BACKUP_DIR}"
    exit 1
fi

# ── Doğrulama: MANIFEST.txt ──────────────────────────────────────────────────
if [ ! -f "${BACKUP_DIR}/MANIFEST.txt" ]; then
    echo "❌ HATA: MANIFEST.txt eksik — geçersiz yedek reddedildi."
    audit "ERROR" "MANIFEST.txt eksik: ${BACKUP_DIR}"
    exit 1
fi

echo ""
echo "[restore] Yedek bilgisi:"
grep -E "^(Tarih|Sürüm|Checkpoint)" "${BACKUP_DIR}/MANIFEST.txt" | sed 's/^/  /'
echo ""

# ── Doğrulama: CHECKSUMS.sha256 ──────────────────────────────────────────────
if [ ! -f "${BACKUP_DIR}/CHECKSUMS.sha256" ]; then
    echo "❌ HATA: CHECKSUMS.sha256 eksik — bütünlük denetimi yapılamıyor. Reddedildi."
    audit "ERROR" "CHECKSUMS.sha256 eksik: ${BACKUP_DIR}"
    exit 1
fi

# ── SHA-256 bütünlük denetimi ────────────────────────────────────────────────
echo "[restore] SHA-256 bütünlük denetimi..."
if ! (cd "${BACKUP_DIR}" && sha256sum -c CHECKSUMS.sha256 --quiet 2>&1); then
    echo "❌ HATA: Bütünlük denetimi başarısız! Yedek bozuk veya değiştirilmiş. Reddedildi."
    audit "ERROR" "Bütünlük denetimi başarısız: ${BACKUP_DIR}"
    exit 1
fi
echo "[restore] ✅ Bütünlük denetimi başarılı"
audit "INFO" "Bütünlük denetimi başarılı: ${BACKUP_DIR}"

# ── Geri yüklenecek dosyaları listele ────────────────────────────────────────
RESTORE_FILES=(
    "config.json:alpha20_v1/config.json"
    "smart_config.json:alpha20_v1/smart_config.json"
    "safety_state.json:alpha20_v1/safety_state.json"
    "state.json:alpha20_v1/state.json"
    "alpha20.log:alpha20_v1/alpha20.log"
    "bot_process.log:alpha20_v1/bot_process.log"
    "security.log:security.log"
)

# JSONL ve weights dosyaları
shopt -s nullglob
for f in "${BACKUP_DIR}/"*.jsonl "${BACKUP_DIR}/"*weights*.json; do
    fname="$(basename "${f}")"
    RESTORE_FILES+=("${fname}:alpha20_v1/${fname}")
done

echo ""
echo "[restore] Geri yüklenecek dosyalar:"
FOUND_COUNT=0
for entry in "${RESTORE_FILES[@]}"; do
    src_name="${entry%%:*}"
    dst_rel="${entry##*:}"
    src="${BACKUP_DIR}/${src_name}"
    dst="${SCRIPT_DIR}/${dst_rel}"

    if [ -f "${src}" ]; then
        if [ -f "${dst}" ]; then
            echo "  [ÜZERİNE YAZ] ${dst_rel}"
        else
            echo "  [YENİ]        ${dst_rel}"
        fi
        FOUND_COUNT=$((FOUND_COUNT + 1))
    fi
done

if [ "${FOUND_COUNT}" -eq 0 ]; then
    echo "  (Geri yüklenecek dosya yok)"
    audit "WARN" "Geri yüklenecek dosya bulunamadı: ${BACKUP_DIR}"
fi

echo ""

# ── Dry-run modunda burada dur ────────────────────────────────────────────────
if "${DRY_RUN}"; then
    echo "════════════════════════════════════════"
    echo " DRY-RUN tamamlandı — hiçbir şey değiştirilmedi."
    echo " Gerçek geri yükleme için --dry-run olmadan çalıştırın."
    echo "════════════════════════════════════════"
    audit "INFO" "Dry-run tamamlandı: ${FOUND_COUNT} dosya geri yüklenebilir"
    exit 0
fi

# ── Ön-yedek al (mevcut veriyi koru) ────────────────────────────────────────
PRE_RESTORE_TS="$(date +"%Y%m%d_%H%M%S")"
PRE_BACKUP_DIR="${SCRIPT_DIR}/backups/pre_restore_${PRE_RESTORE_TS}"
echo "[restore] Mevcut veri ön-yedekleniyor: ${PRE_BACKUP_DIR}"
mkdir -p "${PRE_BACKUP_DIR}"

for entry in "${RESTORE_FILES[@]}"; do
    dst_rel="${entry##*:}"
    dst="${SCRIPT_DIR}/${dst_rel}"
    if [ -f "${dst}" ]; then
        cp "${dst}" "${PRE_BACKUP_DIR}/"
    fi
done

# Ön-yedek için basit MANIFEST
cat > "${PRE_BACKUP_DIR}/MANIFEST.txt" << MANIFEST
Ön-yedek (restore öncesi otomatik)
===================================
Tarih     : ${TIMESTAMP}
Kaynak    : ${BACKUP_DIR}
NOT: Bu yedek geri yükleme öncesi mevcut veriden otomatik alınmıştır.
MANIFEST

echo "[restore] ✅ Ön-yedek alındı"
audit "INFO" "Ön-yedek alındı: ${PRE_BACKUP_DIR}"

# ── Gerçek geri yükleme ──────────────────────────────────────────────────────
echo ""
echo "[restore] Dosyalar geri yükleniyor..."
RESTORED=0
SKIPPED=0

for entry in "${RESTORE_FILES[@]}"; do
    src_name="${entry%%:*}"
    dst_rel="${entry##*:}"
    src="${BACKUP_DIR}/${src_name}"
    dst="${SCRIPT_DIR}/${dst_rel}"

    [ -f "${src}" ] || { SKIPPED=$((SKIPPED + 1)); continue; }

    # Hedef dizini oluştur
    mkdir -p "$(dirname "${dst}")"

    # Atomic write: temp dosyasına yaz, sonra taşı
    TMP_DST="${dst}.restore.tmp"
    cp "${src}" "${TMP_DST}"
    mv "${TMP_DST}" "${dst}"

    echo "[+] ${dst_rel}"
    audit "INFO" "Geri yüklendi: ${dst_rel}"
    RESTORED=$((RESTORED + 1))
done

echo ""
echo "[restore] ✅ Tamamlandı: ${RESTORED} dosya geri yüklendi, ${SKIPPED} dosya atlandı."
audit "INFO" "Geri yükleme tamamlandı: ${RESTORED} dosya, kaynak=${BACKUP_DIR}"

echo ""
echo "════════════════════════════════════════"
echo " Geri yükleme başarıyla tamamlandı."
echo " Ön-yedek: ${PRE_BACKUP_DIR}"
echo " Audit   : ${AUDIT_LOG}"
echo "════════════════════════════════════════"
