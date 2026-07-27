"""Mission 2200 — Agent 01: Operasyon Kontrol Servisi.

Operatör komutlarını Mission 2100 sertifikalı kontrollü yürütme
API'sine bağlayan durum makinesi katmanı.

Değişmezler:
- Tarayıcı/şablon hiçbir zaman borsa katmanına dokunamaz;
  pozisyon kapatma DAİMA ControlledExecutionAPI.submit üzerinden
  kontrollü kapatma NİYETİ olarak akar.
- Her durum değiştiren eylem: idempotent, denetlenir, sterildir
  ve geçersiz geçişleri reddeder (fail-closed).
- Yıkıcı global eylemler yazılı onay ifadesi + neden + idempotency
  anahtarı gerektirir.
- Kill-switch etkinken otomasyon BLOCKED'a düşer; hiçbir koşul
  belirsizken yürütmeye izin verilmez.
"""

from __future__ import annotations

import functools
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Callable, Mapping, Optional, Tuple

from controlled_execution_api import ControlledExecutionAPI
from controlled_execution_api_models import (
    ControlledExecutionAPIDecision, ControlledExecutionOperation,
    ControlledExecutionRequest, ControlledExecutionState)
from controlled_execution_models import ControlledExecutionMode
from execution_enums import OrderSide, OrderType, TimeInForce
from execution_models import ExecutionRequest
from operation_control_audit import (
    FORBIDDEN_AUDIT_TOKENS, OperationAuditTrail)
from operation_control_errors import (
    OperationControlValidationError)
from operation_control_models import (
    AutomationCommand, AutomationState, IdempotencyStatus,
    OperationActionResult, OperationActionStatus,
    OperationAuditRecord, PositionView, SymbolAutomationState,
    SymbolCommand)
from operation_control_policy import (
    DEFAULT_AUTOMATION_STATE, DEFAULT_SYMBOL_STATE,
    resolve_symbol_transition, resolve_transition)
from operation_control_store import (
    OperationControlStateError, OperationControlStateStore)
from paper_execution_models import PaperExecutionReferences
from paper_models import PaperLedgerSnapshot

__all__ = ["CONFIRMATION_PHRASE", "OperationControlService"]

# Yıkıcı eylemler için zorunlu yazılı onay ifadesi.
CONFIRMATION_PHRASE = "ONAYLIYORUM"

_SIDE_CLOSE = {"BUY": OrderSide.SELL, "LONG": OrderSide.SELL,
               "SELL": OrderSide.BUY, "SHORT": OrderSide.BUY}


def _clock_guard(clock: Optional[Callable[[], int]]
                 ) -> Callable[[], int]:
    if clock is None:
        return lambda: 0
    return clock


def _shared_mutation(method):
    """Paylaşımlı depo varsa: kilit → yükle → çalıştır → kaydet.

    Süreçler-arası tutarlılık garantisi: mutasyon SADECE münhasır
    ``flock`` altında, en güncel paylaşımlı anlık görüntü üzerinde
    çalışır ve sonucu atomik olarak geri yazar. Aynı idempotency
    anahtarı böylece hiçbir worker'da ikinci kez kabul edilemez."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        store = self._store
        if store is None or store.in_transaction:
            return method(self, *args, **kwargs)
        with store.locked():
            self._load_shared()
            try:
                return method(self, *args, **kwargs)
            finally:
                self._save_shared()
    return wrapper


def _shared_view(method):
    """Paylaşımlı depo varsa okuma öncesi en güncel durumu yükle."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        store = self._store
        if store is None or store.in_transaction:
            return method(self, *args, **kwargs)
        with store.locked():
            self._load_shared()
            return method(self, *args, **kwargs)
    return wrapper


class OperationControlService:
    """Bellek içi operasyon durum makinesi + sertifikalı köprü."""

    __slots__ = ("_api", "_clock", "_automation_state",
                 "_stop_new_entries", "_symbol_states",
                 "_audit", "_idempotency", "_sequence",
                 "_last_error_code", "_store")

    def __init__(self, execution_api: ControlledExecutionAPI,
                 clock: Optional[Callable[[], int]] = None,
                 state_store: Optional[
                     OperationControlStateStore] = None
                 ) -> None:
        if not isinstance(execution_api,
                          ControlledExecutionAPI):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:execution_api")
        if state_store is not None and not isinstance(
                state_store, OperationControlStateStore):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:state_store")
        self._store = state_store
        self._api = execution_api
        self._clock = _clock_guard(clock)
        # Temiz kurulum varsayılanları — fail-closed.
        self._automation_state = DEFAULT_AUTOMATION_STATE
        self._stop_new_entries = False
        self._symbol_states: dict = {}
        self._audit = OperationAuditTrail()
        self._idempotency: dict = {}
        self._sequence = 0
        self._last_error_code = "-"

    # ── Paylaşımlı durum (süreçler-arası tutarlılık) ─────────

    def _dump_shared(self) -> dict:
        """Süreç durumunu JSON-uyumlu anlık görüntüye dök."""
        return {
            "sequence": self._sequence,
            "automation_state": self._automation_state.value,
            "stop_new_entries": self._stop_new_entries,
            "last_error_code": self._last_error_code,
            "symbol_states": {sym: state.value for sym, state
                              in self._symbol_states.items()},
            "audit": [asdict(rec)
                      for rec in self._audit.records()],
            "idempotency": {
                key: {"signature": sig,
                      "result": self._encode_result(res)}
                for key, (sig, res)
                in self._idempotency.items()},
        }

    @staticmethod
    def _encode_result(result: OperationActionResult) -> dict:
        payload = asdict(result)
        payload["status"] = result.status.value
        payload["idempotency_status"] = \
            result.idempotency_status.value
        payload["detail_codes"] = list(result.detail_codes)
        return payload

    @staticmethod
    def _decode_result(payload: object
                       ) -> OperationActionResult:
        if not isinstance(payload, dict):
            raise OperationControlStateError(
                "STATE_STORE_CORRUPT:result")
        try:
            data = dict(payload)
            data["status"] = OperationActionStatus(
                data["status"])
            data["idempotency_status"] = IdempotencyStatus(
                data["idempotency_status"])
            data["detail_codes"] = tuple(
                data.get("detail_codes") or ())
            return OperationActionResult(**data)
        except (KeyError, TypeError, ValueError,
                OperationControlValidationError) as exc:
            raise OperationControlStateError(
                "STATE_STORE_CORRUPT:result") from exc

    def _load_shared(self) -> None:
        """Paylaşımlı anlık görüntüyü süreç durumuna yükle.

        Dosya yoksa temiz kurulum varsayılanlarına döner
        (fail-closed). Bozuk anlık görüntü steril hata ile
        yükselir — durum ASLA sessizce sıfırlanmaz."""
        payload = self._store.load()
        if payload is None:
            self._automation_state = DEFAULT_AUTOMATION_STATE
            self._stop_new_entries = False
            self._symbol_states = {}
            self._audit = OperationAuditTrail()
            self._idempotency = {}
            self._sequence = 0
            self._last_error_code = "-"
            return
        try:
            audit = OperationAuditTrail()
            for rec in payload.get("audit") or []:
                audit.append(OperationAuditRecord(**rec))
            idempotency = {}
            for key, entry in (payload.get("idempotency")
                               or {}).items():
                idempotency[key] = (
                    entry["signature"],
                    self._decode_result(entry["result"]))
            symbol_states = {
                sym: SymbolAutomationState(value)
                for sym, value in (payload.get("symbol_states")
                                   or {}).items()}
            automation_state = AutomationState(
                payload["automation_state"])
            sequence = payload["sequence"]
            stop_new_entries = payload["stop_new_entries"]
            last_error_code = payload["last_error_code"]
            if not isinstance(sequence, int) or isinstance(
                    sequence, bool) or sequence < 0 or \
                    not isinstance(stop_new_entries, bool) or \
                    not isinstance(last_error_code, str):
                raise ValueError("invalid scalar")
        except OperationControlStateError:
            raise
        except Exception as exc:
            raise OperationControlStateError(
                "STATE_STORE_CORRUPT:state") from exc
        self._automation_state = automation_state
        self._stop_new_entries = stop_new_entries
        self._symbol_states = symbol_states
        self._audit = audit
        self._idempotency = idempotency
        self._sequence = sequence
        self._last_error_code = last_error_code

    def _save_shared(self) -> None:
        self._store.save(self._dump_shared())

    # ── Salt-okunur durum ────────────────────────────────────

    @property
    @_shared_view
    def automation_state(self) -> AutomationState:
        return self._automation_state

    @property
    @_shared_view
    def stop_new_entries(self) -> bool:
        return self._stop_new_entries

    @property
    @_shared_view
    def last_error_code(self) -> str:
        return self._last_error_code

    @_shared_view
    def symbol_state(self, symbol: str
                     ) -> SymbolAutomationState:
        """Kayıtsız sembol DAİMA DISABLED (fail-closed)."""
        if not isinstance(symbol, str) or not symbol:
            return DEFAULT_SYMBOL_STATE
        return self._symbol_states.get(symbol.upper(),
                                       DEFAULT_SYMBOL_STATE)

    @_shared_view
    def symbol_states(self) -> Mapping[str,
                                       SymbolAutomationState]:
        return dict(self._symbol_states)

    @property
    @_shared_view
    def audit(self) -> OperationAuditTrail:
        return self._audit

    # ── Yardımcılar ──────────────────────────────────────────

    def _next_ids(self, prefix: str) -> Tuple[str, str, int]:
        self._sequence += 1
        seq = self._sequence
        return (f"{prefix}-{seq}", f"opc-{seq}", seq)

    def _record(self, actor: str, action: str, target: str,
                previous: str, requested: str, result: str,
                reason: str, correlation_id: str,
                idempotency_key: Optional[str],
                error_code: Optional[str]) -> bool:
        self._audit.append(OperationAuditRecord(
            timestamp=self._clock(),
            actor=actor, action=action, target=target,
            previous_state=previous,
            requested_state=requested, result=result,
            reason=reason, correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            error_code=error_code))
        return True

    def _idempotency_check(self, key: Optional[str],
                           signature: str):
        """(durum, saklı-sonuç) döndür — çakışma CONFLICT."""
        if key is None:
            return IdempotencyStatus.NEW, None
        if not isinstance(key, str) or not key.strip():
            return IdempotencyStatus.CONFLICT, None
        entry = self._idempotency.get(key)
        if entry is None:
            return IdempotencyStatus.NEW, None
        stored_signature, stored_result = entry
        if stored_signature != signature:
            return IdempotencyStatus.CONFLICT, None
        # Tekrar oynatma AÇIKÇA işaretlenir — çift tıklama
        # ikinci bir niyet ÜRETMEZ.
        return IdempotencyStatus.REPLAYED, replace(
            stored_result,
            idempotency_status=IdempotencyStatus.REPLAYED)

    def _store_idempotency(self, key: Optional[str],
                           signature: str,
                           result: OperationActionResult
                           ) -> None:
        if isinstance(key, str) and key.strip():
            self._idempotency[key] = (signature, result)

    @staticmethod
    def _screen(field: str, value: object) -> None:
        """Kullanıcı metni denetim kaydına girmeden ÖNCE taranır.

        Yasak belirteç içeren metin, durum mutasyonundan önce
        reddedilir — böylece denetim zinciri asla mutasyondan
        sonra patlayıp denetimsiz durum değişikliği bırakmaz."""
        if not isinstance(value, str):
            return
        lowered = value.lower()
        for token in FORBIDDEN_AUDIT_TOKENS:
            if token in lowered:
                raise OperationControlValidationError(
                    f"INVALID_OPERATION_FIELD:{field}")

    @classmethod
    def _actor(cls, actor: object) -> str:
        if not isinstance(actor, str) or not actor.strip():
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:actor")
        cls._screen("actor", actor)
        return actor.strip()

    @classmethod
    def _screen_inputs(cls, reason: object,
                       idempotency_key: object) -> None:
        cls._screen("reason", reason)
        cls._screen("idempotency_key", idempotency_key)

    def _denied(self, action_id: str, correlation_id: str,
                previous: str, error_code: str,
                idem: IdempotencyStatus =
                IdempotencyStatus.NEW
                ) -> OperationActionResult:
        self._last_error_code = error_code
        return OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.DENIED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="REJECTED",
            previous_state=previous,
            current_state=previous,
            error_code=error_code)

    # ── Otomasyon komutları ──────────────────────────────────

    @_shared_mutation
    def execute_automation_command(
            self, command: AutomationCommand, actor: str,
            idempotency_key: Optional[str] = None
            ) -> OperationActionResult:
        """START/PAUSE/RESUME/STOP — idempotent + denetimli."""
        actor = self._actor(actor)
        if not isinstance(command, AutomationCommand):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:command")
        self._screen("idempotency_key", idempotency_key)
        signature = f"AUTOMATION:{command.value}"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, _ = self._next_ids("auto")
        previous = self._automation_state
        if idem is IdempotencyStatus.CONFLICT:
            self._record(actor, signature, "automation",
                         previous.value, command.value,
                         "CONFLICT", "idempotency conflict",
                         correlation_id, idempotency_key,
                         "IDEMPOTENCY_CONFLICT")
            return self._denied(action_id, correlation_id,
                                previous.value,
                                "IDEMPOTENCY_CONFLICT", idem)
        # Kill-switch nedenli BLOCKED yalnız STOP kabul eder;
        # START ile baypas edilemez.
        try:
            target, repeat = resolve_transition(previous,
                                                command)
        except KeyError:
            self._record(actor, signature, "automation",
                         previous.value, command.value,
                         "REJECTED", "invalid transition",
                         correlation_id, idempotency_key,
                         "INVALID_TRANSITION")
            return self._denied(action_id, correlation_id,
                                previous.value,
                                "INVALID_TRANSITION")
        self._automation_state = target
        self._record(actor, signature, "automation",
                     previous.value, target.value,
                     "COMPLETED", "operator command",
                     correlation_id, idempotency_key, None)
        result = OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.COMPLETED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="APPLIED" if not repeat
            else "IDEMPOTENT_REPEAT",
            previous_state=previous.value,
            current_state=target.value)
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result

    @_shared_mutation
    def mark_blocked(self, actor: str, reason: str) -> None:
        """Kill-switch devrede → otomasyon BLOCKED."""
        actor = self._actor(actor)
        self._screen("reason", reason)
        previous = self._automation_state
        if previous is AutomationState.STOPPED:
            return
        self._automation_state = AutomationState.BLOCKED
        _, correlation_id, _ = self._next_ids("blk")
        self._record(actor, "AUTOMATION:BLOCK", "automation",
                     previous.value,
                     AutomationState.BLOCKED.value,
                     "COMPLETED", reason if isinstance(
                         reason, str) and reason else
                     "kill switch engaged",
                     correlation_id, None, None)

    # ── Sembol komutları ─────────────────────────────────────

    @_shared_mutation
    def execute_symbol_command(
            self, symbol: str, command: SymbolCommand,
            actor: str,
            idempotency_key: Optional[str] = None
            ) -> OperationActionResult:
        """Sembol düzeyi komut — diğer sembolleri ETKİLEMEZ."""
        actor = self._actor(actor)
        if not isinstance(symbol, str) or not symbol.strip():
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:symbol")
        if not isinstance(command, SymbolCommand):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:command")
        self._screen("symbol", symbol)
        self._screen("idempotency_key", idempotency_key)
        symbol = symbol.strip().upper()
        signature = f"SYMBOL:{symbol}:{command.value}"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, _ = self._next_ids("sym")
        previous = self.symbol_state(symbol)
        if idem is IdempotencyStatus.CONFLICT:
            self._record(actor, signature, symbol,
                         previous.value, command.value,
                         "CONFLICT", "idempotency conflict",
                         correlation_id, idempotency_key,
                         "IDEMPOTENCY_CONFLICT")
            return self._denied(action_id, correlation_id,
                                previous.value,
                                "IDEMPOTENCY_CONFLICT", idem)
        try:
            target, repeat = resolve_symbol_transition(
                previous, command)
        except KeyError:
            self._record(actor, signature, symbol,
                         previous.value, command.value,
                         "REJECTED", "invalid transition",
                         correlation_id, idempotency_key,
                         "INVALID_TRANSITION")
            return self._denied(action_id, correlation_id,
                                previous.value,
                                "INVALID_TRANSITION")
        self._symbol_states[symbol] = target
        self._record(actor, signature, symbol,
                     previous.value, target.value,
                     "COMPLETED", "operator command",
                     correlation_id, idempotency_key, None)
        result = OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.COMPLETED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="APPLIED" if not repeat
            else "IDEMPOTENT_REPEAT",
            previous_state=previous.value,
            current_state=target.value)
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result

    # ── Yıkıcı eylem ön koşulları ────────────────────────────

    def _destructive_guard(self, reason: object,
                           confirm_phrase: object,
                           idempotency_key: object
                           ) -> Optional[str]:
        """Sıralı ön koşul; ihlalde steril kod döndürür."""
        if not isinstance(reason, str) or not reason.strip():
            return "POLICY_DENIED:reason_required"
        if confirm_phrase != CONFIRMATION_PHRASE:
            return "POLICY_DENIED:confirmation_required"
        if not isinstance(idempotency_key, str) or \
                not idempotency_key.strip():
            return "POLICY_DENIED:idempotency_key_required"
        return None

    # ── Global: yeni girişleri durdur ────────────────────────

    @_shared_mutation
    def stop_new_entries_action(
            self, actor: str, reason: str,
            confirm_phrase: str, idempotency_key: str
            ) -> OperationActionResult:
        """Yeni pozisyon açılışını engeller; mevcutları KAPATMAZ."""
        actor = self._actor(actor)
        self._screen_inputs(reason, idempotency_key)
        signature = "GLOBAL:STOP_NEW_ENTRIES"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, _ = self._next_ids("gse")
        previous = "ALLOWED" if not self._stop_new_entries \
            else "STOPPED"
        if idem is IdempotencyStatus.CONFLICT:
            return self._denied(action_id, correlation_id,
                                previous,
                                "IDEMPOTENCY_CONFLICT", idem)
        guard = self._destructive_guard(reason, confirm_phrase,
                                        idempotency_key)
        if guard is not None:
            self._record(actor, signature, "global",
                         previous, "STOPPED", "DENIED",
                         "destructive guard", correlation_id,
                         None, guard)
            return self._denied(action_id, correlation_id,
                                previous, guard)
        self._stop_new_entries = True
        self._record(actor, signature, "global", previous,
                     "STOPPED", "COMPLETED", reason.strip(),
                     correlation_id, idempotency_key, None)
        result = OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.COMPLETED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="APPLIED",
            previous_state=previous,
            current_state="STOPPED")
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result

    # ── Kontrollü pozisyon kapatma niyeti ────────────────────

    @_shared_mutation
    def request_position_close(
            self, position: PositionView,
            ledger: Optional[PaperLedgerSnapshot],
            policy: object, kill_switch: object,
            actor: str, reason: str, confirm_phrase: str,
            idempotency_key: str) -> OperationActionResult:
        """Kontrollü kapatma NİYETİ oluştur.

        Doğrudan borsa çağrısı YOKTUR: niyet Mission 2100
        boru hattından (yetki → izin → risk → kill-switch →
        yaşam döngüsü → defter) geçer. Defter anlık görüntüsü
        yoksa istek fail-closed REDDEDİLİR."""
        actor = self._actor(actor)
        if not isinstance(position, PositionView):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:position")
        self._screen_inputs(reason, idempotency_key)
        signature = f"CLOSE:{position.position_id}"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, seq = self._next_ids("cls")
        previous = position.position_status
        if idem is IdempotencyStatus.CONFLICT:
            return self._denied(action_id, correlation_id,
                                previous,
                                "IDEMPOTENCY_CONFLICT", idem)
        guard = self._destructive_guard(reason, confirm_phrase,
                                        idempotency_key)
        if guard is not None:
            self._record(actor, signature,
                         position.position_id, previous,
                         "CLOSE_REQUESTED", "DENIED",
                         "destructive guard", correlation_id,
                         None, guard)
            return self._denied(action_id, correlation_id,
                                previous, guard)
        close_side = _SIDE_CLOSE.get(position.side.upper())
        quantity = position.quantity
        price = position.current_price
        if close_side is None or not isinstance(
                quantity, Decimal) or quantity <= 0:
            self._record(actor, signature,
                         position.position_id, previous,
                         "CLOSE_REQUESTED", "DENIED",
                         "position data incomplete",
                         correlation_id, idempotency_key,
                         "POSITION_DATA_INCOMPLETE")
            return self._denied(action_id, correlation_id,
                                previous,
                                "POSITION_DATA_INCOMPLETE")
        if ledger is None or not isinstance(
                ledger, PaperLedgerSnapshot) or \
                not isinstance(price, Decimal) or price <= 0:
            self._record(actor, signature,
                         position.position_id, previous,
                         "CLOSE_REQUESTED", "DENIED",
                         "safety dependency unavailable",
                         correlation_id, idempotency_key,
                         "DEPENDENCY_UNAVAILABLE")
            return self._denied(action_id, correlation_id,
                                previous,
                                "DEPENDENCY_UNAVAILABLE")
        request = ControlledExecutionRequest(
            mode=ControlledExecutionMode.PAPER,
            operation=ControlledExecutionOperation.SUBMIT,
            request_reference=action_id,
            logical_sequence=seq,
            policy=policy, kill_switch=kill_switch,
            execution=ExecutionRequest(
                symbol=position.symbol, side=close_side,
                order_type=OrderType.LIMIT,
                quantity=quantity,
                time_in_force=TimeInForce.IOC,
                price=price),
            order_reference=action_id)
        state = ControlledExecutionState(
            ledger=ledger,
            paper_references=PaperExecutionReferences(
                request_reference=action_id,
                previous_ledger_reference=f"led-{seq}-prev",
                current_ledger_reference=f"led-{seq}-cur",
                logical_sequence=seq))
        try:
            response = self._api.submit(request, state)
        except Exception:
            # Sertifikalı kat sözleşme ihlalini istisna ile
            # bildirir; operatöre HAM metin sızdırılmaz —
            # steril kodla fail-closed RED.
            self._record(actor, signature,
                         position.position_id, previous,
                         "CLOSE_REQUESTED", "DENIED",
                         "execution layer rejected",
                         correlation_id, idempotency_key,
                         "EXECUTION_REJECTED")
            result = self._denied(action_id, correlation_id,
                                  previous,
                                  "EXECUTION_REJECTED")
            self._store_idempotency(idempotency_key,
                                    signature, result)
            return result
        accepted = response.decision is \
            ControlledExecutionAPIDecision.ACCEPTED
        self._record(actor, signature, position.position_id,
                     previous, "CLOSE_REQUESTED",
                     "ACCEPTED" if accepted else "DENIED",
                     reason.strip(), correlation_id,
                     idempotency_key,
                     None if accepted
                     else response.decision_code)
        if not accepted:
            self._last_error_code = response.decision_code
        result = OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.ACCEPTED if accepted
            else OperationActionStatus.DENIED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="CLOSE_REQUESTED" if accepted
            else "REJECTED",
            previous_state=previous,
            current_state="CLOSE_REQUESTED" if accepted
            else previous,
            error_code=None if accepted
            else response.decision_code,
            detail_codes=(response.decision_code,))
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result

    # ── Global: tümünü kapatma isteği ────────────────────────

    @_shared_mutation
    def request_close_all(
            self, positions: Tuple[PositionView, ...],
            ledger: Optional[PaperLedgerSnapshot],
            policy: object, kill_switch: object,
            actor: str, reason: str, confirm_phrase: str,
            idempotency_key: str) -> OperationActionResult:
        """Uygun her pozisyon için AYRI kontrollü kapatma niyeti.

        Kısmi başarı ASLA tam başarı olarak raporlanmaz; her
        pozisyonun sonucu ``detail_codes`` içinde ayrı satırdır."""
        actor = self._actor(actor)
        self._screen_inputs(reason, idempotency_key)
        signature = "GLOBAL:REQUEST_CLOSE_ALL"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, _ = self._next_ids("cla")
        if idem is IdempotencyStatus.CONFLICT:
            return self._denied(action_id, correlation_id,
                                "OPEN",
                                "IDEMPOTENCY_CONFLICT", idem)
        guard = self._destructive_guard(reason, confirm_phrase,
                                        idempotency_key)
        if guard is not None:
            self._record(actor, signature, "global", "OPEN",
                         "CLOSE_REQUESTED", "DENIED",
                         "destructive guard", correlation_id,
                         None, guard)
            return self._denied(action_id, correlation_id,
                                "OPEN", guard)
        if not isinstance(positions, tuple):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:positions")
        details = []
        accepted_count = 0
        for position in positions:
            single = self.request_position_close(
                position, ledger, policy, kill_switch, actor,
                reason, confirm_phrase,
                f"{idempotency_key}:{position.position_id}")
            outcome = "ACCEPTED" if single.status is \
                OperationActionStatus.ACCEPTED else "DENIED"
            if outcome == "ACCEPTED":
                accepted_count += 1
            details.append(
                f"{position.position_id}:{outcome}:"
                f"{single.error_code or 'OK'}")
        total = len(positions)
        if total == 0:
            status = OperationActionStatus.COMPLETED
        elif accepted_count == total:
            status = OperationActionStatus.ACCEPTED
        elif accepted_count == 0:
            status = OperationActionStatus.FAILED
        else:
            status = OperationActionStatus.PARTIAL
        self._record(actor, signature, "global", "OPEN",
                     "CLOSE_REQUESTED", status.value,
                     reason.strip(), correlation_id,
                     idempotency_key,
                     None if status in (
                         OperationActionStatus.ACCEPTED,
                         OperationActionStatus.COMPLETED)
                     else "CLOSE_ALL_INCOMPLETE")
        result = OperationActionResult(
            action_id=action_id,
            status=status,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="CLOSE_REQUESTED",
            previous_state="OPEN",
            current_state="CLOSE_REQUESTED",
            error_code=None if status in (
                OperationActionStatus.ACCEPTED,
                OperationActionStatus.COMPLETED)
            else "CLOSE_ALL_INCOMPLETE",
            detail_codes=tuple(details))
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result

    # ── Kill-switch denetim köprüsü ──────────────────────────

    @_shared_mutation
    def record_kill_switch(
            self, actor: str, engaged: bool, reason: str,
            confirm_phrase: str, idempotency_key: str
            ) -> OperationActionResult:
        """Sertifikalı kill-switch eyleminin denetim kaydı.

        Mekanizmanın kendisi mevcut sertifikalı yol üzerinden
        (app katmanında) tetiklenir; bu köprü durumu ve denetimi
        yönetir. Devreye almada otomasyon BLOCKED olur; pozisyon
        kapatıldığı ASLA iddia edilmez."""
        actor = self._actor(actor)
        if not isinstance(engaged, bool):
            raise OperationControlValidationError(
                "INVALID_OPERATION_FIELD:engaged")
        self._screen_inputs(reason, idempotency_key)
        signature = f"GLOBAL:KILL_SWITCH:{engaged}"
        idem, stored = self._idempotency_check(
            idempotency_key, signature)
        if idem is IdempotencyStatus.REPLAYED:
            return stored
        action_id, correlation_id, _ = self._next_ids("ks")
        previous = self._automation_state.value
        if idem is IdempotencyStatus.CONFLICT:
            return self._denied(action_id, correlation_id,
                                previous,
                                "IDEMPOTENCY_CONFLICT", idem)
        # Devreye alma DA devreden çıkarma DA yıkıcı/güvenlik
        # kritiktir: kapatmak ticareti yeniden AÇAR. Her ikisi de
        # neden + onay ifadesi + idempotency anahtarı gerektirir.
        guard = self._destructive_guard(reason, confirm_phrase,
                                        idempotency_key)
        if guard is not None:
            self._record(actor, signature, "global", previous,
                         "BLOCKED" if engaged else previous,
                         "DENIED",
                         "destructive guard", correlation_id,
                         None, guard)
            return self._denied(action_id, correlation_id,
                                previous, guard)
        if engaged:
            self.mark_blocked(actor, "kill switch engaged")
        self._record(actor, signature, "global", previous,
                     "BLOCKED" if engaged else previous,
                     "COMPLETED",
                     reason.strip() if isinstance(reason, str)
                     and reason.strip() else "operator command",
                     correlation_id, idempotency_key, None)
        result = OperationActionResult(
            action_id=action_id,
            status=OperationActionStatus.COMPLETED,
            correlation_id=correlation_id,
            idempotency_status=idem,
            audit_recorded=True,
            lifecycle_status="APPLIED",
            previous_state=previous,
            current_state=self._automation_state.value)
        self._store_idempotency(idempotency_key, signature,
                                result)
        return result
