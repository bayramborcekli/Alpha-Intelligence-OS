# MISSION 2100 — RESMİ TABAN (BASELINE)

Bu belge Mission 2100 için değişmez başlangıç referansıdır.

İki taban değeri BİLİNÇLİ olarak ayrıdır ve karıştırılamaz:

- **Manifesto tabanı (makine-okunur):** `execution_regression_manifest.py`
  — Agent 09'un sertifikaladığı ÇEKİRDEK tabanı: commit `01aa429`
  (Agent 08 teslimi), regresyon **3704**. Bu, sertifika paketi
  eklenmeden ÖNCEKİ dondurulmuş çekirdeğin kanıtıdır ve değişmezdir.
- **Tam paket tabanı (Mission 2100 başlangıç regresyonu):** Agent 09
  teslimi commit `a45dde3` — sertifika testleri DAHİL tam regresyon
  **4375 PASS**. Mission 2100 ajanları bu sayıya karşı teslimat yapar.

---

## 1. Execution Core Sürümü

- **Sürüm:** v1.0.0 "Execution Foundation" — **CERTIFIED**
- **Taban commit:** `a45dde3` (Agent 09, Mission 2000)

## 2. Regresyon Tabanı

- **Tam paket:** 4375 PASS · FAIL 0 · 1 bilinçli skip (~104 sn,
  `python -m pytest -q`, commit `a45dde3`)
- **Manifesto-kilitli çekirdek tabanı:** 3704 (commit `01aa429`,
  `execution_regression_manifest.py`, test-korumalı)
- Mission 2100'de hiçbir ajan bu sayının ALTINA düşen bir durumla
  teslimat yapamaz; her teslimat taban + yeni testler verir.

## 3. Mimari Dondurma

- 20 modül `execution_architecture_freeze.py` ile kilitli
- Kalıcı boru hattı: API → Service → Risk → Gate → Kill Switch →
  Adapter → Broker (bağımlılık yönü KALICI)
- Katman import sözleşmesi AST-testli; ihlal = regresyon hatası

## 4. Kamu API Dondurması

- 20 modülün `__all__` yüzeyi manifesto ile birebir test edilir
- Ekleme: açık mimar incelemesi · Kaldırma/yeniden adlandırma: YASAK
- Kırıcı değişiklik majör sürüm (v2.0.0) gerektirir

## 5. ADR Tabanı

`ADR_INDEX.md` — ADR-001…ADR-010 bağlayıcıdır. Mission 2100
tasarımları bu kararlarla çelişemez; sapma yeni ADR + mimar onayı ister.

## 6. Bilinen Genişletme Noktaları

Çekirdek dondurulmuştur; aşağıdaki noktalar GENİŞLETMEYE açıktır:

| Genişletme | Mekanizma | Çekirdek değişikliği |
|------------|-----------|----------------------|
| **Paper Trading** | `ExecutionMode.PAPER` + paper broker adaptörü (`BrokerAdapter` alt sınıfı) + resolver kaydı | 0 satır |
| **Shadow Mode** | `ExecutionMode.SHADOW` + emir GÖNDERMEYEN gölge adaptör; iz karşılaştırma katmanı çekirdek DIŞI | 0 satır |
| **Micro Live** | `ExecutionMode.MICRO_LIVE` + gerçek Transport/SigningProvider uygulamaları (çekirdek dışı) + canlı yetkilendirme | İzin kapısı GENİŞLETMESİ gerekir (ADR-009 uyarınca, mimar incelemeli); adaptör/transport tarafı 0 satır |
| Yeni broker | Yeni `BrokerAdapter` alt sınıfı + `BrokerProfile` | 0 satır |
| Kalıcılık / telemetri | Çekirdek DIŞI, tek yönlü tüketici katmanları | 0 satır |

## 7. Değişmez Kurallar (Mission 2100 boyunca)

- Exchange Write Request = 0 (canlı geçiş ayrı, açık insan onayı ister)
- Secret Exposure = 0 · Decimal-only · bilinmeyen → None
- Steril hata kodları · deterministik yürütme (ADR-010)
