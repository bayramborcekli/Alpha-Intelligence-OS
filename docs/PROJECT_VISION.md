# Proje Vizyonu

Alpha Intelligence OS; tek sahipli, PAPER (simüle) modda çalışan,
**salt-okunur** borsa bağlantılı bir kripto analiz ve karar destek
platformudur.

## İlkeler
- **Önce güvenlik:** borsaya yazma yeteneği mimari olarak yoktur; canlı
  emir yürütme kalıcı kilitlidir.
- **Doğrulanabilirlik:** her sayı gerçek kaynaktan gelir; bilinmeyen değer
  açıkça "Veri Yok"/"—" gösterilir, asla uydurulmaz.
- **Açıklanabilirlik:** risk ve portföy değerlendirmeleri gerekçesi,
  etkisi, güven seviyesi ve kanıtıyla sunulur (Mission 1500.1).
- **Determinizm:** analiz katmanı kural tabanlıdır; aynı girdi aynı
  çıktıyı üretir. Harici LLM bu fazda kilitlidir.
- **Yalnızca tavsiye:** sistem hiçbir işlem kararı vermez; operatörün
  değerlendirmesine sunar.

## Uzun vadeli yön
Sıradaki genişlemeler (her biri ayrı mission + güvenlik incelemesiyle):
zenginleştirilmiş açıklanabilirlik seviyeleri, geçmişe dönük içgörü
arşivi ve — yalnızca açık sahip talimatıyla — kontrollü harici model
değerlendirmesi. Ayrıntı: `docs/ROADMAP.md`.
