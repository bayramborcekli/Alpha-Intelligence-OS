---
name: Master integration runtime prefs
description: Kalıcı kullanıcı tercihleri (risk profili, tarama aralığı) ve orchestrator başlangıç zinciri
---

- Kalıcı tercihler `alpha20_v1/runtime_preferences.json` (git dışı, atomik yazım): selected_risk_profile (KORUMA/DENGELI/AGRESIF), scan_interval_minutes (varsayılan 5), analysis_scheduler, universe_max (sert üst sınır 20).
- Risk profilleri `services/risk_profiles.py` — adaptive_risk YÜZDE birimi bekler (0.25 = %0,25), profiller kesir (Decimal 0.0025) saklar; `adaptive_flags()` çeviriyi yapar. Birimleri karıştırma.
- **Why:** yüzde/kesir karışıklığı 100x sizing hatası üretir; tercih config.json'a yazılırsa runtime-drift olur.
- **How to apply:** orchestrator (`services/system_runtime_orchestrator.start`) HEM `serve_windows` HEM `gunicorn.conf.py post_fork` içinde, controller başlamadan ÖNCE çağrılır — yeni giriş noktası eklenirse orada da çağır.
- `universe_manager.get_smart_config` yükleme anında BTC/ETH/SOL pin + max 20 zorlar; smart route save'leri bunu `smart_config.json`'a yazar (repo dosyası!) — commit öncesi drift kontrolünde checkout et.
- Dynamic Universe yenilemesi zamanlayıcı çevriminden `um.scheduled_refresh(symbols)` ile senkron çağrılır (ilk çevrimde hemen, sonra eval_interval_hours); sonuç smart_config["scheduler_refresh"] (COMPLETED/FAILED+kod). NOT_RUN_YET kanonik kaynağı bu sonuçtur — panel-tetikli analiz maskeleyemez; NOT_RUN_YET pipeline GREEN'i bloklar ve verify_scheduler PASS vermez.
- Kanonik Analysis Scheduler = auto_controller döngüsü; `sro.scheduler_status(controller_status, preference)` tercihi (Operation Control automation_state öncelikli) GERÇEK worker'dan ayırır — tercih RUNNING + worker yok → STARTUP_FAILED, GREEN yasak; `readiness` blocker listesi döner. Legacy ALPHA_AUTOMATION_* yalnız intelligence raporlama, analiz hattının doğruluk kaynağı DEĞİL.
- Üst çubukta risk profili seçici 1400.5 "buton yok" sözleşmesinin İSTİSNASI: ayrı bölümde, ana script'ten SONRA yaşar (bar-slice testleri ilk `</script>`e kadar bakar); CSRF meta `shell.html`/`dashboard.html`'e eklendi.
