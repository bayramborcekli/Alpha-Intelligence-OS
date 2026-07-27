"""Mission 2000 — Agent 09: Yürütme Çekirdeği mimari dondurması.

Bu modül YENİ iş işlevi içermez. Yürütme Çekirdeği'nin kalıcı
mimari sözleşmesini bildirimsel (declarative) olarak dondurur:
modül kümesi, kamu API yüzeyleri, boru hattı sırası ve alan
sahipliği. Gelecek misyonlar GENİŞLETİR; sessizce DEĞİŞTİRMEZ.

Kamu sembol ekleme: açık mimar incelemesi gerektirir.
Kaldırma: YASAK. Yeniden adlandırma: YASAK.

Buradaki sabitler test paketleri tarafından canlı kodla
karşılaştırılır; her sapma regresyon hatasıdır.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["FREEZE_STATUS", "FROZEN_MODULES", "PUBLIC_API",
           "PIPELINE_ORDER", "DOMAIN_OWNERSHIP",
           "PIPELINE_IMPORT_CONTRACT"]

FREEZE_STATUS = "FROZEN"

# Yürütme Çekirdeği'nin dondurulmuş modül kümesi (tam liste)
FROZEN_MODULES = (
    "execution_enums",
    "execution_models",
    "execution_state_machine",
    "execution_risk_models",
    "execution_risk_policies",
    "execution_risk_engine",
    "execution_kill_switch_models",
    "execution_kill_switch",
    "execution_broker_models",
    "execution_broker_errors",
    "execution_broker_adapter",
    "binance_spot_adapter",
    "binance_normalizer",
    "binance_capabilities",
    "execution_permission_gate",
    "execution_service_models",
    "execution_service",
    "execution_api_models",
    "execution_api_mapper",
    "execution_api",
)

# Dondurulmuş kamu API yüzeyleri — modül → tam export listesi
PUBLIC_API = MappingProxyType({
    "execution_enums": (
        "OrderSide", "OrderType", "TimeInForce", "OrderState",
        "PositionSide", "ExecutionStatus"),
    "execution_models": (
        "ExecutionRequest", "ExecutionResult", "Order", "Position",
        "Fill", "ExecutionMetadata", "ValidationResult"),
    "execution_state_machine": ("validate_transition",),
    "execution_risk_policies": (),
    "execution_kill_switch_models": (
        "KillSwitchState", "KillSwitchReason",
        "KillSwitchSnapshot"),
    "execution_risk_models": (
        "AssetType", "RiskDecisionType", "BrokerProfile",
        "Instrument", "Portfolio", "PortfolioRisk", "PositionRisk",
        "RiskLimits", "Exposure", "CapitalState", "RiskDecision"),
    "execution_risk_engine": (
        "validate_execution", "calculate_position_size",
        "calculate_exposure", "evaluate_portfolio_risk",
        "RiskEngine"),
    "execution_kill_switch": ("KillSwitch",),
    "execution_broker_models": (
        "ExecutionMode", "BrokerOperationStatus",
        "BrokerHealthState", "BrokerRequestContext",
        "BrokerOperationResult", "BrokerHealth", "BrokerBalance",
        "CancelOrderRequest", "OrderQuery", "OpenOrdersQuery",
        "PositionsQuery", "BalancesQuery"),
    "execution_broker_errors": (
        "BrokerErrorCode", "BrokerErrorDetail",
        "BrokerAdapterError", "BrokerContractError",
        "BrokerConfigurationError", "BrokerNormalizationError"),
    "execution_broker_adapter": ("BrokerAdapter",),
    "binance_spot_adapter": (
        "BinanceSpotAdapter", "Transport", "RESTTransport",
        "WebSocketTransport", "TransportFailure",
        "SigningProvider", "CredentialProvider"),
    "binance_normalizer": (
        "normalize_order", "normalize_balance", "normalize_fill",
        "normalize_order_state", "normalize_error",
        "error_result"),
    "binance_capabilities": ("binance_spot_profile",),
    "execution_permission_gate": (
        "ExecutionPermission", "ExecutionPermissionGate"),
    "execution_service_models": (
        "ExecutionServiceStatus", "ExecutionTraceStep",
        "ExecutionTrace", "ExecutionServiceRequest",
        "ExecutionServiceResult"),
    "execution_service": (
        "BrokerAdapterResolver", "ExecutionService",
        "ExecutionServiceError", "ExecutionServiceContractError",
        "ExecutionServiceConfigurationError"),
    "execution_api_models": (
        "ExecutionApiStatus", "ExecutionApiRequest",
        "ExecutionApiResponse"),
    "execution_api_mapper": ("ExecutionApiMapper",),
    "execution_api": (
        "ExecutionApi", "ExecutionApiError",
        "ExecutionApiContractError",
        "ExecutionApiConfigurationError"),
})

# Dondurulmuş boru hattı sırası (üstten alta)
PIPELINE_ORDER = (
    "execution_api",
    "execution_service",
    "execution_risk_engine",
    "execution_permission_gate",
    "execution_kill_switch",
    "execution_broker_adapter",
)

# Katman → import ETMESİ YASAK üst katman modülleri
# (alt katman üst katmanı asla bilmez)
PIPELINE_IMPORT_CONTRACT = MappingProxyType({
    "execution_service": ("execution_api",
                          "execution_api_models",
                          "execution_api_mapper"),
    "execution_permission_gate": ("execution_api",
                                  "execution_api_models",
                                  "execution_api_mapper",
                                  "execution_service"),
    "execution_risk_engine": ("execution_api",
                              "execution_api_models",
                              "execution_api_mapper",
                              "execution_service",
                              "execution_service_models",
                              "execution_permission_gate"),
    "execution_kill_switch": ("execution_api",
                              "execution_api_models",
                              "execution_api_mapper",
                              "execution_service",
                              "execution_service_models",
                              "execution_permission_gate",
                              "execution_risk_engine"),
    "execution_broker_adapter": ("execution_api",
                                 "execution_api_models",
                                 "execution_api_mapper",
                                 "execution_service",
                                 "execution_service_models",
                                 "execution_permission_gate",
                                 "execution_risk_engine",
                                 "execution_kill_switch"),
    "binance_spot_adapter": ("execution_api",
                             "execution_api_models",
                             "execution_api_mapper",
                             "execution_service",
                             "execution_service_models",
                             "execution_permission_gate",
                             "execution_risk_engine",
                             "execution_kill_switch"),
})

# Kanonik model → TEK sahip modül (kopya tanım yasak)
DOMAIN_OWNERSHIP = MappingProxyType({
    "ExecutionRequest": "execution_models",
    "ExecutionResult": "execution_models",
    "Order": "execution_models",
    "Position": "execution_models",
    "Fill": "execution_models",
    "Instrument": "execution_risk_models",
    "Portfolio": "execution_risk_models",
    "BrokerProfile": "execution_risk_models",
    "RiskDecision": "execution_risk_models",
    "RiskLimits": "execution_risk_models",
    "ExecutionMode": "execution_broker_models",
    "BrokerOperationResult": "execution_broker_models",
    "BrokerRequestContext": "execution_broker_models",
    "BrokerHealth": "execution_broker_models",
    "BrokerErrorDetail": "execution_broker_errors",
    "BrokerAdapter": "execution_broker_adapter",
    "KillSwitch": "execution_kill_switch",
    "ExecutionPermission": "execution_permission_gate",
    "ExecutionServiceRequest": "execution_service_models",
    "ExecutionServiceResult": "execution_service_models",
    "ExecutionTrace": "execution_service_models",
    "ExecutionApiRequest": "execution_api_models",
    "ExecutionApiResponse": "execution_api_models",
    "ExecutionApi": "execution_api",
})
