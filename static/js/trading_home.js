/* Mission 2300 Agent 04 — Trading Home istemcisi (görsel göç).
 *
 * Felsefe: kullanıcı trader DEĞİL, portföy sahibidir. Yapay zekâ
 * işlem yapar. Bu sayfa hiçbir teknik gösterge İÇERMEZ.
 *
 * Kurallar:
 * - Yalnız MEVCUT uçlar kullanılır; backend değişikliği yok.
 * - Yoklama ile güncellenir (bu depoda SSE/WebSocket yasak).
 * - Bilinmeyen değer UNKNOWN; sahte 0 üretilmez.
 * - Ham kayan nokta ASLA gösterilmez: tüm sayılar tr-TR biçiminde,
 *   merkezî biçimlendiriciden geçer.
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

  // ── Merkezî sayı/tarih biçimlendirme (tr-TR) ───────────────────
  // Ham float görüntülemek yasak: 9830.331906875032 → 9.830,33

  function fmtMoney(v, unit) {
    var n = parseFloat(v);
    if (v === null || v === undefined || v === "UNKNOWN" ||
        isNaN(n)) return "UNKNOWN";
    return n.toLocaleString("tr-TR", { minimumFractionDigits: 2,
      maximumFractionDigits: 2 }) + (unit ? " " + unit : "");
  }

  function fmtPrice(v) {
    var n = parseFloat(v);
    if (v === null || v === undefined || v === "UNKNOWN" ||
        isNaN(n)) return "UNKNOWN";
    return n.toLocaleString("tr-TR", { minimumFractionDigits: 2,
      maximumFractionDigits: 8 });
  }

  function fmtSigned(v, unit) {
    var n = parseFloat(v);
    if (v === null || v === undefined || v === "UNKNOWN" ||
        isNaN(n)) return "UNKNOWN";
    return (n > 0 ? "+" : "") + fmtMoney(v, unit);
  }

  function fmtTime(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return "UNKNOWN";
    return new Date(t).toLocaleTimeString("tr-TR");
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

  // ── Hesap şeridi — TEK kaynak: bağlı kişisel hesaplar ──────────
  // (Mission 2300 A03: /api/accounts/wallets; borsa sayfalarına
  // doğrudan bağımlılık yok, yeni borsa = UI değişikliği yok.
  //  A04: dikey cüzdan sütunu kaldırıldı; tek yatay şerit.)

  function stripBalance(a) {
    // İlk cüzdan satırı şeritte özet bakiye olarak gösterilir;
    // bilinmeyen değer UNKNOWN kalır, sıfıra çevrilmez.
    var w = (a.wallets || [])[0];
    if (!w) return "UNKNOWN";
    var unit = /TRY/i.test(w.name) ? "TRY" : "USDT";
    var out = fmtMoney(w.balance, unit);
    return out === "UNKNOWN" ? "UNKNOWN" : out;
  }

  function renderWallets(accounts) {
    var el = document.getElementById("th-wallets");
    if (!el) return;
    if (!accounts || !accounts.length) {
      el.innerHTML = "<span class=\"th-empty\">Bağlı hesap yok. " +
        "Hesapları Yönet bağlantısından hesap ekleyin.</span>";
      var dot0 = document.getElementById("th-wallet-conn");
      if (dot0) dot0.className = "th-dot";
      return;
    }
    var ordered = accounts.slice().sort(function (a, b) {
      return (b.status === "OK") - (a.status === "OK");
    });
    el.innerHTML = ordered.map(function (a) {
      var ok = a.status === "OK";
      return "<span class=\"th-acct\">" +
        "<span>" + esc(a.logo) + "</span>" +
        "<span><span class=\"nm\">" + esc(a.nickname) +
        (a.primary ? " ★" : "") + "</span><br>" +
        "<span class=\"bal " +
        (stripBalance(a) === "UNKNOWN" ? "th-unknown" : "") + "\">" +
        esc(stripBalance(a)) + "</span></span>" +
        "<span class=\"st\"><span class=\"th-dot" +
        (ok ? " ok" : "") + "\"></span>" +
        esc(ok ? "bağlı" : a.status) + "</span></span>";
    }).join("");
    var anyOk = accounts.some(function (a) {
      return a.status === "OK";
    });
    var dot = document.getElementById("th-wallet-conn");
    if (dot) {
      dot.className = "th-dot" + (anyOk ? " ok" : "");
      dot.setAttribute("aria-label", anyOk ?
        "hesap bağlantısı: bağlı" : "hesap bağlantısı: UNKNOWN");
    }
  }

  // ── Aktif işlemler: sayfanın en büyük alanı ────────────────────

  function renderTrades(positions) {
    var tbody = document.querySelector("#th-trades tbody");
    if (!tbody) return;
    setText("th-active-count",
            positions ? positions.length : null,
            positions && positions.length ? "th-profit" : "");
    if (!positions || !positions.length) {
      tbody.innerHTML = "<tr><td colspan=\"8\" class=\"th-empty\">" +
        "Şu an açık işlem yok. Yapay zekâ piyasaları izlemeye " +
        "devam ediyor.</td></tr>";
      return;
    }
    // Sahip diline durum eşlemesi (teknik iç durum sızdırılmaz).
    var STATUS_TR = { OPEN: "Yönetiliyor", SCALING: "Kademelendiriliyor",
      CLOSE_REQUESTED: "Kapatılıyor", WAITING_EXIT: "Çıkış Bekliyor",
      EMERGENCY_EXIT: "Acil Çıkış", CLOSED: "Tamamlandı" };
    tbody.innerHTML = positions.map(function (p) {
      var longSide = p.side === "LONG";
      return "<tr><td><b>" + esc(p.symbol) + "</b></td>" +
        "<td class=\"" + (longSide ? "th-long" : "th-short") + "\">" +
        esc(longSide ? "Yükseliş" :
            p.side === "SHORT" ? "Düşüş" : p.side) + "</td>" +
        "<td>" + esc(fmtPrice(p.entry_price)) + "</td>" +
        "<td>" + esc(fmtPrice(p.current_price)) + "</td>" +
        "<td class=\"" + pnlClass(p.unrealized_pnl) + "\">" +
        esc(fmtSigned(p.unrealized_pnl, "USDT")) + "</td><td>" +
        esc(duration(p.opened_at)) + "</td>" +
        "<td><span class=\"th-badge wait\">" +
        esc(STATUS_TR[p.position_status] || "Yönetiliyor") +
        "</span></td><td>" +
        "<button class=\"th-btn\" data-close=\"" + esc(p.position_id) +
        "\" data-symbol=\"" + esc(p.symbol) + "\">Kapat</button>" +
        "</td></tr>";
    }).join("");
  }

  // ── Sıradaki işlemler — mevcut anlık görüntüden türetilir ──────

  function queueRow(symbol, side, badgeCls, label) {
    var sideCell = side === "LONG" ? "<span class=\"th-long\">" +
        "Yükseliş</span>" : side === "SHORT" ?
        "<span class=\"th-short\">Düşüş</span>" : "—";
    return "<tr><td><b>" + esc(symbol) + "</b></td><td>" + sideCell +
      "</td><td><span class=\"th-badge " + badgeCls + "\">" +
      esc(label) + "</span></td></tr>";
  }

  function renderQueue(products, orders, positions, signals) {
    var el = document.getElementById("th-queue");
    if (!el) return;
    // Yön yalnız denetimli sinyal/emir verisinden gelir; yoksa "—".
    var sides = {};
    (signals || []).forEach(function (s) {
      if (s.side) sides[s.symbol] = s.side;
    });
    (orders || []).forEach(function (o) {
      if (o.side) sides[o.symbol] = o.side;
    });
    // Bir sembol sırada tek durumla görünür (çelişki yasak).
    var items = [];
    var taken = {};
    function push(symbol, cls, label) {
      if (taken[symbol]) return;
      taken[symbol] = true;
      items.push(queueRow(symbol, sides[symbol], cls, label));
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
      "<tr><td colspan=\"3\" class=\"th-empty\">Sırada bekleyen " +
      "işlem yok. Piyasalar izlenmeye devam ediyor.</td></tr>";
  }

  // ── Son hareketler — denetimli günlükten sade dil ──────────────

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
    return map[e.kind] || "güncellendi";
  }

  function plainResult(e) {
    var map = { OK: "Tamam", FILLED: "Gerçekleşti",
                SUBMITTED: "Sırada", REJECTED: "Yapılmadı",
                CANCELLED: "İptal", SKIPPED: "Atlandı" };
    return map[e.status] || "—";
  }

  function renderActivity(journal) {
    var el = document.getElementById("th-activity");
    if (!el) return;
    if (!journal || !journal.length) {
      el.innerHTML = "<tr><td colspan=\"4\" class=\"th-empty\">" +
        "Henüz hareket yok.</td></tr>";
      return;
    }
    // En yeni en üstte — uç sıralaması kayarsa bile garanti.
    var sorted = journal.slice().sort(function (a, b) {
      return String(b.event_time).localeCompare(String(a.event_time));
    });
    el.innerHTML = sorted.slice(0, 20).map(function (e) {
      var sym = e.kind === "OPERATOR_ACTION" ? "—" : esc(e.symbol);
      return "<tr><td>" + esc(fmtTime(e.event_time)) + "</td><td>" +
        sym + "</td><td style=\"text-align:left\">" + plainEvent(e) +
        "</td><td>" + esc(plainResult(e)) + "</td></tr>";
    }).join("");
  }

  // ── Üst çubuk + AI paneli ──────────────────────────────────────

  function renderTop(status, portfolio, products) {
    if (portfolio) {
      setText("th-portfolio-value",
              fmtMoney(portfolio.portfolio_value, "USDT"));
      setText("th-daily-pnl",
              fmtSigned(portfolio.daily_pnl, "USDT"),
              pnlClass(portfolio.daily_pnl));
    } else {
      // Portföy ucu düşerse bayat değer taze gibi gösterilmez.
      setText("th-portfolio-value", null);
      setText("th-daily-pnl", null);
    }
    // "Son Güncelleme" yalnız gerçekten veri geldiyse yenilenir.
    if (status || portfolio) {
      setText("th-last-update",
              new Date().toLocaleTimeString("tr-TR"));
    } else {
      setText("th-last-update", null);
    }
    var aiMode = document.getElementById("th-ai-mode");
    var aiSent = document.getElementById("th-ai-sentence");
    if (!status) {
      // Üst çubuk bayat değer göstermez; AI paneli de dürüst kalır.
      setText("th-auto-mode", null);
      setText("th-bot-status", "Çevrimdışı", "th-unknown");
      if (aiMode) { aiMode.textContent = "UNKNOWN";
                    aiMode.className = "mode th-unknown"; }
      if (aiSent) aiSent.textContent = "Durum alınamadı.";
      setText("th-ai-scanned", null);
      setText("th-ai-eligible", null);
      setText("th-ai-last", null);
      return;
    }
    var auto = status.automation_state;
    var autonomous = auto === "RUNNING";
    var modeLabel = autonomous ? "OTONOM" : "DANIŞMAN";
    setText("th-auto-mode", modeLabel,
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
    setText("th-bot-status", badge,
            cls === "err" ? "th-loss" :
            cls === "run" ? "th-profit" : "");
    if (aiMode) {
      aiMode.textContent = modeLabel;
      aiMode.className = "mode " +
        (autonomous ? "autonomous" : "advisor");
    }
    if (aiSent) {
      aiSent.textContent = cls === "err"
        ? "Sistem acil durduruldu; yeni işlem açılmaz."
        : "Piyasalar izleniyor.";
    }
    if (products) {
      setText("th-ai-scanned", products.length);
      setText("th-ai-eligible", products.filter(function (p) {
        return p.entry_eligible;
      }).length);
    } else {
      setText("th-ai-scanned", null);
      setText("th-ai-eligible", null);
    }
    setText("th-ai-last", new Date().toLocaleTimeString("tr-TR"));
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
      get("/api/accounts/wallets")
    ]).then(function (r) {
      function data(i, key) {
        var b = r[i].body;
        return b && b.ok && b.data ? b.data[key] : null;
      }
      renderTop(r[0].body && r[0].body.ok ? r[0].body.data : null,
                data(5, "portfolio"), data(3, "products"));
      renderTrades(data(1, "positions"));
      renderQueue(data(3, "products"), data(2, "orders"),
                  data(1, "positions"), data(4, "signals"));
      renderActivity(data(6, "journal"));
      renderWallets(data(7, "accounts"));
      inflight = false;
    }, function () { inflight = false; });
  }

  window.TH = { refresh: refresh, duration: duration,
                fmtMoney: fmtMoney, fmtPrice: fmtPrice };

  refresh();
  setInterval(refresh, POLL_MS);
})();
