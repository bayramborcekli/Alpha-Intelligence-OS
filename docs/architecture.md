# Alpha Intelligence OS — Mimari Belge

**Sürüm:** 0.1.0-alpha  
**Durum:** Aktif geliştirme — PAPER modu  
**Son güncelleme:** ownership-baseline-v1

---

## Genel Bakış

Alpha Intelligence OS, tamamen **kağıt (simüle) işlem** üzerine tasarlanmış,
modüler bir kripto analiz ve karar destek sistemidir. Gerçek emir gönderimi
yoktur ve bu sürümde planlanmamaktadır.

```
┌─────────────────────────────────────────────────────┐
│               Alpha Intelligence OS                  │
│                  v0.1.0-alpha                         │
│                                                      │
│  ┌──────────────┐   ┌──────────────┐                 │
│  │  Flask Web   │   │   Auth Gate  │                 │
│  │  Dashboard   │◄──│  (auth.py)   │                 │
│  └──────┬───────┘   └──────────────┘                 │
│         │                                            │
│  ┌──────▼────────────────────────────────────┐       │
│  │              Alpha Brain                  │       │
│  │         (decision_engine.py)              │       │
│  └──────┬──────────────────────┬─────────────┘       │
│         │                      │                     │
│  ┌──────▼──────┐   ┌───────────▼────────┐            │
│  │ Alpha Risk  │   │  Smart Coin Select │            │
│  │  Engine     │   │  (universe_mgr)    │            │
│  └──────┬──────┘   └───────────┬────────┘            │
│         │                      │                     │
│  ┌──────▼──────────────────────▼────────────┐        │
│  │         PAPER State & Logging             │        │
│  │     (state.json, *.jsonl, alpha20.log)    │        │
│  └───────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────┘
```

---

## Modüller

### ✅ Mevcut — Üretimde

| Modül | Dosya(lar) | Durum | Açıklama |
|---|---|---|---|
| **Authentication** | `auth.py`, `templates/login.html` | ✅ Aktif | IP rate-limit, PBKDF2 hash, 8h oturum |
| **PAPER Lock** | `app.py` → `enforce_paper_mode_lock()` | ✅ Aktif | Canlı emir özelliğini kalıcı olarak kilitler |
| **Web Dashboard** | `app.py`, `templates/dashboard.html` | ✅ Aktif | Flask, CSRF korumalı, güvenlik başlıkları |
| **Alpha Brain** | `alpha20_v1/decision_engine.py` | ✅ Aktif | Teknik skor, sinyal üretme |
| **Alpha Risk** | `alpha20_v1/adaptive_risk.py` | ✅ Aktif | ATR-tabanlı adaptif risk motoru |
| **Smart Coin Selection** | `alpha20_v1/universe_manager.py` | ✅ Aktif | Coin evreni yönetimi, momentum filtresi |
| **Security Log** | `security_log.py` | ✅ Aktif | RotatingFileHandler, olay maskeleme |
| **Backup** | `backup.sh`, `restore.sh` | ✅ Aktif | SHA-256 bütünlük denetimi, retention |

---

### 🔲 Planned — Henüz Geliştirilmedi

| Modül | Durum | Açıklama |
|---|---|---|
| **Alpha Treasury** | 🔲 planned | Portföy izleme, toplam P&L hesaplama, rezerv yönetimi |
| **Alpha Guardian** | 🔲 planned | Çoklu varlık korelasyon izlemesi, portföy-düzeyinde risk limiti |
| **Alpha Learning** | 🔲 planned | Geçmiş kararlardan otomatik parametre güncelleme |
| **Exchange Adapter** | 🔲 planned | Borsa API soyutlama katmanı (bu sürümde PAPER — emir gönderilmez) |
| **First-run Wizard** | 🔲 planned | `ADMIN_PASSWORD_HASH` kurulum rehberi (Task #5) |
| **Session Refresh** | 🔲 planned | Aktif oturum yenileme, sürpriz çıkış önleme (Task #6) |

---

## Veri Akışı

```
Piyasa Verisi (Binance REST — okuma)
        │
        ▼
  universe_manager.py   ←── config.json (kullanıcı ayarları)
        │
        ▼
  decision_engine.py    ←── smart_config.json (akıllı seçim)
        │
        ▼
  adaptive_risk.py      ←── safety_state.json (kill switch)
        │
        ▼
  PAPER State           ──► state.json (pozisyon/bakiye)
        │
        ▼
  Karar Logları         ──► *.jsonl, alpha20.log
```

---

## Güvenlik Mimarisi

- **Kimlik doğrulama:** `before_request` hook — tüm rotaları tek noktadan korur  
- **CSRF:** Flask-WTF `CSRFProtect`, tüm POST'larda token zorunlu  
- **HTTP güvenlik başlıkları:** CSP, X-Frame-Options, HSTS (production)  
- **Oturum:** 8 saat zaman aşımı, `SESSION_SECRET` ile şifreli  
- **Rate limiting:** 5 deneme / 5 dakika, IP tabanlı  
- **PAPER kilidi:** Başlangıçta `enforce_paper_mode_lock()` çalışır

Detay için: [`security.md`](security.md)

---

## Dizin Yapısı

```
/
├── app.py                  # Flask uygulaması ve tüm rotalar
├── auth.py                 # Kimlik doğrulama ve rate limiting
├── security_log.py         # Güvenli olay loglama
├── version.py              # Merkezi sürüm yönetimi
├── VERSION                 # Sürüm numarası (0.1.0-alpha)
├── backup.sh               # Yedekleme scripti
├── restore.sh              # Geri yükleme scripti
├── alpha20.py              # Bot başlatıcı
├── alpha20_v1/             # Çekirdek iş mantığı
│   ├── config.json         # Kullanıcı yapılandırması (git'te)
│   ├── decision_engine.py
│   ├── adaptive_risk.py
│   ├── universe_manager.py
│   └── ...
├── templates/              # Jinja2 şablonları
│   ├── dashboard.html
│   └── login.html
├── tests/                  # Test paketi
│   ├── test_adaptive.py    # 68 test
│   ├── test_security.py    # 32 test
│   └── test_ownership.py   # Sahiplik ve bütünlük testleri
└── docs/                   # Bu belge seti
```
