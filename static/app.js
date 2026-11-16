/* XAUUSD Bot panel — plain JS, no framework. */
const $ = (id) => document.getElementById(id);
const state = { me: null, cfg: null };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || "Request failed.");
  return data;
}

function pill(text, cls) {
  return `<span class="pill ${cls}">${text}</span>`;
}
function show(el, on) { el.classList.toggle("hidden", !on); }
function err(el, msg) {
  el.textContent = msg || "";
  el.classList.toggle("hidden", !msg);
}

/* ---------- live price ---------- */
async function refreshPrice() {
  try {
    const d = await api("/api/price");
    $("livePrice").textContent = "$" + Number(d.price).toLocaleString(undefined, { maximumFractionDigits: 2 });
  } catch { /* keep last value */ }
}

/* ---------- live TradingView gold chart (streams on its own) ---------- */
function renderTV() {
  const cfg = {
    symbol: "OANDA:XAUUSD", interval: "15", theme: "dark", style: "1",
    locale: "en", allow_symbol_change: true, hide_side_toolbar: false,
    withdateranges: true, backgroundColor: "rgba(22, 22, 26, 1)",
  };
  $("tv-container").innerHTML =
    `<div class="tradingview-widget-container" style="height:100%;width:100%">` +
    `<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>` +
    `<script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>` +
    JSON.stringify(cfg) + `<\/script></div>`;
}

/* ---------- auth / boot ---------- */
async function boot() {
  renderTV();
  const params = new URLSearchParams(location.search);
  if (params.get("error") === "google_not_configured")
    err($("loginErr"), "Google sign-in is not set up yet. The owner is adding it — please try again soon.");
  if (params.get("error") === "google_failed")
    err($("loginErr"), "Google sign-in failed. Please try again.");
  try { state.cfg = await api("/api/config"); } catch { state.cfg = { price_usd: 20 }; }
  $("payPrice").textContent = "$" + (state.cfg.price_usd ?? 20);
  $("payAddr").textContent = state.cfg.deposit_address || "—";
  if (state.cfg.deposit_address)
    $("qrImg").src = "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=" + encodeURIComponent(state.cfg.deposit_address);
  try {
    const d = await api("/api/me");
    state.me = d.user;
    if (d.user) {
      show($("signedIn"), true);
      $("userName").textContent = d.user.name || d.user.email;
      $("userEmail").textContent = d.user.email;
      if (d.is_admin) show($("tabAdmin"), true);
      if (d.license) {
        $("licensePill").outerHTML = pill("Active", "green");
        $("licenseUntil").textContent = "until " + d.license.ends_at.slice(0, 10);
      } else {
        $("licensePill").outerHTML = pill("No license", "red");
        $("licenseUntil").textContent = "— pay $20 on the Pay tab to activate.";
      }
      loadAccounts(); loadPayments(); loadTrades();
      if (d.is_admin) loadAdmin();
    } else {
      show($("signedOut"), true);
    }
  } catch { show($("signedOut"), true); }
  refreshPrice();
  setInterval(refreshPrice, 15000);
}

$("googleBtn").onclick = () => { location.href = "/auth/google/login"; };
$("logoutBtn").onclick = async () => {
  await api("/auth/logout", { method: "POST" });
  location.reload();
};

/* ---------- tabs ---------- */
function tab(name) {
  ["Bot", "Pay", "Trades", "Admin"].forEach((t) => {
    $("tab" + t).classList.toggle("on", t === name);
    show($("pane" + t), t === name);
  });
}
$("tabBot").onclick = () => tab("Bot");
$("tabPay").onclick = () => tab("Pay");
$("tabTrades").onclick = () => tab("Trades");
$("tabAdmin").onclick = () => tab("Admin");

/* ---------- MT5 accounts ---------- */
async function loadAccounts() {
  const d = await api("/api/mt5");
  const el = $("acctList");
  if (!d.accounts.length) { el.innerHTML = "<p class='muted'>No MT5 account connected yet.</p>"; return; }
  el.innerHTML = d.accounts.map((a) => `
    <div class="acct">
      <div class="row">
        <div>
          <div class="login">${a.login} ${a.broker ? "· " + esc(a.broker) : ""}</div>
          <div class="meta">${esc(a.server)} · status: ${esc(a.status || "offline")}${a.last_heartbeat ? " · last seen " + a.last_heartbeat.slice(0, 19).replace("T", " ") : ""}</div>
        </div>
        ${a.bot_enabled ? pill("Bot running", "green") : pill("Bot stopped", "grey")}
      </div>
      <div class="actions">
        ${a.bot_enabled
          ? `<button class="danger" onclick="setBot(${a.id}, false)">Stop bot</button>`
          : `<button onclick="setBot(${a.id}, true)">Start bot</button>`}
        <button class="secondary" onclick="delAcct(${a.id})">Remove</button>
      </div>
    </div>`).join("");
}
window.setBot = async (id, on) => {
  try {
    await api(`/api/mt5/${id}/bot`, { method: "PUT", body: JSON.stringify({ enabled: on }) });
    loadAccounts();
  } catch (e) {
    err($("mt5Err"), e.message);
    if (e.message.includes("license")) tab("Pay");
  }
};
window.delAcct = async (id) => {
  if (!confirm("Remove this MT5 account? The bot will stop for it.")) return;
  await api(`/api/mt5/${id}`, { method: "DELETE" });
  loadAccounts();
};
$("connectBtn").onclick = async () => {
  err($("mt5Err"), ""); err($("mt5Ok"), "");
  try {
    await api("/api/mt5", { method: "POST", body: JSON.stringify({
      broker: $("fBroker").value, server: $("fServer").value,
      login: $("fLogin").value, password: $("fPassword").value,
    })});
    err($("mt5Ok"), "MT5 account connected. Buy a license on the Pay tab, then press Start bot.");
    $("fPassword").value = "";
    loadAccounts();
  } catch (e) { err($("mt5Err"), e.message); }
};

/* ---------- payments ---------- */
$("copyAddrBtn").onclick = () => {
  navigator.clipboard.writeText(state.cfg.deposit_address || "");
  $("copyAddrBtn").textContent = "Copied!";
  setTimeout(() => { $("copyAddrBtn").textContent = "Copy address"; }, 1500);
};
$("verifyBtn").onclick = async () => {
  err($("payErr"), ""); err($("payOk"), "");
  const txid = $("fTxid").value.trim();
  if (!txid) { err($("payErr"), "Paste your TXID first."); return; }
  $("verifyBtn").disabled = true;
  $("verifyBtn").textContent = "Verifying on-chain…";
  try {
    const d = await api("/api/payments/submit", { method: "POST", body: JSON.stringify({ txid }) });
    err($("payOk"), `Payment verified: $${d.amount} USDT. License active until ${d.license_until}.`);
    $("fTxid").value = "";
    loadPayments(); bootLicense();
  } catch (e) { err($("payErr"), e.message); }
  $("verifyBtn").disabled = false;
  $("verifyBtn").textContent = "Verify payment & activate";
};
async function bootLicense() {
  const d = await api("/api/me");
  if (d.license) {
    const p = $("licensePill");
    p.outerHTML = pill("Active", "green");
    $("licenseUntil").textContent = "until " + d.license.ends_at.slice(0, 10);
  }
}
async function loadPayments() {
  const d = await api("/api/payments");
  const el = $("payList");
  if (!d.payments.length) { el.innerHTML = "<p class='muted'>No payments yet.</p>"; return; }
  el.innerHTML = `<table><tr><th>TXID</th><th>Amount</th><th>Status</th></tr>` +
    d.payments.map((p) => `<tr><td class="small">${esc(p.txid.slice(0, 12))}…</td><td>$${p.amount}</td><td>${pill(esc(p.status), "green")}</td></tr>`).join("") + `</table>`;
}

/* ---------- trades ---------- */
async function loadTrades() {
  const d = await api("/api/trades");
  const el = $("tradeList");
  if (!d.trades.length) { el.innerHTML = "<p class='muted'>No trades yet.</p>"; return; }
  el.innerHTML = `<table><tr><th>Ticket</th><th>Side</th><th>Lot</th><th>P/L</th></tr>` +
    d.trades.map((t) => `<tr><td>${esc(t.ticket)}</td><td>${esc(t.direction || "")}</td><td>${t.lot}</td><td class="${t.profit >= 0 ? "pos" : "neg"}">$${Number(t.profit).toFixed(2)}</td></tr>`).join("") + `</table>`;
}

/* ---------- admin ---------- */
async function loadAdmin() {
  const o = await api("/api/admin/overview");
  $("adminStats").innerHTML =
    `Users: <b>${o.users}</b> · Active licenses: <b>${o.active_licenses}</b> · MT5 accounts: <b>${o.mt5_accounts}</b> · Bots running: <b>${o.bots_running}</b> · Revenue: <b>$${o.revenue_usd}</b>`;
  const c = await api("/api/admin/customers");
  $("adminCustomers").innerHTML = c.customers.length
    ? `<table><tr><th>Email</th><th>Accts</th><th>Bot</th><th>License</th><th>Control</th></tr>` +
      c.customers.map((x) => `<tr>
        <td class="small">${esc(x.email)}${x.blocked ? "<br>" + pill("Blocked", "red") : ""}</td>
        <td>${x.accounts}</td>
        <td>${x.bots_running > 0 ? pill("Running", "green") : pill("Stopped", "grey")}</td>
        <td>${x.license_until ? x.license_until.slice(0, 10) : "—"}</td>
        <td class="small">
          <a href="#" onclick="adminBot(${x.id}, true);return false;">Start</a> ·
          <a href="#" onclick="adminBot(${x.id}, false);return false;">Stop</a><br>
          <a href="#" onclick="adminLic(${x.id}, 'grant', 30);return false;">+30d license</a> ·
          <a href="#" onclick="adminLic(${x.id}, 'revoke');return false;">Revoke</a><br>
          ${x.blocked
            ? `<a href="#" onclick="adminBlock(${x.id}, false);return false;">Unblock</a>`
            : `<a href="#" onclick="adminBlock(${x.id}, true);return false;">Block</a>`}
        </td></tr>`).join("") + `</table>`
    : "<p class='muted'>No customers yet.</p>";
  const p = await api("/api/admin/payments");
  $("adminPayments").innerHTML = p.payments.length
    ? `<table><tr><th>Email</th><th>Amount</th><th>Status</th><th>TXID</th></tr>` +
      p.payments.map((x) => `<tr><td class="small">${esc(x.email)}</td><td>$${x.amount}</td><td>${pill(esc(x.status), "green")}</td><td class="small">${esc(x.txid.slice(0, 10))}…</td></tr>`).join("") + `</table>`
    : "<p class='muted'>No payments yet.</p>";
  $("setPrice").value = state.cfg.price_usd;
  $("setAddr").value = state.cfg.deposit_address;
}
window.adminBot = async (id, on) => {
  await api(`/api/admin/customers/${id}/bot`, { method: "POST", body: JSON.stringify({ enabled: on }) });
  loadAdmin();
};
window.adminLic = async (id, action, days) => {
  await api(`/api/admin/customers/${id}/license`, { method: "POST", body: JSON.stringify({ action, days }) });
  loadAdmin();
};
window.adminBlock = async (id, blocked) => {
  if (blocked && !confirm("Block this user? They will be signed out and their bot will stop.")) return;
  await api(`/api/admin/customers/${id}/block`, { method: "POST", body: JSON.stringify({ blocked }) });
  loadAdmin();
};
$("saveSettingsBtn").onclick = async () => {
  err($("setErr"), ""); err($("setOk"), "");
  try {
    const d = await api("/api/admin/settings", { method: "POST", body: JSON.stringify({
      price_usd: $("setPrice").value, deposit_address: $("setAddr").value,
    })});
    state.cfg.price_usd = d.price_usd;
    state.cfg.deposit_address = d.deposit_address;
    err($("setOk"), "Settings saved.");
  } catch (e) { err($("setErr"), e.message); }
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

boot();
