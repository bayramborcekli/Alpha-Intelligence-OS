# Mission 1900 — Monitoring & Alerting

Resmî teknik dokümantasyon. Bu belge tamamlanmış Monitoring yığınının
mimarisini, sözleşmelerini, sahiplik sınırlarını ve doğrulama
sonuçlarını kalıcı olarak belgeler. Uygulamayı TARİF EDER; uygulamayı
DEĞİŞTİRMEZ.

## A. Misyon Özeti

Mission 1900, strateji önerilerinin performansını salt-okunur olarak
izleyen, sağlık durumu türeten, deterministik uyarılar üreten ve
sonucu kanonik JSON olarak dışa aktaran altı katmanlı bir Monitoring &
Alerting yığını ekler. Akış TEK YÖNLÜDÜR: izleme sonuçları Strategy
katmanına geri beslenmez, bildirim gönderilmez, kalıcılık yoktur,
Exchange'e yazma isteği sonsuza dek 0'dır.

## B. Mimari Diyagramı

```
Monitoring Core          (monitoring_intelligence.py)
        ↓
Alert Engine             (alert_engine.py)
        ↓
Monitoring Service       (monitoring_service.py)
        ↓
Monitoring API           (monitoring_api.py)
        ↓
Monitoring Export        (monitoring_export.py)
        ↓
Monitoring Security      (monitoring_security.py)
```

Bağımlılık inversiyonu YOKTUR. Döngüsel import YOKTUR. Katman atlama
YOKTUR.

## C. Katman Sorumlulukları

| Katman | Modül | Sahiplik |
|---|---|---|
| Monitoring Core | `monitoring_intelligence.py` | İzleme hesapları: metrikler, veri kalitesi, sağlık durumu (UNKNOWN/CRITICAL/DEGRADED/HEALTHY), Decimal eşikleri |
| Alert Engine | `alert_engine.py` | Uyarı üretimi: kapalı 11 kodlu küme, sabit kural sırası, severity önceliği, A1/A2… kimlikleri |
| Monitoring Service | `monitoring_service.py` | Sağlayıcı orkestrasyonu: kaynak toplama, tazelik değerlendirmesi, kalite düşürme, analiz zarfı |
| Monitoring API | `monitoring_api.py` | İstek doğrulama, META VERİ ÜRETİMİ, yanıt zarfı, durum normalizasyonu (SUCCESS/PARTIAL/FAILED) |
| Monitoring Export | `monitoring_export.py` | Kanonik serileştirme: sabit dış şema, Decimal→string, bayt-deterministik JSON |
| Monitoring Security | `monitoring_security.py` | Mimari doğrulama: import yüzeyi, bağımlılık grafiği, kamu API, meta veri sahipliği, immutability |

Sahiplik örtüşmesi YOKTUR.

## D. Onaylı Bağımlılık Grafiği

- `monitoring_intelligence` → (proje bağımlılığı yok)
- `alert_engine` → yalnız `monitoring_intelligence`
- `monitoring_service` → `monitoring_intelligence` + `alert_engine`
  (+ varsayılan zincirde tembel, salt-okunur `strategy_service`)
- `monitoring_api` → yalnız `monitoring_service`
- `monitoring_export` → yalnız `monitoring_api`
- `monitoring_security` → beş yığın modülünün tamamı (salt-okunur
  doğrulama; hiçbir katman Security'yi import etmez)

## E. Onaylı Kamu API'si

| Katman | Onaylı girişler |
|---|---|
| Core | `build_monitoring_report` |
| Alert | `build_alert_report` |
| Service | `analyze_monitoring`, `build_default_monitoring_providers`, `MonitoringService` |
| API | `analyze_monitoring_api` |
| Export | `build_monitoring_export`, `serialize_monitoring_export` |
| Security | `verify_monitoring_security` |

Bu listenin dışında kamu iş girişi YOKTUR; beklenmeyen giriş güvenlik
doğrulamasını FAIL eder.

## F. Meta Veri Sahipliği

`report_id` (UUID4), `observed_at` ve `generated_at` (RFC3339 UTC)
YALNIZ Monitoring API katmanında üretilir. Tüm alt katmanlar meta
veriyi yalnız TAŞIR: Core raporunda `report_id`/`observed_at` null,
AlertReport'ta `generated_at` null kalır; Export meta veriyi aynen
korur, asla üretmez. Core/Alert/Service/Export modüllerinde
`uuid`/`datetime`/`time` importu yoktur.

## G. Güvenlik Modeli

- **Yasak importlar** (tüm yığın): `exchange*`, `broker*`, `ccxt`,
  `requests`, `httpx`, `urllib3`, `socket`, `threading`, `asyncio`,
  `subprocess`, `multiprocessing`, `sqlite3`, `sqlalchemy`, `redis`,
  `pickle`, `shelve`, `pathlib`, `os`, `sys`, `dotenv`, `secrets`,
  `cryptography` ve eşdeğer altyapı.
- **Yasak yetenekler**: Exchange erişimi, broker SDK, dosya/DB
  yazımı, snapshot kalıcılığı, HTTP/soket istemcisi, thread,
  zamanlayıcı, subprocess, eval/exec, ortam/secret erişimi.
- **Doğrulama kuralları** (`verify_monitoring_security`):
  IMPORT_SURFACE, FORBIDDEN_MODULES, DANGEROUS_CALLS (takma ad ve
  öznitelik bypass'ları dahil), METADATA_OWNERSHIP,
  METADATA_GENERATION (semantik sentez denetimi), DEPENDENCY_GRAPH,
  PUBLIC_API_SURFACE, IMMUTABLE_MODELS.
- Rapor: `verified` yalnız `violations == ()` iken True.

## H. Determinizm Garantileri

Aynı mantıksal girdi → özdeş çıktı: MonitoringReport, AlertReport,
analiz zarfı, API zarfı (`report_id`/zaman damgaları hariç — bunlar
her çağrıda API'de yeniden üretilir), Export modeli ve bayt-özdeş
JSON, Security raporu. Gizli durum, önbellek bağımlılığı ve sıralama
kararsızlığı yoktur. Core/Alert/Service/Export/Security katmanlarında
saat, UUID ve rastgelelik erişimi yoktur.

## I. Immutability Garantileri

Dışa açık tüm modeller derin immutable'dır (MappingProxyType + tuple):
MonitoringReport, AlertReport, MonitoringAnalysis,
MonitoringApiResponse, MonitoringExport, MonitoringSecurityReport.
Mutasyon denemeleri TypeError üretir. Hiçbir katman aldığı zarfı
değiştirmez; Export girdiyi mutasyonsuz taşır.

## J. Export Sözleşmesi

- Kök şema (tam 9 alan): `api_version, report_id, observed_at,
  generated_at, status, limitations, monitoring, alerts, sources`.
- **Kanonik JSON**: `ensure_ascii=False` (UTF-8 kaçışsız),
  `sort_keys=True`, `separators=(",", ":")`, `allow_nan=False`,
  girinti yok, satır sonu yok → aynı mantıksal girdi bayt-özdeş çıktı.
- **Decimal → string**: `Decimal("12.3400")` → `"12.3400"`
  (hassasiyet korunur, float'a çevrim ve bilimsel gösterim yok).
- **Null korunumu**: bilinmeyen değerler null kalır; tuple'lar dizi
  olur; sıralamalar kararlıdır.
- **FAILED davranışı**: `monitoring_analysis` null ise
  `monitoring=null, alerts=[], sources=[]`; meta veri ve kök
  `limitations` aynen korunur; alt katman çağrılmaz.
- **Yeniden hesap YOK**: status/health/alerts aynen taşınır.

## K. Hata Modeli (yalnız onaylı sterile kodlar)

| Katman | Sterile kodlar |
|---|---|
| Core | `INVALID_INPUT`, `FLOAT_REJECTED` |
| Alert | `INVALID_MONITORING_REPORT`, `UNSUPPORTED_MONITORING_VERSION`, `UNKNOWN_HEALTH_STATUS`, `INCONSISTENT_MONITORING_REPORT` |
| Service | `MONITORING_ANALYSIS_ERROR` (+ kaynak kodları `PROVIDER_FAILED`, `INVALID_PROVIDER_RESULT`) |
| API | `INVALID_API_REQUEST`, `UNSUPPORTED_API_VERSION`, `UNKNOWN_PROVIDER`, `MONITORING_ANALYSIS_ERROR` |
| Export | `INVALID_MONITORING_EXPORT_INPUT` |
| Security | `SECURITY_VERIFICATION_FAILED` |

Hata mesajları yalnız bu kodlardır: iz, yol, kaynak kodu, ortam,
sağlayıcı yükü veya secret ASLA sızmaz.

## L. Regresyon Özeti

- Toplam regresyon: **2083 PASS**
- FAIL: **0** · SKIP: **0**
- Exchange Write Request: **0**
- Secret Exposure: **0**
- Commit: `5d0a50a`

## M. Misyon İstatistikleri

| Ajan | Kapsam | Yeni test |
|---|---|---|
| 01 | Mimari (`docs/architecture/monitoring_alerting.md`) | — |
| 02 | Monitoring Core | 66 |
| 03 | Alert Engine | 65 |
| 04 | Monitoring Service | 36 |
| 05 | Monitoring API | 41 |
| 06 | Monitoring Export | 71 |
| 07 | Security Verification | 109 |
| 08 | Full Regression | 99 |

Test tabanı zinciri: 1596 → 1662 → 1727 → 1763 → 1804 → 1875 → 1984 →
2083 PASS.
