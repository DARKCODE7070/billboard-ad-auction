import { api, session, ApiError } from "./api.js";
import {
  esc, money, num, fmtDateTime, fmtDay, fmtRange, timeAgo, syncClock, countdown, flap, announce, toast, scene, chipForState,
} from "./ui.js";

// A view is (root, ctx) => optional cleanup function.
// ctx: { query, params, go(path), user, onAuth() }

const $ = (root, sel) => root.querySelector(sel);
const $$ = (root, sel) => [...root.querySelectorAll(sel)];
const errorBox = (e) => `<div class="form-error" role="alert">${esc(e.message || e)}</div>`;
const sig = (items) => items.map((a) => `${a.id}:${a.current_price}:${a.bid_count}:${a.end_at}:${a.status}:${a.you?.is_leading}:${a.you?.has_bid}`).join("|");

// =====================================================================
// Home: hero + marketplace
// =====================================================================
const filters = { q: "", city: "", format: "", max_price: "", ending_within_hours: "", sort: "ending" };

function marketCard(a) {
  const bb = a.billboard, hasBids = a.bid_count > 0, open = a.status === "open";
  const tag = open
    ? `<span class="chip chip--dark">${countdown(a.end_at)}</span>`
    : `<span class="chip chip--dark">Sold</span>`;
  return `<a class="card" href="#/auction/${a.id}">
    <div class="card__media">${scene(a, { sold: !open })}
      <div class="card__tags"><span class="chip chip--white">${esc(bb.format)}</span>${tag}</div>
      ${session.user?.role === "advertiser" ? `<button class="card__hide" data-hide="${a.id}" type="button" title="Hide this listing from your view">Hide</button>` : ""}</div>
    <div class="card__body">
      <div><h3 class="card__title">${esc(bb.title)}</h3><p class="card__where">${esc(bb.address)}, ${esc(bb.city)}</p></div>
      <div class="card__specs"><span>${bb.width_ft} × ${bb.height_ft} ft</span><span>${bb.illuminated ? "Lit at night" : "Daylight only"}</span><span>${num(bb.daily_impressions)} daily views</span></div>
      <div class="plate"><div class="plate__inner">
        <div><div class="plate__label">${open ? (hasBids ? "Current bid" : "Starting bid") : "Sold for"}</div>
          <div class="plate__price">${money(open ? a.current_price ?? a.floor_price : a.final_price)}</div></div>
        <div class="plate__side">${a.bid_count} ${a.bid_count === 1 ? "bid" : "bids"}</div>
      </div></div>
      <div class="card__status">${chipForState(a)}</div>
    </div></a>`;
}

function heroStage(a) {
  if (!a) {
    return `<div class="stage"><div class="stage__scene">${scene({ id: 1, billboard: { id: 1, format: "Unipole", illuminated: true } })}</div></div>`;
  }
  const bb = a.billboard;
  return `<div class="stage">
    <div class="stage__scene">${scene(a)}</div>
    <div class="stage__read on-dark">
      <div class="stage__title"><a href="#/auction/${a.id}">${esc(bb.title)}</a><span>${countdown(a.end_at)}</span></div>
      <div class="stage__meta">
        <div class="flap flap--hero" id="hero-flap"></div>
        <div class="muted-on-dark">${a.bid_count} ${a.bid_count === 1 ? "bid" : "bids"} in ${esc(bb.city)}</div>
      </div>
    </div></div>`;
}

export async function home(root, ctx) {
  root.innerHTML = `
    <section class="hero"><div class="container"><div class="hero__frame">
      <div>
        <h1>Bid for billboard space in your city</h1>
        <p class="hero__lead">Owners list a slot with a starting price and a deadline. Advertisers bid. When the clock runs out, the highest bid wins the slot.</p>
        <div class="hero__cta">
          <a class="btn btn--primary btn--lg" href="#market">Browse open auctions</a>
          <a class="btn btn--on-green btn--lg" href="#/${ctx.user?.role === "owner" ? "sell" : "register"}">${ctx.user?.role === "owner" ? "List a billboard" : "Create an account"}</a>
        </div>
        <div class="hero__stats" id="stats"></div>
      </div>
      <div id="stage"></div>
    </div></div></section>
    <section class="section" id="market"><div class="container">
      <div class="section__head"><h2>Open auctions</h2></div>
      <div class="market">
        <aside class="filters" aria-label="Filters">
          <div><h3>City</h3><div class="facet" id="f-city"></div></div>
          <div><h3>Format</h3><div class="facet" id="f-format"></div></div>
          <div class="field"><label for="f-max">Highest price</label>
            <div class="input-affix"><span>₹</span><input class="input num" id="f-max" inputmode="numeric" placeholder="No limit" value="${esc(filters.max_price)}"></div></div>
          <div class="field"><label for="f-end">Ends within</label>
            <select class="select" id="f-end"><option value="">Any time</option><option value="1">1 hour</option><option value="6">6 hours</option><option value="24">24 hours</option><option value="72">3 days</option></select></div>
          <button class="btn btn--ghost btn--sm" id="f-clear" type="button">Clear filters</button>
        </aside>
        <div>
          <div class="toolbar">
            <input class="input" id="f-q" type="search" placeholder="Search by name, street or city" aria-label="Search billboards" value="${esc(filters.q)}">
            <select class="select" id="f-sort" aria-label="Sort by">
              <option value="ending">Ending soonest</option><option value="price_low">Price: low to high</option>
              <option value="price_high">Price: high to low</option><option value="popular">Most bids</option><option value="newest">Newest</option>
            </select>
          </div>
          <p class="results-count" id="count" aria-live="polite"></p>
          <div class="cards" id="cards"></div>
        </div>
      </div>
    </div></section>
    <section class="how" id="how"><div class="container section">
      <h2>How an auction works</h2>
      <ol class="how__list">
        <li><h3>Owner lists a slot</h3><p class="muted">Pick a billboard, set the starting price, the minimum raise and the deadline.</p></li>
        <li><h3>Advertisers bid</h3><p class="muted">Place a bid, or set a maximum budget and let the system raise your bid for you.</p></li>
        <li><h3>The clock runs out</h3><p class="muted">The highest bid wins and pays that bid. A bid in the last two minutes extends the deadline.</p></li>
        <li><h3>Winner uploads the ad</h3><p class="muted">The winner sends an image or video, the owner approves it, and the slot is booked.</p></li>
      </ol>
    </div></section>`;

  $(root, "#f-end").value = filters.ending_within_hours;
  $(root, "#f-sort").value = filters.sort;

  let lastSig = "", facets = { cities: [], formats: [] }, heroId = null, loaded = false;

  const renderFacets = () => {
    const mk = (list, key) =>
      list.length
        ? list.map((f) => `<button type="button" data-k="${key}" data-v="${esc(f.name)}" aria-pressed="${filters[key] === f.name}"><span>${esc(f.name)}</span><span>${f.count}</span></button>`).join("")
        : `<p class="muted">None yet</p>`;
    $(root, "#f-city").innerHTML = mk(facets.cities, "city");
    $(root, "#f-format").innerHTML = mk(facets.formats, "format");
  };

  const load = async (initial) => {
    try {
      const params = { status: "open", sort: filters.sort, limit: 48, q: filters.q, city: filters.city, format: filters.format,
        max_price: filters.max_price, ending_within_hours: filters.ending_within_hours };
      const [list, st, fc] = await Promise.all([api("/auctions", { query: params }), api("/stats"), initial ? api("/facets") : null]);
      if (fc) { facets = fc; renderFacets(); }
      if (list.items[0]) syncClock(list.items[0].server_time);
      $(root, "#stats").innerHTML = `
        <div class="stat"><strong>${st.open_auctions}</strong><span>auctions open now</span></div>
        <div class="stat"><strong>${st.bidders}</strong><span>advertisers bidding</span></div>
        <div class="stat"><strong>${money(st.sold_value)}</strong><span>booked so far</span></div>`;

      // Hero shows the most contested auction among those ending soonest.
      const all = filters.city || filters.format || filters.q ? await api("/auctions", { query: { status: "open", sort: "ending", limit: 12 } }) : list;
      const featured = [...all.items].sort((a, b) => (b.bid_count > 0) - (a.bid_count > 0) || a.seconds_left - b.seconds_left)[0];
      if (!loaded || featured?.id !== heroId) {
        heroId = featured?.id ?? null;
        $(root, "#stage").innerHTML = heroStage(featured);
      }
      if (featured && $(root, "#hero-flap")) flap($(root, "#hero-flap"), money(featured.current_price ?? featured.floor_price));

      const s = sig(list.items) + `|${list.total}`;
      if (s !== lastSig) {
        lastSig = s;
        $(root, "#count").textContent = `${list.total} ${list.total === 1 ? "auction" : "auctions"}`;
        $(root, "#cards").innerHTML = list.items.length
          ? list.items.map(marketCard).join("")
          : `<div class="empty" style="grid-column:1/-1"><h3>No auctions match</h3><p class="muted">Try a different city or raise the price limit.</p><button class="btn btn--ghost" id="empty-clear" type="button">Clear filters</button></div>`;
        $(root, "#empty-clear")?.addEventListener("click", clear);
      }
      loaded = true;
    } catch (e) {
      if (!loaded) $(root, "#cards").innerHTML = `<div style="grid-column:1/-1">${errorBox(e)}</div>`;
    }
  };

  const clear = () => {
    Object.assign(filters, { q: "", city: "", format: "", max_price: "", ending_within_hours: "", sort: "ending" });
    $(root, "#f-q").value = ""; $(root, "#f-max").value = ""; $(root, "#f-end").value = ""; $(root, "#f-sort").value = "ending";
    renderFacets(); lastSig = ""; load();
  };

  root.addEventListener("click", async (ev) => {
    const h = ev.target.closest("[data-hide]");
    if (h) {
      ev.preventDefault(); ev.stopPropagation();
      try {
        await api(`/auctions/${h.dataset.hide}/hide`, { method: "POST" });
        toast("Hidden from your view. Undo it in Dashboard → Hidden.", "ok");
        lastSig = ""; load();
      } catch (e) { toast(e.message, "error"); }
      return;
    }
    const b = ev.target.closest(".facet button");
    if (b) {
      const k = b.dataset.k;
      filters[k] = filters[k] === b.dataset.v ? "" : b.dataset.v;
      renderFacets(); lastSig = ""; load();
    }
  });
  let t;
  $(root, "#f-q").addEventListener("input", (e) => { filters.q = e.target.value.trim(); clearTimeout(t); t = setTimeout(() => { lastSig = ""; load(); }, 250); });
  $(root, "#f-max").addEventListener("input", (e) => { filters.max_price = e.target.value.replace(/[^\d]/g, ""); clearTimeout(t); t = setTimeout(() => { lastSig = ""; load(); }, 300); });
  $(root, "#f-end").addEventListener("change", (e) => { filters.ending_within_hours = e.target.value; lastSig = ""; load(); });
  $(root, "#f-sort").addEventListener("change", (e) => { filters.sort = e.target.value; lastSig = ""; load(); });
  $(root, "#f-clear").addEventListener("click", clear);

  renderFacets();
  await load(true);
  if (ctx.query.section) document.getElementById(ctx.query.section)?.scrollIntoView();
  const poll = setInterval(() => !document.hidden && load(false), 5000);
  return () => clearInterval(poll);
}

// =====================================================================
// Auction page with live bidding
// =====================================================================
export async function auctionPage(root, ctx) {
  const id = ctx.params.id;
  let a;
  try { a = await api(`/auctions/${id}`); } catch (e) {
    root.innerHTML = `<div class="container section"><div class="empty"><h3>We can't find that auction</h3><p class="muted">${esc(e.message)}</p><a class="btn btn--dark" href="#/">Back to open auctions</a></div></div>`;
    return;
  }
  syncClock(a.server_time);
  const bb = a.billboard;
  root.innerHTML = `
    <div class="container">
      <nav class="crumbs" aria-label="Breadcrumb"><a href="#/">Open auctions</a> / ${esc(bb.city)}</nav>
      <div class="auction">
        <div>
          <div class="auction__scene" id="scene"></div>
          <h1>${esc(bb.title)}</h1>
          <p class="auction__where">${esc(bb.address)}, ${esc(bb.city)}</p>
          <dl class="specs">
            <div class="spec"><dt>Format</dt><dd>${esc(bb.format)}</dd></div>
            <div class="spec"><dt>Size</dt><dd>${bb.width_ft} × ${bb.height_ft} ft</dd></div>
            <div class="spec"><dt>Lighting</dt><dd>${bb.illuminated ? "Lit at night" : "Daylight only"}</dd></div>
            <div class="spec"><dt>Daily views</dt><dd>${num(bb.daily_impressions)}</dd></div>
            <div class="spec"><dt>Display period</dt><dd>${esc(fmtRange(a.slot_start, a.slot_end))}</dd></div>
            <div class="spec"><dt>Owner</dt><dd>${esc(bb.owner_name)}</dd></div>
          </dl>
          ${bb.description ? `<p class="prose">${esc(bb.description)}</p>` : ""}
          <section class="panel" style="margin-top:28px" aria-labelledby="hist-h">
            <div class="panel__head"><h2 id="hist-h">Bid history</h2><span class="muted" id="hist-count"></span></div>
            <div id="history"></div>
          </section>
        </div>
        <aside class="bidbox" aria-label="Bidding" id="bidbox"></aside>
      </div>
    </div>`;

  let seen = new Set(), firstBids = true, wasLeading = a.you?.is_leading, dirty = false, submitting = false;
  const sceneEl = $(root, "#scene");
  const bidbox = $(root, "#bidbox");

  const buildBox = () => {
    const y = a.you, open = a.status === "open";
    let action = "";
    if (!open) {
      if (a.status === "closed") {
        action = `<div class="bidbox__form"><div class="state ${y?.won ? "state--lead" : "state--info"}">${
          y?.won ? `You won this slot for ${money(a.final_price)}.` : `Sold for ${money(a.final_price)} on ${esc(fmtDateTime(a.closed_at))}.`}</div>
          ${y?.won ? `<a class="btn btn--primary btn--block" href="#/dashboard?tab=bookings">Upload your creative</a>` : `<a class="btn btn--ghost btn--block" href="#/">See open auctions</a>`}</div>`;
      } else {
        action = `<div class="bidbox__form"><div class="state state--info">${a.status === "no_sale" ? "This auction ended without bids." : "The owner cancelled this auction."}</div></div>`;
      }
    } else if (!ctx.user) {
      action = `<div class="bidbox__form"><p class="muted">Create an advertiser account to bid on this slot.</p>
        <a class="btn btn--primary btn--block" href="#/login" data-next>Sign in to bid</a><a class="btn btn--ghost btn--block" href="#/register" data-next>Create an account</a></div>`;
    } else if (y?.is_owner) {
      action = `<div class="bidbox__form"><div class="state state--info">This is your listing. You can't bid on it.</div>
        ${a.bid_count === 0 ? `<button class="btn btn--danger btn--block" id="cancel" type="button">Cancel this auction</button>` : ""}</div>`;
    } else if (ctx.user.role !== "advertiser") {
      action = `<div class="bidbox__form"><div class="state state--info">Bidding needs an advertiser account.</div></div>`;
    } else {
      const leading = y?.is_leading;
      action = `<form class="bidbox__form" id="bidform" novalidate>
        <div id="state"></div>
        ${leading ? `
          <div class="field"><label for="max">Raise your maximum budget</label>
            <div class="input-affix"><span>₹</span><input class="input num" id="max" inputmode="numeric" autocomplete="off"></div>
            <small>You'd pay no more than this. Your current maximum is ${money(y.my_ceiling)}.</small></div>`
        : `
          <div class="field"><label for="amount">Your bid</label>
            <div class="input-affix"><span>₹</span><input class="input num" id="amount" inputmode="numeric" autocomplete="off"></div>
            <div class="quick" id="quick"></div></div>
          <label class="check"><input type="checkbox" id="proxy"> Auto-bid up to a maximum</label>
          <div class="field" id="max-wrap" hidden><label for="max">Maximum budget</label>
            <div class="input-affix"><span>₹</span><input class="input num" id="max" inputmode="numeric" autocomplete="off"></div>
            <small>When someone outbids you, we raise your bid by the minimum step, up to this limit. You pay your winning bid, never more than your maximum.</small></div>`}
        <div id="form-msg" aria-live="polite"></div>
        <button class="btn btn--primary btn--lg btn--block" id="submit" type="submit">${leading ? "Raise maximum" : "Place bid"}</button>
        <p class="muted" style="font-size:var(--step--1)">Bids can't be withdrawn. A bid in the last ${Math.round(a.anti_snipe_seconds / 60)} minutes extends the deadline.</p>
      </form>`;
    }

    bidbox.innerHTML = `
      <div class="bidbox__plate"><div class="bidbox__plate-in">
        <div class="bidbox__row"><span>${a.bid_count ? (a.status === "open" ? "Current bid" : "Final price") : "Starting bid"}</span><span id="bid-count">${a.bid_count} ${a.bid_count === 1 ? "bid" : "bids"}</span></div>
        <div class="flap flap--panel" id="price-flap"></div>
        <div class="bidbox__row"><span>${a.status === "open" ? "Time left" : "Status"}</span>
          <span class="bidbox__time">${a.status === "open" ? countdown(a.end_at) : { closed: "Sold", no_sale: "No sale", cancelled: "Cancelled" }[a.status]}</span></div>
      </div></div>
      <div class="panel">${action}</div>`;
    flap($(bidbox, "#price-flap"), money(a.status === "closed" ? a.final_price : a.current_price ?? a.floor_price));
    bindForm();
  };

  const setDefaults = () => {
    const amount = $(bidbox, "#amount");
    if (amount && !dirty) amount.value = a.next_min_bid;
    const quick = $(bidbox, "#quick");
    if (quick) {
      quick.innerHTML = [0, 1, 3].map((n) => {
        const v = a.next_min_bid + a.min_increment * n;
        return `<button type="button" data-v="${v}">${n === 0 ? "Minimum " : "+" + money(a.min_increment * n) + " "}${n === 0 ? money(v) : ""}</button>`;
      }).join("");
    }
    const max = $(bidbox, "#max");
    if (max && !dirty && !max.value && a.you?.is_leading) max.placeholder = `More than ${num(a.you.my_ceiling)}`;
  };

  const showState = () => {
    const el = $(bidbox, "#state");
    if (!el || !a.you) return;
    el.innerHTML = a.you.is_leading
      ? `<div class="state state--lead">You're leading at ${money(a.current_price)}.</div>`
      : a.you.has_bid ? `<div class="state state--out">You've been outbid. The lowest bid now is ${money(a.next_min_bid)}.</div>` : "";
  };

  function bindForm() {
    $(bidbox, "#cancel")?.addEventListener("click", async () => {
      if (!confirm("Cancel this auction? This can't be undone.")) return;
      try { await api(`/auctions/${id}/cancel`, { method: "POST" }); toast("Auction cancelled.", "ok"); ctx.go("/dashboard"); }
      catch (e) { toast(e.message, "error"); }
    });
    $$(bidbox, "[data-next]").forEach((l) => l.addEventListener("click", () => sessionStorage.setItem("bidboard.next", `/auction/${id}`)));
    const form = $(bidbox, "#bidform");
    if (!form) return;
    dirty = false;
    setDefaults(); showState();
    form.addEventListener("input", () => (dirty = true));
    $(bidbox, "#proxy")?.addEventListener("change", (e) => { $(bidbox, "#max-wrap").hidden = !e.target.checked; if (e.target.checked) $(bidbox, "#max").focus(); });
    $(bidbox, "#quick")?.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { $(bidbox, "#amount").value = b.dataset.v; dirty = true; } });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (submitting) return;
      const leading = a.you?.is_leading;
      const parse = (v) => (v ? Number(String(v).replace(/[^\d.]/g, "")) : null);
      const maxVal = parse($(bidbox, "#max")?.value);
      const useMax = leading || $(bidbox, "#proxy")?.checked;
      const amount = leading ? a.current_price : parse($(bidbox, "#amount").value);
      const msg = $(bidbox, "#form-msg"), btn = $(bidbox, "#submit");
      if (!amount) { msg.innerHTML = errorBox("Enter an amount."); return; }
      if (useMax && !maxVal) { msg.innerHTML = errorBox("Enter your maximum budget."); return; }
      submitting = true; btn.disabled = true; msg.innerHTML = "";
      try {
        const res = await api(`/auctions/${id}/bids`, { method: "POST", body: { amount, max_amount: useMax ? maxVal : null } });
        a = res.auction; syncClock(a.server_time); dirty = false;
        toast(res.message, res.is_leading ? "ok" : "info");
        if (res.extended) toast(`Deadline extended by ${Math.round(a.anti_snipe_seconds / 60)} minutes because of a late bid.`);
        wasLeading = a.you?.is_leading;
        buildBox(); await refreshBids();
      } catch (err) {
        msg.innerHTML = errorBox(err);
        if (err.code === "BID_TOO_LOW" || err.code === "AUCTION_CLOSED") await refresh();
      } finally { submitting = false; const b = $(bidbox, "#submit"); if (b) b.disabled = false; }
    });
  }

  const renderHistory = (data) => {
    $(root, "#hist-count").textContent = data.items.length ? `${data.bidders} ${data.bidders === 1 ? "bidder" : "bidders"}` : "";
    if (!data.items.length) {
      $(root, "#history").innerHTML = `<div class="empty" style="border:0"><h3>No bids yet</h3><p class="muted">The first bid at ${money(a.floor_price)} or more takes the lead.</p></div>`;
      return;
    }
    $(root, "#history").innerHTML = `<table class="history"><thead><tr><th scope="col">Bidder</th><th scope="col">When</th><th scope="col">Bid</th><th scope="col">Price after</th></tr></thead><tbody>${
      data.items.map((b) => `<tr class="${b.mine ? "is-mine" : ""} ${!firstBids && !seen.has(b.seq) ? "is-new" : ""}">
        <td>${esc(b.bidder)}${b.mine ? " (you)" : ""}</td><td class="muted">${esc(timeAgo(b.created_at))}</td>
        <td class="num">${money(b.amount)}</td><td class="num">${money(b.price_after)}</td></tr>`).join("")}</tbody></table>`;
    data.items.forEach((b) => seen.add(b.seq));
    firstBids = false;
  };

  const refreshBids = async () => { try { renderHistory(await api(`/auctions/${id}/bids`)); } catch {} };

  const refresh = async () => {
    try {
      const next = await api(`/auctions/${id}`);
      syncClock(next.server_time);
      const changed = next.current_price !== a.current_price || next.bid_count !== a.bid_count || next.status !== a.status || next.end_at !== a.end_at;
      const statusChanged = next.status !== a.status || next.you?.is_leading !== a.you?.is_leading;
      if (wasLeading && next.you && !next.you.is_leading && next.status === "open") { toast("You've been outbid.", "error"); announce("You've been outbid."); }
      wasLeading = next.you?.is_leading;
      a = next;
      if (statusChanged) buildBox();
      else if (changed) {
        flap($(bidbox, "#price-flap"), money(a.status === "closed" ? a.final_price : a.current_price ?? a.floor_price));
        $(bidbox, "#bid-count").textContent = `${a.bid_count} ${a.bid_count === 1 ? "bid" : "bids"}`;
        const c = $(bidbox, "[data-end]"); if (c) c.dataset.end = a.end_at;
        setDefaults(); showState();
        announce(`Current bid is now ${money(a.current_price)}.`);
      }
      if (changed) { await refreshBids(); sceneEl.innerHTML = scene(a, { sold: a.status === "closed" }); }
    } catch {}
  };

  sceneEl.innerHTML = scene(a, { sold: a.status === "closed" });
  buildBox();
  await refreshBids();
  const poll = setInterval(() => !document.hidden && refresh(), 3000);
  const tickOver = setInterval(() => { // when the clock hits zero, check right away
    if (a.status === "open" && new Date(a.end_at).getTime() <= Date.now() + 0) refresh();
  }, 4000);
  return () => { clearInterval(poll); clearInterval(tickOver); };
}

// =====================================================================
// Auth
// =====================================================================
function afterAuth(ctx) {
  ctx.onAuth();
  const next = sessionStorage.getItem("bidboard.next");
  sessionStorage.removeItem("bidboard.next");
  ctx.go(next || "/");
}

export function loginPage(root, ctx) {
  const local = ["localhost", "127.0.0.1"].includes(location.hostname);
  root.innerHTML = `<div class="container"><div class="panel auth">
    <h1>Sign in</h1><p class="muted">Welcome back. Pick up your bids where you left them.</p>
    <form id="f" novalidate>
      <div class="field"><label for="email">Email</label><input class="input" id="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="pw">Password</label><input class="input" id="pw" type="password" autocomplete="current-password" required></div>
      <div id="msg" aria-live="polite"></div>
      <button class="btn btn--primary btn--lg" type="submit">Sign in</button>
    </form>
    <p style="margin-top:18px">New here? <a href="#/register">Create an account</a></p>
    ${local ? `<div class="demo">Demo accounts (password123): <button type="button" data-e="chaipoint@demo.test">advertiser</button>, <button type="button" data-e="meridian@demo.test">billboard owner</button></div>` : ""}
  </div></div>`;
  $(root, "#email").focus();
  $$(root, ".demo button").forEach((b) => b.addEventListener("click", () => { $(root, "#email").value = b.dataset.e; $(root, "#pw").value = "password123"; }));
  $(root, "#f").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter; btn.disabled = true;
    try {
      const res = await api("/auth/login", { method: "POST", body: { email: $(root, "#email").value, password: $(root, "#pw").value } });
      session.set(res); afterAuth(ctx);
    } catch (err) { $(root, "#msg").innerHTML = errorBox(err); btn.disabled = false; }
  });
}

export function registerPage(root, ctx) {
  root.innerHTML = `<div class="container"><div class="panel auth">
    <h1>Create an account</h1><p class="muted">Choose how you'll use Bidboard. You can't switch later, so pick the one that fits.</p>
    <form id="f" novalidate>
      <fieldset style="border:0;padding:0;margin:0"><legend class="label" style="font-weight:600;font-size:var(--step--1);margin-bottom:6px">I want to</legend>
        <div class="role-pick">
          <label><input type="radio" name="role" value="advertiser" checked><strong>Advertise</strong><span class="muted">Bid on billboard slots</span></label>
          <label><input type="radio" name="role" value="owner"><strong>List billboards</strong><span class="muted">Auction the space I own</span></label>
        </div></fieldset>
      <div class="field"><label for="name">Your name</label><input class="input" id="name" autocomplete="name" required></div>
      <div class="field"><label for="company">Company <span class="muted">(optional)</span></label><input class="input" id="company" autocomplete="organization"></div>
      <div class="field"><label for="email">Email</label><input class="input" id="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="pw">Password</label><input class="input" id="pw" type="password" autocomplete="new-password" minlength="8" required><small>At least 8 characters.</small></div>
      <div id="msg" aria-live="polite"></div>
      <button class="btn btn--primary btn--lg" type="submit">Create account</button>
    </form>
    <p style="margin-top:18px">Already registered? <a href="#/login">Sign in</a></p>
  </div></div>`;
  $(root, "#name").focus();
  $(root, "#f").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter; btn.disabled = true;
    try {
      const res = await api("/auth/register", { method: "POST", body: {
        role: root.querySelector("input[name=role]:checked").value, name: $(root, "#name").value, company: $(root, "#company").value || null,
        email: $(root, "#email").value, password: $(root, "#pw").value } });
      session.set(res); afterAuth(ctx);
    } catch (err) { $(root, "#msg").innerHTML = errorBox(err); btn.disabled = false; }
  });
}

// =====================================================================
// Dashboard
// =====================================================================
const STATE_CHIP = {
  leading: `<span class="chip chip--green">You're leading</span>`,
  outbid: `<span class="chip chip--red">You've been outbid</span>`,
  won: `<span class="chip chip--green">You won</span>`,
  lost: `<span class="chip">Not won</span>`,
};
const PLACEMENT_CHIP = {
  awaiting_creative: `<span class="chip chip--yellow">Waiting for creative</span>`,
  in_review: `<span class="chip chip--yellow">In review</span>`,
  approved: `<span class="chip chip--green">Approved</span>`,
  rejected: `<span class="chip chip--red">Changes requested</span>`,
};
const AUCTION_CHIP = {
  open: `<span class="chip chip--green">Open</span>`, closed: `<span class="chip chip--dark">Sold</span>`,
  no_sale: `<span class="chip">No sale</span>`, cancelled: `<span class="chip">Cancelled</span>`,
};

const empty = (title, body, cta = "") => `<div class="empty"><h3>${esc(title)}</h3><p class="muted">${esc(body)}</p>${cta}</div>`;

function auctionRow(a, { mine } = {}) {
  const bb = a.billboard, open = a.status === "open";
  const price = open ? a.current_price ?? a.floor_price : a.final_price;
  return `<div class="row">
    <div class="row__thumb">${scene(a, { sold: a.status === "closed" })}</div>
    <div><div class="row__title"><a href="#/auction/${a.id}">${esc(bb.title)}</a></div>
      <div class="row__meta"><span>${esc(bb.address)}, ${esc(bb.city)}</span><span>${esc(fmtRange(a.slot_start, a.slot_end))}</span></div>
      <div class="row__chips">${mine ? STATE_CHIP[a.my_state] || "" : AUCTION_CHIP[a.status]}
        ${open ? `<span class="chip chip--dark">${countdown(a.end_at)}</span>` : ""}
        <span class="chip">${a.bid_count} ${a.bid_count === 1 ? "bid" : "bids"}</span>
        ${mine && open && a.you?.my_ceiling && a.you.my_ceiling > (a.current_price || 0) ? `<span class="chip">Your maximum ${money(a.you.my_ceiling)}</span>` : ""}</div></div>
    <div class="row__side"><div><div class="muted" style="font-size:var(--step--1)">${open ? (a.bid_count ? "Current bid" : "Starting bid") : "Sold for"}</div><div class="row__price">${money(price)}</div></div>
      ${!mine && open && a.bid_count === 0 ? `<button class="btn btn--danger btn--sm" data-cancel="${a.id}" type="button">Cancel</button>` : `<a class="btn btn--ghost btn--sm" href="#/auction/${a.id}">${open && mine ? "Open auction" : "View"}</a>`}</div>
  </div>`;
}

async function loadCreative(placementId, host) {
  try {
    const res = await api(`/placements/${placementId}/creative`, { raw: true });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    host.innerHTML = blob.type.startsWith("video/")
      ? `<video class="preview" src="${url}" controls preload="metadata"></video>`
      : `<img class="preview" src="${url}" alt="Uploaded creative">`;
  } catch { host.innerHTML = ""; }
}

function bookingRow(p, role) {
  const bb = p.billboard;
  const due = `Upload by ${fmtDateTime(p.creative_due_at)}`;
  let side = "";
  if (role === "advertiser") {
    if (p.status === "approved") side = `<div class="muted">Your ad is booked for this slot.</div>`;
    else side = `<label class="drop" data-drop="${p.id}"><strong>${p.has_creative ? "Replace creative" : "Upload your creative"}</strong>
      <span class="muted">PNG, JPEG, WebP or MP4, up to 50 MB</span><input type="file" accept="image/png,image/jpeg,image/webp,video/mp4"></label><div data-msg="${p.id}"></div>`;
  } else {
    const unpaid = p.payment_status !== "paid" && p.status !== "approved";
    const pay = unpaid ? `<div class="field"><label for="ref-${p.id}">Payment reference <span class="muted">(UPI/UTR, optional)</span></label><input class="input" id="ref-${p.id}" type="text" maxlength="120"></div>
      <button class="btn btn--primary btn--sm" data-pay="${p.id}" type="button" style="margin-bottom:14px">Mark as paid (${money(p.amount)})</button>` : "";
    const review = p.status === "in_review" ? `<div class="field"><label for="note-${p.id}">Note to advertiser <span class="muted">(needed to request changes)</span></label><textarea class="textarea" id="note-${p.id}" rows="2"></textarea></div>
      <div style="display:flex;gap:8px"><button class="btn btn--primary btn--sm" data-review="approve" data-id="${p.id}" type="button">Approve${unpaid ? " (after payment)" : ""}</button>
      <button class="btn btn--ghost btn--sm" data-review="reject" data-id="${p.id}" type="button">Request changes</button></div>` : "";
    side = (pay || review) ? `${pay}${review}<div data-msg="${p.id}"></div>`
      : p.status === "approved" ? `<div class="muted">Approved. Ready to display.</div>`
      : `<div class="muted">Waiting for ${esc(p.advertiser_name)} to upload.</div>`;
  }
  return `<div class="row booking">
    <div><div class="row__title"><a href="#/auction/${p.auction_id}">${esc(bb.title)}</a></div>
      <div class="row__meta"><span>${esc(bb.city)}</span><span>${esc(fmtRange(p.slot_start, p.slot_end))}</span><span>${role === "owner" ? esc(p.advertiser_name) : `Owner ${esc(bb.owner_name)}`}</span><span>${money(p.amount)}</span></div>
      <div class="row__chips">${p.payment_status === "paid" ? `<span class="chip chip--green">Paid${p.payment_ref ? " · " + esc(p.payment_ref) : ""}</span>` : `<span class="chip chip--red">Payment pending</span>`}${PLACEMENT_CHIP[p.status]}${p.status === "awaiting_creative" ? `<span class="chip">${esc(due)}</span>` : ""}</div>
      ${p.review_note && p.status === "rejected" ? `<p class="state state--out" style="margin-top:10px">${esc(p.review_note)}</p>` : ""}
      <div data-preview="${p.id}" style="margin-top:12px;max-width:360px"></div></div>
    <div>${side}</div></div>`;
}

export async function dashboard(root, ctx) {
  const u = ctx.user, owner = u.role === "owner";
  const tabs = owner
    ? [["auctions", "My auctions"], ["billboards", "My billboards"], ["bookings", "Bookings"]]
    : [["bids", "My bids"], ["bookings", "Bookings"], ["hidden", "Hidden"]];
  let tab = tabs.some(([k]) => k === ctx.query.tab) ? ctx.query.tab : tabs[0][0];
  const data = {};

  root.innerHTML = `<div class="container">
    <div class="page-head"><div><h1>${owner ? "Your billboards" : "Your bids"}</h1><p class="muted">${esc(u.company || u.name)}</p></div>
      ${owner ? `<a class="btn btn--primary btn--lg" href="#/sell">List a billboard</a>` : `<a class="btn btn--primary btn--lg" href="#/">Find more auctions</a>`}</div>
    <div class="tabs" role="tablist" id="tabs"></div>
    <div id="panel" role="tabpanel" style="padding-bottom:56px"></div></div>`;

  const renderTabs = () => {
    $(root, "#tabs").innerHTML = tabs.map(([k, label]) => {
      const n = data[k]?.length;
      return `<button role="tab" id="tab-${k}" aria-selected="${k === tab}" data-tab="${k}">${label}${n ? `<span class="count">${n}</span>` : ""}</button>`;
    }).join("");
  };

  const wireBookings = (items) => {
    items.forEach((p) => { if (p.has_creative) loadCreative(p.id, $(root, `[data-preview="${p.id}"]`)); });
    $$(root, "[data-drop] input").forEach((input) => {
      const zone = input.closest("[data-drop]"), pid = zone.dataset.drop;
      const send = async (file) => {
        if (!file) return;
        const msg = $(root, `[data-msg="${pid}"]`);
        const form = new FormData(); form.append("file", file);
        msg.innerHTML = `<p class="muted">Uploading ${esc(file.name)}…</p>`;
        try { await api(`/placements/${pid}/creative`, { method: "POST", form }); toast("Creative uploaded. The owner will review it.", "ok"); await loadTab("bookings", true); }
        catch (e) { msg.innerHTML = errorBox(e); }
      };
      input.addEventListener("change", () => send(input.files[0]));
      ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
      ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("is-over"); }));
      zone.addEventListener("drop", (e) => send(e.dataTransfer.files[0]));
    });
    $$(root, "[data-pay]").forEach((b) => b.addEventListener("click", async () => {
      const pid = b.dataset.pay;
      try {
        await api(`/placements/${pid}/payment`, { method: "POST", body: { paid: true, ref: $(root, `#ref-${pid}`).value || null } });
        toast("Marked as paid.", "ok"); await loadTab("bookings", true);
      } catch (e) { $(root, `[data-msg="${pid}"]`).innerHTML = errorBox(e); }
    }));
    $$(root, "[data-review]").forEach((b) => b.addEventListener("click", async () => {
      const pid = b.dataset.id, approve = b.dataset.review === "approve";
      try {
        await api(`/placements/${pid}/review`, { method: "POST", body: { approve, note: $(root, `#note-${pid}`).value || null } });
        toast(approve ? "Creative approved." : "Sent back to the advertiser.", "ok"); await loadTab("bookings", true);
      } catch (e) { $(root, `[data-msg="${pid}"]`).innerHTML = errorBox(e); }
    }));
  };

  const loadTab = async (key, force) => {
    tab = key; renderTabs();
    const panel = $(root, "#panel");
    if (!data[key] || force) {
      panel.innerHTML = `<div class="skeleton" style="min-height:140px"></div>`;
      try {
        if (key === "bids") data[key] = (await api("/me/bids")).items;
        if (key === "auctions") data[key] = (await api("/owner/auctions")).items;
        if (key === "billboards") data[key] = (await api("/owner/billboards")).items;
        if (key === "bookings") data[key] = (await api("/me/placements")).items;
        if (key === "hidden") data[key] = (await api("/me/hidden")).items;
        if (data[key][0]?.server_time) syncClock(data[key][0].server_time);
      } catch (e) { panel.innerHTML = errorBox(e); return; }
      renderTabs();
    }
    const items = data[key];
    if (key === "bids") {
      panel.innerHTML = items.length ? `<div class="rows">${items.map((a) => auctionRow(a, { mine: true })).join("")}</div>`
        : empty("You haven't bid yet", "Find a slot you like and place your first bid.", `<a class="btn btn--dark" href="#/">Browse open auctions</a>`);
    } else if (key === "auctions") {
      panel.innerHTML = items.length ? `<div class="rows">${items.map((a) => auctionRow(a)).join("")}</div>`
        : empty("No auctions yet", "List a billboard with a starting price and a deadline.", `<a class="btn btn--dark" href="#/sell">List a billboard</a>`);
      $$(panel, "[data-cancel]").forEach((b) => b.addEventListener("click", async () => {
        if (!confirm("Cancel this auction?")) return;
        try { await api(`/auctions/${b.dataset.cancel}/cancel`, { method: "POST" }); toast("Auction cancelled.", "ok"); await loadTab("auctions", true); }
        catch (e) { toast(e.message, "error"); }
      }));
    } else if (key === "hidden") {
      panel.innerHTML = items.length ? `<div class="rows">${items.map((a) => `<div class="row" style="grid-template-columns:1fr auto">
        <div><div class="row__title">${esc(a.billboard.title)}</div><div class="row__meta"><span>${esc(a.billboard.address)}, ${esc(a.billboard.city)}</span><span>${esc(a.billboard.owner_name)}</span></div></div>
        <button class="btn btn--dark btn--sm" data-unhide="${a.id}" type="button">Unhide</button></div>`).join("")}</div>`
        : empty("Nothing hidden", "Use the Hide button on a marketplace card to remove a listing from your view.");
      $$(panel, "[data-unhide]").forEach((b) => b.addEventListener("click", async () => {
        try { await api(`/auctions/${b.dataset.unhide}/hide`, { method: "DELETE" }); toast("Listing is visible again.", "ok"); await loadTab("hidden", true); }
        catch (e) { toast(e.message, "error"); }
      }));
    } else if (key === "billboards") {
      panel.innerHTML = items.length ? `<div class="rows">${items.map((bb) => `<div class="row" style="grid-template-columns:1fr auto">
        <div><div class="row__title">${esc(bb.title)}</div><div class="row__meta"><span>${esc(bb.address)}, ${esc(bb.city)}</span><span>${esc(bb.format)}</span><span>${bb.width_ft} × ${bb.height_ft} ft</span><span>${num(bb.daily_impressions)} daily views</span></div></div>
        <div style="display:flex;gap:8px"><a class="btn btn--dark btn--sm" href="#/sell?billboard=${bb.id}">Start an auction</a>
        <button class="btn btn--danger btn--sm" data-remove="${bb.id}" data-title="${esc(bb.title)}" type="button">Remove</button></div></div>`).join("")}</div>`
        : empty("No billboards listed", "Add your first billboard to start auctioning its space.", `<a class="btn btn--dark" href="#/sell">List a billboard</a>`);
      $$(panel, "[data-remove]").forEach((b) => b.addEventListener("click", async () => {
        if (!confirm(`Remove "${b.dataset.title}"? Any open auction on it will be cancelled, even if it has bids, and bidders will be notified.`)) return;
        try {
          const r = await api(`/billboards/${b.dataset.remove}`, { method: "DELETE" });
          toast(r.auctions_cancelled ? `Billboard removed. ${r.auctions_cancelled} auction(s) cancelled.` : "Billboard removed.", "ok");
          await loadTab("billboards", true); loadTab_quiet("auctions");
        } catch (e) { toast(e.message, "error"); }
      }));
    } else {
      panel.innerHTML = items.length ? `<div class="rows">${items.map((p) => bookingRow(p, owner ? "owner" : "advertiser")).join("")}</div>`
        : empty("No bookings yet", owner ? "When an auction ends with a winner, the booking appears here." : "Win an auction and your booking appears here.");
      wireBookings(items);
    }
  };

  $(root, "#tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) { history.replaceState(null, "", `#/dashboard?tab=${b.dataset.tab}`); loadTab(b.dataset.tab); } });
  renderTabs();
  await loadTab(tab);
  // Prefetch the other tabs so counts show up.
  tabs.forEach(([k]) => { if (k !== tab && !data[k]) loadTab_quiet(k); });
  async function loadTab_quiet(k) {
    try {
      data[k] = k === "bids" ? (await api("/me/bids")).items : k === "auctions" ? (await api("/owner/auctions")).items
        : k === "billboards" ? (await api("/owner/billboards")).items : k === "hidden" ? (await api("/me/hidden")).items : (await api("/me/placements")).items;
      renderTabs();
    } catch {}
  }
  const refreshTimer = setInterval(() => { if (!document.hidden && (tab === "bids" || tab === "auctions")) loadTab(tab, true); }, 10000);
  return () => clearInterval(refreshTimer);
}

// =====================================================================
// Sell: list a billboard and open an auction
// =====================================================================
const pad = (n) => String(n).padStart(2, "0");
const localInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const dayInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

export async function sellPage(root, ctx) {
  let boards = [];
  try { boards = (await api("/owner/billboards")).items; } catch (e) { root.innerHTML = `<div class="container section">${errorBox(e)}</div>`; return; }
  let mode = boards.length && ctx.query.billboard !== "new" ? "existing" : "new";
  const preselect = Number(ctx.query.billboard) || boards[0]?.id;

  const end = new Date(Date.now() + 24 * 3600_000); end.setSeconds(0, 0);
  const slotStart = new Date(end.getTime() + 2 * 86400_000), slotEnd = new Date(slotStart.getTime() + 29 * 86400_000);

  root.innerHTML = `<div class="container"><div class="page-head"><div><h1>List a billboard</h1><p class="muted">Set the rules once. Bidding starts as soon as you publish.</p></div></div>
    <form class="steps" id="f" style="padding-bottom:56px" novalidate>
      <section aria-labelledby="s1"><h2 id="s1"><span>1</span>Billboard</h2>
        ${boards.length ? `<div class="seg" role="group" aria-label="Billboard source"><button type="button" data-mode="existing" aria-pressed="${mode === "existing"}">Use one of mine</button><button type="button" data-mode="new" aria-pressed="${mode === "new"}">Add a new one</button></div>` : ""}
        <div id="existing" ${mode === "new" ? "hidden" : ""}><div class="field"><label for="bb">Billboard</label>
          <select class="select" id="bb">${boards.map((b) => `<option value="${b.id}" ${b.id === preselect ? "selected" : ""}>${esc(b.title)} in ${esc(b.city)}</option>`).join("")}</select></div></div>
        <div id="new" ${mode === "existing" ? "hidden" : ""} style="display:grid;gap:16px">
          <div class="field"><label for="title">Name</label><input class="input" id="title" placeholder="MG Road flyover unipole" maxlength="160"></div>
          <div class="grid-2"><div class="field"><label for="city">City</label><input class="input" id="city" placeholder="Bengaluru"></div>
            <div class="field"><label for="format">Format</label><select class="select" id="format"><option>Unipole</option><option>Gantry</option><option>Digital LED</option><option>Hoarding</option><option>Bus shelter</option></select></div></div>
          <div class="field"><label for="address">Street or landmark</label><input class="input" id="address" placeholder="MG Road, near Trinity Circle"></div>
          <div class="grid-2"><div class="field"><label for="w">Width (ft)</label><input class="input num" id="w" type="number" min="1" value="40"></div>
            <div class="field"><label for="h">Height (ft)</label><input class="input num" id="h" type="number" min="1" value="20"></div></div>
          <div class="field"><label for="imp">Daily views</label><input class="input num" id="imp" type="number" min="0" value="100000"><small>Your best estimate of people who pass it each day.</small></div>
          <label class="check"><input type="checkbox" id="lit" checked> Lit at night</label>
          <div class="field"><label for="desc">Description <span class="muted">(optional)</span></label><textarea class="textarea" id="desc" maxlength="2000" placeholder="Sightlines, traffic, nearby landmarks"></textarea></div>
        </div></section>
      <section aria-labelledby="s2"><h2 id="s2"><span>2</span>Auction rules</h2>
        <div class="grid-2"><div class="field"><label for="floor">Starting bid</label><div class="input-affix"><span>₹</span><input class="input num" id="floor" inputmode="numeric" value="100000"></div><small>No bid can be lower than this.</small></div>
          <div class="field"><label for="inc">Minimum raise</label><div class="input-affix"><span>₹</span><input class="input num" id="inc" inputmode="numeric" value="5000"></div><small>Each new bid must beat the price by at least this.</small></div></div>
        <div class="field"><label for="end">Bidding closes</label><input class="input" id="end" type="datetime-local" value="${localInput(end)}" min="${localInput(new Date(Date.now() + 120_000))}"><small>The highest bid at this time wins.</small></div>
        <div class="grid-2"><div class="field"><label for="ss">Ad runs from</label><input class="input" id="ss" type="date" value="${dayInput(slotStart)}"></div>
          <div class="field"><label for="se">Ad runs until</label><input class="input" id="se" type="date" value="${dayInput(slotEnd)}"></div></div>
        <div class="field"><label for="snipe">Late bids</label><select class="select" id="snipe"><option value="120">Extend by 2 minutes if a bid lands in the last 2 minutes</option><option value="300">Extend by 5 minutes if a bid lands in the last 5 minutes</option><option value="0">Never extend the deadline</option></select></div>
      </section>
      <div id="msg" aria-live="polite"></div>
      <div><button class="btn btn--primary btn--lg" type="submit">Publish auction</button></div>
    </form></div>`;

  $$(root, "[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode;
    $$(root, "[data-mode]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $(root, "#existing").hidden = mode !== "existing"; $(root, "#new").hidden = mode !== "new";
  }));

  const intval = (s) => Number(String(s).replace(/[^\d]/g, "")) || 0;
  let createdBoardId = null;
  $(root, "#f").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter, msg = $(root, "#msg"); msg.innerHTML = ""; btn.disabled = true;
    try {
      let billboardId;
      if (mode === "existing") billboardId = Number($(root, "#bb").value);
      else if (createdBoardId) billboardId = createdBoardId; // auction failed last time; don't list it twice
      else {
        const bb = await api("/billboards", { method: "POST", body: {
          title: $(root, "#title").value.trim(), city: $(root, "#city").value.trim(), address: $(root, "#address").value.trim(),
          format: $(root, "#format").value, width_ft: Number($(root, "#w").value), height_ft: Number($(root, "#h").value),
          daily_impressions: Number($(root, "#imp").value), illuminated: $(root, "#lit").checked, description: $(root, "#desc").value } });
        billboardId = createdBoardId = bb.id;
      }
      if (!$(root, "#end").value) throw new ApiError("Choose when bidding closes.", "VALIDATION");
      const a = await api("/auctions", { method: "POST", body: {
        billboard_id: billboardId, floor_price: intval($(root, "#floor").value), min_increment: intval($(root, "#inc").value),
        end_at: new Date($(root, "#end").value).toISOString(), slot_start: $(root, "#ss").value, slot_end: $(root, "#se").value,
        anti_snipe_seconds: Number($(root, "#snipe").value) } });
      toast("Auction published. Bidding is open.", "ok");
      ctx.go(`/auction/${a.id}`);
    } catch (err) { msg.innerHTML = errorBox(err); btn.disabled = false; msg.scrollIntoView({ block: "center" }); }
  });
}
