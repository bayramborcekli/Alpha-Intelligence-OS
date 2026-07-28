/* Mission 2300 Agent 03 — Hesaplarım istemcisi.
 *
 * Felsefe: Alpha Intelligence borsa değildir; kullanıcının kendi
 * hesaplarını yöneten otonom yatırım işletim sistemidir.
 *
 * Kurallar:
 * - Sır asla görüntülenmez; anahtar yalnız maskeli gelir.
 * - Panoya kopyalama yok (kopyalama düğmesi üretilmez).
 * - Hazır olmayan bağlayıcı: düğmeler dürüstçe devre dışı.
 * - Bilinmeyen değer UNKNOWN; tahmin yok.
 * - Otomatik eşitleme: 30 sn yoklama; elle düğmeler de mevcut.
 */
(function () {
  "use strict";

  var POLL_MS = 30000;

  function esc(v) {
    return String(v === null || v === undefined ? "UNKNOWN" : v)
      .replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
                 '"': "&quot;", "'": "&#39;" }[c];
      });
  }

  function api(path, method, body) {
    return fetch(path, {
      method: method || "GET",
      headers: { "Content-Type": "application/json",
                 "X-CSRFToken": window.MA_CSRF },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, message: "Ağ hatası" }; });
  }

  var lastCards = [];
  var walletsByAccount = {};

  function badge(cls, text) {
    return "<span class=\"ma-badge " + cls + "\">" + esc(text) +
      "</span>";
  }

  function row(label, value, extraCls) {
    return "<div class=\"row\"><span>" + esc(label) + "</span>" +
      "<span class=\"" + (extraCls || "") + "\">" + esc(value) +
      "</span></div>";
  }

  function btn(label, action, id, opts) {
    opts = opts || {};
    return "<button class=\"ma-btn " + (opts.cls || "") + "\"" +
      " data-action=\"" + action + "\" data-id=\"" + esc(id) + "\"" +
      (opts.disabled ? " disabled title=\"" +
        esc(opts.reason || "Kullanılamaz") + "\"" : "") + ">" +
      esc(label) + "</button>";
  }

  function card(a) {
    var w = walletsByAccount[a.account_id] || null;
    var ready = a.connector_ready;
    var creds = a.credentials_configured;
    var statusBadge = !ready ? badge("warn", "Bağlayıcı Hazır Değil")
      : a.connected ? badge("ok", "Bağlı") : badge("off", "Bağlı Değil");
    var noConn = { disabled: !ready,
                   reason: "Bağlayıcı henüz hazır değil" };
    var offline = { disabled: !a.connected,
                    reason: "Önce hesabı bağlayın" };
    return "<div class=\"ma-card" +
      (a.connected ? "" : " disconnected") + "\" data-account=\"" +
      esc(a.account_id) + "\">" +
      "<div class=\"ma-head\"><span class=\"ma-logo\">" +
      esc(a.logo) + "</span><div><div class=\"ma-name\">" +
      esc(a.display_name) + "</div><div class=\"ma-nick\">" +
      esc(a.nickname) + "</div></div>" + statusBadge +
      (a.primary ? badge("primary", "Birincil") : "") + "</div>" +
      "<div class=\"ma-rows\">" +
      row("API Anahtarı", a.api_key_masked, "ma-key") +
      row("Gizli Anahtar", creds ? "Kayıtlı — asla gösterilmez"
                                 : "Tanımlı değil") +
      row("Ortam", a.environment) +
      row("Spot", a.spot_enabled ? "Etkin" :
          a.spot_capable ? "Kapalı" : "Desteklenmiyor") +
      row("Cüzdan Sayısı", w ? w.wallet_count : null) +
      row("Portföy Değeri (USDT)", w ? w.value_usdt : null,
          w && w.value_usdt !== "UNKNOWN" ? "" : "ma-unknown") +
      row("Son Eşitleme", w ? w.last_sync_at : a.last_sync_at) +
      "</div>" +
      "<div class=\"ma-actions\">" +
      (a.connected
        ? btn("Bağlantıyı Kes", "disconnect", a.account_id,
              { cls: "danger" })
        : btn("Bağlan", "connect", a.account_id,
              !ready ? noConn : !creds
                ? { disabled: true,
                    reason: "Önce ortam sırlarına anahtar ekleyin" }
                : { cls: "accent" })) +
      btn("Düzenle", "edit", a.account_id, noConn.disabled ? noConn : {}) +
      btn("Eşitle", "sync", a.account_id,
          !ready ? noConn : offline) +
      btn("Bağlantı Testi", "test", a.account_id,
          !ready ? noConn : offline) +
      btn("Cüzdanları Yenile", "refresh", a.account_id,
          !ready ? noConn : offline) +
      (a.primary ? "" :
        btn("Birincil Yap", "primary", a.account_id,
            (!ready || !a.connected)
              ? { disabled: true,
                  reason: "Yalnız bağlı ve hazır hesap" } : {})) +
      "</div>" +
      "<div class=\"ma-test\" id=\"ma-test-" + esc(a.account_id) +
      "\"></div></div>";
  }

  function render() {
    var el = document.getElementById("ma-accounts");
    if (!el || !lastCards.length) return;
    el.innerHTML = lastCards.map(card).join("");
  }

  function refresh() {
    return Promise.all([
      api("/api/accounts"),
      api("/api/accounts/wallets"),
      api("/api/accounts/portfolio")
    ]).then(function (r) {
      if (r[0].ok) lastCards = r[0].data.accounts;
      if (r[1].ok) {
        walletsByAccount = {};
        r[1].data.accounts.forEach(function (w) {
          walletsByAccount[w.account_id] = w;
        });
      }
      if (r[2].ok) {
        var totalEl = document.getElementById("ma-total");
        var total = r[2].data.total_usdt;
        totalEl.textContent = total === "UNKNOWN" ? "UNKNOWN"
          : total + " USDT";
        totalEl.className = total === "UNKNOWN" ? "ma-unknown" : "";
        document.getElementById("ma-total-note").textContent =
          r[2].data.note === "OK"
            ? "Bağlı hesapların toplamı." : r[2].data.note;
      }
      render();
    });
  }

  function showTest(id, data) {
    var el = document.getElementById("ma-test-" + id);
    if (!el) return;
    var LABELS = { connected: "Bağlantı", authentication: "Kimlik",
      wallet_access: "Cüzdan Erişimi", spot_permission: "Spot İzni",
      trading_permission: "İşlem İzni",
      synchronization: "Eşitleme" };
    var out = "<b>Sonuç: " + esc(data.overall) + "</b><br>";
    Object.keys(LABELS).forEach(function (k) {
      var v = data.checks[k];
      out += "<span class=\"" + (v === "OK" ? "ok" : "") + "\">" +
        LABELS[k] + ": " + esc(v) + "</span>";
    });
    el.innerHTML = out;
  }

  function doAction(action, id) {
    if (action === "edit") return openEdit(id);
    if (action === "test") {
      return api("/api/accounts/" + encodeURIComponent(id) + "/test",
                 "POST").then(function (r) {
        if (r.ok) showTest(id, r.data);
      });
    }
    var path = { connect: "/connect", disconnect: "/disconnect",
                 primary: "/primary", sync: "/sync",
                 refresh: "/sync" }[action];
    if (!path) return;
    if (action === "disconnect" &&
        !window.confirm("Bu hesabın bağlantısı kesilecek. Bağlantısı " +
          "kesik hesaba otomasyon asla emir göndermez. Devam?")) {
      return;
    }
    return api("/api/accounts/" + encodeURIComponent(id) + path,
               "POST", {}).then(function (r) {
      if (!r.ok && r.message) window.alert(r.message);
      return refresh();
    });
  }

  function openEdit(id) {
    var a = lastCards.filter(function (c) {
      return c.account_id === id;
    })[0];
    var dlg = document.getElementById("ma-edit-dialog");
    if (!a || !dlg) return;
    document.getElementById("ma-edit-nickname").value = a.nickname;
    var spot = document.getElementById("ma-edit-spot");
    spot.checked = a.spot_enabled; spot.disabled = !a.spot_capable;
    dlg.returnValue = "cancel";
    dlg.showModal();
    dlg.onclose = function () {
      if (dlg.returnValue !== "ok") return;
      api("/api/accounts/" + encodeURIComponent(id) + "/edit", "POST", {
        nickname: document.getElementById("ma-edit-nickname").value,
        spot_enabled: spot.disabled ? null : spot.checked
      }).then(function (r) {
        if (!r.ok && r.message) window.alert(r.message);
        refresh();
      });
    };
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ?
      ev.target.closest("button[data-action]") : null;
    if (b && !b.disabled) doAction(b.dataset.action, b.dataset.id);
  });

  // Panoya kopyalama devre dışı: maskeli anahtar alanında bile.
  document.addEventListener("copy", function (ev) {
    var sel = String(window.getSelection ? window.getSelection() : "");
    if (sel && document.querySelector(".ma-key") &&
        sel.indexOf("*") !== -1) ev.preventDefault();
  });

  window.MA = { refresh: refresh };
  refresh();
  setInterval(refresh, POLL_MS);
})();
