---
name: Sinyal görünürlüğü kanonik durum
description: Sembol karar durumu tek kaynaktan (dual_model.symbol_status); zaman kıyası ve hata zarfı kuralları
---

- Sembol→karar durumu TEK kaynak: `dual_model.symbol_status()` (runtime rejections+positions+last_refresh). Overview products ve `/api/paper/state` strategies buradan beslenir; UNKNOWN yalnız runtime okunamayınca.
- **Zaman damgası kıyası asla lexical string yapılmaz** — farklı ofsetler ('+03:00' vs 'Z') yanlış kazanan seçer. Her zaman UTC instant'a normalize et; bozuk damga fail-closed en-eski sayılır.
- **Why:** mimar incelemesi lexical `>=` kıyasının bayat kararı kanonik gösterdiğini yakaladı; ayrıca API hata cevabında `str(exc)` iç yol sızdırır — sabit error_code + log.
- **How to apply:** yeni karar/etkinlik "en yeni kazanır" birleştirmelerinde `_ts_utc` kalıbını kullan; yeni JSON hata zarflarında ham istisna metni dönme.
- ProductView (operation_control_models) frozen dataclass'ına yeni alan eklerken trailing default'la ekle — pozisyonel kurulum kırılmaz; eski sözleşme testleri (mission2300 banned-terms, paper_consolidation alan seti, cockpit sütunları) bilinçli yüzey değişiminde gerekçeli yorumla güncellenir.
- `import fcntl` koşulsuz yazılamaz — Windows'ta 500 üretir; `try/except ImportError: import portable_flock as fcntl` + test_mission2400 parametrik listesine dosyayı ekle. Windows import simülasyonunda portable_flock ÖNCE yüklenmeli.
