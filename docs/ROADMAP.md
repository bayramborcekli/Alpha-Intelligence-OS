# Yol Haritası

## Tamamlanan
- **1310B** — Defter mutabakat kanıtı
- **1400 serisi** — platform temeli, canlı pano, portföy/pozisyon/emir,
  defter/denetim/raporlar, yönetici üst çubuğu, Risk İstihbarat Motoru
- **1500.1** — Intelligence Katmanı: deterministik çekirdek, açıklama ve
  tavsiye motorları, servis, salt-okunur API, UI, ayarlar, güvenlik ve
  regresyon doğrulaması (805 test)

## Aday sonraki adımlar (planlanmış değil — ayrı mission gerektirir)
- Intelligence içgörü geçmişi (ekle-yalnız arşiv) ve trend görünümü
- Açıklanabilirlik seviyelerinin UI'da seçilebilir hale gelmesi
  (`basic` görünümü)
- Rapor dışa aktarımına Intelligence özeti bölümü
- Kontrollü harici model değerlendirmesi — yalnızca açık sahip talimatı,
  ayrı güvenlik incelemesi ve veri-sızıntı analizi ile (bugün sert kilitli)

## Kalıcı kısıtlar
Her adımda: borsa yazma isteği 0 · secret sızıntısı 0 · tam regresyon
yeşil · advisory-only dil · deterministik doğrulanabilirlik.
