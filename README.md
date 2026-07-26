# Alpha Intelligence OS

PAPER modunda çalışan, salt-okunur borsa bağlantılı kripto işlem platformu.
Canlı emir yürütme **devre dışıdır** ve yalnızca sunucu tarafı bayrakla,
açık talimatla etkinleştirilebilir.

## Bileşenler
- `app.py` — Flask web uygulaması (kabuk, kimlik doğrulama, v1 API'ler)
- `alpha20_v1/` — işlem motoru, yapılandırma, görev kanıtları (değiştirilmez)
- `exchange_gateway.py` — salt-okunur borsa geçidi (yalnızca GET + allowlist)
- `dashboard_api.py` — canlı pano servis katmanı (tipli modeller, önbellek,
  tazelik politikası, yazma sayaçları — Mission 1400.2)
- `portfolio_api.py` — Portföy/Pozisyon/Emir servis katmanı + güvenli CSV
  dışa aktarım (formül-enjeksiyon korumalı — Mission 1400.3)
- `executive_api.py` — Yönetici üst çubuğu özeti: doğrulanmış-yalnız
  performans şeridi + durum çubuğu (Mission 1400.5)
- `risk_api.py` — Risk İstihbarat Motoru: deterministik sağlık skoru,
  maruziyet/konsantrasyon analizi, tekrarsız tavsiye uyarıları, ekle-yalnız
  risk geçmişi, yerel işlem-öncesi simülatör (Mission 1400.6; salt-okunur)
- `ledger_api.py` — Defter/Denetim/Rapor servis katmanı: ekle-yalnız defter
  görünümü, bütünlük doğrulaması, 1310B mutabakat kanıtı, sabit rapor kayıt
  defteri, güvenli CSV (Mission 1400.4; PDF ertelendi)
- `intelligence_models.py` / `intelligence_api.py` / `risk_explainer.py` /
  `recommendation_api.py` / `intelligence_service.py` /
  `intelligence_settings.py` — Intelligence Katmanı: deterministik,
  yalnızca-tavsiye analiz; açıklanabilir içgörüler, öncelikli öneriler,
  `/intelligence` sayfası ve `GET /api/intelligence/*` uçları
  (Mission 1500.1; salt-okunur, harici LLM kilitli)
- `tools/` — görev (mission) betikleri
- `tests/` — test paketi (`python -m pytest tests/ -q`)

## Başlatma
```
gunicorn -c gunicorn.conf.py app:app
```

## Gerekli sahip secret'ları
- `ALPHA_OWNER_USERNAME` — sahip kullanıcı adı
- `ALPHA_OWNER_PASSWORD_HASH` — parola hash'i (sihirbaz `/setup` ile üretilir;
  düz metin parola asla saklanmaz)
- `SESSION_SECRET` — oturum imzalama

Eksiklerse uygulama **Kurulum Kilitli** moduna girer. Ayrıntılar:
- [docs/live_application.md](docs/live_application.md)
- [docs/security_model.md](docs/security_model.md)
- [docs/deployment_replit.md](docs/deployment_replit.md)
- [docs/operator_guide_tr.md](docs/operator_guide_tr.md)

## Güvenli varsayılanlar
`ALPHA_ENABLE_DRY_RUN`, `ALPHA_ENABLE_LIVE_TRADING`, `ALPHA_ENABLE_TRANSFERS`,
`ALPHA_ENABLE_WITHDRAWALS` = `false`. Borsa secret'ları yalnızca backend'de
kalır; frontend'e hiçbir secret geçmez.

Intelligence katmanı da varsayılan kapalıdır: açmak için
`ALPHA_INTELLIGENCE_ENABLED=true`. Harici LLM 1500.1 kapsamında sert
kilitlidir (ortamdan bile açılamaz). Diğer belgeler:
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- [docs/MISSION_INDEX.md](docs/MISSION_INDEX.md)
- [docs/TEST_PROGRAM.md](docs/TEST_PROGRAM.md)
- [docs/RELEASE_NOTES_1500_1.md](docs/RELEASE_NOTES_1500_1.md)
- [docs/RELEASE_NOTES_1500_2.md](docs/RELEASE_NOTES_1500_2.md)
- [CHANGELOG.md](CHANGELOG.md) · [AGENTS.md](AGENTS.md)
