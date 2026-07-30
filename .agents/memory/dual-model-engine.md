---
name: Dual-model PAPER engine
description: CORE SCALP + OPPORTUNITY BURST iki-liste mimarisi — sözleşmeler ve kritik kurallar
---
İki kısa vadeli PAPER modeli tek modülde: `alpha20_v1/dual_model.py`.
- Runtime durumu git dışı `alpha20_v1/dual_model_runtime.json` (flock'lu `_update_runtime`); restart sonrası listeler + açık pozisyonlar korunur.
- **Tüm Binance istekleri `_guarded_get` üzerinden**: alpha20'nin paylaşımlı 429/418 dosya durumuna uyar ve kendi 429'larını oraya kaydeder. Doğrudan `requests.get` eklemek anti-ban korumasını deler.
- Monitör TP/SL kararını yalnız TAZE toplu `ticker/price` fiyatıyla verir; RateLimited'da çıkış ertelenir (bayat cache ile karar yasak).
- Sahiplik arbitrajı iki modelin adayları BİRLEŞİK kümede çözülür (yüksek net edge kazanır, kaybeden DUPLICATE_MODEL_OWNERSHIP).
- Reason code sözlüğü 16 sabit kod (`REASON_CODES`); yeni kod eklerken testteki sayı bekçisi güncellenmeli.
- Döngü gunicorn post_fork'ta flock ile tek worker'da koşar; LIVE ORDERS DISABLED (private uç/imza yok — test bekçisi var).
- UI/API tek kanonik snapshot: `/api/dual-model/state`.
