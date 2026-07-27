/* Mission 2300 Agent 01 — Trading Home istemcisi.
 *
 * Felsefe: kullanıcı trader DEĞİL, portföy sahibidir. Yapay zekâ
 * işlem yapar. Bu sayfa hiçbir teknik gösterge İÇERMEZ.
 *
 * Kurallar:
 * - Yalnız MEVCUT uçlar kullanılır; backend değişikliği yok.
 * - Yoklama ile güncellenir (bu depoda SSE/WebSocket yasak).
 * - Bilinmeyen değer UNKNOWN; sahte 0 üretilmez.
 * - Kapat düğmesi mevcut kontrollü kapatma NİYETİ ucuna bağlıdır
 *   (neden + ONAYLIYORUM + idempotency anahtarı zorunlu).
 */
(function () {
  "use strict";

  var POLL_MS = 12000;

  function esc(v) {
    return String(v === null || v === undefined ? "UNKNOWN" : v)
      .replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
                 '"': "&quot;", "'": "&#39;" }[c];
      });
  }

  function get(path) {
    return fetch(path, { headers: { "X-CSRFToken": window.TH_CSRF } })
      .then(function (r) {
        return r.json().then(function (j) {
          return { http: r.status, body: j };
        });
      }).catch(function () { return { http: 0, body: null }; });
  }

  function setText(id, value, cls) {
    var el = document.getElementById(id);
    if (!el) return;
    var unknown = value === null || value === undefined ||
      value === "UNKNOWN";
    el.textContent = unknown ? "UNKNOWN" : String(value);
    el.className = unknown ? "th-unknown" : (cls || "");
  }

  function pnlClass(v) {
    var n = parseFloat(v);
    if (v === null || v === undefined || isNaN(n)) return "th-unknown";
    return n > 0 ? "th-profit" : n < 0 ? "th-loss" : "";
  }

  function duration(openedAt) {
    var t = Date.parse(openedAt);
    if (isNaN(t)) return "UNKNOWN";
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h >= 24) return Math.floor(h / 24) + " gün";
    if (h >= 1) return h + " sa " + m + " dk";
    return m + " dk";
  }

  // ── Cüzdanlar (sol) — mevcut hesap uçlarından ──────────────────

  function walletCard(name, fields, status, lastSync) {
    var rows = fields.map(function (f) {
      return "<div class=\"row\"><span>" + esc(f[0]) + "</span><span>" +
        esc(f[1]) + "</span></div>";
    }).join("");
    return "<div class=\"th-wallet\"><div class=\"name\">" + esc(name) +
      "</div>" + rows +
      "<div class=\"row\"><span>Durum</span><span class=\"" +
      (status === "OK" ? "th-profit" : "th-unknown") + "\">" +
      esc(status) + "</span></div>" +
      "<div class=\"row\"><span>Son Eşitleme</span><span>" +
      esc(lastSync) + "</span></div></div>";
  }

  function renderWallets(globalAcc, trAcc) {
    var el = document.getElementById("th-wallets");
    if (!el) return;
    var cards = "";
    if (globalAcc && globalAcc.ok) {
      cards += walletCard("Binance Global (Vadeli)", [
        ["Bakiye", globalAcc.wallet_balance_usdt ||
                   globalAcc.total_wallet_balance || null],
        ["Kullanılabilir", globalAcc.available_balance_usdt ||
                           globalAcc.available_balance || null]
      ], "OK", (globalAcc.meta || {}).retrieved_at);
    } else {
      cards += walletCard("Binance Global (Vadeli)",
        [["Bakiye", null], ["Kullanılabilir", null]],
        ((globalAcc || {}).meta || {}).freshness || "UNKNOWN",
        ((globalAcc || {}).meta || {}).retrieved_at);
    }
    if (trAcc && trAcc.ok) {
      cards += walletCard("Binance TR", [
        ["Bakiye (TRY)", trAcc.try_total || trAcc.try_free || null],
        ["Kullanılabilir (TRY)", trAcc.try_free || null]
      ], trAcc.auth_status === "OK" ? "OK" : esc(trAcc.auth_status),
        (trAcc.meta || {}).retrieved_at);
    } else {
      cards += walletCard("Binance TR",
        [["Bakiye (TRY)", null], ["Kullanılabilir (TRY)", null]],
        ((trAcc || {}).meta || {}).freshness || "UNKNOWN",
        ((trAcc || {}).meta || {}).retrieved_at);
    }
    el.innerHTML = cards;
    var anyOk = (globalAcc && globalAcc.ok) || (trAcc && trAcc.ok);
    setText("th-wallet-conn", anyOk ? "BAĞLI" : "UNKNOWN",
            anyOk ? "th-profit" : "th-unknown");
  }

  // ── Aktif işlemler (orta) ──────────────────────────────────────

  function renderTrades(positions) {
    var tbody = document.querySelector("#th-trades tbody");
    if (!tbody) return;
    setText("th-active-count",
            positions ? positions.length : null,
            positions && positions.length ? "th-profit" : "");
    if (!positions || !positions.length) {
      tbody.innerHTML = "<tr><td colspan=\"7\" class=\"th-empty\">" +
        "Şu an açık işlem yok. Yapay zekâ fırsat bulduğunda burada " +
        "görünecek.</td></tr>";
      return;
    }
    // Sahip diline durum eşlemesi (teknik iç durum sızdırılmaz).
    var STATUS_TR = { OPEN: "Yönetiliyor", SCALING: "Kademelendiriliyor",
      CLOSE_REQUESTED: "Kapatılıyor", WAITING_EXIT: "Çıkış Bekliyor",
      EMERGENCY_EXIT: "Acil Çıkış", CLOSED: "Tamamlandı" };
    tbody.innerHTML = positions.map(function (p) {
      return "<tr><td><b>" + esc(p.symbol) + "</b></td><td>" +
        esc(p.side === "LONG" ? "Yükseliş" :
            p.side === "SHORT" ? "Düşüş" : p.side) + "</td>" +
        "<td>" + esc(p.entry_price) + "</td>" +
        "<td class=\"" + pnlClass(p.unrealized_pnl) + "\">" +
        esc(p.unrealized_pnl) + "</td><td>" +
        esc(duration(p.opened_at)) + "</td><td>" +
        esc(STATUS_TR[p.position_status] || "Yönetiliyor") +
        "</td><td>" +
        "<button class=\"th-btn\" data-close=\"" + esc(p.position_id) +
        "\" data-symbol=\"" + esc(p.symbol) + "\">Kapat</button>" +
        "</td></tr>";
    }).join("");
  }

  // ── Sıra (sağ) — mevcut anlık görüntüden türetilir ─────────────

  function queueItem(symbol, badgeCls, label) {
    return "<div class=\"th-queue-item\"><b>" + esc(symbol) +
      "</b><span class=\"th-badge " + badgeCls + "\">" + esc(label) +
      "</span></div>";
  }

  function renderQueue(products, orders, positions, signals) {
    var el = document.getElementById("th-queue");
    if (!el) return;
    // Bir sembol sırada tek durumla görünür (çelişki yasak).
    var items = [];
    var taken = {};
    function push(symbol, cls, label) {
      if (taken[symbol]) return;
      taken[symbol] = true;
      items.push(queueItem(symbol, cls, label));
    }
    var openSymbols = {};
    (positions || []).forEach(function (p) {
      openSymbols[p.symbol] = true;
      if (p.position_status === "CLOSE_REQUESTED") {
        push(p.symbol, "close", "Kapanıyor");
      }
    });
    (orders || []).forEach(function (o) {
      if (o.status && ["FILLED", "CANCELLED", "REJECTED",
                       "EXPIRED"].indexOf(o.status) === -1) {
        push(o.symbol, "exec", "Yürütülüyor");
      }
    });
    (signals || []).forEach(function (s) {
      if (s.execution_result === "SUBMITTED") {
        push(s.symbol, "prep", "Hazırlanıyor");
      }
    });
    (products || []).forEach(function (pr) {
      if (openSymbols[pr.symbol]) return;
      if (pr.entry_eligible) {
        push(pr.symbol, "wait", "Hazır");
      } else if (pr.automation_state === "ENABLED") {
        push(pr.symbol, "gray", "Bekliyor");
      }
    });
    setText("th-queued-count", items.length);
    el.innerHTML = items.length ? items.join("") :
      "<div class=\"th-empty\">Sırada bekleyen varlık yok.</div>";
  }

  // ── Son hareketler (alt) — denetimli günlükten sade dil ───────

  function plainEvent(e) {
    // Yalın zaman akışı: teknik ayrıntı, gerekçe veya günlük
    // metni sızdırılmaz — yalnız ne olduğu söylenir.
    var map = { FILLED: "pozisyonu açıldı",
                SUBMITTED: "sıraya alındı",
                REJECTED: "işlemi yapılmadı",
                CANCELLED: "emri iptal edildi",
                SIGNAL_GENERATED: "değerlendirildi",
                POSITION_CLOSED: "pozisyonu kapatıldı" };
    if (e.kind === "OPERATOR_ACTION") {
      return "Portföy ayarı güncellendi";
    }
    return esc(e.symbol) + " " + (map[e.kind] || "güncellendi");
  }

  function renderActivity(journal) {
    var el = document.getElementById("th-activity");
    if (!el) return;
    if (!journal || !journal.length) {
      el.innerHTML = "<li class=\"th-empty\">Henüz hareket yok.</li>";
      return;
    }
    // En yeni en üstte — uç sıralaması kayarsa bile garanti.
    var sorted = journal.slice().sort(function (a, b) {
      return String(b.event_time).localeCompare(String(a.event_time));
    });
    el.innerHTML = sorted.slice(0, 20).map(function (e) {
      return "<li><time>" + esc(e.event_time) + "</time>" +
        plainEvent(e) + "</li>";
    }).join("");
  }

  // ── Üst çubuk ──────────────────────────────────────────────────

  function renderTop(status, portfolio) {
    if (portfolio) {
      setText("th-portfolio-value", portfolio.portfolio_value);
      setText("th-daily-pnl", portfolio.daily_pnl,
              pnlClass(portfolio.daily_pnl));
    }
    var modeEl = document.getElementById("th-mode-big");
    var badgeEl = document.getElementById("th-status-badge");
    if (!status) {
      if (modeEl) { modeEl.textContent = "UNKNOWN";
                    modeEl.className = "mode"; }
      if (badgeEl) { badgeEl.textContent = "Çevrimdışı";
                     badgeEl.className = "th-status-badge off"; }
      // Üst çubuk da bayatlamış değer göstermesin.
      setText("th-auto-mode", null);
      setText("th-bot-status", "Çevrimdışı", "th-unknown");
      return;
    }
    var auto = status.automation_state;
    var autonomous = auto === "RUNNING";
    // Tek büyük mod kartı: OTONOM ya da DANIŞMAN.
    if (modeEl) {
      modeEl.textContent = autonomous ? "OTONOM" : "DANIŞMAN";
      modeEl.className = "mode " +
        (autonomous ? "autonomous" : "advisor");
    }
    setText("th-auto-mode", autonomous ? "OTONOM" : "DANIŞMAN",
            autonomous ? "th-profit" : "");
    // Basit durum rozetleri: Çalışıyor / Duraklatıldı / Hata.
    var badge, cls;
    if (status.kill_switch_state === "ACTIVE") {
      badge = "Hata — Acil Durduruldu"; cls = "err";
    } else if (autonomous) {
      badge = "Çalışıyor"; cls = "run";
    } else {
      badge = "Duraklatıldı"; cls = "pause";
    }
    if (badgeEl) { badgeEl.textContent = badge;
                   badgeEl.className = "th-status-badge " + cls; }
    setText("th-bot-status", badge,
            cls === "err" ? "th-loss" :
            cls === "run" ? "th-profit" : "");
  }

  // ── Kapatma niyeti (mevcut kontrollü uç) ───────────────────────

  var dialogBusy = false;

  function confirmClose(positionId, symbol) {
    var dlg = document.getElementById("th-dialog");
    if (!dlg || dialogBusy) return;
    document.getElementById("th-dialog-body").innerHTML =
      "<b>" + esc(symbol) + "</b> işlemi için kontrollü kapatma " +
      "isteği oluşturulacak. Bu istek güvenlik denetimlerinden geçer.";
    document.getElementById("th-dialog-reason").value = "";
    document.getElementById("th-dialog-phrase").value = "";
    dlg.returnValue = "cancel";
    dlg.showModal();
    dlg.onclose = function () {
      if (dlg.returnValue !== "ok") return;
      var reason = document.getElementById("th-dialog-reason").value;
      var phrase = document.getElementById("th-dialog-phrase").value;
      dialogBusy = true;
      fetch("/api/operation-control/positions/" +
            encodeURIComponent(positionId) + "/close", {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   "X-CSRFToken": window.TH_CSRF },
        body: JSON.stringify({
          reason: reason, confirm_phrase: phrase,
          idempotency_key: "th-close-" + positionId + "-" + Date.now()
        })
      }).then(function () { dialogBusy = false; refresh(); },
              function () { dialogBusy = false; });
    };
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest(
      "button[data-close]") : null;
    if (b) confirmClose(b.dataset.close, b.dataset.symbol);
  });

  // ── Yoklama ────────────────────────────────────────────────────

  var inflight = false;

  function refresh() {
    if (inflight) return;
    inflight = true;
    Promise.all([
      get("/api/operation-control/status"),
      get("/api/operation-control/positions"),
      get("/api/operation-control/orders"),
      get("/api/operation-control/products"),
      get("/api/operation-control/signals"),
      get("/api/operation-control/workspace/portfolio"),
      get("/api/operation-control/workspace/journal"),
      get("/api/v1/global/account"),
      get("/api/v1/tr/account")
    ]).then(function (r) {
      function data(i, key) {
        var b = r[i].body;
        return b && b.ok && b.data ? b.data[key] : null;
      }
      renderTop(r[0].body && r[0].body.ok ? r[0].body.data : null,
                data(5, "portfolio"));
      renderTrades(data(1, "positions"));
      renderQueue(data(3, "products"), data(2, "orders"),
                  data(1, "positions"), data(4, "signals"));
      renderActivity(data(6, "journal"));
      renderWallets(r[7].body, r[8].body);
      inflight = false;
    }, function () { inflight = false; });
  }

  window.TH = { refresh: refresh, duration: duration };

  refresh();
  setInterval(refresh, POLL_MS);
})();
