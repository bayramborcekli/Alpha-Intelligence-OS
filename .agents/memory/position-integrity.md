---
name: Pozisyon bütünlüğü sözleşmesi
description: Aktif pozisyonların dürüst durum kodları — Yönetiliyor maskesi yasak
---
Aktif pozisyon iki kaynaktan gelir: legacy `state.json.position` (overview'a `_classify_legacy_position` ile) ve dual runtime (`snapshot` position_status ile).

**Kurallar:**
- Durum kodları: OPEN/ACTIVE, INCOMPLETE_POSITION_DATA, STALE_POSITION, ORPHAN_POSITION (aktif listeye alınmaz + audit), PRICE_REFRESH_FAILED. UI hiçbir eksik kaydı "Yönetiliyor" ile maskeleyemez; statusCell + EXIT_BLOCKED sözleşmesi bekçi testli (test_position_integrity, mission2300 güncellendi).
- Zaman kıyasları ASLA string ile yapılmaz — `_parse_ts` (ISO + epoch) normalize eder; çözümlenemeyen damga ORPHAN kanıtı sayılmaz (mimar bulgusuydu).
- Legacy'de max-hold yok → bayat eşiği varsayılan 24h (`position_stale_hours` ile ayarlanabilir); agresif eşik yanlış alarm üretir.
- Eksik entry/quantity ile exit YAPILMAZ: dual_model `_position_fields_valid` monitor+manual_close'u fail-closed keser.
- Audit `position_integrity_audit.jsonl` git dışı, flock + tail-read dedupe (tam dosya okunmaz).

**How to apply:** Yeni pozisyon kaynağı/alanı eklerken aynı durum sözlüğünü ve fail-closed exit kuralını uygula.
