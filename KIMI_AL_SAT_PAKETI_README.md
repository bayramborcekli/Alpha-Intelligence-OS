# Kimi — Alpha Intelligence OS Al/Sat Kod Paketi

Taban: GitHub `main@04abee56926714350403324900ef196ae37e5bf3`

Bu paketteki ana yürütme zinciri:

- `alpha20_v1/auto_controller.py`: sembol tarama, veri kalitesi, karar skoru ve otomatik Paper açılış orkestrasyonu.
- `alpha20_v1/alpha20.py`: klasik Paper giriş, SL/TP kapanış ve defter işlemleri.
- `alpha20_v1/dual_model.py`: CORE/OPPORTUNITY Paper giriş–çıkış motoru ve kanonik runtime.
- `alpha20_v1/decision_engine.py`: karar puanı ve koşul kontrolleri.
- `alpha20_v1/adaptive_risk.py`: pozisyon büyüklüğü, stop ve hedef hesabı.
- `alpha20_v1/market_regime.py`: piyasa/coin rejim tespiti.
- `alpha20_v1/safety_guard.py`: kill-switch, günlük zarar ve drawdown güvenlikleri.
- `alpha20_v1/dual_learning.py`: kapanmış Paper işlemlerinden öğrenme köprüsü.
- `alpha20_v1/universe_manager.py`: izlenecek sembol evreni.
- `serve_windows.py`: Windows başlangıç ve Paper runtime bağlantısı.
- `app.py`, `static/js/trading_home.js`, `templates/trading_home.html`: API ve Windows ekranı.

Kimi P0 talimatları doğrultusunda bu çalışma kopyasında henüz commit edilmemiş
runtime karantina/yedek/fsync ve gerçek Spot hacim–spread–timestamp–önceki fiyat
değişiklikleri bulunmaktadır. Canlı emir yolu kapalıdır; borsa yazma isteği 0'dır.

Pakette secret, `.env`, API anahtarı, runtime JSON/JSONL, işlem geçmişi veya log yoktur.
