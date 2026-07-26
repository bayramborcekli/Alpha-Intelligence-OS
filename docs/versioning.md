# Alpha Intelligence OS — Sürümleme Politikası

**Sürüm:** 0.1.0-alpha  
**Son güncelleme:** ownership-baseline-v1

---

## Semantic Versioning

Bu proje [Semantic Versioning 2.0.0](https://semver.org/lang/tr/) kullanır:

```
MAJOR.MINOR.PATCH[-PRE_RELEASE]

Örnek: 0.1.0-alpha
```

| Alan | Anlamı |
|---|---|
| `MAJOR` | Geriye dönük uyumsuz API/davranış değişikliği |
| `MINOR` | Geriye dönük uyumlu yeni özellik |
| `PATCH` | Hata düzeltmesi |
| `-PRE_RELEASE` | `alpha`, `beta`, `rc1` — kararlı değil |

---

## Mevcut Sürüm

**0.1.0-alpha**

| Özellik | Durum |
|---|---|
| Temel Flask dashboard | ✅ |
| Kimlik doğrulama (auth.py) | ✅ |
| PAPER modu kilidi | ✅ |
| Alpha Brain (karar motoru) | ✅ |
| Alpha Risk (adaptif risk) | ✅ |
| Smart Coin Selection | ✅ |
| Güvenlik baseline | ✅ |
| Sahiplik baseline | ✅ |
| İlk çalıştırma sihirbazı | 🔲 planned |
| Oturum yenileme | 🔲 planned |
| Alpha Treasury | 🔲 planned |
| Alpha Guardian | 🔲 planned |
| Alpha Learning | 🔲 planned |

---

## Sürüm Dosyaları

| Dosya | Amaç |
|---|---|
| `VERSION` | Tek kaynak of truth — düz metin sürüm numarası |
| `version.py` | Python'dan `get_version()` ile okunur |

### Sürüm Okuma (Python)

```python
from version import get_version, __version__, VERSION_INFO

print(get_version())          # "0.1.0-alpha"
print(__version__)            # "0.1.0-alpha"
print(VERSION_INFO["major"])  # 0
print(VERSION_INFO["pre"])    # "alpha"
```

### Dashboard'da Sürüm Gösterimi

Dashboard'da **yalnızca sürüm numarası** gösterilebilir:
- ✅ `v0.1.0-alpha`
- ❌ Build hash, commit SHA, sunucu IP, Python sürümü — gizli altyapı bilgileri gösterilmez

---

## Sürüm Yükseltme Prosedürü

1. `VERSION` dosyasını güncelle
2. `CHANGELOG.md` (gelecek) bölümüne değişiklikleri yaz
3. Testleri çalıştır: `python -m pytest`
4. Git commit: `git commit -m "chore: bump version to X.Y.Z"`
5. Git tag: `git tag vX.Y.Z`

---

## Checkpoint İsimlendirme

Replit checkpoint isimleri şu formatı izler:

```
<özellik>-baseline-v<N>
<modül>-v<MAJOR>.<MINOR>

Örnekler:
  ownership-baseline-v1
  security-baseline-v1
  alpha-risk-v1.2
```
