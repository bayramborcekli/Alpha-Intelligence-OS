"""Mission 2100 — Agent 05: Gölge modu modelleri.

Tamamı değişmez (frozen+slots) veri sınıfları ve kapalı enum'lar.
Gölge modu piyasayı YALNIZ gözlemler: canlı emir, borsa yazımı,
broker durum değişikliği yoktur. Kimlik / zaman / UUID / rastgelelik
ÜRETİLMEZ — tüm kimlikler ve mantıksal sıralar çağıran-sahiplidir.

Kanonik modeller (ExecutionRequest / RiskDecision /
KillSwitchSnapshot / PaperOrder / PaperLedgerSnapshot) KOPYALANMAZ;
referansla kullanılır. Para/miktar YALNIZ Decimal; bilinmeyen değer
None kalır (uydurma yasak).

Bilinçli ek modeller (spesifikasyon listesi dışında, raporda
gerekçeli): ShadowMarketObservation (salt-okunur piyasa gözlem
girdisi — gözlem verisi çağıran tarafın salt-okunur adaptöründen
gelir, bu modül ağa ÇIKMAZ) ve ShadowResult (işlem sonucu zarfı —
bir SONRAKİ değişmez gölge durumunu taşır).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, unique
from typing import Optional, Tuple

from execution_enums import OrderSide
from paper_models import PaperLedgerSnapshot
from shadow_errors import ShadowContractError

__all__ = ["ShadowOperation", "ShadowDecision",
           "ShadowDecisionCode", "ShadowStage", "ShadowAudit",
           "ShadowOrder", "ShadowExecution",
           "ShadowMarketObservation", "ShadowComparison",
           "ShadowStatistics", "ShadowSnapshot",
           "ShadowHeartbeat", "ShadowResult"]

_ERROR_INVALID_FIELD = "INVALID_SHADOW_FIELD"

_ZERO = Decimal("0")


def _fail(field: str) -> None:
    raise ShadowContractError(f"{_ERROR_INVALID_FIELD}:{field}")


def _require_reference(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(field)


def _require_optional_reference(value: object,
                                field: str) -> None:
    if value is None:
        return
    _require_reference(value, field)


def _require_int(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or \
            value < 0:
        _fail(field)


def _require_optional_int(value: object, field: str) -> None:
    if value is None:
        return
    _require_int(value, field)


def _require_enum(value: object, enum_type: type,
                  field: str) -> None:
    if not isinstance(value, enum_type):
        _fail(field)


def _require_decimal(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite() or value <= _ZERO:
        _fail(field)


def _require_optional_decimal(value: object, field: str) -> None:
    if value is None:
        return
    _require_decimal(value, field)


def _require_optional_signed_decimal(value: object,
                                     field: str) -> None:
    """Delta alanları işaretli olabilir; bilinmeyen None kalır."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail(field)
    if not value.is_finite():
        _fail(field)


def _require_tuple_of(value: object, element_type: type,
                      field: str) -> None:
    if not isinstance(value, tuple):
        _fail(field)
    for element in value:
        if not isinstance(element, element_type):
            _fail(field)


@unique
class ShadowOperation(Enum):
    """Kapalı gölge işlem kümesi."""

    SUBMIT_SHADOW = "SUBMIT_SHADOW"
    CANCEL_SHADOW = "CANCEL_SHADOW"
    COMPARE_EXECUTION = "COMPARE_EXECUTION"


@unique
class ShadowDecision(Enum):
    """Kapalı üst karar kümesi — gölge asla GERÇEK yürütmez."""

    SIMULATED = "SIMULATED"
    DENIED = "DENIED"
    RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"


@unique
class ShadowDecisionCode(Enum):
    """Kapalı karar kodu kümesi — steril, deterministik."""

    ORDER_SIMULATED = "ORDER_SIMULATED"
    CANCEL_SIMULATED = "CANCEL_SIMULATED"
    COMPARISON_COMPLETED = "COMPARISON_COMPLETED"
    MODE_DENIED = "MODE_DENIED"
    POLICY_DENIED = "POLICY_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_REDUCE_SIZE = "RISK_REDUCE_SIZE"
    RISK_CONFIRMATION_REQUIRED = "RISK_CONFIRMATION_REQUIRED"
    KILL_SWITCH_DENIED = "KILL_SWITCH_DENIED"


@unique
class ShadowStage(Enum):
    """Sabit boru hattı denetim aşamaları — sıra değişmez."""

    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    MODE_VALIDATED = "MODE_VALIDATED"
    RISK_EVALUATED = "RISK_EVALUATED"
    PERMISSION_EVALUATED = "PERMISSION_EVALUATED"
    KILL_SWITCH_CHECKED = "KILL_SWITCH_CHECKED"
    PAPER_SIMULATED = "PAPER_SIMULATED"
    MARKET_OBSERVED = "MARKET_OBSERVED"
    COMPARISON_COMPLETED = "COMPARISON_COMPLETED"


# Karar ↔ kod eşleşmesi (kapalı tutarlılık kuralı)
_SIMULATED_CODES = frozenset({
    ShadowDecisionCode.ORDER_SIMULATED,
    ShadowDecisionCode.CANCEL_SIMULATED,
    ShadowDecisionCode.COMPARISON_COMPLETED})
_RECOMMENDATION_CODES = frozenset({
    ShadowDecisionCode.RISK_REDUCE_SIZE})
_DENIED_CODES = frozenset({
    ShadowDecisionCode.MODE_DENIED,
    ShadowDecisionCode.POLICY_DENIED,
    ShadowDecisionCode.PERMISSION_DENIED,
    ShadowDecisionCode.RISK_REJECTED,
    ShadowDecisionCode.RISK_CONFIRMATION_REQUIRED,
    ShadowDecisionCode.KILL_SWITCH_DENIED})


@dataclass(frozen=True, slots=True)
class ShadowAudit:
    """Değişmez denetim kaydı — kimlik çağırandan TÜRETİLİR."""

    audit_reference: str
    stage: ShadowStage
    event_code: str
    subject_reference: Optional[str] = None
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.audit_reference,
                           "audit_reference")
        _require_enum(self.stage, ShadowStage, "stage")
        _require_reference(self.event_code, "event_code")
        _require_optional_reference(self.subject_reference,
                                    "subject_reference")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowOrder:
    """Gölge emri — YALNIZ simülasyon kaydı, borsaya gitmez."""

    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.order_reference,
                           "order_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_decimal(self.quantity, "quantity")
        _require_decimal(self.price, "price")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowExecution:
    """Gölge gerçekleşmesi — kağıt simülasyonundan türetilir."""

    execution_reference: str
    order_reference: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.execution_reference,
                           "execution_reference")
        _require_reference(self.order_reference,
                           "order_reference")
        _require_reference(self.symbol, "symbol")
        _require_enum(self.side, OrderSide, "side")
        _require_decimal(self.quantity, "quantity")
        _require_decimal(self.price, "price")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowMarketObservation:
    """Salt-okunur piyasa gözlemi — çağıran-sahipli girdi.

    İzinli alanlar: fiyat, en iyi alış, en iyi satış, işlem
    fiyatı, defter derinliği. Emir gönderme/iptal/değiştirme
    YETENEĞİ YOKTUR; bu model saf veridir, ağa çıkmaz.
    Bilinmeyen alan None kalır."""

    observation_reference: str
    symbol: str
    price: Optional[Decimal] = None
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    last_trade_price: Optional[Decimal] = None
    bid_quantity: Optional[Decimal] = None
    ask_quantity: Optional[Decimal] = None
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.observation_reference,
                           "observation_reference")
        _require_reference(self.symbol, "symbol")
        _require_optional_decimal(self.price, "price")
        _require_optional_decimal(self.best_bid, "best_bid")
        _require_optional_decimal(self.best_ask, "best_ask")
        _require_optional_decimal(self.last_trade_price,
                                  "last_trade_price")
        _require_optional_decimal(self.bid_quantity,
                                  "bid_quantity")
        _require_optional_decimal(self.ask_quantity,
                                  "ask_quantity")
        _require_int(self.logical_sequence, "logical_sequence")
        if self.best_bid is not None and \
                self.best_ask is not None and \
                self.best_bid > self.best_ask:
            _fail("best_bid")


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    """Değişmez karşılaştırma raporu.

    Delta alanları işaretli Decimal'dir; hesaplanamayan delta
    None kalır (skor manipülasyonu / uydurma yok). Gecikme,
    mantıksal sıra farkıdır — duvar saati YOKTUR."""

    request_reference: str
    paper_reference: str
    market_reference: str
    price_delta: Optional[Decimal] = None
    fill_delta: Optional[Decimal] = None
    pnl_delta: Optional[Decimal] = None
    latency: Optional[int] = None
    decision: ShadowDecision = ShadowDecision.SIMULATED
    audit: Tuple[ShadowAudit, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.request_reference,
                           "request_reference")
        _require_reference(self.paper_reference,
                           "paper_reference")
        _require_reference(self.market_reference,
                           "market_reference")
        _require_optional_signed_decimal(self.price_delta,
                                         "price_delta")
        _require_optional_signed_decimal(self.fill_delta,
                                         "fill_delta")
        _require_optional_signed_decimal(self.pnl_delta,
                                         "pnl_delta")
        _require_optional_int(self.latency, "latency")
        _require_enum(self.decision, ShadowDecision, "decision")
        _require_tuple_of(self.audit, ShadowAudit, "audit")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowStatistics:
    """Anlık görüntüden türetilmiş deterministik sayaçlar."""

    total_orders: int = 0
    total_executions: int = 0
    total_comparisons: int = 0
    total_denied: int = 0
    total_cancels: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_int(self.total_orders, "total_orders")
        _require_int(self.total_executions, "total_executions")
        _require_int(self.total_comparisons,
                     "total_comparisons")
        _require_int(self.total_denied, "total_denied")
        _require_int(self.total_cancels,
                     "total_cancels")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowSnapshot:
    """Değişmez gölge durumu — servis DEĞİL, durum sahibidir.

    Her yazan işlem YENİ bir anlık görüntü döner; mevcut görüntü
    asla değiştirilmez. Gerçekleşmeler bilinen emirlere referans
    vermek ZORUNDADIR (iç tutarlılık)."""

    snapshot_reference: str
    orders: Tuple[ShadowOrder, ...] = ()
    executions: Tuple[ShadowExecution, ...] = ()
    comparisons: Tuple[ShadowComparison, ...] = ()
    denied_count: int = 0
    cancel_request_count: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_reference(self.snapshot_reference,
                           "snapshot_reference")
        _require_tuple_of(self.orders, ShadowOrder, "orders")
        _require_tuple_of(self.executions, ShadowExecution,
                          "executions")
        _require_tuple_of(self.comparisons, ShadowComparison,
                          "comparisons")
        _require_int(self.denied_count, "denied_count")
        _require_int(self.cancel_request_count,
                     "cancel_request_count")
        _require_int(self.logical_sequence, "logical_sequence")
        references = tuple(order.order_reference
                           for order in self.orders)
        if len(frozenset(references)) != len(references):
            _fail("orders")
        for execution in self.executions:
            if execution.order_reference not in references:
                _fail("executions")

    def order_for(self, order_reference: str
                  ) -> Optional[ShadowOrder]:
        """Referansla emir arama — bulunamazsa None."""
        for order in self.orders:
            if order.order_reference == order_reference:
                return order
        return None

    def executions_for(self, order_reference: str
                       ) -> Tuple[ShadowExecution, ...]:
        """Emre ait gerçekleşmeler (değişmez)."""
        return tuple(execution for execution in self.executions
                     if execution.order_reference
                     == order_reference)

    def statistics(self) -> ShadowStatistics:
        """Türetilmiş sayaçlar — saklanan ayrı durum yoktur."""
        return ShadowStatistics(
            total_orders=len(self.orders),
            total_executions=len(self.executions),
            total_comparisons=len(self.comparisons),
            total_denied=self.denied_count,
            total_cancels=self.cancel_request_count,
            logical_sequence=self.logical_sequence)


@dataclass(frozen=True, slots=True)
class ShadowHeartbeat:
    """Deterministik kalp atışı — duvar saati/ağ içermez."""

    alive: bool
    order_count: int = 0
    execution_count: int = 0
    comparison_count: int = 0
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.alive, bool):
            _fail("alive")
        _require_int(self.order_count, "order_count")
        _require_int(self.execution_count, "execution_count")
        _require_int(self.comparison_count, "comparison_count")
        _require_int(self.logical_sequence, "logical_sequence")


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """Değişmez işlem sonucu zarfı (bilinçli ek model).

    `shadow` bir SONRAKİ gölge durumu, `ledger` bir SONRAKİ kağıt
    defter durumudur; reddedilen yollarda ikisi de girdiyle
    AYNIDIR. Karar ↔ kod kapalı eşleşme kuralına bağlıdır."""

    operation: ShadowOperation
    decision: ShadowDecision
    decision_code: ShadowDecisionCode
    shadow: ShadowSnapshot
    ledger: PaperLedgerSnapshot
    order_reference: Optional[str] = None
    comparison: Optional[ShadowComparison] = None
    recommended_quantity: Optional[Decimal] = None
    audit: Tuple[ShadowAudit, ...] = ()
    logical_sequence: int = 0

    def __post_init__(self) -> None:
        _require_enum(self.operation, ShadowOperation,
                      "operation")
        _require_enum(self.decision, ShadowDecision, "decision")
        _require_enum(self.decision_code, ShadowDecisionCode,
                      "decision_code")
        if not isinstance(self.shadow, ShadowSnapshot):
            _fail("shadow")
        if not isinstance(self.ledger, PaperLedgerSnapshot):
            _fail("ledger")
        _require_optional_reference(self.order_reference,
                                    "order_reference")
        if self.comparison is not None and not isinstance(
                self.comparison, ShadowComparison):
            _fail("comparison")
        _require_optional_decimal(self.recommended_quantity,
                                  "recommended_quantity")
        _require_tuple_of(self.audit, ShadowAudit, "audit")
        _require_int(self.logical_sequence, "logical_sequence")
        self._require_consistency()

    def _require_consistency(self) -> None:
        if self.decision is ShadowDecision.SIMULATED:
            if self.decision_code not in _SIMULATED_CODES:
                _fail("decision_code")
        elif self.decision is ShadowDecision.RECOMMENDATION_ONLY:
            if self.decision_code not in _RECOMMENDATION_CODES:
                _fail("decision_code")
        elif self.decision_code not in _DENIED_CODES:
            _fail("decision_code")

    @property
    def simulated(self) -> bool:
        """Gölge işlemi HİÇBİR ZAMAN gerçek yürütme değildir."""
        return self.decision is ShadowDecision.SIMULATED

    def audit_stage_codes(self) -> Tuple[str, ...]:
        """Ulaşılan aşamaların steril kodları (sıralı)."""
        return tuple(record.event_code for record in self.audit)
