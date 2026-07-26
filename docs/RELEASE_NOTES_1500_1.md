# Sürüm Notları — Mission 1500.1 "Intelligence Katmanı"

**Tarih:** 2026-07-26 · **Dal:** main · **Kapanış commit'i:** bkz. kapanış raporu

## Ne eklendi?
Operatörün portföy, pozisyon ve risk durumunu **açıklanabilir** biçimde
görmesini sağlayan, tamamen yerel ve deterministik bir Intelligence
katmanı: `/intelligence` sayfası + salt-okunur `GET /api/intelligence/*`
uçları.

## Intelligence mimarisi (5 katman)
1. **Veri sözleşmeleri** (`intelligence_models.py`) — tipli, Decimal-string
   serileştirilen modeller; emir/secret alanları model düzeyinde yasak.
2. **Deterministik çekirdek** (`intelligence_api.py`) — kural tabanlı
   analiz; aynı girdi her zaman aynı çıktıyı üretir. LLM, rastgelelik,
   ağ erişimi ve borsa importu yoktur.
3. **Açıklama + tavsiye motorları** (`risk_explainer.py`,
   `recommendation_api.py`) — Risk Motoru çıktısını değiştirmeden Türkçe
   Gözlem/Gerekçe/Etki/Öneri şablonlarına çevirir; öncelik sıralı,
   tekilleştirilmiş tavsiyeler üretir.
4. **Servis katmanı** (`intelligence_service.py`) — salt-okunur
   sağlayıcılardan (pano, risk) anlık görüntü toplar; kaynak bazlı
   tazelik ve sterile hata kaydı tutar.
5. **API + UI** — kimlik doğrulamalı GET-only uçlar ve mobil uyumlu sayfa.

## Explainable insight yapısı
Her içgörü şu alanları taşır: **Gözlem** (ne görüldü), **Gerekçe** (neden),
**Olası etki**, **Öneri**, **Confidence**, **Kanıt** (kaynak+alan+değer).
Risk skoru için izlenebilir döküm üretilir ("100 taban puandan N düşüldü;
faktörler: ...").

## Confidence seviyeleri
`HIGH / MEDIUM / LOW / INSUFFICIENT_DATA`. Tazelik kanıtı olmayan
kaynaktan asla HIGH üretilmez. UI'da yalnızca renkle değil metinle de
gösterilir ("Yüksek güven", "Yetersiz veri" …).

## Advisory-only politikası
Tüm çıktılar `advisory_only:true` işaretlidir. Emir dili ("al", "sat",
"pozisyon aç"), fiyat tahmini ve kazanç garantisi üretilmez; UI'da işlem
düğmesi yoktur. Sistem hiçbir otomatik karar vermez.

## Feature flag'ler ve 1500.1 varsayılanları
| Değişken | Varsayılan |
|---|---|
| `ALPHA_INTELLIGENCE_ENABLED` (eski `ALPHA_ENABLE_INTELLIGENCE` de okunur) | `false` — açmak için `true` |
| `ALPHA_INTELLIGENCE_LOCAL_ONLY` | `true` |
| `ALPHA_INTELLIGENCE_EXTERNAL_LLM_ENABLED` | `false` (sert kilitli) |
| `ALPHA_INTELLIGENCE_EXPLAINABILITY_LEVEL` | `detailed` |
| `ALPHA_INTELLIGENCE_RECOMMENDATION_LEVEL` | `advisory` |

Geçersiz değer → güvenli varsayılan + uyarı kodu (ham değer asla
loglanmaz/dönmez).

## UI kullanımı
1. `ALPHA_INTELLIGENCE_ENABLED=true` yapın ve uygulamayı yeniden başlatın.
2. Giriş yaptıktan sonra kenar menüden **🧠 Intelligence**'a girin.
3. Bölümler: genel durum kartları, portföy özeti, risk açıklaması,
   içgörüler, öneriler, veri tazeliği, kısmi-veri uyarısı, son güncelleme.
   Bilinmeyen değerler "—" gösterilir.

## Güvenlik sınırlamaları
- Yalnızca GET; kimlik doğrulama zorunlu; `no-store` önbellek politikası.
- Intelligence modülleri borsa istemcisini, ledger'ı ve audit geçmişini
  **import bile edemez** (statik testle zorlanır); dosya yazımı yasaktır.
- Çıktılarda API key/secret/token/oturum verisi bulunmaz (testle taranır).
- Kullanıcı/borsa kaynaklı metin karar mekanizmasını değiştiremez
  (kural tabanlı motor — prompt injection etkisizdir) ve HTML'e her zaman
  kaçırılarak basılır.

## External LLM neden kapalı?
1500.1'in amacı **doğrulanabilir ve deterministik** bir taban kurmaktır:
aynı girdinin aynı çıktıyı üretmesi denetlenebilirliği ve test
edilebilirliği garanti eder. Harici LLM hem veri gizliliği (portföy
verisinin dışarı çıkmaması) hem de çıktı tutarlılığı nedeniyle bu fazda
**ortam değişkeninden bile açılamayacak şekilde** kilitlidir. İleride
ayrı bir mission ile, yalnızca açık talimat ve ayrı güvenlik incelemesiyle
değerlendirilebilir.

## Bilinen sınırlamalar
- Intelligence varsayılan kapalıdır (bilinçli güvenli varsayılan).
- Çıktılar mevcut pano/risk verisinin kalitesiyle sınırlıdır; kaynak
  eksikse durum PARTIAL/UNAVAILABLE olarak açıkça işaretlenir, değer
  uydurulmaz.
- Sektör/korelasyon analizi yoktur (doğrulanmış veri kaynağı yok).
- Statik AST kontrolleri güvence katmanıdır, matematiksel kanıt değildir.
