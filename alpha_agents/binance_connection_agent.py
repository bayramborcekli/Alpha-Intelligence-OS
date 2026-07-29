"""Binance Connection Agent — kalıcı sistem agent'ı.

Binance Global ve Binance TR bağlantılarını bağımsız yönetir: API
bağlantısı, hesap doğrulaması, salt okunur veri, izin kontrolü, durum.
CANLI EMİR AÇILMAZ. Secret'lar hiçbir çıktıya yazılmaz.
"""
from __future__ import annotations

from alpha_agents.registry import Agent

AGENT_ID = "binance-connection"
CAPABILITIES = [
    "Binance Global read-only bağlantı testi",
    "Binance TR read-only bağlantı testi",
    "izin kontrolü (withdraw/trade reddi)",
    "şifreli credential saklama (Windows DPAPI)",
    "bağlantı durumu raporu", "audit log",
]


def build() -> Agent:
    from services import binance_connection as bc

    def _run() -> dict:
        # "Çalıştır" = kayıtlı credential'larla iki bağımsız test.
        out: dict = {}
        for provider in bc.PROVIDERS:
            try:
                out[provider] = bc.test_stored(provider)
            except Exception as exc:  # biri düşerse diğeri bozulmaz
                out[provider] = {"status": "ERROR",
                                 "error": type(exc).__name__}
        ok = any(str(v.get("status", "")).startswith("CONNECTED")
                 for v in out.values())
        out["last_result"] = "PASS" if ok else "PARTIAL"
        return out

    return Agent(
        agent_id=AGENT_ID,
        agent_name="Binance Connection Agent",
        capabilities=CAPABILITIES,
        status_fn=bc.status,
        run_fn=_run,
    )
