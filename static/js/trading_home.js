/* Trading Home istemcisi — kokpit yerleşimi.
 *
 * Felsefe: kullanıcı trader DEĞİL, portföy sahibidir. Yapay zekâ
 * işlem yapar. Bu sayfa hiçbir teknik gösterge İÇERMEZ.
 *
 * Kurallar:
 * - Yalnız MEVCUT uçlar kullanılır; backend değişikliği yok.
 * - Yoklama ile güncellenir (bu depoda SSE/WebSocket yasak).
 * - Bilinmeyen değer UNKNOWN; sahte 0 üretilmez; kaynak yoksa
 *   "Veri yok" gösterilir.
 * - Ham kayan nokta ASLA gösterilmez: tüm sayılar tr-TR biçiminde,
 *   merkezî biçimlendiriciden geçer.
 * - Kapat düğmesi mevcut kontrollü kapatma NİYETİ ucuna bağlıdır
 *   (neden + ONAYLIYORUM + idempotency anahtarı zorunlu).
 * - Tüm widget'lar aynı yoklama turundaki snapshot'lardan beslenir;
 *   sayaçlar liste satırlarından türetilir (çelişki imkânsız).
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

  // Sütun boşsa "—": UNKNOWN enflasyonu yerine sade tire (veri
  // kaynağında alan hiç yoksa). Değer VAR ama alınamadıysa UNKNOWN.
  function dash(v) {
    return (v === null || v === undefined || v === "" ||
            v === "UNKNOWN") ? "—" : esc(v);
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

  // "Veri yok": bu alan için denetimli kaynak bu turda yanıt vermedi
  // ya da hiç yok — uydurma değer basılmaz.
  function setDatum(id, value, cls) {
    var el = document.getElementById(id);
    if (!el) return;
    var missing = value === null || value === undefined ||
      value === "" || value === "UNKNOWN";
    el.textContent = missing ? "Veri yok" : String(value);
    el.className = missing ? "th-unknown" : (cls || "");
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

  function renderWallets(accounts, allAccounts) {
    var el = document.getElementById("th-wallets");
    if (!el) return;
    // Bağlı olmayan hesaplar (referans tasarım: 'Bybit — bağlı değil')
    // şeridin SONUNA eklenir; bakiye uydurulmaz, sade etiket.
    var connectedIds = {};
    (accounts || []).forEach(function (a) {
      connectedIds[a.account_id] = true;
    });
    var offline = (allAccounts || []).filter(function (a) {
      return !a.connected && !connectedIds[a.account_id];
    });
    if ((!accounts || !accounts.length) && !offline.length) {
      el.innerHTML = "<span class=\"th-empty\">Bağlı hesap yok. " +
        "Hesapları Yönet bağlantısından hesap ekleyin.</span>";
      var dot0 = document.getElementById("th-wallet-conn");
      if (dot0) dot0.className = "th-dot";
      return;
    }
    var ordered = (accounts || []).slice().sort(function (a, b) {
      return (b.status === "OK") - (a.status === "OK");
    });
    // Kanonik connection_state → Türkçe etiket (my_accounts.js
    // STATE_BADGE / overview.html STATE_TXT ile aynı metinler).
    // Bilinmeyen/eksik alan asla "bağlı" göstermez.
    var STATE_TR = {
      HEALTHY: "bağlı",
      STALE: "Bağlı (eski veri)",
      NOT_CONFIGURED: "Anahtar Yapılandırılmamış",
      AUTH_FAILED: "Kimlik Hatası",
      CONNECTION_FAILED: "Bağlantı Hatası",
      DISABLED: "Bağlı Değil"
    };
    function stateLabel(a) {
      var s = STATE_TR[a.connection_state];
      if (s) return s;
      // connection_state yoksa/bilinmiyorsa yalnızca eşlenmiş eski
      // status kodlarına düş; asla "bağlı" varsayma — teknik kod ya
      // da yanlış pozitif basılmaz.
      return STATE_TR[a.status] || "Durum Bilinmiyor";
    }
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
        esc(stateLabel(a)) + "</span></span>";
    }).join("") + offline.map(function (a) {
      return "<span class=\"th-acct th-offline\">" +
        "<span>" + esc(a.logo) + "</span>" +
        "<span><span class=\"nm\">" + esc(a.nickname) + "</span><br>" +
        "<span class=\"bal th-unknown\">—</span></span>" +
        "<span class=\"st\"><span class=\"th-dot\"></span>" +
        "bağlı değil</span></span>";
    }).join("");
    var anyOk = (accounts || []).some(function (a) {
      return a.status === "OK";
    });
    var dot = document.getElementById("th-wallet-conn");
    if (dot) {
      dot.className = "th-dot" + (anyOk ? " ok" : "");
      dot.setAttribute("aria-label", anyOk ?
        "hesap bağlantısı: bağlı" : "hesap bağlantısı: UNKNOWN");
    }
    // Sistem & Otomasyon kartı: API durumu aynı hesap snapshot'ından.
    setDatum("th-sys-api", (accounts && accounts.length)
      ? (anyOk ? "Bağlı" : "Bağlantı sorunu") : null,
      anyOk ? "th-profit" : "th-loss");
  }

  // ── Aktif işlemler: sayfanın en büyük alanı ────────────────────
  // Klasik motor pozisyonları + iki model (CORE/OPPORTUNITY) paper
  // pozisyonları TEK tabloda; her satır kendi kanonik snapshot'ından.

  var MODEL_TR = {
    ALPHA_CORE_SCALP: "ALPHA CORE SCALP",
    ALPHA_OPPORTUNITY_BURST: "ALPHA OPPORTUNITY BURST"
  };

  function listTypeOf(model) {
    if (model === "ALPHA_CORE_SCALP") return "CORE";
    if (model === "ALPHA_OPPORTUNITY_BURST") return "OPPORTUNITY";
    return "—";
  }

  function renderTrades(positions, dmPositions) {
    var tbody = document.querySelector("#th-trades tbody");
    if (!tbody) return;
    var legacy = positions || [];
    var dual = dmPositions || [];
    var total = legacy.length + dual.length;
    setText("th-active-count", positions || dmPositions ? total : null,
            total ? "th-profit" : "");
    setText("th-sc-open", positions || dmPositions ? total : null,
            total ? "th-profit" : "");
    if (!total) {
      tbody.innerHTML = "<tr><td colspan=\"15\" class=\"th-empty\">" +
        "Şu an açık işlem yok. Yapay zekâ piyasaları izlemeye " +
        "devam ediyor.</td></tr>";
      setDatum("th-sc-notional",
               positions || dmPositions ? fmtMoney(0, "USDT") : null);
      return;
    }
    // Sahip diline durum eşlemesi (teknik iç durum sızdırılmaz).
    var STATUS_TR = { OPEN: "Yönetiliyor", SCALING: "Kademelendiriliyor",
      CLOSE_REQUESTED: "Kapatılıyor", WAITING_EXIT: "Çıkış Bekliyor",
      EMERGENCY_EXIT: "Acil Çıkış", CLOSED: "Tamamlandı" };
    var notional = 0, notionalPartial = false;
    var rows = legacy.map(function (p) {
      var longSide = p.side === "LONG";
      var nv = parseFloat(p.notional_usdt);
      if (!isNaN(nv)) { notional += nv; } else { notionalPartial = true; }
      return "<tr><td><b>" + esc(p.symbol) + "</b></td>" +
        "<td class=\"" + (longSide ? "th-long" : "th-short") + "\">" +
        esc(longSide ? "Yükseliş" :
            p.side === "SHORT" ? "Düşüş" : p.side) + "</td>" +
        "<td>" + dash(p.quantity) + "</td>" +
        "<td>" + dash(p.notional_usdt != null ?
                      fmtMoney(p.notional_usdt) : null) + "</td>" +
        "<td>" + esc(fmtPrice(p.entry_price)) + "</td>" +
        "<td>" + esc(fmtPrice(p.current_price)) + "</td>" +
        "<td class=\"" + pnlClass(p.unrealized_pnl) + "\">" +
        esc(fmtSigned(p.unrealized_pnl, "USDT")) + "</td>" +
        "<td>—</td><td>" + esc(duration(p.opened_at)) + "</td>" +
        "<td>—</td><td>—</td><td>—</td><td>—</td>" +
        "<td><span class=\"th-badge wait\">" +
        esc(STATUS_TR[p.position_status] || "Yönetiliyor") +
        "</span></td><td>" +
        "<button class=\"th-btn\" data-close=\"" + esc(p.position_id) +
        "\" data-symbol=\"" + esc(p.symbol) + "\">Kapat</button>" +
        "</td></tr>";
    }).concat(dual.map(function (p) {
      var nv = parseFloat(p.notional_usdt);
      if (!isNaN(nv)) { notional += nv; } else { notionalPartial = true; }
      return "<tr><td><b>" + esc(p.symbol) + "</b></td>" +
        "<td class=\"th-long\">Yükseliş</td>" +
        "<td>" + dash(p.quantity != null ?
                      fmtPrice(p.quantity) : null) + "</td>" +
        "<td>" + dash(p.notional_usdt != null ?
                      fmtMoney(p.notional_usdt) : null) + "</td>" +
        "<td>" + esc(fmtPrice(p.entry)) + "</td>" +
        "<td>" + esc(fmtPrice(p.current_price)) + "</td>" +
        "<td class=\"" + pnlClass(p.unrealized_net_pnl) + "\">" +
        esc(fmtSigned(p.unrealized_net_pnl, "USDT")) + "</td>" +
        "<td class=\"" + pnlClass(p.unrealized_pnl_pct) + "\">" +
        (p.unrealized_pnl_pct != null ?
          esc(fmtSigned(p.unrealized_pnl_pct)) + "%" : "UNKNOWN") +
        "</td>" +
        "<td>" + esc(duration(p.opened_at)) + "</td>" +
        "<td>" + esc(fmtPrice(p.tp)) + "</td>" +
        "<td>" + esc(fmtPrice(p.sl)) + "</td>" +
        "<td>" + dash(MODEL_TR[p.model]) + "</td>" +
        "<td>" + esc(listTypeOf(p.model)) + "</td>" +
        "<td><span class=\"th-badge exec\">Yönetiliyor</span></td>" +
        "<td><button class=\"th-btn th-dm-close\" data-symbol=\"" +
        esc(p.symbol) + "\">Kapat</button></td></tr>";
    }));
    tbody.innerHTML = rows.join("");
    bindDmClose(tbody, dual);
    // Toplam pozisyon değeri: bilinen tutarların toplamı; herhangi
    // bir satır fiyatlanamadıysa "en az X (kısmi)" dürüstlüğü.
    var txt = fmtMoney(notional, "USDT");
    setDatum("th-sc-notional",
             notionalPartial ? "en az " + txt + " (kısmi)" : txt);
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
    var realPending = 0; // gerçek emir/niyet — yalnız bunlar "sıradaki işlem"
    (positions || []).forEach(function (p) {
      openSymbols[p.symbol] = true;
      if (p.position_status === "CLOSE_REQUESTED") {
        push(p.symbol, "close", "Kapanıyor"); realPending++;
      }
    });
    (orders || []).forEach(function (o) {
      if (o.status && ["FILLED", "CANCELLED", "REJECTED",
                       "EXPIRED"].indexOf(o.status) === -1) {
        push(o.symbol, "exec", "Yürütülüyor"); realPending++;
      }
    });
    (signals || []).forEach(function (s) {
      if (s.execution_result === "SUBMITTED") {
        push(s.symbol, "prep", "Emir niyeti oluştu"); realPending++;
      }
    });
    // UI senkron sözleşmesi: products = kanonik etkin evren; HER
    // sembol tabloda görünür (üst şerit Evren sayısıyla birebir).
    (products || []).forEach(function (pr) {
      if (openSymbols[pr.symbol]) return;
      if (pr.entry_eligible) {
        push(pr.symbol, "wait", "Sinyal bekliyor");
      } else if (pr.automation_state === "ENABLED") {
        push(pr.symbol, "gray", "İzleniyor");
      } else {
        push(pr.symbol, "gray", "İzleniyor (giriş kapalı)");
      }
    });
    // Dürüst etiket: gerçek emir/niyet yoksa "Sıradaki İşlemler" denmez —
    // yalnız analiz/sinyal bekleyen semboller "İzlenen Piyasalar"dır.
    var queueLabel = realPending > 0 ? "Sıradaki İşlemler"
                                     : "İzlenen Piyasalar";
    setText("th-h-queue", queueLabel);
    setText("th-queue-cell-label", queueLabel);
    setText("th-queued-count",
            realPending > 0 ? realPending : items.length);
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

  // ── Üst şerit pipeline hücreleri — /api/paper/state ────────────

  var SCHED_TR = { RUNNING: "Çalışıyor", STOPPED: "Durdu",
                   ERROR: "Hata" };

  function renderStrip(paper) {
    if (!paper) {
      // Uç düşerse bayat değer taze gibi gösterilmez.
      ["th-strip-pipeline", "th-strip-scheduler", "th-strip-scan",
       "th-strip-universe", "th-strip-risk",
       "th-strip-lastanalysis"].forEach(function (id) {
        setDatum(id, null);
      });
      return;
    }
    setDatum("th-strip-pipeline", paper.overall_pipeline,
             paper.overall_pipeline === "OK" ? "th-profit" : "");
    setDatum("th-strip-scheduler",
             SCHED_TR[paper.analysis_scheduler] ||
             paper.analysis_scheduler,
             paper.analysis_scheduler === "RUNNING" ? "th-profit" : "");
    setDatum("th-strip-scan", paper.scan_interval != null ?
             paper.scan_interval + " dk" : null);
    setDatum("th-strip-universe", paper.universe_size);
    setDatum("th-strip-risk", paper.selected_risk_profile);
    setDatum("th-strip-lastanalysis",
             paper.last_complete_analysis ?
             fmtTime(paper.last_complete_analysis) : null);
    setDatum("th-sys-data", paper.market_data_status,
             paper.market_data_status === "OK" ? "th-profit" : "");
  }

  // ── Üst çubuk + AI paneli ──────────────────────────────────────

  function renderTop(status, portfolio, products, paper) {
    if (portfolio) {
      setText("th-portfolio-value",
              fmtMoney(portfolio.portfolio_value, "USDT"));
      setText("th-daily-pnl",
              fmtSigned(portfolio.daily_pnl, "USDT"),
              pnlClass(portfolio.daily_pnl));
      setText("th-sc-intraday",
              fmtSigned(portfolio.daily_pnl, "USDT"),
              pnlClass(portfolio.daily_pnl));
      setDatum("th-sc-risk", portfolio.open_risk != null ?
               fmtMoney(portfolio.open_risk, "USDT") : null);
    } else {
      // Portföy ucu düşerse bayat değer taze gibi gösterilmez.
      setText("th-portfolio-value", null);
      setText("th-daily-pnl", null);
      setText("th-sc-intraday", null);
      setDatum("th-sc-risk", null);
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
      setDatum("th-sys-auto", null);
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
    var rl = status.rate_limit;
    if (status.kill_switch_state === "ACTIVE") {
      // Runtime çalışıyor olabilir — blokaj ACİL STOP'tur; SSL gibi
      // geçici uyarılar ana neden gibi gösterilmez.
      badge = "Otomasyon durdu — ACİL STOP"; cls = "err";
    } else if (rl && rl.active) {
      // Task 93: 429/418 geri çekilmesi — tarama duraklatıldı rozeti.
      badge = "Tarama duraklatıldı (" + rl.remaining_seconds + " sn)";
      cls = "pause";
    } else if (autonomous) {
      badge = "Çalışıyor"; cls = "run";
    } else {
      badge = "Duraklatıldı"; cls = "pause";
    }
    setText("th-bot-status", badge,
            cls === "err" ? "th-loss" :
            cls === "run" ? "th-profit" : "");
    setDatum("th-sys-auto", modeLabel + " — " + badge,
             cls === "err" ? "th-loss" :
             cls === "run" ? "th-profit" : "");
    if (aiMode) {
      aiMode.textContent = modeLabel;
      aiMode.className = "mode " +
        (autonomous ? "autonomous" : "advisor");
    }
    if (aiSent) {
      aiSent.textContent = cls === "err"
        ? "Runtime çalışıyor; ticaret otomasyonu ACİL STOP nedeniyle " +
          "kapalı. Neden ve güvenli kaldırma: Operation Center."
        : "Piyasalar izleniyor.";
    }
    if (products) {
      setText("th-ai-scanned", products.length);
    } else {
      setText("th-ai-scanned", null);
    }
    // "Uygun Fırsatlar" = GERÇEK sinyal adayları (karar kayıtlarında
    // WATCH/OPEN) — enabled/entry_eligible sembol fırsat DEĞİLDİR.
    // Üç sembol yalnız "Sinyal bekliyor" ise sayı 0 olmalıdır.
    setText("th-ai-eligible",
            paper && typeof paper.signal_candidate_count === "number"
              ? paper.signal_candidate_count : null);
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

  // ── İki model (CORE / OPPORTUNITY) — tek kanonik snapshot ─────
  // Task 135: dual-model sağlık rozeti — saha koşusu başarısız olursa
  // operatör panelden görür (manuel verify_dual_model beklenmez).
  // YEŞİL: her iki liste dolu ve hata yok; SARI: liste(ler) boş;
  // KIRMIZI: last_error var veya state alınamadı.
  function renderDualHealth(d) {
    var badge = document.getElementById("th-dm-health");
    var lastEl = document.getElementById("th-dm-last-refresh");
    var errEl = document.getElementById("th-dm-last-error");
    if (!badge) return;
    function set(label, bg, fg) {
      badge.textContent = "SAĞLIK: " + label;
      badge.style.background = bg;
      badge.style.color = fg;
      // Sol menü alt kartı aynı sağlık durumunu yansıtır.
      var nh = document.getElementById("nav-health");
      if (nh) nh.textContent = label;
    }
    if (!d) {
      set("KIRMIZI — durum alınamadı", "#f85149", "#0d1117");
      if (lastEl) lastEl.textContent = "UNKNOWN";
      if (errEl) errEl.textContent =
        " — /api/dual-model/state yanıt vermedi";
      return;
    }
    if (lastEl) lastEl.textContent = d.last_refresh || "UNKNOWN";
    if (errEl) errEl.textContent = d.last_error ?
      " — Son hata: " + d.last_error : "";
    var c = d.counters || {};
    var coreOk = (c.core_universe || 0) > 0;
    var oppOk = (c.opportunity_universe || 0) > 0;
    if (d.last_error) {
      set("KIRMIZI — hata", "#f85149", "#0d1117");
    } else if (coreOk && oppOk) {
      set("YEŞİL", "#3fb950", "#0d1117");
    } else {
      set("SARI — liste(ler) boş", "#d29922", "#0d1117");
    }
  }
  function renderDualModel(d) {
    renderDualHealth(d);
    var core = document.getElementById("th-dm-core");
    var opp = document.getElementById("th-dm-opp");
    if (!core || !opp) return;
    if (!d) {
      core.innerHTML = opp.innerHTML =
        "<tr><td colspan=\"6\" class=\"th-empty\">UNKNOWN</td></tr>";
      renderMarkets(null, null);
      renderMovers(null);
      renderTicker(null);
      return;
    }
    var c = d.counters || {};
    setText("th-dm-core-uni", c.core_universe);
    setText("th-dm-opp-uni", c.opportunity_universe);
    setText("th-dm-core-open", c.core_open);
    setText("th-dm-opp-open", c.opportunity_open);
    setText("th-dm-total-open", c.total_open);
    var openBy = {};
    (d.positions || []).forEach(function (p) {
      openBy[p.symbol] = p;
    });
    function stateOf(sym) {
      return openBy[sym] ? "POZİSYONDA" : "İZLENİYOR";
    }
    // İki listenin sütunları AYNI: Sembol / Sinyal / Spread / Güven /
    // Model Durumu / Açık Pozisyon (referans tasarım gereği).
    function listRow(r) {
      var p = openBy[r.symbol];
      return "<tr><td><b>" + esc(r.symbol) + "</b></td><td>" +
        (p ? esc(p.side) : "—") + "</td><td>" +
        (r.spread_pct != null ? r.spread_pct.toFixed(3) + "%" : "—") +
        "</td><td>" + (p && p.confidence != null ? p.confidence : "—") +
        "</td><td>" + stateOf(r.symbol) + "</td><td>" +
        (p ? esc(p.side) + " @" + fmtPrice(p.entry) : "—") +
        "</td></tr>";
    }
    core.innerHTML = (d.core_list || []).map(listRow).join("") ||
      "<tr><td colspan=\"6\" class=\"th-empty\">Liste henüz " +
      "yenilenmedi.</td></tr>";
    opp.innerHTML = (d.opportunity_list || []).map(listRow).join("") ||
      "<tr><td colspan=\"6\" class=\"th-empty\">Fırsat taraması " +
      "henüz koşmadı.</td></tr>";
    renderDualPositions(d.positions || []);
    renderMarkets(d, openBy);
    renderMovers(d);
    renderTicker(d);

    // ── Task 130: ayrı performans metrik kartları ────────────────
    renderDualMetrics(d);
  }

  // ── İzlenen Piyasalar / Kazanan-Kaybeden / fiyat şeridi ────────
  // Kaynak: dual-model snapshot list satırları (Binance 24s ticker
  // alanları: last / change_pct / volume_usdt). Uydurma veri yok.

  function unionRows(d) {
    if (!d) return [];
    var seen = {};
    return (d.core_list || []).concat(d.opportunity_list || [])
      .filter(function (r) {
        if (!r || !r.symbol || seen[r.symbol]) return false;
        seen[r.symbol] = true;
        return true;
      });
  }

  function chgCell(v) {
    if (v === null || v === undefined || isNaN(v)) {
      return "<span class=\"th-unknown\">Veri yok</span>";
    }
    var cls = v > 0 ? "th-profit" : v < 0 ? "th-loss" : "";
    return "<span class=\"" + cls + "\">" + (v > 0 ? "+" : "") +
      Number(v).toFixed(2) + "%</span>";
  }

  function renderMarkets(d, openBy) {
    var el = document.getElementById("th-markets");
    if (!el) return;
    var rows = unionRows(d);
    if (!rows.length) {
      el.innerHTML = "<tr><td colspan=\"5\" class=\"th-empty\">" +
        (d ? "Liste henüz yenilenmedi." : "Veri yok") + "</td></tr>";
      return;
    }
    rows.sort(function (a, b) {
      return (b.volume_usdt || 0) - (a.volume_usdt || 0);
    });
    el.innerHTML = rows.slice(0, 30).map(function (r) {
      var pos = openBy && openBy[r.symbol];
      return "<tr><td><b>" + esc(r.symbol) + "</b></td><td>" +
        esc(fmtPrice(r.last)) + "</td><td>" + chgCell(r.change_pct) +
        "</td><td>" + (r.volume_usdt != null ?
          esc(fmtMoney(r.volume_usdt)) : "—") + "</td><td>" +
        (pos ? "<span class=\"th-badge exec\">POZİSYONDA</span>"
             : "<span class=\"th-badge gray\">İZLENİYOR</span>") +
        "</td></tr>";
    }).join("");
  }

  function renderMovers(d) {
    var g = document.getElementById("th-gainers");
    var l = document.getElementById("th-losers");
    if (!g || !l) return;
    var rows = unionRows(d).filter(function (r) {
      return typeof r.change_pct === "number";
    });
    if (!rows.length) {
      g.className = l.className = "th-empty";
      g.textContent = l.textContent = "Veri yok";
      return;
    }
    rows.sort(function (a, b) { return b.change_pct - a.change_pct; });
    function block(title, list) {
      return "<b style=\"font-size:.68rem;color:#8b949e;" +
        "text-transform:uppercase\">" + title + "</b>" +
        list.map(function (r) {
          return "<div class=\"th-kv\"><span><b>" + esc(r.symbol) +
            "</b></span><span>" + esc(fmtPrice(r.last)) + " · " +
            chgCell(r.change_pct) + "</span></div>";
        }).join("");
    }
    g.className = ""; l.className = "";
    g.innerHTML = block("En Çok Kazananlar", rows.slice(0, 4));
    l.innerHTML = block("En Çok Kaybedenler",
                        rows.slice(-4).reverse());
  }

  function renderTicker(d) {
    var el = document.getElementById("th-ticker");
    if (!el) return;
    var rows = unionRows(d);
    if (!rows.length) {
      el.innerHTML = "<span class=\"th-unknown\">Veri yok</span>";
      return;
    }
    rows.sort(function (a, b) {
      return (b.volume_usdt || 0) - (a.volume_usdt || 0);
    });
    el.innerHTML = rows.slice(0, 10).map(function (r) {
      return "<span class=\"tk\"><b>" + esc(r.symbol) + "</b>" +
        esc(fmtPrice(r.last)) + " " + chgCell(r.change_pct) +
        "</span>";
    }).join("");
  }

  function fmtMetric(v, suffix) {
    if (v === null || v === undefined) {
      return "<span class=\"th-unknown\">UNKNOWN</span>";
    }
    return esc(String(v)) + (suffix || "");
  }

  function fmtNum(v, dp) {
    return (v === null || v === undefined || isNaN(v)) ?
      "UNKNOWN" : Number(v).toFixed(dp);
  }

  // Model pozisyonu kapatma onayı: PAPER MANUAL_CLOSE. Onay metni
  // tahmini ücret/kayma ve tahmini net PnL'i açıkça gösterir.
  function dmConfirmText(p) {
    var lines = [(p && p.symbol) + " pozisyonu kapatılsın mı? (PAPER)"];
    if (p) {
      lines.push("Miktar: " + fmtNum(p.quantity, 6));
      lines.push("Tahmini ücret: " + fmtNum(p.est_fees, 4) + " USDT");
      lines.push("Tahmini kayma: " + fmtNum(p.est_slippage, 4) +
                 " USDT");
      lines.push("Tahmini net PnL: " +
                 fmtNum(p.unrealized_net_pnl, 4) + " USDT");
    }
    return lines.join("\n");
  }

  function bindDmClose(scope, positions) {
    var by = {};
    (positions || []).forEach(function (p) { by[p.symbol] = p; });
    scope.querySelectorAll(".th-dm-close").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var sym = btn.getAttribute("data-symbol");
        if (!window.confirm(dmConfirmText(by[sym] ||
            { symbol: sym }))) return;
        btn.disabled = true;
        fetch("/api/dual-model/close", {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     "X-CSRFToken": window.TH_CSRF },
          body: JSON.stringify({ symbol: sym })
        }).then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.ok) {
              window.alert("Kapatılamadı: " + (d.message || ""));
              btn.disabled = false;
            } else { refresh(); }
          })
          .catch(function () { btn.disabled = false; });
      });
    });
  }

  function renderDualPositions(list) {
    var tb = document.getElementById("th-dm-pos");
    if (!tb) return;
    if (!list.length) {
      tb.innerHTML = "<tr><td colspan=\"13\" class=\"th-empty\">" +
        "Açık model pozisyonu yok.</td></tr>";
      return;
    }
    tb.innerHTML = list.map(function (p) {
      var neg = (p.unrealized_net_pnl || 0) < 0;
      return "<tr><td><b>" + esc(p.symbol) + "</b></td><td>" +
        esc(p.model || "—") + "</td><td>" + fmtNum(p.quantity, 6) +
        "</td><td>" + fmtNum(p.notional_usdt, 2) +
        "</td><td>" + fmtPrice(p.entry) + "</td><td>" +
        (p.current_price != null ? fmtPrice(p.current_price) :
          "UNKNOWN") +
        "</td><td style=\"color:" + (neg ? "#f85149" : "#3fb950") +
        "\">" + fmtNum(p.unrealized_net_pnl, 4) + "</td><td>" +
        fmtNum(p.unrealized_pnl_pct, 2) + "</td><td>" +
        fmtNum(p.est_fees, 4) + "</td><td>" +
        fmtNum(p.est_slippage, 4) + "</td><td>" +
        fmtPrice(p.tp) + "</td><td>" + fmtPrice(p.sl) +
        "</td><td><button class=\"th-btn th-dm-close\" " +
        "data-symbol=\"" + esc(p.symbol) + "\">Kapat</button>" +
        "</td></tr>";
    }).join("");
    bindDmClose(tb, list);
  }

  // ── Yoklama ────────────────────────────────────────────────────

  var inflight = false;

  function refresh() {
    if (inflight) return;
    inflight = true;
    Promise.all([
      get("/api/operation-control/status"),
      // TEK atomik snapshot: positions/orders/products/signals aynı
      // sunucu anlık görüntüsünden — widget'lar çelişemez.
      get("/api/operation-control/overview"),
      get("/api/operation-control/workspace/portfolio"),
      get("/api/operation-control/workspace/journal"),
      get("/api/accounts/wallets"),
      get("/api/accounts"),
      get("/api/dual-model/state"),
      get("/api/paper/state")
    ]).then(function (r) {
      function data(i, key) {
        var b = r[i].body;
        return b && b.ok && b.data ? b.data[key] : null;
      }
      // Task 70: eski Binance env isim uyarıları ana panelde de
      // banner olarak görünür (dash_base.html → showWarnings).
      var st = r[0].body && r[0].body.ok ? r[0].body.data : null;
      if (st && typeof showWarnings === "function") {
        var warns = (st.legacy_env_warnings || []).slice();
        // Task 93: aktif 429/418 geri çekilmesi banner + rozet olarak
        // görünür; süre dolunca sonraki yenilemede kendiliğinden kaybolur.
        var rl = st.rate_limit;
        if (rl && rl.active) {
          warns.push("Tarama " + rl.remaining_seconds +
                     " saniye duraklatıldı — " + rl.reason);
        }
        showWarnings(warns);
      }
      // /api/paper/state zarfı düz JSON'dur (ok/data zarfı yok).
      var paper = r[7].http === 200 ? r[7].body : null;
      var dual = r[6].body && r[6].body.ok ? r[6].body.data : null;
      renderTop(r[0].body && r[0].body.ok ? r[0].body.data : null,
                data(2, "portfolio"), data(1, "products"), paper);
      renderStrip(paper);
      renderTrades(data(1, "positions"),
                   dual ? dual.positions : null);
      renderQueue(data(1, "products"), data(1, "orders"),
                  data(1, "positions"), data(1, "signals"));
      renderActivity(data(3, "journal"));
      renderWallets(data(4, "accounts"), data(5, "accounts"));
      renderDualModel(dual);
      // Gerçekleşmiş PnL (model, net) — dual snapshot toplamı.
      setDatum("th-sc-realized", dual &&
               dual.portfolio_net_pnl != null ?
               fmtSigned(dual.portfolio_net_pnl, "USDT") : null,
               dual ? pnlClass(dual.portfolio_net_pnl) : "");
      inflight = false;
    }, function () { inflight = false; });
  }

  window.TH = { refresh: refresh, duration: duration,
                fmtMoney: fmtMoney, fmtPrice: fmtPrice };

  refresh();
  setInterval(refresh, POLL_MS);

  function fmtPnl(v) {
    if (v === null || v === undefined) {
      return "<span class=\"th-unknown\">UNKNOWN</span>";
    }
    var cls = v > 0 ? "th-profit" : v < 0 ? "th-loss" : "";
    return "<span class=\"" + cls + "\">" +
      (v > 0 ? "+" : "") + esc(String(v)) + "</span>";
  }

  var REJECT_LABELS = {
    NO_SIGNAL: "Sinyal yok",
    LOW_CONFIDENCE: "Düşük güven",
    SPREAD_TOO_HIGH: "Spread çok yüksek",
    LOW_BOOK_DEPTH: "Yetersiz emir defteri derinliği",
    LOW_LIQUIDITY: "Düşük likidite",
    SLIPPAGE_TOO_HIGH: "Kayma çok yüksek",
    FEE_DRAG: "Ücret yükü",
    EXPECTED_EDGE_TOO_LOW: "Beklenen edge çok düşük",
    MOMENTUM_EXHAUSTED: "Momentum tükendi",
    FALSE_BREAKOUT_RISK: "Sahte kırılım riski",
    RISK_LIMIT: "Risk limiti",
    POSITION_LIMIT: "Pozisyon limiti dolu",
    COOLDOWN: "Bekleme süresi (cooldown)",
    DUPLICATE_POSITION: "Yinelenen pozisyon",
    DUPLICATE_MODEL_OWNERSHIP: "Sembol diğer modelde",
    DATA_QUALITY: "Veri kalitesi"
  };

  function renderDualMetrics(d) {
    var coreCard = document.getElementById("th-dm-metrics-core");
    var oppCard = document.getElementById("th-dm-metrics-opp");
    var rej = document.getElementById("th-dm-rejections");
    if (!coreCard || !oppCard) return;
    var metrics = (d && d.metrics) || {};
    var mCore = metrics.ALPHA_CORE_SCALP || null;
    var mOpp = metrics.ALPHA_OPPORTUNITY_BURST || null;
    coreCard.innerHTML =
      "<b style=\"font-size:.72rem\">CORE — ALPHA CORE SCALP</b>" +
      metricCardBody(mCore);
    oppCard.innerHTML =
      "<b style=\"font-size:.72rem\">OPPORTUNITY — ALPHA " +
      "OPPORTUNITY BURST</b>" + metricCardBody(mOpp);
    var pf = document.getElementById("th-dm-portfolio");
    if (pf) {
      var v = d ? d.portfolio_net_pnl : null;
      if (v === null || v === undefined) {
        pf.textContent = "UNKNOWN";
        pf.className = "th-unknown";
      } else {
        pf.textContent = (v > 0 ? "+" : "") + v;
        pf.className = v > 0 ? "th-profit" : v < 0 ? "th-loss" : "";
      }
    }
    if (rej) {
      if (!d) {
        rej.innerHTML = "<span class=\"th-unknown\">UNKNOWN</span>";
      } else {
        var parts = [];
        [["CORE", mCore], ["OPPORTUNITY", mOpp]].forEach(function (e) {
          var reasons = (e[1] && e[1].rejection_reasons) || {};
          var keys = Object.keys(reasons).sort(function (a, b) {
            return reasons[b] - reasons[a];
          });
          if (!keys.length) return;
          parts.push("<div><b>" + e[0] + ":</b> " +
            keys.map(function (k) {
              return esc(REJECT_LABELS[k] || k) + " × " +
                reasons[k];
            }).join(", ") + "</div>");
        });
        rej.className = "";
        rej.innerHTML = parts.join("") ||
          "<span class=\"th-empty\">Henüz ret kaydı yok.</span>";
      }
    }
  }

  function metricCardBody(m) {
    if (!m) {
      return "<div class=\"th-empty\">UNKNOWN</div>";
    }
    var rows = [
      ["Taranan Sembol", fmtMetric(m.scanned_symbols)],
      ["Aday Sinyal", fmtMetric(m.candidates)],
      ["Açık Pozisyon", fmtMetric(m.opened_positions)],
      ["Kapanan İşlem", fmtMetric(m.closed_positions)],
      ["İşlem/Gün", fmtMetric(m.trades_per_day)],
      ["Kazanç Oranı", fmtMetric(m.win_rate, "%")],
      ["Brüt PnL", fmtPnl(m.gross_pnl)],
      ["Ücretler", fmtMetric(m.fees)],
      ["Kayma (Slippage)", fmtMetric(m.slippage)],
      ["Net PnL", fmtPnl(m.net_pnl)],
      ["Ort. Tutma (dk)", fmtMetric(m.average_hold_minutes)],
      ["Profit Factor", fmtMetric(m.profit_factor)],
      ["Maks. Drawdown", fmtMetric(m.max_drawdown)],
      ["Beklenti/İşlem", fmtPnl(m.expectancy_per_trade)]
    ];
    return rows.map(function (r) {
      return "<div style=\"display:flex;justify-content:" +
        "space-between;gap:8px\"><span>" + r[0] + "</span><span>" +
        r[1] + "</span></div>";
    }).join("");
  }
})();
