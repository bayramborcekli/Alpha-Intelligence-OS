"""Mimari bekçi: credential çözümleme ve hesap veri yolu tekliği.

Bu test dosyası, aşağıdakiler yeniden ortaya çıkarsa KIRMIZI olur:
1. Resolver dışında runtime modülde doğrudan Binance env okuması.
2. Runtime aktif istemcilerde legacy Global env adları.
3. dashboard_api dışında ikinci bir imzalı hesap (wallet) fetch yolu.
4. İmzalı/özel Futures endpoint'i (/fapi) — public PAPER market
   verisi (klines) yanlış pozitif ÜRETMEZ.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Resolver + geriye dönük uyumluluk katmanı: env isimleri YALNIZ burada.
RESOLVER_ALLOWED = {"exchange_credentials.py", "local_env.py"}

LEGACY_GLOBAL_NAMES = ("BINANCE_GLOBAL_API_KEY",
                       "BINANCE_GLOBAL_API_SECRET",
                       "BINANCE_API_KEY", "BINANCE_API_SECRET",
                       "BINANCE_API_Key", "BINANCE_Secret_Key")


def _runtime_files():
    skip_dirs = {"tests", "tools", "attached_assets", "docs",
                 ".pythonlibs", ".cache", ".git", ".local", ".agents",
                 "alpha20_v1", "__pycache__", "data"}
    for p in sorted(ROOT.glob("*.py")):
        if p.name in skip_dirs:
            continue
        yield p
    for sub in ROOT.iterdir():
        if sub.is_dir() and sub.name not in skip_dirs \
                and not sub.name.startswith("."):
            for p in sorted(sub.rglob("*.py")):
                if "__pycache__" not in p.parts:
                    yield p


def test_no_direct_binance_env_reads_outside_resolver():
    pat = re.compile(r"(os\.environ|os\.getenv)[^\n]{0,80}BINANCE",
                     re.IGNORECASE)
    offenders = []
    for p in _runtime_files():
        if p.name in RESOLVER_ALLOWED:
            continue
        for i, line in enumerate(
                p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, (
        "Resolver dışında doğrudan Binance env okuması yasak:\n"
        + "\n".join(offenders))


def test_no_legacy_global_env_names_in_runtime_clients():
    offenders = []
    for p in _runtime_files():
        if p.name in RESOLVER_ALLOWED:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for name in LEGACY_GLOBAL_NAMES:
                if re.search(rf"[\"']{re.escape(name)}[\"']", line):
                    offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, (
        "Legacy Global env adları yalnız resolver'ın geriye dönük "
        "uyumluluk katmanında yaşayabilir:\n" + "\n".join(offenders))


def test_single_signed_account_fetch_path():
    # İmzalı hesap endpoint literal'leri yalnız kanonik servis
    # (dashboard_api) ve düşük seviyeli istemci modüllerinde yaşar.
    allowed = {
        "/api/v3/account": {"dashboard_api.py",
                            "binance_global_client.py"},
        "/open/v1/account/spot": {"dashboard_api.py",
                                  "binance_tr_client.py"},
    }
    offenders = []
    for p in _runtime_files():
        text = p.read_text(encoding="utf-8")
        for literal, files in allowed.items():
            if literal in text and p.name not in files:
                offenders.append(f"{p.name}: {literal}")
    assert not offenders, (
        "Duplicate imzalı hesap fetch yolu yasak (kanonik servis: "
        "dashboard_api):\n" + "\n".join(offenders))


def test_portfolio_delegates_to_canonical_service():
    text = (ROOT / "portfolio_api.py").read_text(encoding="utf-8")
    assert "_spot_account_raw" in text and "_tr_account_raw" in text
    assert "/api/v3/account" not in text
    assert "os.environ" not in text


def test_no_authenticated_futures_endpoints():
    # Spot-only mimari: imzalı /fapi çağrısı geri gelemez. Public
    # klines (PAPER market verisi) bilinçli istisnadır.
    pat = re.compile(r"/fapi/v\d+/(?!klines|continuousKlines|"
                     r"markPriceKlines|premiumIndex|ticker|exchangeInfo)"
                     r"[A-Za-z]+")
    offenders = []
    for p in _runtime_files():
        for i, line in enumerate(
                p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and "tombstone" not in line.lower() \
                    and not line.strip().startswith("#"):
                offenders.append(f"{p.name}:{i}: {line.strip()}")
    assert not offenders, ("İmzalı Futures endpoint yasak:\n"
                           + "\n".join(offenders))


def test_no_cwd_relative_paper_ledger_read():
    # PAPER defteri ROOT'a bağlı STATE_PATH ile okunur; göreli
    # Path("alpha20_v1/state.json") Windows'ta yanlış UNKNOWN üretir.
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'Path("alpha20_v1/state.json")' not in src, (
        "PAPER defteri göreli yolla okunamaz — STATE_PATH kullanın")


def test_wallets_portfolio_delegate_to_snapshot():
    # /api/accounts/wallets ve /portfolio kendi exchange fetch /
    # health hesaplaması yapamaz; kanonik _account_snapshot'a delege eder.
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for fn in ("def api_accounts_wallets", "def api_accounts_portfolio"):
        body = src.split(fn)[1].split("\n@app.")[0]
        assert "_account_snapshot(" in body, f"{fn} delege etmeli"
        for banned in ("global_spot_account(", "tr_account(",
                       "BinanceGlobalClient", "BinanceTRClient",
                       "os.environ", "credentials("):
            assert banned not in body, (
                f"{fn}: ikinci hesap fetch/health yolu yasak ({banned})")
