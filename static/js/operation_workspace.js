/* Mission 2200 Agent 02 — İşlem Çalışma Alanı istemcisi.
 *
 * Kurallar:
 * - Yalnız /api/operation-control/* uçları; borsa API'sine istek YOK.
 * - Depo gerçek-zamanlı mekanizması: yoklama (SSE/WebSocket bu
 *   depoda sertifikasyonca yasaklıdır) — sayfa yenilemesi olmadan
 *   güncellenir.
 * - Operatör seçimleri sıfırlanmaz: arama kutuları yeniden
 *   çizilmez; genişletilmiş emir satırları yenilemeden sonra
 *   yeniden uygulanır; sıralama tercihi korunur.
 * - Bilinmeyen değer UNKNOWN olarak gösterilir; sahte 0 yok.
 */
(function () {
  "use strict";

  var API = "/api/operation-control";
  var POLL_MS = 10000;

  function esc(v) {
    return String(v === null || v === undefined ? "UNKNOWN" : v)
      .replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
                 '"': "&quot;", "'": "&#39;" }[c];
      });
  }

  function req(path, method, body) {
    var opts = { method: method || "GET",
                 headers: { "X-CSRFToken": window.OC_CSRF } };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(API + path, opts).then(function (r) {
      return r.json().then(function (j) {
        return { http: r.status, body: j };
      });
    });
  }

  // ── Yardımcılar ─────────────────────────────────────────────────

  function duration(openedAt) {
    var t = Date.parse(openedAt);
    if (isNaN(t)) return "UNKNOWN";
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h >= 24) return Math.floor(h / 24) + "g " + (h % 24) + "s";
    return h + "s " + m + "d";
  }

  function pnlClass(value) {
    var n = parseFloat(value);
    if (value === null || value === undefined || isNaN(n)) return "ows-unknown";
    if (n > 0) return "ows-profit";
    if (n < 0) return "ows-loss";
    return "";
  }

  function kv(label, value, cls) {
    return "<div><b>" + esc(label) + "</b><span class=\"" +
      (cls || "") + "\">" + esc(value) + "</span></div>";
  }

  function setText(id, value, cls) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = value === null || value === undefined
      ? "UNKNOWN" : String(value);
    if (cls !== undefined) el.className = cls;
  }

  // ── Üst çubuk + portföy + performans + broker ───────────────────

  function stateCls(v) {
    v = String(v || "UNKNOWN").toUpperCase();
    if (v === "OK" || v === "READY" || v === "RUNNING" ||
        v === "SYNCED" || v === "AUTHENTICATED" ||
        v === "INACTIVE") return "oc-state good";
    if (v === "UNKNOWN" || v === "STALE") return "oc-state unknown";
    return "oc-state bad";
  }

  function renderTopbar(status, broker) {
    if (status) {
      setText("ows-tb-system", status.risk_engine_state,
              stateCls(status.risk_engine_state));
      setText("ows-tb-bot", status.automation_state,
              stateCls(status.automation_state === "RUNNING"
                       ? "OK" : status.automation_state));
      setText("ows-tb-kill", status.kill_switch_state,
              status.kill_switch_state === "ACTIVE"
              ? "oc-state bad" : stateCls(status.kill_switch_state));
    }
    if (broker) {
      setText("ows-tb-broker", broker.api_status,
              stateCls(broker.api_status));
      setText("ows-tb-latency", broker.latency_ms === null ||
              broker.latency_ms === undefined
              ? "UNKNOWN" : broker.latency_ms + " ms",
              broker.latency_ms === null ? "ows-unknown" : "");
    }
  }

  function renderPortfolio(p) {
    var el = document.getElementById("ows-portfolio-bar");
    if (!el) return;
    if (!p) { el.innerHTML = "<div class=\"oc-empty\">Veri yok (UNKNOWN)</div>"; return; }
    el.innerHTML =
      kv("Portföy Değeri", p.portfolio_value) +
      kv("Nakit", p.cash) +
      kv("Özkaynak", p.equity) +
      kv("Günlük PnL", p.daily_pnl, pnlClass(p.daily_pnl)) +
      kv("Son 7 Gün PnL", p.weekly_pnl, pnlClass(p.weekly_pnl)) +
      kv("Son 30 Gün PnL", p.monthly_pnl, pnlClass(p.monthly_pnl)) +
      kv("Açık Risk", p.open_risk) +
      kv("Maruziyet", p.exposure) +
      kv("Düşüş %", p.drawdown_pct, pnlClass(p.drawdown_pct === null ? null : -parseFloat(p.drawdown_pct))) +
      kv("En Büyük Kazanan", p.largest_winner, pnlClass(p.largest_winner)) +
      kv("En Büyük Kaybeden", p.largest_loser, pnlClass(p.largest_loser)) +
      kv("Açık Pozisyon", p.open_position_count);
  }

  function holdText(seconds) {
    if (seconds === null || seconds === undefined) return "UNKNOWN";
    var h = Math.floor(seconds / 3600);
    return h + "s " + Math.floor((seconds % 3600) / 60) + "d";
  }

  function renderPerformance(p) {
    var el = document.getElementById("ows-performance");
    if (!el) return;
    if (!p) { el.innerHTML = "<div class=\"oc-empty\">Veri yok (UNKNOWN)</div>"; return; }
    el.innerHTML =
      kv("İşlem Sayısı", p.trade_count) +
      kv("Kazanma Oranı %", p.win_rate_pct, pnlClass(p.win_rate_pct)) +
      kv("Kaybetme Oranı %", p.loss_rate_pct) +
      kv("Ort. Kazanç", p.average_win, pnlClass(p.average_win)) +
      kv("Ort. Kayıp", p.average_loss, pnlClass(p.average_loss)) +
      kv("Kâr Faktörü", p.profit_factor) +
      kv("Sharpe", p.sharpe) +
      kv("Maks. Düşüş %", p.max_drawdown_pct) +
      kv("Ort. Tutma", holdText(p.average_hold_seconds)) +
      kv("Günlük Kâr", p.daily_profit, pnlClass(p.daily_profit)) +
      kv("Son 7 Gün Kâr", p.weekly_profit, pnlClass(p.weekly_profit)) +
      kv("Son 30 Gün Kâr", p.monthly_profit, pnlClass(p.monthly_profit)) +
      kv("Düşen Kayıt", p.dropped_records,
         p.dropped_records > 0 ? "ows-pending" : "");
  }

  function renderBroker(b) {
    var el = document.getElementById("ows-broker");
    if (!el) return;
    if (!b) { el.innerHTML = "<div class=\"oc-empty\">Veri yok (UNKNOWN)</div>"; return; }
    el.innerHTML =
      kv("Kalp Atışı", b.heartbeat_state, stateCls(b.heartbeat_state)) +
      kv("Gecikme (ms)", b.latency_ms) +
      kv("API Durumu", b.api_status, stateCls(b.api_status)) +
      kv("Hız Limiti", b.rate_limit_state, stateCls(b.rate_limit_state)) +
      kv("Yeniden Bağlanma", b.reconnect_count) +
      kv("Senkronizasyon", b.synchronization_state, stateCls(b.synchronization_state)) +
      kv("Kimlik Doğrulama", b.authentication_state, stateCls(b.authentication_state)) +
      kv("İzin", b.permission_state) +
      kv("Veri Yaşı (sn)", b.data_age_seconds);
  }

  // ── Stratejiler ─────────────────────────────────────────────────

  function renderStrategies(rows) {
    var el = document.getElementById("ows-strategies");
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = "<div class=\"oc-empty\">Strateji verisi yok (UNKNOWN)</div>";
      return;
    }
    el.innerHTML = rows.map(function (s) {
      return "<div class=\"ows-strategy\" data-symbol=\"" + esc(s.symbol) + "\">" +
        "<div class=\"row\"><span class=\"sym\">" + esc(s.symbol) + "</span>" +
        "<span>" + esc(s.strategy) + "</span>" +
        "<span class=\"" + stateCls(s.state === "ENABLED" ? "OK" : s.state) +
        "\">" + esc(s.state) + "</span></div>" +
        "<div class=\"row\"><span>Güven: " + esc(s.confidence_pct) + "</span>" +
        "<span>Bugün PnL: <span class=\"" + pnlClass(s.pnl_today) + "\">" +
        esc(s.pnl_today) + "</span></span>" +
        "<span>Poz: " + esc(s.open_position_count) + "</span>" +
        "<span>Giriş: " + (s.entry_eligible ? "UYGUN" : "KAPALI") + "</span></div>" +
        "<div class=\"actions\">" +
        "<button class=\"oc-btn\" data-strategy-cmd=\"pause\" data-symbol=\"" +
        esc(s.symbol) + "\">Duraklat</button>" +
        "<button class=\"oc-btn\" data-strategy-cmd=\"resume\" data-symbol=\"" +
        esc(s.symbol) + "\">Sürdür</button>" +
        "<button class=\"oc-btn danger\" data-strategy-cmd=\"stop\" data-symbol=\"" +
        esc(s.symbol) + "\">Devre Dışı</button>" +
        "<button class=\"oc-btn\" data-strategy-cmd=\"enable\" data-symbol=\"" +
        esc(s.symbol) + "\">Yeniden Etkinleştir</button>" +
        "<button class=\"oc-btn primary\" data-strategy-detail=\"" +
        esc(s.symbol) + "\">Ayrıntı</button>" +
        "</div></div>";
    }).join("");
  }

  // ── Günlük ──────────────────────────────────────────────────────

  function kindCls(kind) {
    if (kind === "FILLED" || kind === "RISK_APPROVED" ||
        kind === "AUTHORIZED") return "ows-profit";
    if (kind === "REJECTED" || kind === "RISK_REJECTED" ||
        kind === "CANCELLED") return "ows-loss";
    if (kind === "SUBMITTED") return "ows-pending";
    return "ows-info";
  }

  function renderJournal(rows) {
    var tbody = document.querySelector("#ows-journal-table tbody");
    if (!tbody) return;
    if (!rows || !rows.length) {
      tbody.innerHTML = "<tr><td colspan=\"6\" class=\"oc-empty\">" +
        "Günlük olayı yok (UNKNOWN)</td></tr>";
      return;
    }
    tbody.innerHTML = rows.map(function (e) {
      return "<tr tabindex=\"0\" data-correlation=\"" + esc(e.correlation_id) +
        "\"><td>" + esc(e.event_time) + "</td><td class=\"" +
        kindCls(e.kind) + "\">" + esc(e.kind) + "</td><td>" +
        esc(e.symbol) + "</td><td>" + esc(e.detail) + "</td><td>" +
        esc(e.status) + "</td><td>" + esc(e.correlation_id) +
        "</td></tr>";
    }).join("");
  }

  // ── Tablo araçları: sıralama, filtre, genişletme ───────────────

  var sortState = {};   // tableId -> {col, dir}
  var expanded = {};    // rowKey -> true

  function sortTable(table, col) {
    var id = table.id;
    var cur = sortState[id] || {};
    var dir = cur.col === col && cur.dir === 1 ? -1 : 1;
    sortState[id] = { col: col, dir: dir };
    applySort(table);
  }

  function applySort(table) {
    var st = sortState[table.id];
    if (!st) return;
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.filter.call(tbody.rows, function (r) {
      return !r.classList.contains("ows-detail-row");
    });
    rows.sort(function (a, b) {
      var av = (a.cells[st.col] || {}).textContent || "";
      var bv = (b.cells[st.col] || {}).textContent || "";
      var an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return (an - bn) * st.dir;
      return av.localeCompare(bv) * st.dir;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  }

  function applyFilter(input) {
    var table = document.getElementById(input.dataset.filterTable);
    if (!table || !table.tBodies[0]) return;
    var term = input.value.trim().toLowerCase();
    Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
      if (r.classList.contains("ows-detail-row")) return;
      r.style.display = !term ||
        r.textContent.toLowerCase().indexOf(term) !== -1 ? "" : "none";
    });
  }

  var reapplying = false;

  function reapplyAll() {
    if (reapplying) return;
    reapplying = true;
    try { doReapply(); } finally {
      // Gözlemci tetiklemeleri bittikten sonra bayrağı bırak.
      setTimeout(function () { reapplying = false; }, 0);
    }
  }

  function doReapply() {
    document.querySelectorAll("table[data-sortable]").forEach(applySort);
    document.querySelectorAll("input[data-filter-table]").forEach(applyFilter);
    // Genişletilmiş emir satırlarını yeniden uygula.
    document.querySelectorAll("#oc-orders tr[data-expandable]")
      .forEach(function (row) {
        if (expanded[row.dataset.rowKey]) expandRow(row, true);
      });
  }

  // Yaşam döngüsü zinciri önbelleği: rowKey -> olay listesi.
  // Yenilemede genişletilmiş satır önce önbellekten çizilir,
  // arka planda taze veri çekilir (seçim sıfırlanmaz).
  var lifecycleCache = {};
  var lifecycleInflight = {};

  function lifecycleHtml(events) {
    if (events === undefined) {
      return "Yaşam döngüsü zinciri yükleniyor…";
    }
    if (events === null) {
      return "Yaşam döngüsü verisi alınamadı (UNKNOWN)";
    }
    if (!events.length) {
      return "Yaşam döngüsü olayı yok (UNKNOWN)";
    }
    return "<div class=\"ows-lifecycle\">" +
      events.map(function (e) {
        return "<div class=\"ows-lc-event\">" +
          "<span>" + esc(e.event_time) + "</span> · " +
          "<b class=\"" + kindCls(e.state) + "\">" +
          esc(e.event_type) + "</b> · " +
          "<span>" + esc(e.state) + "</span> · " +
          "<span>" + esc(e.detail) + "</span> · " +
          "<span>Kaynak: " + esc(e.source) + "</span> · " +
          "<span>Korelasyon: " + esc(e.correlation_id) +
          "</span></div>";
      }).join("") + "</div>";
  }

  function renderDetailRow(row, key) {
    var next = row.nextElementSibling;
    if (!next || !next.classList.contains("ows-detail-row")) return;
    next.innerHTML = "<td colspan=\"" + row.cells.length + "\">" +
      lifecycleHtml(lifecycleCache[key]) + "</td>";
  }

  function fetchLifecycle(row, key) {
    if (lifecycleInflight[key]) return;
    lifecycleInflight[key] = true;
    req("/workspace/orders/" + encodeURIComponent(key) +
        "/lifecycle")
      .then(function (r) {
        lifecycleInflight[key] = false;
        lifecycleCache[key] = (r.body && r.body.ok &&
          r.body.data && r.body.data.lifecycle) || null;
        if (expanded[key]) renderDetailRow(row, key);
      }, function () {
        lifecycleInflight[key] = false;
        lifecycleCache[key] = null;
        if (expanded[key]) renderDetailRow(row, key);
      });
  }

  function expandRow(row, force) {
    var key = row.dataset.rowKey;
    var next = row.nextElementSibling;
    var isOpen = next && next.classList.contains("ows-detail-row");
    if (isOpen && !force) {
      next.remove();
      delete expanded[key];
      return;
    }
    if (isOpen) return;
    expanded[key] = true;
    var detail = document.createElement("tr");
    detail.className = "ows-detail-row";
    row.parentNode.insertBefore(detail, row.nextSibling);
    renderDetailRow(row, key);
    // Gerçek zincir sunucudan salt-okunur çekilir (Task 29);
    // her açılış/yenilemede tazelenir, sahte zaman çizelgesi yok.
    fetchLifecycle(row, key);
  }

  // ── Olay bağlama ────────────────────────────────────────────────

  document.addEventListener("click", function (ev) {
    var th = ev.target.closest("table[data-sortable] thead th");
    if (th) {
      sortTable(th.closest("table"),
                Array.prototype.indexOf.call(th.parentNode.children, th));
      return;
    }
    var row = ev.target.closest("tr[data-expandable]");
    if (row && !ev.target.closest("button")) { expandRow(row); return; }
    var btn = ev.target.closest("button[data-strategy-cmd]");
    if (btn && !btn.disabled) {
      btn.disabled = true;
      req("/symbols/" + encodeURIComponent(btn.dataset.symbol) + "/" +
          btn.dataset.strategyCmd, "POST",
          { idempotency_key: "ows-" + btn.dataset.strategyCmd + "-" +
            btn.dataset.symbol + "-" + Date.now() })
        .then(function () { btn.disabled = false; refresh(); },
              function () { btn.disabled = false; });
      return;
    }
    var det = ev.target.closest("button[data-strategy-detail]");
    if (det) {
      var search = document.getElementById("ows-search-positions");
      if (search) { search.value = det.dataset.strategyDetail;
                    applyFilter(search); }
      var pos = document.getElementById("oc-positions");
      if (pos) pos.scrollIntoView({ behavior: "smooth" });
      return;
    }
    var jr = ev.target.closest("#ows-journal-table tr[data-correlation]");
    if (jr && navigator.clipboard) {
      navigator.clipboard.writeText(jr.dataset.correlation).catch(function () {});
    }
  });

  // Klavye erişilebilirliği: başlıkta ve genişletilebilir satırda Enter.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    var th = ev.target.closest ? ev.target.closest(
      "table[data-sortable] thead th") : null;
    if (th) {
      ev.preventDefault();
      sortTable(th.closest("table"),
                Array.prototype.indexOf.call(th.parentNode.children, th));
      return;
    }
    var row = ev.target.closest ? ev.target.closest(
      "tr[data-expandable]") : null;
    if (row) { ev.preventDefault(); expandRow(row); }
  });

  document.addEventListener("input", function (ev) {
    if (ev.target.matches && ev.target.matches("input[data-filter-table]")) {
      applyFilter(ev.target);
    }
  });

  // Başlıklar klavyeyle odaklanabilir olsun.
  document.querySelectorAll("table[data-sortable] thead th")
    .forEach(function (th) {
      th.setAttribute("tabindex", "0");
      th.setAttribute("role", "columnheader");
    });

  // ── Yoklama döngüsü ─────────────────────────────────────────────

  var inflight = false;

  function refresh() {
    if (inflight) return;
    inflight = true;
    Promise.all([
      req("/status"),
      req("/workspace/portfolio"),
      req("/workspace/performance"),
      req("/workspace/broker-health"),
      req("/workspace/strategies"),
      req("/workspace/journal")
    ]).then(function (r) {
      var status = r[0].body.ok ? r[0].body.data : null;
      var broker = r[3].body.ok ? r[3].body.data.broker_health : null;
      renderTopbar(status, broker);
      renderPortfolio(r[1].body.ok ? r[1].body.data.portfolio : null);
      renderPerformance(r[2].body.ok ? r[2].body.data.performance : null);
      renderBroker(broker);
      renderStrategies(r[4].body.ok ? r[4].body.data.strategies : null);
      renderJournal(r[5].body.ok ? r[5].body.data.journal : null);
      reapplyAll();
      inflight = false;
    }, function () { inflight = false; });
  }

  // Agent 01 istemcisi tablolarını yeniledikçe sıralama/filtre/
  // genişletme durumunu yeniden uygula (seçimler sıfırlanmaz).
  ["oc-positions", "oc-orders", "oc-signals"].forEach(function (id) {
    var tbody = document.querySelector("#" + id + " tbody");
    if (tbody) {
      new MutationObserver(function () { reapplyAll(); })
        .observe(tbody, { childList: true });
    }
  });

  window.OWS = { duration: duration, refresh: refresh,
                 _sortState: sortState, _expanded: expanded };

  refresh();
  setInterval(refresh, POLL_MS);
})();
