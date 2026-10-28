import { api, session, setUnauthorizedHandler } from "./api.js";
import { esc, startClock, timeAgo, toast } from "./ui.js";
import { home, auctionPage, loginPage, registerPage, dashboard, sellPage } from "./views.js";

const root = document.getElementById("app");
const headerEl = document.getElementById("header");

const ROUTES = [
  { re: /^\/$/, view: home },
  { re: /^\/auction\/(\d+)$/, view: auctionPage, params: ["id"] },
  { re: /^\/login$/, view: loginPage, guest: true },
  { re: /^\/register$/, view: registerPage, guest: true },
  { re: /^\/dashboard$/, view: dashboard, auth: true },
  { re: /^\/sell$/, view: sellPage, auth: true, role: "owner" },
];

let cleanup = null;
let unreadTimer = null;
let navToken = 0;

const user = () => session.user;
export const go = (path) => { location.hash = `#${path}`; };

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  // "#market" style anchors are in-page jumps on the home screen, not routes.
  if (!raw.startsWith("/")) return null;
  const [path, qs] = raw.split("?");
  return { path, query: Object.fromEntries(new URLSearchParams(qs || "")) };
}

async function render() {
  const parsed = parseHash();
  if (!parsed) { // in-page anchor
    document.getElementById(location.hash.slice(1))?.scrollIntoView();
    return;
  }
  const token = ++navToken;
  if (cleanup) { try { cleanup(); } catch {} cleanup = null; }

  const route = ROUTES.find((r) => r.re.test(parsed.path));
  if (!route) {
    root.innerHTML = `<div class="container section"><div class="empty"><h3>That page doesn't exist</h3><a class="btn btn--dark" href="#/">Back to open auctions</a></div></div>`;
    return;
  }
  const u = user();
  if (route.auth && !u) { sessionStorage.setItem("bidboard.next", location.hash.slice(1)); go("/login"); return; }
  if (route.guest && u) { go("/"); return; }
  if (route.role && u?.role !== route.role) {
    root.innerHTML = `<div class="container section"><div class="empty"><h3>This is for billboard owners</h3><p class="muted">Your account is set up to bid on slots. Create an owner account to list billboards.</p><a class="btn btn--dark" href="#/dashboard">Go to your dashboard</a></div></div>`;
    return;
  }

  const match = parsed.path.match(route.re);
  const params = {};
  (route.params || []).forEach((k, i) => (params[k] = match[i + 1]));
  root.innerHTML = `<div class="container section"><div class="skeleton" style="min-height:240px"></div></div>`;
  window.scrollTo(0, 0);
  renderHeader(parsed.path);

  const ctx = { query: parsed.query, params, user: u, go, onAuth: () => { renderHeader(); startNotifications(); } };
  try {
    const result = await route.view(root, ctx);
    if (token !== navToken) { if (typeof result === "function") result(); return; } // navigated away mid-load
    cleanup = typeof result === "function" ? result : null;
  } catch (e) {
    console.error(e);
    root.innerHTML = `<div class="container section"><div class="empty"><h3>Something went wrong</h3><p class="muted">${esc(e.message || "Try reloading the page.")}</p><button class="btn btn--dark" onclick="location.reload()">Reload</button></div></div>`;
  }
  root.focus({ preventScroll: true });
}

// ---------- Header ----------
let menuOpen = null; // "user" | "notes" | null
let notes = { unread: 0, items: [] };

function renderHeader(path = parseHash()?.path || "/") {
  const u = user();
  const cur = (p) => (path === p ? ' aria-current="page"' : "");
  headerEl.innerHTML = `<div class="container site-header__row">
    <a class="brand" href="#/" aria-label="Bidboard home">
      <svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#0E5A43"/><rect x="5" y="7" width="22" height="11" rx="1" fill="#FFC400"/><rect x="14.5" y="18" width="3" height="9" fill="#fff"/></svg>Bidboard</a>
    <nav class="nav" aria-label="Main">
      <a href="#/"${cur("/")}>Auctions</a>
      ${u?.role === "owner" ? `<a href="#/sell"${cur("/sell")}>List a billboard</a>` : ""}
      ${u ? `<a href="#/dashboard"${cur("/dashboard")}>${u.role === "owner" ? "My billboards" : "My bids"}</a>` : ""}
      <a href="#/?section=how">How it works</a>
    </nav>
    <div class="header-actions">
      ${u ? `
        <button class="icon-btn" id="bell" aria-label="Notifications${notes.unread ? `, ${notes.unread} unread` : ""}" aria-haspopup="true" aria-expanded="${menuOpen === "notes"}">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
          ${notes.unread ? `<span class="badge-dot">${notes.unread > 9 ? "9+" : notes.unread}</span>` : ""}</button>
        <button class="user-chip" id="userbtn" aria-haspopup="true" aria-expanded="${menuOpen === "user"}"><span class="avatar" aria-hidden="true">${esc((u.name || "?")[0].toUpperCase())}</span><span>${esc(u.name.split(" ")[0])}</span></button>
        ${menuOpen === "user" ? `<div class="menu" role="menu"><div style="padding:10px 12px"><strong>${esc(u.name)}</strong><div class="muted" style="font-size:var(--step--1)">${esc(u.email)}</div><div class="chip" style="margin-top:6px">${u.role === "owner" ? "Billboard owner" : "Advertiser"}</div></div><div class="menu__sep"></div>
          <a href="#/dashboard" role="menuitem">Dashboard</a><button id="logout" role="menuitem" type="button">Sign out</button></div>` : ""}
        ${menuOpen === "notes" ? `<div class="menu menu--wide"><div class="menu__head"><span>Notifications</span><button id="markread" type="button">Mark all read</button></div>
          ${notes.items.length ? notes.items.map((n) => `<a class="note-item ${n.read ? "" : "is-unread"}" href="#/${n.auction_id ? `auction/${n.auction_id}` : "dashboard"}"><strong>${esc(n.title)}</strong><span>${esc(n.body)} ${esc(timeAgo(n.created_at))}</span></a>`).join("") : `<div class="menu__empty">Nothing yet. You'll hear here when you're outbid or win a slot.</div>`}</div>` : ""}
      ` : `<a class="btn btn--ghost" href="#/login">Sign in</a><a class="btn btn--dark" href="#/register">Create account</a>`}
    </div></div>`;

  document.getElementById("bell")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    menuOpen = menuOpen === "notes" ? null : "notes";
    if (menuOpen) await loadNotes();
    renderHeader();
  });
  document.getElementById("userbtn")?.addEventListener("click", (e) => { e.stopPropagation(); menuOpen = menuOpen === "user" ? null : "user"; renderHeader(); });
  document.getElementById("logout")?.addEventListener("click", () => {
    session.clear(); menuOpen = null; notes = { unread: 0, items: [] }; clearInterval(unreadTimer); renderHeader(); go("/");
    if (location.hash === "#/") render();
  });
  document.getElementById("markread")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    try { await api("/notifications/read", { method: "POST" }); await loadNotes(); renderHeader(); } catch {}
  });
}

document.addEventListener("click", () => { if (menuOpen) { menuOpen = null; renderHeader(); } });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menuOpen) { menuOpen = null; renderHeader(); } });

async function loadNotes() {
  try { notes = await api("/notifications"); } catch {}
}

function startNotifications() {
  clearInterval(unreadTimer);
  if (!user()) return;
  let lastUnread = null;
  const poll = async () => {
    if (document.hidden || !user()) return;
    const before = notes.unread;
    await loadNotes();
    if (lastUnread !== null && notes.unread > before) {
      const newest = notes.items.find((n) => !n.read);
      if (newest) toast(newest.title, newest.kind === "outbid" || newest.kind === "rejected" ? "error" : "ok", 6000);
    }
    lastUnread = notes.unread;
    if (!menuOpen) renderHeader();
  };
  poll();
  unreadTimer = setInterval(poll, 15000);
}

setUnauthorizedHandler(() => {
  session.clear(); renderHeader(); toast("Your session ended. Sign in again to continue.", "error");
  sessionStorage.setItem("bidboard.next", location.hash.slice(1) || "/");
  go("/login");
});

window.addEventListener("hashchange", () => { menuOpen = null; render(); });
startClock();
renderHeader();
startNotifications();
render();
