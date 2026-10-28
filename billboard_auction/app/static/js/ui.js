// Shared UI helpers: formatting, escaping, countdown clock, split-flap display,
// the procedural billboard scene, and toasts.

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
export const money = (n) => (n === null || n === undefined ? "" : inr.format(n));
export const num = (n) => new Intl.NumberFormat("en-IN").format(n);

const dayFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });
const dayYearFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
export const fmtDateTime = (iso) => dateTimeFmt.format(new Date(iso));
export const fmtDay = (iso) => dayYearFmt.format(new Date(iso));
export const fmtRange = (a, b) => `${dayFmt.format(new Date(a))} to ${dayYearFmt.format(new Date(b))}`;

export function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

// ---------- Clock (server-corrected countdowns) ----------
let skew = 0; // server time minus browser time, in ms
export function syncClock(serverIso) {
  if (serverIso) skew = new Date(serverIso).getTime() - Date.now();
}
export const nowMs = () => Date.now() + skew;

function fmtRemaining(ms) {
  if (ms <= 0) return "Closing";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  if (d >= 2) return `${d} days ${h} h`;
  if (d === 1) return `1 day ${h} h`;
  return `${p(h)}:${p(m)}:${p(sec)}`;
}

export const countdown = (endIso, extra = "") =>
  `<span class="clock ${extra}" data-end="${esc(endIso)}">${fmtRemaining(new Date(endIso).getTime() - nowMs())}</span>`;

let clockTimer;
export function startClock() {
  if (clockTimer) return;
  const tick = () => {
    document.querySelectorAll("[data-end]").forEach((el) => {
      const ms = new Date(el.dataset.end).getTime() - nowMs();
      el.textContent = fmtRemaining(ms);
      el.classList.toggle("is-urgent", ms < 3600_000);
    });
  };
  tick();
  clockTimer = setInterval(tick, 1000);
}

// ---------- Split-flap display ----------
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isDigit = (c) => /[0-9]/.test(c);

export function flap(el, text) {
  if (!el || el.dataset.v === text) return;
  const chars = [...text];
  const fresh = el.children.length !== chars.length;
  el.dataset.v = text;
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", text);
  if (fresh) {
    el.innerHTML = chars
      .map((c) => `<span class="flap__cell ${isDigit(c) ? "" : "flap__cell--sym"}" data-c="${esc(c)}" aria-hidden="true"><span>${esc(c)}</span></span>`)
      .join("");
    return;
  }
  chars.forEach((c, i) => {
    const cell = el.children[i];
    if (cell.dataset.c === c) return;
    cell.dataset.c = c;
    const face = cell.firstElementChild;
    if (!isDigit(c) || reduceMotion()) { face.textContent = c; return; }
    let n = 0;
    const spin = setInterval(() => {
      face.textContent = n < 4 ? String(Math.floor(Math.random() * 10)) : c;
      cell.classList.remove("is-rolling");
      void cell.offsetWidth; // restart the animation
      cell.classList.add("is-rolling");
      if (++n > 4) clearInterval(spin);
    }, 70);
  });
}

export function announce(text) {
  const el = document.getElementById("announcer");
  if (el) { el.textContent = ""; setTimeout(() => (el.textContent = text), 50); }
}

// ---------- Toasts ----------
export function toast(message, kind = "info", ms = 4500) {
  const wrap = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast toast--${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---------- Procedural billboard scene ----------
function rng(seed) {
  let s = (seed * 2654435761) >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

const SKIES = [
  ["#b9dcec", "#eaf4f1"],
  ["#f2c8a4", "#fbe9d2"],
  ["#9fb9d6", "#dbe7f2"],
  ["#b4d8c8", "#eef5ee"],
];

// A roadside scene whose structure follows the billboard's format. The face is
// yellow with hazard edging and reads "Your ad here" while the slot is open.
export function scene(a, { sold = false } = {}) {
  const bb = a.billboard;
  const r = rng(a.id * 97 + bb.id * 13);
  const [s1, s2] = SKIES[Math.floor(r() * SKIES.length)];
  const uid = `s${a.id}`;
  const W = 640, H = 360;

  let skyline = "";
  let x = -10;
  while (x < W) {
    const w = 30 + r() * 46, h = 50 + r() * 120;
    skyline += `<rect x="${x.toFixed(0)}" y="${(300 - h).toFixed(0)}" width="${w.toFixed(0)}" height="${h.toFixed(0)}" fill="${r() > 0.5 ? "#9db5b0" : "#8aa39e"}"/>`;
    x += w + 2 + r() * 6;
  }

  const fmt = (bb.format || "").toLowerCase();
  const label = sold ? "Booked" : "Your ad here";
  const lit = bb.illuminated;
  let structure = "", face = { x: 170, y: 64, w: 300, h: 118 };
  const pole = "#55646a";

  if (fmt.includes("gantry")) {
    face = { x: 90, y: 92, w: 460, h: 86 };
    structure = `<rect x="104" y="170" width="14" height="132" fill="${pole}"/><rect x="522" y="170" width="14" height="132" fill="${pole}"/>
      <path d="M96 182H544M96 182l46 28M544 182l-46 28M142 210h356" stroke="${pole}" stroke-width="5" fill="none"/>`;
  } else if (fmt.includes("hoarding")) {
    face = { x: 120, y: 150, w: 400, h: 120 };
    structure = `<rect x="150" y="268" width="12" height="34" fill="${pole}"/><rect x="478" y="268" width="12" height="34" fill="${pole}"/>
      <rect x="318" y="268" width="12" height="34" fill="${pole}"/>`;
  } else if (fmt.includes("shelter")) {
    face = { x: 250, y: 190, w: 140, h: 96 };
    structure = `<rect x="236" y="172" width="168" height="12" fill="${pole}"/><rect x="244" y="284" width="152" height="8" fill="${pole}"/>
      <rect x="244" y="184" width="6" height="104" fill="${pole}"/><rect x="390" y="184" width="6" height="104" fill="${pole}"/>`;
  } else if (fmt.includes("led") || fmt.includes("digital")) {
    face = { x: 150, y: 72, w: 340, h: 130 };
    structure = `<rect x="312" y="200" width="16" height="102" fill="${pole}"/><rect x="296" y="296" width="48" height="8" fill="${pole}"/>`;
  } else {
    structure = `<rect x="312" y="180" width="16" height="122" fill="${pole}"/><rect x="298" y="294" width="44" height="10" fill="${pole}"/>
      <path d="M320 182l-70 -6M320 182l70 -6" stroke="${pole}" stroke-width="4"/>`;
  }

  const { x: fx, y: fy, w: fw, h: fh } = face;
  const isLed = fmt.includes("led") || fmt.includes("digital");
  const fs = Math.max(12, Math.min(fh * 0.32, fw * 0.11));
  const faceSvg = isLed
    ? `<rect x="${fx}" y="${fy}" width="${fw}" height="${fh}" rx="6" fill="#12181b"/>
       <rect x="${fx + 6}" y="${fy + 6}" width="${fw - 12}" height="${fh - 12}" fill="url(#px${uid})"/>
       <text x="${fx + fw / 2}" y="${fy + fh / 2 + fs * 0.34}" text-anchor="middle" font-family="Barlow Condensed, Arial Narrow, sans-serif" font-weight="700" font-size="${fs}" fill="#ffc400">${label}</text>`
    : `<rect x="${fx}" y="${fy}" width="${fw}" height="${fh}" rx="4" fill="url(#hz${uid})"/>
       <rect x="${fx + 9}" y="${fy + 9}" width="${fw - 18}" height="${fh - 18}" fill="#ffc400"/>
       <text x="${fx + fw / 2}" y="${fy + fh / 2 + fs * 0.34}" text-anchor="middle" font-family="Barlow Condensed, Arial Narrow, sans-serif" font-weight="700" font-size="${fs}" fill="#1e2326">${label}</text>`;

  const lamps = lit && !isLed
    ? [0.2, 0.5, 0.8].map((t) => {
        const lx = fx + fw * t;
        return `<rect x="${lx - 5}" y="${fy - 9}" width="10" height="6" fill="#3b464b"/><path d="M${lx - 5} ${fy - 3}L${lx - 26} ${fy + 22}H${lx + 26}L${lx + 5} ${fy - 3}Z" fill="#ffe27a" opacity="0.22"/>`;
      }).join("")
    : "";

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(bb.format)} billboard illustration" preserveAspectRatio="xMidYMid slice">
    <defs>
      <linearGradient id="sky${uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s1}"/><stop offset="1" stop-color="${s2}"/></linearGradient>
      <pattern id="hz${uid}" width="16" height="16" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="16" height="16" fill="#ffc400"/><rect width="8" height="16" fill="#1e2326"/></pattern>
      <pattern id="px${uid}" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="4" fill="#1b2328"/><rect x="1" y="1" width="2" height="2" fill="#27333a"/></pattern>
    </defs>
    <rect width="${W}" height="${H}" fill="url(#sky${uid})"/>
    ${skyline}
    <rect y="300" width="${W}" height="${H - 300}" fill="#2b3134"/>
    <rect y="298" width="${W}" height="4" fill="#d8d8d0"/>
    <path d="M0 336H${W}" stroke="#e8e6c8" stroke-width="3" stroke-dasharray="34 26"/>
    ${structure}${lamps}${faceSvg}
  </svg>`;
}

export const chipForState = (a) => {
  const y = a.you;
  if (!y) return "";
  if (y.is_owner) return `<span class="chip chip--dark">Your listing</span>`;
  if (a.status === "open" && y.is_leading) return `<span class="chip chip--green">You're leading</span>`;
  if (a.status === "open" && y.has_bid) return `<span class="chip chip--red">You've been outbid</span>`;
  if (y.won) return `<span class="chip chip--green">You won</span>`;
  return "";
};
