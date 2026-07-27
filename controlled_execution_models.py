"""Mission 2100 — Agent 01: Kontrollü Yürütme kanonik modelleri.

Kapalı çalışma modu enum'u (PAPER / SHADOW / MICRO_LIVE — sınırsız
canlı mod YOKTUR), değişmez mod politikası ve kapalı karar modeli.

Kurallar: frozen+slots+hashable; finansal limitler yalnız Decimal
(float yasak); bilinmeyen → None; kimlik/zaman üretimi yok
(çağıran-sahipli referanslar); steril doğrulama kodu
INVALID_CONTROLLED_MODEL_FIELD.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from controlled_execution_errors import (
    ControlledExecutionContractError)

__all__ = ["ControlledExecutionMode",
           "ControlledExecutionDecisionCode",
           "ControlledExecutionPolicy",
           "ControlledExecutionDecision"]

_ERROR_INVALID_FIELD = "INVALID_CONTROLLED_MODEL_FIELD"


class ControlledExecutionMode(Enum):
    """Kapalı çalışma modu kümesi — sınırsız canlı mod yoktur."""

    PAPER = "PAPER"
    SHADOW = "SHADOW"
    MICRO_LIVE = "MICRO_LIVE"


class ControlledExecutionDecisionCode(Enum):
    """Kapalı politika karar kümesi."""

    ALLOW_NON_WRITING_MODE = "ALLOW_NON_WRITING_MODE"
    DENY_EXCHANGE_WRITE = "DENY_EXCHANGE_WRITE"
    REQUIRE_EXPLICIT_AUTHORIZATION = (
        "REQUIRE_EXPLICIT_AUTHORIZATION")
    INVALID_MODE = "INVALID_MODE"
    INVALID_POLICY = "INVALID_POLICY"
    INVALID_TRANSITION = "INVALID_TRANSITION"


def _require_bool(value: object, field: str) -> None:
    if not isinstance(value, bool):
        raise ControlledExecutionContractError(
            f"{_ERROR_INVALID_FIELD}:{field}")


def _require_optional_decimal(value: object, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ControlledExecutionContractError(
            f"{_ERROR_INVALID_FIELD}:{field}")
    if not value.is_finite() or value < Decimal("0"):
        raise ControlledExecutionContractError(
            f"{_ERROR_INVALID_FIELD}:{field}")


def _require_optional_int(value: object, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        raise ControlledExecutionContractError(
            f"{_ERROR_INVALID_FIELD}:{field}")


def _require_optional_reference(value: object,
                                field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ControlledExecutionContractError(
            f"{_ERROR_INVALID_FIELD}:{field}")


@dataclass(frozen=True, slots=True)
class ControlledExecutionPolicy:
    """Değişmez mod politikası — çağıran-sahipli referanslar.

    Finansal limitler yalnız Decimal; bilinmeyen limit None kalır.
    Agent 01 kapsamında borsa yazmaları HER ZAMAN reddedilir;
    Micro Live limitleri temsil edilebilir ama etkinleştirilemez.
    """

    mode: ControlledExecutionMode
    exchange_write_allowed: bool = False
    simulated_fill_allowed: bool = False
    broker_read_allowed: bool = False
    human_confirmation_required: bool = False
    explicit_authorization_required: bool = False
    maximum_order_notional: Optional[Decimal] = None
    maximum_daily_notional: Optional[Decimal] = None
    maximum_open_orders: Optional[int] = None
    authorization_reference: Optional[str] = None
    logical_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ControlledExecutionMode):
            raise ControlledExecutionContractError(
                f"{_ERROR_INVALID_FIELD}:mode")
        _require_bool(self.exchange_write_allowed,
                      "exchange_write_allowed")
        _require_bool(self.simulated_fill_allowed,
                      "simulated_fill_allowed")
        _require_bool(self.broker_read_allowed,
                      "broker_read_allowed")
        _require_bool(self.human_confirmation_required,
                      "human_confirmation_required")
        _require_bool(self.explicit_authorization_required,
                      "explicit_authorization_required")
        _require_optional_decimal(self.maximum_order_notional,
                                  "maximum_order_notional")
        _require_optional_decimal(self.maximum_daily_notional,
                                  "maximum_daily_notional")
        _require_optional_int(self.maximum_open_orders,
                              "maximum_open_orders")
        _require_optional_reference(self.authorization_reference,
                                    "authorization_reference")
        _require_optional_int(self.logical_sequence,
                              "logical_sequence")


@dataclass(frozen=True, slots=True)
class ControlledExecutionDecision:
    """Değişmez politika kararı — steril gerekçe kodu taşır."""

    code: ControlledExecutionDecisionCode
    mode: Optional[ControlledExecutionMode] = None
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.code,
                          ControlledExecutionDecisionCode):
            raise ControlledExecutionContractError(
                f"{_ERROR_INVALID_FIELD}:code")
        if self.mode is not None and not isinstance(
                self.mode, ControlledExecutionMode):
            raise ControlledExecutionContractError(
                f"{_ERROR_INVALID_FIELD}:mode")
        _require_optional_reference(self.reason, "reason")

    @property
    def allowed(self) -> bool:
        """Yalnız yazmayan mod izni gerçek izindir."""
        return self.code is (ControlledExecutionDecisionCode
                             .ALLOW_NON_WRITING_MODE)
