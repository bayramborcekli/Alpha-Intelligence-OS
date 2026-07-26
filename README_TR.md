# Alpha-20 v1 — Güvenli Başlangıç

Bu sürüm **yalnızca PAPER (sanal işlem)** modundadır. Binance hesabına bağlanmaz, API anahtarı istemez ve gerçek emir göndermez.

> **Mission 1500.1 — Intelligence Katmanı:** Platform artık deterministik,
> yalnızca-tavsiye niteliğinde bir Intelligence katmanı içerir
> (`/intelligence` sayfası ve `GET /api/intelligence/*` uçları).
> Varsayılan kapalıdır; `ALPHA_INTELLIGENCE_ENABLED=true` ile açılır.
> Harici LLM bu fazda kilitlidir; tüm analiz yereldir ve hiçbir işlem
> kararı vermez. Ayrıntı: `docs/RELEASE_NOTES_1500_1.md`.

## Ne yapar?

- Binance USDⓈ-M Futures'ın herkese açık mum verilerini okur.
- BTCUSDT, ETHUSDT ve SOLUSDT'yi tarar.
- 1 saatlik trend ile 15 dakikalık sinyali karşılaştırır.
- EMA, RSI, ATR ve hacim kullanarak puan üretir.
- En yüksek puanlı fırsatta sanal LONG veya SHORT açar.
- ATR tabanlı stop ve 1:2 risk/ödül hedefi kullanır.
- Aynı anda yalnızca bir sanal pozisyon taşır.
- Günlük zarar ve art arda kayıp limitlerinde yeni işlem açmaz.
- Durumu `state.json`, kayıtları `alpha20.log` dosyasında tutar.

## Güvenlik

- Bu sürüme Binance API anahtarı veya şifre yazmayın.
- Gerçek para transfer etmeyin.
- `mode` değerini değiştirmek gerçek işlem özelliği açmaz.
- Geçmiş performans, gelecekte kâr garantisi değildir.

## Bilgisayarda çalıştırma

Python 3.11 veya daha yenisini kurun.

```bash
pip install -r requirements.txt
python alpha20.py --once
```

Sürekli çalıştırmak için:

```bash
python alpha20.py
```

Sanal hesabı sıfırlamak için:

```bash
python alpha20.py --reset --once
```

## iPhone kullanıcısı için

iPhone, bu Python işlemini güvenilir biçimde 7/24 arka planda çalıştırmaz. Sonraki adım bu klasörü bir bulut çalışma ortamına yüklemektir. İlk kurulumda yine PAPER modunu kullanacağız.

## Ayarlar

`config.json` içinden:

- `symbols`: izlenen pariteler
- `minimum_score`: işlem eşiği
- `risk_per_trade_pct`: sanal işlem başına risk
- `daily_loss_limit_pct`: günlük zarar limiti
- `max_consecutive_losses`: art arda zarar limiti

İlk test sırasında ayarları değiştirmemek daha sağlıklıdır.
