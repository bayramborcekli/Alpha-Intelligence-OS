"""Mission 1500.1 / Agent 11 — Tam test ve regresyon doğrulaması.

Ek statik kontroller: float kullanımı, secret pattern, unsafe HTML,
yeni yazma rotası ve exchange-write taramaları.
"""

import ast
import re
from pathlib import Path

import pytest

import app as flask_app

ROOT = Path(__file__).resolve().parent.parent
INTEL_MODULES = ("intelligence_models.py", "intelligence_api.py",
                 "risk_explainer.py", "recommendation_api.py",
                 "intelligence_service.py", "intelligence_settings.py")


class TestFloatBan:
    """Para matematiği yalnızca Decimal — float() dönüşümü yasak."""

    @pytest.mark.parametrize("mod", INTEL_MODULES)
    def test_no_float_calls(self, mod):
        tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
        offenders = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            if isinstance(n.func, ast.Name) and n.func.id == "float":
                offenders.append(n.lineno)
            # builtins.float / np.float64 gibi nitelikli çağrılar
            if isinstance(n.func, ast.Attribute) and \
                    n.func.attr in ("float", "float64", "float32"):
                offenders.append(n.lineno)
        assert not offenders, (mod, offenders)

    @pytest.mark.parametrize("mod", INTEL_MODULES)
    def test_no_float_literals_in_money_context(self, mod):
        """Decimal(0.1) gibi float-literal Decimal kurulumu yasak."""
        tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))

        def is_float_literal(a):
            if isinstance(a, ast.Constant) and isinstance(a.value, float):
                return True
            # -0.5 gibi tekli işleçli float
            return (isinstance(a, ast.UnaryOp)
                    and isinstance(a.operand, ast.Constant)
                    and isinstance(a.operand.value, float))

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            # Decimal(...) veya decimal.Decimal(...) veya takma ad D(...)
            callee = None
            if isinstance(n.func, ast.Name):
                callee = n.func.id
            elif isinstance(n.func, ast.Attribute):
                callee = n.func.attr
            if callee == "Decimal":
                for a in n.args:
                    assert not is_float_literal(a), (mod, n.lineno)


class TestSecretPatterns:
    """Kaynakta gömülü secret/pattern yok."""

    PATTERNS = (
        # anahtar = "değer" (sözlük/atama, : veya =, tek/çift tırnak)
        re.compile(r"(?i)['\"]?(api[_-]?key|api[_-]?secret|secret[_-]?key"
                   r"|access[_-]?token|auth[_-]?token|password)['\"]?"
                   r"\s*[:=]\s*['\"][A-Za-z0-9+/_\-=]{12,}['\"]"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY"),
        re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.eyJ"),   # JWT gövdesi
    )

    @pytest.mark.parametrize("mod", INTEL_MODULES +
                             ("templates/intelligence.html",))
    def test_no_hardcoded_secrets(self, mod):
        text = (ROOT / mod).read_text(encoding="utf-8")
        for pat in self.PATTERNS:
            assert not pat.search(text), (mod, pat.pattern)

    def test_no_env_secret_names_in_template(self):
        text = (ROOT / "templates/intelligence.html")\
            .read_text(encoding="utf-8")
        for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET",
                     "SESSION_SECRET", "PASSWORD_HASH"):
            assert name not in text, name


class TestUnsafeHtml:
    """Şablonlarda güvensiz işaretleme yolu yok."""

    def test_no_jinja_safe_filter_on_dynamic_data(self):
        for tpl in (ROOT / "templates").glob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            assert "|safe" not in text and "| safe" not in text, tpl.name

    def test_intelligence_innerhtml_paths_escaped(self):
        text = (ROOT / "templates/intelligence.html")\
            .read_text(encoding="utf-8")
        for chunk in re.findall(r"innerHTML\s*=\s*([^;]+);", text):
            assert ("esc(" in chunk or "vy(" in chunk or "empty(" in chunk
                    or "item(" in chunk or "<tr><td colspan" in chunk
                    or "failState" in chunk), chunk

    def test_no_document_write_or_eval(self):
        text = (ROOT / "templates/intelligence.html")\
            .read_text(encoding="utf-8")
        for banned in ("document.write", "eval(", "new Function",
                       "insertAdjacentHTML", "outerHTML"):
            assert banned not in text, banned


class TestRouteSurface:
    """1500.1, yazma metotlu YENİ rota eklemedi."""

    # 1500.1 ÖNCESİNDEN beri var olan yazma rotaları (sabit anlık görüntü)
    ALLOWED_WRITE_ROUTES = {
        "/adaptive/auto-paper", "/adaptive/enable", "/adaptive/kill-switch",
        "/adaptive/learn-now", "/adaptive/mode", "/adaptive/settings",
        "/adaptive/unlock", "/api/v1/auth/login", "/api/v1/auth/logout",
        "/api/v1/refresh", "/api/v1/risk/simulator", "/bot/start",
        "/bot/stop", "/coins/add", "/coins/delete", "/coins/move",
        "/coins/preset", "/login", "/settings", "/setup/hash",
        # Kurulum sihirbazı (görev #43/#48/#49): parola hash kaydı,
        # auth öncesi tek seferlik kurulum — bilinçli genişletme.
        "/setup/save",
        "/smart/analyze", "/smart/apply", "/smart/coin-action",
        "/smart/mode", "/smart/pin", "/smart/restore", "/smart/settings",
        # Mission 1600 / Agent 04: Automation manuel tetik (CSRF+auth
        # korumalı; append yalnız Core üzerinden — bilinçli genişletme)
        "/api/automation/run", "/api/v1/automation/run",
        # Mission 2300 / Agent 03: Hesaplarım kayıt defteri (CSRF+auth
        # korumalı; yalnız sunum meta verisi — sır saklamaz, işlem
        # mantığına dokunmaz — bilinçli genişletme)
        "/api/accounts/<account_id>/connect",
        "/api/accounts/<account_id>/disconnect",
        "/api/accounts/<account_id>/primary",
        "/api/accounts/<account_id>/edit",
        "/api/accounts/<account_id>/test",
        "/api/accounts/<account_id>/sync",
        # Bot Kontrolü paneli: yürütme modu seçimi (CSRF+auth korumalı;
        # LIVE fail-closed reddedilir — bilinçli genişletme)
        "/execution/mode",
        # Mission 2200 / Agent 01: Operation Control Center yazma uçları
        # (CSRF+auth korumalı; tamamı Mission 2100 kontrollü yürütme
        # hattına bağlanır — bilinçli genişletme)
        "/api/operation-control/automation/<command>",
        "/api/operation-control/symbols/<symbol>/<command>",
        "/api/operation-control/positions/<position_id>/close",
        "/api/operation-control/global/stop-new-entries",
        "/api/operation-control/global/request-close-all",
        "/api/operation-control/global/kill-switch",
    }

    def test_no_new_write_routes(self):
        offenders = []
        for rule in flask_app.app.url_map.iter_rules():
            writes = {"POST", "PUT", "PATCH", "DELETE"} & set(rule.methods)
            if writes and rule.rule not in self.ALLOWED_WRITE_ROUTES:
                offenders.append((rule.rule, sorted(writes)))
        assert not offenders, offenders

    def test_intelligence_routes_get_only(self):
        for rule in flask_app.app.url_map.iter_rules():
            if "intelligence" in rule.rule:
                assert not ({"POST", "PUT", "PATCH", "DELETE"}
                            & set(rule.methods)), rule.rule

    EXPECTED_INTEL_ROUTES = {
        "/intelligence",
        "/api/intelligence", "/api/v1/intelligence",
        "/api/intelligence/summary", "/api/v1/intelligence/summary",
        "/api/intelligence/insights", "/api/v1/intelligence/insights",
        "/api/intelligence/recommendations",
        "/api/v1/intelligence/recommendations",
        "/api/intelligence/status", "/api/v1/intelligence/status",
        "/api/intelligence/settings", "/api/v1/intelligence/settings",
        # Mission 1700 / Agent 04: Portfolio Intelligence (GET-only,
        # salt-okunur) — yüzey bilinçli olarak genişletildi.
        "/api/portfolio/intelligence", "/api/v1/portfolio/intelligence",
        # Mission 1700 / Agent 05: UI sayfası (GET-only).
        "/portfolio-intelligence",
        # Mission 1700 / Agent 06: Export uçları (GET-only).
        "/api/portfolio/intelligence/export/json",
        "/api/v1/portfolio/intelligence/export/json",
        "/api/portfolio/intelligence/export/csv",
        "/api/v1/portfolio/intelligence/export/csv",
        # Mission 1800 / Agent 04: Strategy Intelligence (GET-only,
        # advisory-only) — yüzey bilinçli olarak genişletildi.
        "/api/strategy/intelligence", "/api/v1/strategy/intelligence",
        # Mission 1800 / Agent 05: UI sayfası (GET-only).
        "/strategy-intelligence",
    }

    def test_intelligence_route_set_exact(self):
        """Tüm takma adlar VAR ve fazladan intelligence rotası YOK."""
        paths = {r.rule for r in flask_app.app.url_map.iter_rules()
                 if "intelligence" in r.rule}
        assert paths == self.EXPECTED_INTEL_ROUTES, (
            paths ^ self.EXPECTED_INTEL_ROUTES)

    def test_intelligence_routes_method_contract(self):
        """Her intelligence rotası yalnızca GET+HEAD+OPTIONS sunar."""
        for rule in flask_app.app.url_map.iter_rules():
            if "intelligence" in rule.rule:
                assert set(rule.methods) <= {"GET", "HEAD", "OPTIONS"}, \
                    (rule.rule, sorted(rule.methods))


class TestExchangeWriteScan:
    """Uygulama genelinde borsa yazma çağrısı taraması (1500.1 zinciri)."""

    def test_intel_modules_never_reference_signing(self):
        """İmzalama/istek gönderme KODU yok — kelimeler yalnızca
        SECRET_FIELD_BLOCKLIST gibi savunma listelerinde geçebilir."""
        for mod in INTEL_MODULES:
            tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
            names = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Call):
                    if isinstance(n.func, ast.Name):
                        names.add(n.func.id.lower())
                    elif isinstance(n.func, ast.Attribute):
                        names.add(n.func.attr.lower())
            banned = {"hmac", "new", "sign", "signed_request",
                      "private_request", "order_request", "request",
                      "urlopen"}
            assert not names & banned, (mod, names & banned)
