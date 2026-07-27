# Mission 2000 — Execution Foundation: Mimari (DONDURULMUŞ)

MISSION 2000 — EXECUTION FOUNDATION · AGENT 01 — EXECUTION ARCHITECTURE

Bu belge Execution mimarisini TANIMLAR ve DONDURUR. Yalnız mimaridir:
yürütme mantığı, exchange bağlantısı, emir yürütme veya üretim işlem
davranışı İÇERMEZ. Sonraki ajanlar bu sözleşmelere birebir uyar.

Taban: Mission 1900 OFFICIALLY CLOSED · commit `a79415e` ·
2207 PASS / 0 FAIL / 0 SKIP · Exchange Write Request 0 ·
Secret Exposure 0.

## 1. Onaylı Yığın (Execution Architecture)

```
Portfolio Intelligence
        ↓
Strategy Intelligence
        ↓
Monitoring
        ↓
Execution API              (execution_api.py)
        ↓
Execution Service          (execution_service.py)
        ↓
Risk Engine                (execution_risk_engine.py)
        ↓
Kill Switch                (execution_kill_switch.py)
        ↓
Exchange Adapter           (broker_adapter.py — BrokerAdapter, soyut)
        ↓
Exchange Implementation    (örnek: binance_spot_adapter.py)
```

Adapter soyutlaması `BrokerAdapter`'dır (soyut sınıf) ve hem kripto
exchange'lerini hem aracı kurumları (broker) kapsar. Gelecek
uygulamalar — tümü aynı `BrokerAdapter` soyutlamasının arkasında:

- BinanceSpotAdapter (örnek ilk uygulama)
- BinanceFuturesAdapter
- InteractiveBrokersAdapter
- MidasAdapter
- BybitAdapter
- OKXAdapter
- KrakenAdapter

**Hiçbir Strategy katmanı bir Exchange ile doğrudan iletişim
kuramaz.** Tüm yürütme ZORUNLU olarak şu zincirden geçer:

```
Execution Service → Risk Engine → Kill Switch → Exchange Adapter
```

## 2. Katman Sorumlulukları (Sahiplik)

| Katman | Sahiplik |
|---|---|
| Execution API | İstek doğrulama · meta veri üretimi · `execution_id` · zaman damgaları · yanıt zarfı |
| Execution Service | Emir yaşam döngüsü orkestrasyonu · yönlendirme · durum geçişleri |
| Risk Engine | Maruziyet doğrulama · pozisyon boyutlama · azami zarar · günlük limitler · sermaye doğrulama |
| Kill Switch | Acil durdurma · işlem durdurma (trading halt) · devre kesici (circuit breaker) · zorunlu ret |
| Exchange Adapter | Exchange soyutlaması · istek çevirisi · yanıt normalizasyonu |
| Exchange Implementation | REST/WebSocket protokolü · kimlik doğrulama · imzalama · uç nokta iletişimi |

Sahiplik örtüşmesi YOKTUR.

## 3. Sahiplik Kuralları (MUST NOT)

- Execution API emir YÜRÜTEMEZ.
- Execution Service risk HESAPLAYAMAZ.
- Risk Engine exchange ile İLETİŞEMEZ.
- Kill Switch strateji HESAPLAYAMAZ.
- Exchange Adapter iş mantığı İÇEREMEZ.
- Exchange Implementation risk mantığı İÇEREMEZ.

## 4. Onaylı Bağımlılık Grafiği

```
Execution API → Execution Service → Risk Engine → Kill Switch
    → Exchange Adapter → Exchange Implementation
```

Yasak: ters bağımlılık · döngüsel bağımlılık · katman atlama ·
çapraz sahiplik.

## 5. Yürütme Yaşam Döngüsü (gelecek)

```
Execution Request → Risk Validation → Kill Switch Validation
    → Exchange Translation → Exchange Execution → Exchange Response
    → Normalized Result → Monitoring
```

Monitoring SALT-OKUNUR kalır. Monitoring asla emir yürütmez.

## 6. Meta Veri Sahipliği

YALNIZ Execution API üretir: `execution_id`, `requested_at`,
`processed_at`. Tüm alt katmanlar meta veriyi yalnız TAŞIR
(propagate); asla üretmez, asla değiştirmez.

## 7. Onaylı Kamu API'si (DONDURULMUŞ — toplam 7 giriş)

| Katman | Onaylı girişler |
|---|---|
| Execution API | `execute_order_api` |
| Execution Service | `execute_order`, `ExecutionService` |
| Risk Engine | `validate_execution` |
| Kill Switch | `verify_execution` |
| Exchange Adapter | `BrokerAdapter` |
| Exchange Implementation | `BinanceSpotAdapter` |

Ek kamu API YOKTUR.

## 8. Güvenlik Modeli

Mimari şunları GARANTİ eder:

- Exchange izolasyonu — exchange erişimi yalnız Exchange
  Implementation katmanında
- Risk izolasyonu — risk mantığı yalnız Risk Engine'de
- Kill Switch izolasyonu — durdurma yetkisi yalnız Kill Switch'te
- Strategy izolasyonu — Strategy katmanları Execution'a erişemez
- Monitoring izolasyonu — Monitoring salt-okunur, yürütme yolu yok

Ayrıca: gizli yürütme yolu YOK · doğrudan exchange erişimi YOK ·
arka planda yürütme YOK · otomatik yeniden deneme YOK · kalıcılık
YOK · zamanlayıcı YOK.

Canlı işlem varsayılan olarak **DISABLED** kalır.

## 9. Kapsam Dışı (Agent 01)

Exchange bağlantısı · REST · WebSocket · kimlik doğrulama · HMAC
imzalama · emirler · iptal · değiştirme · dolumlar (fills) · Paper
Trading · Shadow Trading · Micro Live — hiçbiri bu ajanda YOKTUR.
Bu belge üretim davranışı eklemez.
