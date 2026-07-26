# Test Programı

Çalıştırma: `python -m pytest tests/ -q`

**Kapanış durumu (Mission 1500.2):** 969 PASS / 0 FAIL / 0 SKIP
(1500.1 kapanışı 805 + 1500.2 ile eklenen 164).

## 1500.2 test grupları

| Dosya | Kapsam | Test |
|---|---|---|
| `test_mission1500_2_timeline.py` | Append-only JSONL, Decimal, float reddi, kilit, sınırlar | 24 |
| `test_mission1500_2_workspace_service.py` | Timeline/snapshot/compare/tavsiye/risk/arama | 26 |
| `test_mission1500_2_workspace_api.py` | API: auth, alias, 400/404/405, no-store, determinizm | 18 |
| `test_mission1500_2_workspace_ui.py` | UI: bölümler, XSS, salt-okunurluk, data-l, CDN yokluğu | 23 |
| `test_mission1500_2_workspace_export.py` | JSON/CSV export, başlıklar, formül koruması | 20 |
| `test_mission1500_2_workspace_security.py` | Auth/metot/CSRF/traversal/sterile/sızıntı/bütünlük | 45 |
| `test_mission1500_2_full_regression.py` | Uçtan uca zincir: boş/tek/çoklu/eski şema | 8 |

**1500.1 kapanış durumu:** 805 PASS / 0 FAIL / 0 SKIP
(taban 593 + 1500.1 ile eklenen 212).

## 1500.1 test grupları

| Dosya | Kapsam | Test |
|---|---|---|
| `test_mission1500_1_models.py` | Veri sözleşmeleri, Decimal-string, yasak alanlar | 26 |
| `test_mission1500_1_core.py` | Deterministik çekirdek analiz | 25 |
| `test_mission1500_1_explainer.py` | Risk açıklama şablonları, skor izi | 14 |
| `test_mission1500_1_recommendations.py` | Öncelik/tekilleştirme/confidence türetimi | 16 |
| `test_mission1500_1_service.py` | Servis birleşimi, kaynak hataları, durum | 18 |
| `test_mission1500_1_api.py` | API: auth, 405, başlıklar, şema, bayrak, sızıntı | 13 |
| `test_mission1500_1_ui.py` | UI: navigasyon, mobil, XSS, erişilebilirlik | 16 |
| `test_mission1500_1_settings.py` | Ayar doğrulama, güvenli varsayılan, LLM kilidi | 20 |
| `test_mission1500_1_security.py` | AST izolasyonu, prompt injection, 1400 tabanı | 36 |
| `test_mission1500_1_regression.py` | Statik tarayıcılar (float/secret/HTML/rota) | 28 |

## İlkeler
- Başarısız test gizlenmez, skip edilmez, geçsin diye zayıflatılmaz.
- Güvenlik assertion'ları kaldırılmaz; yalnızca güçlendirilir.
- Her mission tam regresyonla kapanır; taban test sayısı korunmalıdır.
- Testler gerçek borsaya istek atmaz; sağlayıcılar monkeypatch ile izole edilir.

## Statik güvence katmanı (çalışma zamanı testlerine ek)
- Intelligence modüllerinde import beyaz listesi (ağ/borsa/ledger/audit yasak)
- Exchange-write fonksiyon adı ve imzalama çağrısı yasağı (AST)
- Dosya yazımı yasağı (ledger/audit değişmezliği)
- `float()` ve `Decimal(float-literal)` yasağı
- Secret pattern taraması (atama, AWS anahtarı, private key, JWT)
- Şablonlarda `|safe` ve kaçışsız `innerHTML` yolu yasağı
- Intelligence rota kümesinin birebir doğrulanması + GET-only sözleşmesi
- 1500.1 öncesi yazma rotası anlık görüntüsü (yeni yazma rotası eklenemez)
