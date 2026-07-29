/* Mission 2200 — Operation Control Center istemcisi.
 *
 * Kurallar:
 * - Hiçbir istek borsa API'sine gitmez; yalnız uygulamanın
 *   /api/operation-control/* uçları çağrılır.
 * - Bekleyen istek sırasında düğmeler kilitlenir (çift tıklama
 *   çift niyet ÜRETMEZ; idempotency anahtarı da sunucudadır).
 * - Bilinmeyen durum asla başarı gibi stillenmez.
 * - Yenileme operatör seçimlerini sıfırlamaz (tablolar yeniden
 *   çizilir; açık onay diyaloğu yenilemeden etkilenmez).
 */
(function () {
  "use strict";

  var API = "/api/operation-control";
  var pending = false;
  var confirmationPhrase = "ONAYLIYORUM";

  function esc(v) {
    return String(v === null || v === undefined ? "UNKNOWN" : v)
      .replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
                 '"': "&quot;", "'": "&#39;" }[c];
      });
  }

  function stateClass(value) {
    var v = String(value || "UNKNOWN").toUpperCase();
    if (v === "UNKNOWN" || v === "STALE" || v === "PENDING") return "unknown";
    if (v === "ERROR" || v === "MISMATCH" || v === "BLOCKED" ||
        v === "ACTIVE" || v === "FAILED") return "bad";
    if (v === "READY" || v === "OK" || v === "MATCHED" || v === "RUNNING" ||
        v === "FRESH" || v === "INACTIVE") return "good";
    return "unknown";
  }

  function banner(id, cls, text) {
    var el = document.getElementById(id);
    if (!el) return;
    el.className = "oc-banner" + (text ? " " + cls : "");
    el.textContent = text || "";
  }

  function req(path, method, body) {
    var opts = { method: method || "GET",
                 headers: { "X-CSRFToken": window.OC_CSRF } };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(API + path, opts).then(function (r) {
      return r.json().then(function (j) { return { http: r.status, body: j }; });
    });
  }

  function setButtonsDisabled(disabled) {
    document.querySelectorAll("button.oc-btn").forEach(function (b) {
      if (b.getAttribute("aria-disabled") === "true") return;
      b.disabled = disabled;
    });
  }

  function idemKey(prefix) {
    return prefix + "-" + Date.now() + "-" +
      Math.random().toString(36).slice(2, 10);
  }

  function action(path, body, okMsg) {
    if (pending) return Promise.resolve();
    pending = true;
    setButtonsDisabled(true);
    return req(path, "POST", body).then(function (res) {
      if (res.body && res.body.ok) {
        banner("oc-error", "", "");
        if (okMsg) banner("oc-stale", "warn", okMsg +
          " (eylem: " + esc(res.body.action_id) + ")");
      } else {
        banner("oc-error", "error", "Eylem reddedildi — kod: " +
          esc(res.body && res.body.error_code) + " (HTTP " + res.http + ")");
      }
    }).catch(function () {
      banner("oc-error", "error", "İstek başarısız — ağ hatası (OC_NETWORK).");
    }).then(function () {
      pending = false;
      setButtonsDisabled(false);
      return refresh();
    });
  }

  // ── Yıkıcı eylem onayı ────────────────────────────────────
  function confirmDestructive(title, bodyHtml) {
    return new Promise(function (resolve) {
      var dlg = document.getElementById("oc-dialog");
      document.getElementById("oc-dialog-title").textContent = title;
      document.getElementById("oc-dialog-body").innerHTML = bodyHtml;
      document.getElementById("oc-dialog-phrase-label").textContent =
        confirmationPhrase;
      var reason = document.getElementById("oc-dialog-reason");
      var phrase = document.getElementById("oc-dialog-phrase");
      reason.value = ""; phrase.value = "";
      dlg.returnValue = "cancel";
      dlg.showModal();
      dlg.addEventListener("close", function onClose() {
        dlg.removeEventListener("close", onClose);
        if (dlg.returnValue !== "ok" || !reason.value.trim()) {
          resolve(null); return;
        }
        resolve({ reason: reason.value.trim(),
                  confirm_phrase: phrase.value.trim() });
      });
    });
  }

  // ── Bölüm çizimleri ───────────────────────────────────────
  function kv(label, value, stateful) {
    var cls = stateful ? " class=\"oc-state " + stateClass(value) + "\"" : "";
    return "<div><b>" + esc(label) + "</b><span" + cls + ">" +
      esc(value) + "</span></div>";
  }

  function renderStatus(s) {
    var grid = document.getElementById("oc-status-grid");
    grid.innerHTML =
      kv("Uygulama Sürümü", s.app_version) +
      kv("Yürütme Modu", s.execution_mode) +
      kv("Otomasyon", s.automation_state, true) +
      kv("Kill-Switch", s.kill_switch_state, true) +
      kv("İzin Kapısı", s.permission_gate_state, true) +
      kv("Risk Motoru", s.risk_engine_state, true) +
      kv("Broker", s.broker_state, true) +
      kv("Defter", s.ledger_state, true) +
      kv("Mutabakat", s.reconciliation_state, true) +
      kv("Son Senkron", s.last_sync_at) +
      kv("Son Hata Kodu", s.last_error_code) +
      kv("Yeni Giriş Bloklu", s.stop_new_entries ? "EVET" : "HAYIR");
    var auto = document.getElementById("oc-auto-state");
    auto.textContent = s.automation_state;
    auto.className = "oc-state " + stateClass(s.automation_state);
    var mode = document.getElementById("oc-mode");
    mode.textContent = s.execution_mode;
    mode.className = s.execution_mode === "PAPER" ? "oc-mode-paper"
      : (s.execution_mode === "LIVE" ? "oc-mode-live" : "oc-mode-other");
    var fresh = document.getElementById("oc-freshness");
    fresh.textContent = s.data_freshness;
    fresh.className = "oc-state " + stateClass(s.data_freshness);
    if (s.data_freshness !== "FRESH") {
      banner("oc-stale", "warn", "Veri tazeliği: " + s.data_freshness +
        " — durum sağlıklı VARSAYILMAZ.");
    } else {
      banner("oc-stale", "", "");
    }
    document.getElementById("oc-sf-blocked").textContent =
      s.kill_switch_state === "ACTIVE" || s.stop_new_entries ? "EVET" : "HAYIR";
    document.getElementById("oc-sf-blocked").className =
      "oc-state " + (s.kill_switch_state === "ACTIVE" ? "bad" : "good");
    if (s.confirmation_phrase) confirmationPhrase = s.confirmation_phrase;
  }

  function fillTable(id, rows, mapper, emptyText, cols) {
    var tbody = document.querySelector("#" + id + " tbody");
    if (!rows || !rows.length) {
      tbody.innerHTML = "<tr><td colspan=\"" + cols +
        "\" class=\"oc-empty\">" + esc(emptyText) + "</td></tr>";
      return;
    }
    tbody.innerHTML = rows.map(mapper).join("");
  }

  function renderProducts(rows) {
    fillTable("oc-products", rows, function (p) {
      var sym = esc(p.symbol);
      return "<tr><td>" + sym + "</td><td>" + esc(p.market) + "</td><td>" +
        esc(p.strategy) + "</td><td class=\"oc-state " +
        stateClass(p.automation_state) + "\">" + esc(p.automation_state) +
        "</td><td>" + esc(p.signal_state) + "</td><td>" +
        esc(p.execution_mode) + "</td><td>" + esc(p.direction) + "</td><td>" +
        (p.entry_eligible ? "EVET" : "HAYIR") + "</td><td>" +
        esc(p.last_signal_at) + "</td><td>" + esc(p.last_decision) +
        "</td><td>" + esc(p.last_rejection_reason) + "</td><td>" +
        "<button class=\"oc-btn\" data-sym=\"" + sym + "\" data-symcmd=\"enable\">Etkinleştir</button> " +
        "<button class=\"oc-btn\" data-sym=\"" + sym + "\" data-symcmd=\"pause\">Duraklat</button> " +
        "<button class=\"oc-btn\" data-sym=\"" + sym + "\" data-symcmd=\"resume\">Sürdür</button> " +
        "<button class=\"oc-btn danger\" data-sym=\"" + sym + "\" data-symcmd=\"stop\">Girişleri Durdur</button>" +
        "</td></tr>";
    }, "Yönetilen ürün verisi yok.", 12);
  }

  function renderPositions(rows) {
    document.getElementById("oc-sf-open").textContent =
      rows ? String(rows.length) : "UNKNOWN";
    fillTable("oc-positions", rows, function (p) {
      return "<tr><td>" + esc(p.symbol) + "</td><td>" + esc(p.market) +
        "</td><td>" + esc(p.side) + "</td><td>" + esc(p.position_status) +
        "</td><td>" + esc(p.strategy) + "</td><td>" + esc(p.entry_price) +
        "</td><td>" + esc(p.current_price) + "</td><td>" + esc(p.quantity) +
        "</td><td>" + esc(p.notional_value) + "</td><td>" +
        esc(p.realized_pnl) + "</td><td>" + esc(p.unrealized_pnl) +
        "</td><td>" + esc(p.pnl_percent) + "</td><td>" + esc(p.fees) +
        "</td><td>" + esc(p.stop_loss) + "</td><td>" + esc(p.take_profit) +
        "</td><td>" + esc(p.max_favorable_excursion) + "</td><td>" +
        esc(p.max_adverse_excursion) + "</td><td>" + esc(p.opened_at) +
        "</td><td>" + esc(window.OWS && window.OWS.duration ?
          window.OWS.duration(p.opened_at) : "UNKNOWN") +
        "</td><td class=\"oc-state " + stateClass(p.reconciliation_state) +
        "\">" + esc(p.reconciliation_state) + "</td><td>" +
        esc(p.execution_mode) + "</td><td>" +
        "<button class=\"oc-btn danger\" data-close=\"" + esc(p.position_id) +
        "\" data-side=\"" + esc(p.side) + "\" data-qty=\"" + esc(p.quantity) +
        "\" data-mode=\"" + esc(p.execution_mode) + "\">Kapatma İsteği</button> " +
        "<button class=\"oc-btn\" disabled aria-disabled=\"true\" " +
        "title=\"Sertifikalı yürütme API'si desteklemiyor\">Stop/TP</button>" +
        "</td></tr>";
    }, "Açık pozisyon yok veya veri kullanılamıyor (UNKNOWN).", 22);
  }

  function renderOrders(rows) {
    fillTable("oc-orders", rows, function (o) {
      return "<tr tabindex=\"0\" data-expandable data-row-key=\"" +
        esc(o.order_id) + "\"><td>" + esc(o.order_id) + "</td><td>" +
        esc(o.client_order_id) + "</td><td>" + esc(o.symbol) + "</td><td>" +
        esc(o.side) + "</td><td>" + esc(o.order_type) + "</td><td>" +
        esc(o.quantity) + "</td><td>" + esc(o.requested_price) + "</td><td>" +
        esc(o.average_fill_price) + "</td><td>" + esc(o.filled_quantity) +
        "</td><td>" + esc(o.remaining_quantity) + "</td><td>" +
        esc(o.status) + "</td><td>" + esc(o.created_at) + "</td><td>" +
        esc(o.updated_at) + "</td><td>" + esc(o.strategy) + "</td><td>" +
        esc(o.correlation_id) + "</td><td>" + esc(o.execution_mode) +
        "</td><td class=\"oc-state " + stateClass(o.reconciliation_state) +
        "\">" + esc(o.reconciliation_state) + "</td></tr>";
    }, "Açık emir yok veya veri kullanılamıyor (UNKNOWN).", 17);
  }

  function renderSignals(rows) {
    fillTable("oc-signals", rows, function (s) {
      return "<tr><td>" + esc(s.signal_time) + "</td><td>" + esc(s.symbol) +
        "</td><td>" + esc(s.strategy) + "</td><td>" + esc(s.direction) +
        "</td><td>" + esc(s.confidence) + "</td><td>" + esc(s.kind) +
        "</td><td>" + esc(s.decision) + "</td><td>" + esc(s.risk_outcome) +
        "</td><td>" + esc(s.permission_outcome) + "</td><td>" +
        esc(s.rejection_code) + "</td><td>" + esc(s.execution_result) +
        "</td><td>" + esc(s.correlation_id) + "</td></tr>";
    }, "Sinyal kaydı yok — intelligence önerileri emir DEĞİLDİR.", 12);
  }

  function renderRecon(rows) {
    var pendingCount = 0;
    (rows || []).forEach(function (r) {
      if (r.state === "PENDING" || r.state === "UNKNOWN") pendingCount++;
    });
    document.getElementById("oc-sf-recon").textContent =
      rows && rows.length ? String(pendingCount) : "UNKNOWN";
    fillTable("oc-recon", rows, function (r) {
      return "<tr><td>" + esc(r.symbol) + "</td><td>" +
        esc(r.last_reconciled_at) + "</td><td>" + esc(r.ledger_position) +
        "</td><td>" + esc(r.broker_position) + "</td><td>" +
        esc(r.difference) + "</td><td>" + (r.order_mismatch ? "EVET" : "-") +
        "</td><td>" + (r.quantity_mismatch ? "EVET" : "-") + "</td><td>" +
        (r.price_mismatch ? "EVET" : "-") + "</td><td>" +
        (r.orphan_order ? "EVET" : "-") + "</td><td>" +
        (r.orphan_position ? "EVET" : "-") + "</td><td class=\"oc-state " +
        stateClass(r.state) + "\">" + esc(r.state) + "</td><td>" +
        esc(r.operator_action) + "</td></tr>";
    }, "Mutabakat verisi yok (UNKNOWN — sağlıklı VARSAYILMAZ).", 12);
  }

  function renderRisk(limits) {
    var grid = document.getElementById("oc-risk-grid");
    if (!limits) {
      grid.innerHTML = kv("Risk Limitleri", "UNKNOWN", true);
      return;
    }
    grid.innerHTML =
      kv("Maks. Emir Notyoneli", limits.max_order_notional) +
      kv("Maks. Pozisyon Notyoneli", limits.max_position_notional) +
      kv("Maks. Açık Pozisyon", limits.max_open_positions) +
      kv("Maks. Günlük Zarar %", limits.max_daily_loss) +
      kv("Maks. Drawdown", limits.max_drawdown) +
      kv("Maks. Sembol Maruziyeti", limits.max_symbol_exposure) +
      kv("Cooldown (sn)", limits.cooldown_seconds) +
      kv("İzinli Piyasalar", (limits.allowed_markets || []).join(", ")) +
      kv("İzinli Yönler", (limits.allowed_directions || []).join(", ")) +
      kv("İzinli Modlar", (limits.allowed_execution_modes || []).join(", ")) +
      kv("Micro-Live Yetkisi",
         limits.micro_live_authorized ? "VAR" : "DENIED", true) +
      kv("Yetki Bitişi", limits.authorization_expiry) +
      kv("Kill-Switch", limits.kill_switch_active ? "ACTIVE" : "INACTIVE",
         true);
  }

  function actorLabel(actor) {
    // local-dev-bypass teknik oturum kimliğidir, kullanıcı adı değil.
    // UI'da "Yerel Windows Oturumu" gösterilir (auth davranışı değişmez);
    // gerçek admin girişinde gerçek kullanıcı adı aynen görünür.
    if (actor === "local-dev-bypass" || actor === "replit-dev-bypass") {
      return "Yerel Windows Oturumu";
    }
    return actor;
  }

  function renderAudit(rows) {
    fillTable("oc-audit", rows, function (a) {
      return "<tr><td>" + esc(a.timestamp) + "</td><td>" +
        esc(actorLabel(a.actor)) +
        "</td><td>" + esc(a.action) + "</td><td>" + esc(a.target) +
        "</td><td>" + esc(a.previous_state) + "</td><td>" +
        esc(a.requested_state) + "</td><td>" + esc(a.result) + "</td><td>" +
        esc(a.reason) + "</td><td>" + esc(a.correlation_id) + "</td><td>" +
        esc(a.idempotency_key) + "</td><td>" + esc(a.error_code) +
        "</td></tr>";
    }, "Henüz denetim kaydı yok.", 11);
  }

  // ── Yenileme ─────────────────────────────────────────────
  function refresh() {
    return Promise.all([
      req("/status"), req("/products"), req("/positions"),
      req("/orders"), req("/signals"), req("/reconciliation"),
      req("/risk"), req("/audit")
    ]).then(function (r) {
      var status = r[0].body;
      if (!status || !status.ok) {
        banner("oc-error", "error", "Durum okunamadı — kod: " +
          esc(status && status.error_code));
        return;
      }
      var s = status.data;
      s.data_freshness = status.data_freshness;
      renderStatus(s);
      renderProducts(r[1].body.ok ? r[1].body.data.products : null);
      renderPositions(r[2].body.ok ? r[2].body.data.positions : null);
      renderOrders(r[3].body.ok ? r[3].body.data.orders : null);
      renderSignals(r[4].body.ok ? r[4].body.data.signals : null);
      renderRecon(r[5].body.ok ? r[5].body.data.reconciliation : null);
      renderRisk(r[6].body.ok ? r[6].body.data.risk_limits : null);
      renderAudit(r[7].body.ok ? r[7].body.data.audit : null);
      document.getElementById("oc-refreshed").textContent =
        new Date().toLocaleTimeString("tr-TR");
    }).catch(function () {
      banner("oc-error", "error",
        "Yenileme başarısız — ağ hatası (OC_NETWORK).");
    });
  }

  // ── Eylem bağlama ────────────────────────────────────────
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.auto) {
      action("/automation/" + b.dataset.auto,
        { idempotency_key: idemKey("auto") },
        "Otomasyon komutu uygulandı");
    } else if (b.dataset.symcmd) {
      action("/symbols/" + encodeURIComponent(b.dataset.sym) + "/" +
        b.dataset.symcmd, { idempotency_key: idemKey("sym") },
        b.dataset.sym + " sembol komutu uygulandı");
    } else if (b.dataset.close) {
      confirmDestructive("Pozisyon Kapatma Onayı",
        "Sembol: <b>" + esc(b.dataset.close) + "</b><br>Yön: <b>" +
        esc(b.dataset.side) + "</b> · Miktar: <b>" + esc(b.dataset.qty) +
        "</b><br>Yürütme modu: <b>" + esc(b.dataset.mode) +
        "</b><br>Beklenen eylem: kontrollü KAPATMA NİYETİ<br>" +
        "Bu istek <b>PAPER</b> simülasyon hattındadır; yetkili canlı " +
        "yürütme DEĞİLDİR.").then(function (c) {
        if (!c) return;
        action("/positions/" + encodeURIComponent(b.dataset.close) +
          "/close", { reason: c.reason, confirm_phrase: c.confirm_phrase,
                      idempotency_key: idemKey("close") },
          "Kapatma niyeti oluşturuldu");
      });
    } else if (b.id === "oc-stop-entries") {
      confirmDestructive("Yeni Girişleri Durdur",
        "Yeni pozisyon oluşturmayı bloklar. Mevcut pozisyonları " +
        "KAPATMAZ; onaylı çıkışlar ve koruyucu kontroller sürebilir.")
        .then(function (c) {
          if (!c) return;
          action("/global/stop-new-entries",
            { reason: c.reason, confirm_phrase: c.confirm_phrase,
              idempotency_key: idemKey("gse") },
            "Yeni girişler durduruldu");
        });
    } else if (b.id === "oc-close-all") {
      confirmDestructive("Tümünü Kapatma İsteği",
        "Uygun her açık pozisyon için AYRI kontrollü kapatma niyeti " +
        "oluşturur. Doğrudan borsa çağrısı YAPILMAZ. Kısmi başarısızlık " +
        "pozisyon başına raporlanır.").then(function (c) {
          if (!c) return;
          action("/global/request-close-all",
            { reason: c.reason, confirm_phrase: c.confirm_phrase,
              idempotency_key: idemKey("cla") },
            "Kapatma niyetleri istendi").then(function () {
              var el = document.getElementById("oc-sf-intents");
              el.textContent = String(parseInt(el.textContent || "0", 10));
            });
        });
    } else if (b.id === "oc-kill") {
      confirmDestructive("Acil Kill-Switch",
        "Yeni yürütme yetkisi DERHAL bloklanır. Pozisyonlar otomatik " +
        "kapanmaz; kapatma ayrı niyet gerektirir.").then(function (c) {
          if (!c) return;
          action("/global/kill-switch",
            { engage: true, reason: c.reason,
              confirm_phrase: c.confirm_phrase,
              idempotency_key: idemKey("ks") },
            "Kill-switch devrede — ticaret bloklandı");
        });
    } else if (b.id === "oc-kill-off") {
      confirmDestructive("Kill-Switch Devre Dışı",
        "Kill-switch kapatmak ticaret yetkisini yeniden AÇAR. " +
        "Otomasyon otomatik başlamaz; ayrıca başlatılmalıdır.").then(
        function (c) {
          if (!c) return;
          action("/global/kill-switch",
            { engage: false, reason: c.reason,
              confirm_phrase: c.confirm_phrase,
              idempotency_key: idemKey("ksoff") },
            "Kill-switch kapatıldı");
        });
    }
  });

  refresh();
  setInterval(function () { if (!pending) refresh(); }, 15000);
})();
