/* Binance Bağlantıları sihirbazı — secret yalnız POST body'de gider,
   asla gösterilmez/loglanmaz. */
(function () {
  "use strict";
  const PROVIDERS = [
    { id: "BINANCE_GLOBAL", name: "Binance Global", api: "global" },
    { id: "BINANCE_TR", name: "Binance TR", api: "tr" },
  ];
  const BADGE = {
    CONNECTED_READ_ONLY: ["ok", "🟢 Bağlı (salt okunur)"],
    CONNECTED_PERMISSIONS_UNVERIFIED: ["warn", "🟡 Bağlı (yetki doğrulanamadı)"],
    NOT_CONFIGURED: ["off", "⚪ Bağlı değil"],
    DISCONNECTED: ["off", "⚪ Bağlantı kaldırıldı"],
    TESTING: ["warn", "… Test ediliyor"],
    INVALID_CREDENTIALS: ["err", "🔴 Anahtar geçersiz"],
    IP_RESTRICTED: ["err", "🔴 IP kısıtlı"],
    PERMISSION_DENIED: ["err", "🔴 Yetki reddedildi"],
    TIMESTAMP_DRIFT: ["err", "🔴 Saat farkı"],
    NETWORK_DEGRADED: ["warn", "🟡 Ağ sorunu"],
    ERROR: ["err", "🔴 Hata"],
  };
  const grid = document.getElementById("bc-grid");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function api(path, body) {
    const r = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : {
        "Content-Type": "application/json", "X-CSRFToken": window.BC_CSRF },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return r.json();
  }

  function card(p, st) {
    const s = st || { status: "NOT_CONFIGURED" };
    const [cls, label] = BADGE[s.status] || BADGE.ERROR;
    const connected = String(s.status || "").startsWith("CONNECTED");
    return `
    <div class="bc-card" data-p="${p.api}">
      <div class="bc-head"><span class="bc-name">${p.name}</span>
        <span class="bc-badge ${cls}">${label}</span></div>
      <div class="bc-rows">
        <div class="row"><span>Son test</span><span>${esc(s.tested_at || "—")}</span></div>
        <div class="row"><span>Hesap türü</span><span>${esc(s.account_type || "SPOT")}</span></div>
        <div class="row"><span>Yetki doğrulama</span><span>${
          s.status === "CONNECTED_READ_ONLY" ? "READ-ONLY doğrulandı" :
          s.status === "CONNECTED_PERMISSIONS_UNVERIFIED" ? "Doğrulanamadı (sarı)" : "—"}</span></div>
        <div class="row"><span>API anahtarı</span><span>${esc(s.masked_api_key || "—")}</span></div>
        ${p.id === "BINANCE_GLOBAL" ? `<div class="row"><span>Futures</span><span>${esc(s.futures || "NOT_TESTED")}</span></div>` : ""}
      </div>
      <div class="bc-btns">
        <button class="bc-btn accent" data-act="form">${connected ? "Güncelle" : "Bağlan"}</button>
        ${connected ? `<button class="bc-btn" data-act="test">Test Et</button>
        <button class="bc-btn danger" data-act="disconnect">Bağlantıyı Kaldır</button>` : ""}
      </div>
      <form class="bc-form" autocomplete="off">
        <input type="text" name="apiKey" placeholder="API Key" autocomplete="off" spellcheck="false">
        <input type="password" name="apiSecret" placeholder="API Secret (bir kez girilir, gösterilmez)" autocomplete="new-password">
        <div class="bc-btns">
          <button class="bc-btn accent" data-act="connect" type="submit">Kaydet ve Test Et</button>
          <button class="bc-btn" data-act="cancel" type="button">Vazgeç</button>
        </div>
      </form>
      <div class="bc-msg" role="status"></div>
    </div>`;
  }

  async function refresh() {
    try {
      const res = await api("/api/integrations/binance/status");
      const d = (res && res.data) || {};
      grid.innerHTML = PROVIDERS.map(p => card(p, d[p.id])).join("");
    } catch (e) {
      grid.innerHTML = '<div class="bc-card">Durum alınamadı — sayfayı yenileyin.</div>';
    }
  }

  /* Düşük frekanslı otomatik tazeleme: sunucu saatlik arka plan testinin
     sonuçlarını sayfa açıkken elle yenileme olmadan yansıtır.
     - Sekme gizliyken duraklar (visibilitychange).
     - Kullanıcı bir formu açıkken kartlar yeniden çizilmez (girdi kaybolmasın).
     - Ağ/HTTP hatası sessizce yutulur; mevcut kart durumu bozulmaz. */
  const AUTO_REFRESH_MS = 5 * 60 * 1000;
  let autoTimer = null;

  function formOpen() {
    return Array.prototype.some.call(
      grid.querySelectorAll(".bc-form"),
      f => f.style.display === "block");
  }

  async function autoRefresh() {
    if (document.hidden || formOpen()) return;
    try {
      const res = await api("/api/integrations/binance/status");
      if (!res || res.ok === false) return;
      const d = res.data || {};
      grid.innerHTML = PROVIDERS.map(p => card(p, d[p.id])).join("");
    } catch (e) { /* sessiz: mevcut kartlar korunur */ }
  }

  function startAuto() {
    if (autoTimer === null) autoTimer = setInterval(autoRefresh, AUTO_REFRESH_MS);
  }
  function stopAuto() {
    if (autoTimer !== null) { clearInterval(autoTimer); autoTimer = null; }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopAuto(); }
    else { startAuto(); autoRefresh(); }
  });

  function msgFor(res) {
    const d = (res && res.data) || {};
    const st = d.status || "ERROR";
    if (String(st).startsWith("CONNECTED")) {
      return ["ok", st === "CONNECTED_READ_ONLY"
        ? "Bağlandı — salt okunur yetki doğrulandı."
        : "Bağlandı — API yetki alanı vermedi; Binance panelinden anahtarın yalnız okuma yetkili olduğunu kontrol edin."];
    }
    const extra = d.guidance ? "\n" + d.guidance : "";
    return ["err", (BADGE[st] ? BADGE[st][1] : st) + extra];
  }

  grid.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const cardEl = btn.closest(".bc-card");
    const p = cardEl.getAttribute("data-p");
    const form = cardEl.querySelector(".bc-form");
    const msg = cardEl.querySelector(".bc-msg");
    const act = btn.getAttribute("data-act");
    if (act === "form") { form.style.display = "block"; return; }
    if (act === "cancel") { form.style.display = "none"; form.reset(); return; }
    if (act === "connect") {
      ev.preventDefault();
      const key = form.apiKey.value.trim();
      const sec = form.apiSecret.value.trim();
      form.reset();
      msg.className = "bc-msg warn"; msg.textContent = "Test ediliyor…";
      const res = await api(`/api/integrations/binance/${p}/connect`,
        { apiKey: key, apiSecret: sec });
      const [cls, text] = res.ok ? msgFor(res) : ["err", res.error || res.message || "İşlem başarısız."];
      msg.className = "bc-msg " + cls; msg.textContent = text;
      if (res.ok && String((res.data || {}).status || "").startsWith("CONNECTED")) await refresh();
      return;
    }
    if (act === "test" || act === "disconnect") {
      ev.preventDefault();
      msg.className = "bc-msg warn"; msg.textContent = "İşleniyor…";
      const res = await api(`/api/integrations/binance/${p}/${act}`, {});
      const [cls, text] = res.ok
        ? (act === "disconnect" ? ["ok", "Bağlantı kaldırıldı; anahtar yerel depodan silindi."] : msgFor(res))
        : ["err", res.error || res.message || "İşlem başarısız."];
      msg.className = "bc-msg " + cls; msg.textContent = text;
      await refresh();
    }
  });

  refresh();
  startAuto();
})();
