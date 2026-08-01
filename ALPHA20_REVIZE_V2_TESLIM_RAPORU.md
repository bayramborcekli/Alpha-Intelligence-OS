# Alpha20 Revize v2 — Teslim ve Doğrulama Raporu

Tarih: 2026-08-01  
Hedef: `D:\GENESIS2000\Alpha-Intelligence-OS`  
Mod: **Yalnız PAPER**

## Sonuç

Eski `alpha20_revize.zip` içindeki engeller önce ayrı bir geçici klasörde
yeniden üretildi. Düzeltmeler, minimal `app.py` paketini büyütmek yerine tam
uygulamanın gerçek Windows yürütme yoluna uygulandı. Tam uygulamadaki
`app.py`, `serve_windows.py`, `launcher_windows.py`, `start_alpha.cmd` ve
kullanıcı `config.json` dosyaları değiştirilmedi ve v2 ZIP tarafından
üzerlerine yazılmayacak.

## Yeniden Üretim ve Kök Nedenler

| Bulgu | Yeniden üretim | Kök neden | Uygulanan çözüm |
|---|---|---|---|
| Ana ekran ve başlangıç zinciri kaybı | Eski ZIP'in 15 dosyası tam proje ile karşılaştırıldı | ZIP, 256 KB üzerindeki tam `app.py` yerine bağımsız minimal Flask uygulaması taşıyordu | Minimal `app.py` atıldı; tam uygulama ve gerçek başlatıcı zinciri korundu |
| `/home` 404 | Eski ZIP Flask test istemcisinde `/home` çağrısı 404 döndü | Minimal uygulamada rota yoktu | Tam uygulamanın mevcut `/home` rotası kullanılıyor |
| Config yolu uyumsuzluğu | Eski paket kök `config.json` içerirken aktif modüller `alpha20_v1/config.json` okudu | İki farklı config konumu | V2, kullanıcı config'ini paketlemiyor; aktif kod mevcut proje config yolunu kullanıyor |
| Windows `fcntl` riski | Eski kaynakta doğrudan `import fcntl` bulundu | POSIX'e özel kilit | Aktif yol `portable_flock.py` üzerinden Windows `msvcrt`/POSIX uyumlu kilit kullanıyor |
| SHORT kapanış/PnL | SHORT 100 giriş, 90 TP, 110 SL ve fiyat 100 iken eski kod TP kapattı; net zarar yazdı | LONG yönlü TP/SL ve PnL formülü SHORT'a da uygulanmıştı | Yöne duyarlı TP/SL, gross PnL, MFE ve MAE hesapları eklendi |
| Minimum 4 saat tutma | Eski config değeri okunuyor ancak kapanış yolunda kullanılmıyordu | Kapanış kapısına bağlanmamış parametre | Varsayılan 4 saat; kâr/TP/trailing/time kapanışları bekler, stop-loss daima anında çalışır |
| Komisyon iki kez | Eski beklenen ekonomi hesabında round-trip maliyet iki ayrı kalemde toplandı | Aynı maliyetin iki defa düşülmesi | Tek round-trip ücret düşümü korunuyor |
| Stablecoin/leveraged filtreleri | `USDCUSDT`, `BTCUPUSDT` ve benzerleri eski filtreyi geçti | Yalnız quote/karakter kontrolü vardı | Base asset stablecoin listesi ve leveraged suffix filtresi aktif evren yoluna bağlandı |
| Karar eşiği pasif | Config eşiği yerine sabit `60` karşılaştırması bulundu | Aktif karar yolu config'i kullanmıyordu | Aktif model `decision_threshold` değerini çalışma anında okuyor |
| Otomatik Paper motoru başlamıyor | Eski ZIP'te `serve_windows.py`/launcher zinciri ve bootstrap çağrıları yoktu | Bağımsız uygulama paketi | Mevcut zincir korundu: `start_alpha.cmd -> launcher_windows.py -> serve_windows.py` |
| Waitress/Windows bağımlılığı | Eski ZIP requirements içinde Windows sunucu yolu eksikti | Minimal paket tam bağımlılık setini taşımıyordu | Mevcut tam `requirements.txt` korunup ZIP'e eklendi |
| PAPER kilidi | Eski ve yeni kodda kontrol edildi | — | `LIVE_ORDERS=DISABLED`, `paper_only=true`, borsa yazma isteği `0` kaldı |
| Regresyon eksikliği | Eski ZIP'te tam uygulama entegrasyon testleri yoktu | Bağımsız paketleme | Her engeli kapsayan `tests/test_alpha20_revize_v2_compat.py` eklendi |

## Değişen / Paketlenen Dosyalar

Aktif kod:

- `alpha20_v1/alpha20.py`
- `alpha20_v1/auto_controller.py`
- `alpha20_v1/dual_learning.py`
- `alpha20_v1/dual_model.py`
- `alpha20_v1/universe_manager.py`
- `static/js/trading_home.js`
- `portable_flock.py`

Test ve doğrulama:

- `tests/test_alpha20_revize_v2_compat.py`
- `tests/test_kimi_runtime_p0.py`
- `tests/test_dual_model.py`
- `tests/test_paper_trading.py`
- `tests/test_mission2400_windows_launcher.py`
- `tests/test_project_governance.py`
- `tests/test_hold_intelligence.py`
- `tests/test_signal_visibility.py`
- `tests/test_cost_aware_gates.py`
- `VERIFY_ALPHA20_REVIZE_V2_WINDOWS.cmd`

Yönetişim ve teslim:

- `.github/workflows/project-governance.yml`
- `AGENTS.md`
- `CURRENT_TASK.md`
- `DECISIONS.md`
- `SYSTEM_CONSTITUTION.md`
- `governance/project_state.json`
- `ALPHA20_REVIZE_V2_README.md`
- `ALPHA20_REVIZE_V2_TESLIM_RAPORU.md`
- `KIMI_AL_SAT_PAKETI_README.md`
- `requirements.txt`
- `requirements-dev.txt`

Özellikle paketlenmeyenler: `.env`, anahtar/parola, `data/`, `runtime/`,
state, işlem geçmişi, loglar, `app.py`, `serve_windows.py`,
`launcher_windows.py`, `start_alpha.cmd` ve kullanıcı `config.json`.

## Git Diff Özeti

Çalışma dalındaki Kimi P0 değişiklikleriyle birlikte tabana göre özet:

```text
17 files changed, 721 insertions(+), 116 deletions(-)
```

Yeni teslim ve test dosyaları untracked olduğundan yukarıdaki `git diff
--stat` sayısına dahil değildir. Kullanıcı değişiklikleri silinmedi; `git
reset`, `checkout` veya `clean` kullanılmadı.

## Test Komutları ve Sonuçları

Python derleme ve diff denetimi:

```text
python -m compileall -q app.py serve_windows.py launcher_windows.py portable_flock.py alpha20_v1
PASS

git diff --check
PASS
```

Hedefli ve ilgili regresyon paketi:

```text
python -m pytest -q \
  tests/test_alpha20_revize_v2_compat.py \
  tests/test_dual_model.py \
  tests/test_paper_trading.py \
  tests/test_mission2400_windows_launcher.py \
  tests/test_project_governance.py \
  tests/test_signal_visibility.py::TestRealBehaviorUnchanged \
  tests/test_hold_intelligence.py::TestRealBehaviorUnchanged \
  tests/test_cost_aware_gates.py

192 passed in 1.29s
```

Tam test koleksiyonu ayrıca çalıştırıldı:

```text
13758 passed, 321 failed, 11 skipped, 11 errors in 232.64s
```

Tam koleksiyon yeşil değildir. Kalan hataların büyük bölümü görev dışı,
önceden var olan local-admin/auth test fixture'larının yeni kalıcı yönetici
kaynağıyla uyuşmamasından doğuyor. Güvenlik sınırı gereği yerel yönetici
kaydına veya auth davranışına müdahale edilmedi. Bu sonuç gizlenmemiş ve
"tam paket geçti" olarak sunulmamıştır.

## Başlangıç ve Aktif Paper Motoru Kanıtı

Gerçek modüllerle, dış piyasa çağrıları devre dışı bırakılmış yerel bootstrap
smoke testi:

```text
HEALTH_HTTP 200
HOME_HTTP 200
HOME_NOT_404 True
DUAL_THREAD_ACTIVE True
LIVE_ORDERS DISABLED
CONFIG_MODE PAPER
```

Doğrulanan gerçek başlangıç yolu:

```text
start_alpha.cmd -> launcher_windows.py -> serve_windows.py
```

`serve_windows.py`, `universe_manager.start_auto_loop()` ve aktif dual model
döngüsünü başlatır. Kontrolcü yalnız mevcut kullanıcı ayarı izin verirse
başlar; v2 ZIP kullanıcı config'ini değiştirmez.

## Canlı İşlemlerin Kapalı Olduğu Kanıt

```text
dual_model.snapshot()["live_orders"] == "DISABLED"
governance.project_state.safety.paper_only == true
governance.project_state.safety.exchange_write_requests_allowed == 0
```

Futures, transfer, çekim veya borsaya yazma yolu eklenmedi.

## Gerçek Windows Doğrulaması

Bu hazırlama ortamı Linux olduğundan `D:` sürücüsündeki Windows `.cmd`
dosyası burada fiziksel olarak çalıştırılamaz. ZIP içindeki
`VERIFY_ALPHA20_REVIZE_V2_WINDOWS.cmd`, hedef Windows makinesinde aşağıdaki
kontrolleri tek seferde yapar:

1. Governance preflight
2. Python compileall
3. Hedefli regresyon testleri
4. Gerçek `start_alpha.cmd` başlangıcı
5. `/health` ve `/home` HTTP kontrolü
6. `LIVE_ORDERS=DISABLED`, PAPER ve sıfır borsa yazma kontrolü

Windows kanıtı, bu dosyanın hedef makinede `[PASS]` üretmesiyle tamamlanır.
