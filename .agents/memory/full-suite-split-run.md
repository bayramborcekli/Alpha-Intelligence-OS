---
name: Tam test paketi ikiye bölünerek koşulur
description: 13.600+ testlik paket tek koşuda summary basmadan sessizce ölüyor (muhtemel OOM); iki yarıda koş
---
Tam paket ~13.650 teste ulaştı; tek `pytest` koşusu iki denemede de tüm dot'ları yazdıktan sonra summary basmadan sessizce öldü (muhtemel bellek tükenmesi — hata/iz yok).
**Why:** Süreç OOM ile öldürülünce "0 FAILED" görünümü yanıltıcı; yeşil sanılabilir.
**How to apply:** `--collect-only -q` çıktısından dosya listesini kümülatif sayıya göre ikiye böl, iki ayrı `timeout 290 pytest` koşusu yap (her biri ~150 sn); iki summary satırının toplamını kanıt olarak raporla. Tek koşu summary basmadıysa PASS sayma.
