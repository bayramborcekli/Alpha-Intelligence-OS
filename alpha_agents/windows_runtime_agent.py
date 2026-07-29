"""Windows Runtime Recovery Agent — kalıcı sistem agent'ı.

Kanıtlanmış tek tık kurtarma akışını (services/windows_runtime_recovery)
registry/dashboard/endpoint üzerinden erişilebilir kılar. Yeni mantık
İCAT ETMEZ; mevcut akışı yeniden kullanır. Yalnız yerel Windows'ta
çalıştırılabilir; Replit'te salt durum gösterir.
"""
from __future__ import annotations

from alpha_agents.registry import Agent

AGENT_ID = "windows-runtime"
CAPABILITIES = [
    "git bulma/kurma/güncelleme", "python/.venv hazırlama",
    "requirements kurulumu", "certifi/truststore hazırlama",
    ".env güvenli onarımı", "Binance public veri kontrolü",
    "yalnız Alpha süreçlerini kapatma", "serve_windows başlatma",
    "/health/runtime doğrulama", "Paper AUTO/controller başlatma",
    "FINAL PASS/FAIL raporu",
]


def build() -> Agent:
    from services import windows_runtime_recovery as wrr
    return Agent(
        agent_id=AGENT_ID,
        agent_name="Windows Runtime Recovery Agent",
        capabilities=CAPABILITIES,
        status_fn=wrr.status,
        run_fn=wrr.run_recovery if wrr.is_windows_local() else None,
    )
