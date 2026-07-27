"""Mission 2200 — Agent 01: Operasyon kontrol politikası.

Fail-closed kurallar:
- Temiz kurulumda: PAPER, otomasyon STOPPED, LIVE yetkisi RED,
  sembol otomasyonu DISABLED, canlı toplu kapatma RED.
- Herhangi bir güvenlik bağımlılığı BİLİNMEYEN veya BAYAT ise
  otomatik yürütme REDDEDİLİR — fail-open yolu YOKTUR.
- Durum geçiş tablosu kapalıdır; tabloda olmayan geçiş 409'dur.
- Bağlantı yetki DEĞİLDİR: bağlı API anahtarı canlı yetki
  varlığı anlamına gelmez.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Tuple

from operation_control_models import (
    AutomationCommand, AutomationState, DataFreshness,
    ReconciliationState, SymbolAutomationState, SymbolCommand)

__all__ = [
    "DEFAULT_EXECUTION_MODE",
    "DEFAULT_AUTOMATION_STATE",
    "DEFAULT_SYMBOL_STATE",
    "DEFAULT_LIVE_AUTHORIZATION",
    "DEFAULT_CLOSE_ALL_LIVE",
    "AUTOMATION_TRANSITIONS",
    "SYMBOL_TRANSITIONS",
    "DESTRUCTIVE_ACTIONS",
    "REQUIRED_SAFETY_DEPENDENCIES",
    "resolve_transition",
    "resolve_symbol_transition",
    "evaluate_safety_dependencies",
    "automation_allows_new_entries",
    "can_execute_automatically",
]

DEFAULT_EXECUTION_MODE = "PAPER"
DEFAULT_AUTOMATION_STATE = AutomationState.STOPPED
DEFAULT_SYMBOL_STATE = SymbolAutomationState.DISABLED
DEFAULT_LIVE_AUTHORIZATION = "DENIED"
DEFAULT_CLOSE_ALL_LIVE = "DENIED"

# Komut → (izinli-kaynak-durumlar, hedef-durum) kapalı tablosu.
AUTOMATION_TRANSITIONS: Mapping[
    AutomationCommand,
    Tuple[Tuple[AutomationState, ...], AutomationState]] = \
    MappingProxyType({
        AutomationCommand.START: (
            (AutomationState.STOPPED,),
            AutomationState.RUNNING),
        AutomationCommand.PAUSE: (
            (AutomationState.RUNNING,
             AutomationState.STARTING),
            AutomationState.PAUSED),
        AutomationCommand.RESUME: (
            (AutomationState.PAUSED,
             AutomationState.PAUSING),
            AutomationState.RUNNING),
        AutomationCommand.STOP: (
            (AutomationState.RUNNING,
             AutomationState.STARTING,
             AutomationState.PAUSED,
             AutomationState.PAUSING,
             AutomationState.STOPPING,
             AutomationState.BLOCKED,
             AutomationState.ERROR),
            AutomationState.STOPPED),
    })

# Komut sonucu zaten hedef durumda ise IDEMPOTENT tekrar sayılır.
_IDEMPOTENT_TARGETS: Mapping[AutomationCommand,
                             AutomationState] = \
    MappingProxyType({
        command: target for command, (_, target)
        in AUTOMATION_TRANSITIONS.items()})

SYMBOL_TRANSITIONS: Mapping[
    SymbolCommand,
    Tuple[Tuple[SymbolAutomationState, ...],
          SymbolAutomationState]] = MappingProxyType({
        SymbolCommand.ENABLE: (
            (SymbolAutomationState.DISABLED,
             SymbolAutomationState.STOPPED),
            SymbolAutomationState.ENABLED),
        SymbolCommand.PAUSE: (
            (SymbolAutomationState.ENABLED,),
            SymbolAutomationState.PAUSED),
        SymbolCommand.RESUME: (
            (SymbolAutomationState.PAUSED,),
            SymbolAutomationState.ENABLED),
        SymbolCommand.STOP: (
            (SymbolAutomationState.ENABLED,
             SymbolAutomationState.PAUSED,
             SymbolAutomationState.DISABLED),
            SymbolAutomationState.STOPPED),
    })

_SYMBOL_IDEMPOTENT_TARGETS: Mapping[SymbolCommand,
                                    SymbolAutomationState] = \
    MappingProxyType({
        command: target for command, (_, target)
        in SYMBOL_TRANSITIONS.items()})

# Güçlü onay + neden + idempotency anahtarı gerektiren eylemler.
DESTRUCTIVE_ACTIONS: Tuple[str, ...] = (
    "GLOBAL_STOP_NEW_ENTRIES",
    "GLOBAL_REQUEST_CLOSE_ALL",
    "GLOBAL_KILL_SWITCH",
)

# Otomatik yürütme için TAMAMI sağlanması zorunlu bağımlılıklar.
REQUIRED_SAFETY_DEPENDENCIES: Tuple[str, ...] = (
    "permission_gate",
    "risk_engine",
    "kill_switch",
    "ledger",
    "lifecycle",
    "reconciliation",
)

_HEALTHY_DEPENDENCY_VALUES = frozenset({"OK", "PASS", "READY"})


def resolve_transition(current: AutomationState,
                       command: AutomationCommand
                       ) -> Tuple[AutomationState, bool]:
    """Geçişi çöz: (yeni durum, idempotent-tekrar-mi).

    Tabloda olmayan geçiş ``None`` yerine açıkça reddedilir:
    dönen değer yoksa çağıran 409 üretir."""
    if not isinstance(current, AutomationState) or \
            not isinstance(command, AutomationCommand):
        raise KeyError("INVALID_TRANSITION_INPUT")
    if current is _IDEMPOTENT_TARGETS[command]:
        return current, True
    sources, target = AUTOMATION_TRANSITIONS[command]
    if current not in sources:
        raise KeyError(
            f"INVALID_TRANSITION:{current.value}"
            f"->{command.value}")
    return target, False


def resolve_symbol_transition(current: SymbolAutomationState,
                              command: SymbolCommand
                              ) -> Tuple[SymbolAutomationState,
                                         bool]:
    """Sembol geçişini çöz: (yeni durum, idempotent-mi)."""
    if not isinstance(current, SymbolAutomationState) or \
            not isinstance(command, SymbolCommand):
        raise KeyError("INVALID_TRANSITION_INPUT")
    if current is _SYMBOL_IDEMPOTENT_TARGETS[command]:
        return current, True
    sources, target = SYMBOL_TRANSITIONS[command]
    if current not in sources:
        raise KeyError(
            f"INVALID_TRANSITION:{current.value}"
            f"->{command.value}")
    return target, False


def evaluate_safety_dependencies(
        dependencies: Mapping[str, str]) -> Tuple[str, ...]:
    """Eksik/bilinmeyen/bayat bağımlılıkları döndür.

    Boş tuple = tüm bağımlılıklar sağlıklı. Herhangi bir girdi
    eksikse veya değeri sağlıklı kümede değilse o bağımlılık
    ihlal olarak raporlanır (fail-closed)."""
    if not isinstance(dependencies, Mapping):
        return tuple(f"DEPENDENCY_UNAVAILABLE:{name}"
                     for name in REQUIRED_SAFETY_DEPENDENCIES)
    findings = []
    for name in REQUIRED_SAFETY_DEPENDENCIES:
        value = dependencies.get(name)
        if not isinstance(value, str) or \
                value.upper() not in _HEALTHY_DEPENDENCY_VALUES:
            findings.append(f"DEPENDENCY_UNAVAILABLE:{name}")
    return tuple(findings)


def automation_allows_new_entries(state: AutomationState
                                  ) -> bool:
    """Yalnız RUNNING yeni giriş izni verir."""
    return state is AutomationState.RUNNING


def can_execute_automatically(
        automation_state: AutomationState,
        execution_mode: str,
        symbol_state: SymbolAutomationState,
        stop_new_entries: bool,
        kill_switch_active: bool,
        dependencies: Mapping[str, str],
        reconciliation_state: ReconciliationState,
        data_freshness: DataFreshness,
        candidate_exists: bool,
        intent_normalized: bool,
        authorization_valid: bool,
        permission_pass: bool,
        risk_pass: bool,
        cooldown_pass: bool,
        idempotency_pass: bool) -> Tuple[str, ...]:
    """Otomatik yürütme ön koşulları; boş tuple = İZİN.

    TEK koşulun bile başarısızlığı yürütmeyi engeller ve neden
    kodu döner. Fail-open yolu yoktur: bilinmeyen her girdi
    engel üretir."""
    findings = []
    if automation_state is not AutomationState.RUNNING:
        findings.append("AUTOMATION_NOT_RUNNING")
    if execution_mode not in ("PAPER", "SHADOW", "MICRO_LIVE"):
        findings.append("EXECUTION_MODE_NOT_PERMITTED")
    if symbol_state is not SymbolAutomationState.ENABLED:
        findings.append("SYMBOL_AUTOMATION_DISABLED")
    if stop_new_entries is not False:
        findings.append("NEW_ENTRIES_STOPPED")
    if kill_switch_active is not False:
        findings.append("KILL_SWITCH_ACTIVE")
    findings.extend(evaluate_safety_dependencies(dependencies))
    if reconciliation_state in (ReconciliationState.ERROR,
                                ReconciliationState.STALE,
                                ReconciliationState.UNKNOWN,
                                ReconciliationState.MISMATCH):
        findings.append("RECONCILIATION_BLOCKING")
    if data_freshness is not DataFreshness.FRESH:
        findings.append("DATA_NOT_FRESH")
    if candidate_exists is not True:
        findings.append("NO_ELIGIBLE_CANDIDATE")
    if intent_normalized is not True:
        findings.append("INTENT_NOT_NORMALIZED")
    if authorization_valid is not True:
        findings.append("AUTHORIZATION_INVALID")
    if permission_pass is not True:
        findings.append("PERMISSION_GATE_FAIL")
    if risk_pass is not True:
        findings.append("RISK_ENGINE_FAIL")
    if cooldown_pass is not True:
        findings.append("COOLDOWN_FAIL")
    if idempotency_pass is not True:
        findings.append("IDEMPOTENCY_FAIL")
    return tuple(findings)
