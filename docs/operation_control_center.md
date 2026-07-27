# Operation Control Center (Mission 2200 / Agent 01)

Operatörün kontrollü ticaret çalışma alanı. Sayfa: `/operation-center`.
API ad alanı: `/api/operation-control/*`.

## Mimari sınır (değişmez)

```
Tarayıcı (operation_control.js)
  → Flask /api/operation-control/* (CSRF + auth + _security_gate)
    → OperationControlService (durum makinesi, idempotency, denetim)
      → Mission 2100 ControlledExecutionAPI (SERTİFİKALI, DONMUŞ)
        → izin kapısı → risk → kill-switch → yürütme → defter
```

- UI hiçbir zaman borsa API'sini veya iç yürütme modüllerini doğrudan
  çağırmaz; tek yol yukarıdaki hattır.
- `version_manifest.json` içinde SHA-256 ile sabitlenen 31 modül
  DEĞİŞTİRİLMEMİŞTİR; Operation Control katmanı yalnız bu sertifikalı
  yüzeyi ÇAĞIRIR (`tests/test_operation_control_architecture.py`).
- Varsayılanlar fail-closed: mod PAPER, otomasyon STOPPED, semboller
  DISABLED, micro-live yetkisi DENIED, kill-switch etkinken ticareti
  açan komutlar 423 ile reddedilir.

## Modüller

| Modül | Sorumluluk |
|---|---|
| `operation_control_models.py` | Kapalı enum'lar + dondurulmuş görünüm modelleri |
| `operation_control_errors.py` | Steril hata hiyerarşisi (`KOD:alan`) |
| `operation_control_policy.py` | Kapalı geçiş tabloları + güvenlik bağımlılık değerlendirmesi |
| `operation_control_service.py` | Durum makinesi, idempotency, yıkıcı eylem koruması, kapatma niyetleri |
| `operation_control_mapper.py` | Ham veri → görünüm modeli (Decimal-katı, bilinmeyen → UNKNOWN) |
| `operation_control_snapshot.py` | Tutarlı anlık görüntü + veri tazeliği (FRESH/STALE/UNKNOWN) |
| `operation_control_audit.py` | Append-only denetim halkası (5000), sızıntı token reddi |
| `operation_control_api.py` | Çerçeveden bağımsız zarf/HTTP kod katmanı |

## API sözleşmesi

Her yanıt zarfı: `ok`, `data`, `error_code`, `message`,
`correlation_id`, `generated_at`, `data_freshness`, `execution_mode`.
Yazma yanıtları ek olarak: `action_id`, `idempotency_status`,
`audit_recorded`, `lifecycle_status`.

### Okuma uçları (GET)
`/status`, `/products`, `/positions`, `/orders`, `/signals`,
`/reconciliation`, `/risk`, `/audit` — tümü `no-store`, auth zorunlu.

### Yazma uçları (POST, CSRF + auth + idempotency_key)
- `/automation/{start|pause|resume|stop}` — kapalı geçiş tablosu;
  geçersiz geçiş → **409 INVALID_TRANSITION**; aynı-durum tekrarına
  yeni yan etki üretilmez.
- `/symbols/<symbol>/{enable|pause|resume|stop}` — sembol düzeyi kontrol;
  bir sembolün durumu diğerini etkilemez.
- `/positions/<id>/close` — kontrollü PAPER **kapatma niyeti**; yıkıcı
  eylem koruması (neden + `ONAYLIYORUM` + idempotency anahtarı).
- `/global/stop-new-entries` — yeni girişleri bloklar; pozisyon kapatmaz.
- `/global/request-close-all` — pozisyon başına ayrı niyet; kısmi
  başarısızlık `PARTIAL` + `detail_codes` ile pozisyon bazında raporlanır.
- `/global/kill-switch` — `engage: bool`; sertifikalı mekanizmayı
  (Mission 1500 `safety_guard` yolu) tetikler; "pozisyonlar kapandı"
  iddiası ASLA üretilmez.

### HTTP kodları
409 geçersiz geçiş / idempotency çakışması · 403 politika reddi ·
423 kill-switch · 503 bağımlılık kullanılamaz/bayat · 422 politika
nedeniyle reddedilen niyet · 404 bilinmeyen hedef · 400 bozuk istek.

## Kill-switch semantiği (kritik)

Sertifikalı katmanda `KillSwitchState.ENABLED` = koruma mekanizması
sağlıklı ve yazma İZİNLİ; acil durdurma etkinse anlık görüntü
`DISABLED` geçilir ve sertifikalı hat `KILL_SWITCH_DENIED` üretir.
Uygulamadaki acil durdurma bayrağı `alpha20_v1/config.json →
adaptive_system.kill_switch` kaynağındadır (Mission 1500 ile aynı).

## Kapatma niyeti semantiği

- Kapatma isteği doğrudan borsa çağrısı DEĞİLDİR; görünen pozisyondan
  türetilen defter anlık görüntüsü ile sertifikalı PAPER hattına giren
  bir NİYETTİR (`lifecycle_status: CLOSE_REQUESTED`).
- Sertifikalı hat reddederse sonuç steril kodla `DENIED` olur
  (`EXECUTION_REJECTED`, `KILL_SWITCH_DENIED`, …); ham istisna metni
  operatöre sızmaz.

## Bilinen sınırlar (sonraki agent giriş noktaları)

1. Stop-loss / take-profit güncelleme: sertifikalı API desteklemiyor —
   UI'da devre dışı düğme, dekoratif uç YOK.
2. Risk limiti düzenleme: sertifikalı API desteklemiyor — salt gösterim.
3. Mutabakat motoru yok: mutabakat durumu dürüstçe UNKNOWN gösterilir.
4. Sinyal zaman çizelgesi veri kaynağı henüz bağlı değil (boş liste +
   "öneri emir değildir" etiketi).
5. Canlı (LIVE) yürütme yok; kapatma niyetleri PAPER simülasyonudur ve
   UI bunu onay diyaloğunda açıkça söyler.
6. Servis durumu (otomasyon durumu, idempotency kayıtları, denetim
   zinciri, stop-new-entries bayrağı) SÜREÇ-YERELDİR. Gunicorn 2
   worker ile çalıştığından aynı idempotency anahtarı farklı
   worker'larda bağımsız kabul edilebilir ve durum worker'lar arası
   ayrışabilir. Kill-switch bayrağı ise `alpha20_v1/config.json`
   üzerinden GLOBALDİR (tüm worker'lar aynı dosyayı okur). Riskin
   kapsamı PAPER niyetleriyle sınırlıdır; kalıcı çözüm (paylaşımlı
   durum deposu veya tek-yazar koordinasyonu) sonraki agent'ın
   giriş noktasıdır.
