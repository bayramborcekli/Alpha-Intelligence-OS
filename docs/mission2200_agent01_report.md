# Mission 2200 / Agent 01 — Operation Control Center Raporu

Tarih: 2026-07-27 · Temel: Mission 2100 v1.1.0 "Controlled Execution"
(`version_manifest.json`, 31 donmuş modül, SHA-256 sabit).

## Kapsam

1. Operation Control Center: `/operation-center` sayfası + 
   `/api/operation-control/*` ad alanı (8 GET + 6 POST ailesi).
2. Yeni katman: 8 modül (`operation_control_*.py`), UI
   (`templates/operation_control.html` + `static/js/operation_control.js`),
   8 test dosyası.
3. Legacy çelişki giderimi (aşağıdaki envanter).

## Eylem bağlama tablosu (UI → sertifikalı hat)

| UI eylemi | Uç | Servis yolu | Sertifikalı temas |
|---|---|---|---|
| Otomasyon Başlat/Duraklat/Sürdür/Durdur | `POST /automation/<cmd>` | `execute_automation_command` (kapalı tablo) | durum makinesi; yürütme yetkisi vermez |
| Sembol Etkinleştir/Duraklat/Sürdür/Durdur | `POST /symbols/<s>/<cmd>` | `execute_symbol_command` | sembol izolasyonu |
| Pozisyon Kapatma İsteği | `POST /positions/<id>/close` | `request_position_close` | `ControlledExecutionAPI.submit` (PAPER LIMIT/IOC karşı yön) |
| Tümünü Kapatma İsteği | `POST /global/request-close-all` | `request_close_all` | pozisyon başına ayrı submit; PARTIAL raporu |
| Yeni Girişleri Durdur | `POST /global/stop-new-entries` | `stop_new_entries_action` | giriş bloğu; kapatma iddiası yok |
| Acil Kill-Switch | `POST /global/kill-switch` | `record_kill_switch` + `safety_guard.activate_kill_switch` | Mission 1500 sertifikalı yolu |
| Stop/TP güncelle | YOK | YOK | sertifikalı API desteklemiyor — devre dışı düğme |
| Risk limiti düzenle | YOK | YOK | sertifikalı API desteklemiyor — salt gösterim |

## Legacy envanteri ve kararlar

| Bulgu | Sınıf | Eylem |
|---|---|---|
| `templates/automation.html` "hiçbir emir oluşturulmaz / SALT İZLEME" | MISLEADING_CAPABILITY_CLAIM | Yeniden yazıldı: analiz otomasyonu ile kontrollü ticaret ayrımı + Operation Center bağlantısı |
| `docs/operator_guide_tr.md` "kapatma düğmesi yoktur ve olmayacaktır" | OUTDATED_DESIGN_PROMISE | Güncel sürüm notu eklendi; tarih falsifiye edilmedi |
| `docs/automation.md` evrensel salt-okunur iddiası | SCOPE_OVERCLAIM | Katman-kapsamlı ifadeye daraltıldı + sürüm notu |
| `docs/API_REFERENCE.md` "salt-okunur yüzey" başlığı | SCOPE_OVERCLAIM | "veri/intelligence yüzeyi" + operation-control notu |
| `app.py.bak`, `app.py.bak2/3`, `templates/dashboard.html.bak3`, `alpha20_v1/config.json.bak2/3` | JUNK_BACKUP | `git rm` + dosya silme + `.gitignore` `*.bak*` |
| `alpha20_v1/.auto_controller.lock`, `automation_state.json(.lock)` | RUNTIME_STATE_IN_GIT | Untrack + `.gitignore` |
| `attached_assets/Pasted-*.txt` görev metinleri | FROZEN_HISTORY | Dokunulmadı (tarihî kayıt) |

## Güvenlik

- Tüm POST uçları CSRF + oturum korumalı (`_security_gate`);
  `ALLOWED_WRITE_ROUTES` bilinçli genişletme olarak belgelendi.
- Yıkıcı eylemler: neden + `ONAYLIYORUM` + idempotency anahtarı;
  çift tıklama REPLAYED döner, farklı imza aynı anahtar → 409 CONFLICT.
- Denetim kaydı sızıntı tokenlarını (api_key/secret/traceback…) reddeder.
- Yeni modüller yalnız stdlib + sertifikalı katmanı import eder; borsa
  istemcisi importu yok (`tests/test_operation_control_security.py`).

## Test kanıtı

`tests/test_operation_control_{models,policy,service,api,ui,security,`
`architecture,legacy_remediation}.py` — ayrıntılı sayılar final
raporda; donmuş 31 modülün SHA-256 doğrulaması ve v1.1.0 release
testleri değişmeden geçmektedir.
