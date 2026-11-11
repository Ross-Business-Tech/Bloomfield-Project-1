/* Deal Radar front-end. Reads /api/snapshot.json (same-origin, behind the site password). Every value from the data
   is written with textContent / attributes, never parsed as HTML. Colours come from the tokens in app.css only.
   Screens follow the originator's workflow: where to look (This week, Map) -> what is happening (Leads, lead detail)
   -> who is taking it over (Buyers) -> can I trust this (The numbers, How it works) -> who do I contact (lead detail). */
(function () {
  "use strict";

  const STATES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado",
    CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii",
    ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine",
    MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri",
    MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico",
    NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon",
    PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas",
    UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming" };
  const CODE_BY_NAME = Object.fromEntries(Object.entries(STATES).map(([k, v]) => [v, k]));
  const SENIOR_TYPES = ["AL", "IL", "MC", "CCRC"];
  const CARE_TYPES = ["SNF", "AL", "IL", "MC", "CCRC"];
  const STAGES = ["early", "middle", "late", "sale"];
  const STAGE_LABEL = { early: "Early distress", middle: "Deepening", late: "Late stage", sale: "In a sale" };
  const STAGE_TIP = { early: "Early distress: warning signs such as losses or low occupancy, with no legal action yet.",
    middle: "Deepening: several signals at once, or a regulator or lender has stepped in.",
    late: "Late stage: a court, receiver or lender now controls what happens to the property.",
    sale: "In a sale: the property is being sold, usually through a court process, or a sale was recently reported." };
  const BUYER_LABEL = { confirmed: "Named", candidate: "Candidate · not final", inferred: "Unverified", none: "NOT FOUND" };
  const BUYER_TIP = { confirmed: "Named: a public source states this buyer.",
    candidate: "Candidate: a proposed or stalking-horse bidder. Not final; another bidder may win.",
    inferred: "Unverified: an older or shell-company name that no recent source confirms.",
    none: "No public source names a buyer yet." };
  const HARD = new Set(["chapter_11", "chapter_7", "receivership", "foreclosure", "deed_in_lieu", "closure",
    "warn_closure", "hud_loan_terminated", "loan_or_lease_default", "court_sale_process", "hud_delinquency",
    "special_servicing", "lender_takeback", "maturity_default"]);
  /* Plain-English names and definitions. Signals not listed fall back to their name with underscores removed. */
  const SIGNAL = {
    chapter_11: ["Chapter 11", "Chapter 11: a bankruptcy where the owner keeps operating while a court supervises a restructuring or a sale."],
    chapter_7: ["Chapter 7", "Chapter 7: a bankruptcy where a trustee sells the assets and the business winds down."],
    receivership: ["Receivership", "Receivership: a court appoints a neutral manager (a receiver) to run or sell the property, usually at a lender's request."],
    foreclosure: ["Foreclosure", "Foreclosure: the lender is taking the property because the loan was not paid."],
    deed_in_lieu: ["Deed-in-lieu", "Deed-in-lieu: the owner hands the property to the lender instead of going through foreclosure."],
    court_sale_process: ["Court-supervised sale", "Court-supervised sale: a bankruptcy court is running a sale. A first bidder (the stalking horse) sets the floor price and others may outbid it."],
    operator_turnover: ["Operator change (CHOW)", "CHOW, change of ownership: Medicare records show a new owner or operator took over the licence."],
    negative_noi: ["Operating at a loss", "Operating at a loss: the latest public cost report shows expenses above revenue (negative net operating income)."],
    low_occupancy: ["Low occupancy", "Low occupancy: the share of beds or units filled is well below normal in the latest public data."],
    sff_or_candidate: ["Special Focus Facility", "Special Focus Facility: Medicare's list of nursing homes with a history of serious quality problems, or a candidate for that list."],
    hud_loan_terminated: ["HUD loan ended", "HUD loan ended: a federally insured mortgage on the property was terminated, often a sign of a payoff, a sale or a default."],
    hud_delinquency: ["HUD loan delinquent", "HUD loan delinquent: payments on a federally insured mortgage are behind."],
    loan_or_lease_default: ["Loan or lease default", "Loan or lease default: a source reports missed payments or a broken loan or lease term."],
    closure: ["Closure", "Closure: the property has closed or announced that it will."],
    warn_closure: ["Closure notice (WARN)", "WARN notice: a legally required public notice of a closure or mass layoff."],
    special_servicing: ["Special servicing", "Special servicing: the loan was moved to a servicer that handles troubled loans."],
    lender_takeback: ["Lender take-back", "Lender take-back: the lender now owns or controls the property."],
    maturity_default: ["Maturity default", "Maturity default: the loan came due and was not repaid or refinanced."],
  };
  const CARE_TIP = { SNF: "SNF, skilled nursing facility: a nursing home with 24-hour medical care, mostly paid by Medicare and Medicaid.",
    AL: "AL, assisted living: housing with help for daily activities such as meals, bathing and medication.",
    IL: "IL, independent living: senior apartments with services but no personal care.",
    MC: "MC, memory care: secured assisted living for residents with dementia.",
    CCRC: "CCRC, continuing-care retirement community: one campus with independent living, assisted living and nursing care." };
  const GLOSSARY = [
    ["CHOW (change of ownership)", "Medicare's record that a new owner or operator has taken over a nursing home's licence. It is the clearest public sign of who is taking a property over."],
    ["Receivership", "A court appoints a neutral manager, the receiver, to run or sell a property, usually because a lender asked."],
    ["Chapter 11", "A bankruptcy where the owner keeps operating while a court supervises a restructuring or a sale."],
    ["Stalking horse", "The first bidder in a court-supervised sale. Its offer sets the floor price; other bidders can still outbid it, so it is shown as a candidate, not a final buyer."],
    ["Deed-in-lieu", "The owner hands the property to the lender instead of going through foreclosure."],
    ["SNF", "Skilled nursing facility: a nursing home with 24-hour medical care."],
    ["AL / IL / MC", "Assisted living (help with daily activities), independent living (apartments with services), memory care (secured care for dementia)."],
    ["CCRC", "Continuing-care retirement community: independent living, assisted living and nursing care on one campus."],
    ["Special Focus Facility", "Medicare's list of nursing homes with a history of serious quality problems."],
    ["List A / List B / List C", "A: live deals with a buyer named in a source. B: distress before any sale, no buyer yet. C: buyers named on two or more properties."],
  ];
  const FLAG_TEXT = { buyer_chain_perf_missing: "no public quality record for the buyer's other properties",
    family_capped: "part of a large bankruptcy that is counted as one deal", unconfirmed: "the match to this building is not yet confirmed",
    court_name_only: "the court case was matched by name only", buyer_stale: "the buyer's name is more than two years old",
    portfolio_link: "linked to a portfolio sale", senior_housing_maybe: "the care type is uncertain",
    buyer_is_own_operator: "the buyer already operates this property", state_inferred: "the state was worked out from the city",
    buyer_hospital_district: "the buyer is a public hospital district", buyer_mega_platform: "the buyer is a very large platform",
    buyer_shell: "the buyer is a shell company", size_check: "the size needs checking",
    buyer_not_found: null, size_unknown: null, sale_closed_over_12m: null, buyer_possible_insider: null };
  const flagText = (f) => (f in FLAG_TEXT ? FLAG_TEXT[f] : pretty(f));
  const TABS = [
    { key: "all", label: "All leads" },
    { key: "A", label: "List A", sub: "live deals, buyer named" },
    { key: "B", label: "List B", sub: "distress before a sale" },
    { key: "C", label: "List C", sub: "repeat buyers" },
  ];
  const BD_LABELS = [["court_sale_priority", "Court-supervised sale under way"], ["stage", "Distress stage"], ["buyer", "Buyer named or proposed"],
    ["recency", "How recent the latest signal is"], ["size_fit", "Size fits a $3–30M loan"], ["hud_exit", "HUD loan ended"], ["market_sale", "Market sale reported"],
    ["buyer_chain_quality", "Buyer's quality record"], ["closed_sale_penalty", "Sale closed over a year ago"]];
  const PAGE_SIZE = 25;

  const S = { data: null, tab: "all", state: "", page: 1, sort: { key: "rank", dir: 1 }, mode: "brief", sel: -1, ctx: [], ctxIdx: -1 };
  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.style.cssText = v;        // CSSOM, allowed by the CSP (no inline style attributes)
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(3)) if (kid != null && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return n;
  }
  const nf = () => el("span", { class: "nf", text: "NOT FOUND" });
  const pretty = (s) => String(s || "").replace(/_/g, " ");
  const sigName = (s) => (SIGNAL[s] ? SIGNAL[s][0] : pretty(s).replace(/^./, (c) => c.toUpperCase()));
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const fmtInt = (n) => Number(n).toLocaleString("en-US");
  const size = (r) => r.units || r.beds || null;
  const sizeText = (r) => (r.units ? fmtInt(r.units) + " units" : r.beds ? fmtInt(r.beds) + " beds" : null);
  const place = (r) => [r.city, r.state].filter(Boolean).join(", ");
  const plural = (n, one, many) => fmtInt(n) + " " + (n === 1 ? one : many || one + "s");
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const phoneText = (d) => { d = String(d || "").replace(/\D/g, ""); return d.length === 10 ? "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6) : null; };
  const addressText = (c) => [c.street, c.zip5].filter(Boolean).join(", ");
  const mapsUrl = (r) => { const c = r.contact || {};
    const q = [r.property, c.street, r.city, r.state, c.zip5].filter(Boolean).join(", ");
    return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q); };
  function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return u; } }
  function fmtDate(v, opts) { const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(v) ? v + "T12:00:00" : v);
    return isNaN(d) ? String(v || "") : d.toLocaleDateString("en-US", opts || { year: "numeric", month: "short", day: "numeric" }); }
  function telLink(r) {
    const d = (r.contact || {}).phone;
    return d && phoneText(d) ? el("a", { class: "tel", href: "tel:+1" + d, text: phoneText(d), onclick: (e) => e.stopPropagation() }) : nf();
  }
  function ext(u, label, cls) { return el("a", { class: cls, href: u, target: "_blank", rel: "noopener noreferrer", text: label, onclick: (e) => e.stopPropagation() }); }

  /* ---------- plain-English tooltips: one floating box, opened by hover, focus or tap ---------- */
  function info(tipText, label, cls) {
    return el("button", { class: "term " + (cls || ""), type: "button", "data-tip": tipText, "aria-label": (label ? label + ". " : "") + tipText,
      onclick: (e) => e.stopPropagation() }, label || null, el("span", { class: "i", "aria-hidden": "true", text: "ⓘ" }));
  }
  function showTip(text, x, y) {
    const tip = $("tip");
    tip.textContent = text;
    tip.classList.remove("hidden");
    const w = tip.offsetWidth, h = tip.offsetHeight, vw = document.documentElement.clientWidth;
    const left = Math.max(8, Math.min(vw - w - 8, x - w / 2));
    const top = y + h + 12 > window.innerHeight ? y - h - 28 : y + 12;
    tip.style.left = left + "px"; tip.style.top = Math.max(8, top) + "px";
  }
  const hideTip = () => $("tip").classList.add("hidden");
  function tipFor(node) { const b = node.getBoundingClientRect(); showTip(node.dataset.tip, b.left + b.width / 2, b.bottom); }
  document.addEventListener("mouseover", (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t) tipFor(t); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest && e.target.closest("[data-tip]")) hideTip(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t) tipFor(t); });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("click", (e) => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t) tipFor(t); else hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true, capture: true });

  /* ---------- shared pieces ---------- */
  function stageMark(r) {
    const i = STAGES.indexOf(r.stage);
    if (i < 0) return nf();
    return el("span", { class: "stage", "data-tip": STAGE_TIP[r.stage] },
      el("span", { class: "pips", "aria-hidden": "true" }, STAGES.map((_, k) => el("i", { class: k <= i ? "on" : "" }))),
      el("span", { text: STAGE_LABEL[r.stage] }));
  }
  function buyerChip(group) {
    if (group === "none") return el("span", { class: "nf", text: "NOT FOUND", "data-tip": BUYER_TIP.none });
    return el("span", { class: "chip buyer-" + group, text: BUYER_LABEL[group] || group, "data-tip": BUYER_TIP[group] });
  }
  function buyerLine(r) {
    return el("span", { class: "buyerline" }, el("span", { class: "k", text: "Buyer" }), " ", buyerChip(r.buyer_group),
      r.buyer ? el("span", { class: "bname", text: r.buyer, title: r.buyer }) : null);
  }
  function careText(r) { return r.care_types.length ? r.care_types.join(" / ") : null; }
  function metaLine(r) {
    return el("p", { class: "meta" }, place(r) || nf(), " · ", sizeText(r) || ["size ", nf()], " · ", careText(r) || ["care type ", nf()]);
  }
  function scoreMark(v) {
    return el("span", { class: "score", "data-tip": "Score: points from fixed rules (stage, buyer, recency, size). Open the lead for its breakdown." },
      el("b", { text: v == null ? "–" : String(Math.round(v)) }), el("span", { text: "score" }));
  }
  function signalChip(s) {
    const d = SIGNAL[s];
    return el(d ? "button" : "span", { class: "chip sig" + (HARD.has(s) ? " hard" : ""), type: d ? "button" : null,
      "data-tip": d ? d[1] : null, onclick: d ? (e) => e.stopPropagation() : null }, sigName(s), d ? el("span", { class: "i", "aria-hidden": "true", text: "ⓘ" }) : null);
  }

  /* ---------- "Why this lead" (rules in code, no AI) ---------- */
  function whyParts(r) {
    const bd = r.breakdown || {}, parts = [];
    const hard = (bd.hard_events || r.signals.filter((x) => HARD.has(x)));
    if (bd.court_sale_priority > 0) parts.push("court-supervised sale under way");
    else if (hard.length) parts.push(hard.map(sigName).join(" and ").toLowerCase().replace("chapter", "Chapter").replace("hud", "HUD").replace("(chow)", "(CHOW)").replace("(warn)", "(WARN)"));
    else if (r.signals.length) parts.push(r.signals.length + " separate distress signals");
    if (r.buyer && r.buyer_group === "confirmed") parts.push("buyer named in a source");
    else if (r.buyer && r.buyer_group === "candidate") parts.push("candidate buyer, not final");
    else if (!r.buyer) parts.push("no buyer named yet");
    if (bd.size_fit > 0) parts.push("fits a $3–30M loan");
    if (bd.closed_sale_penalty < 0) parts.push("sale closed over a year ago");
    return parts;
  }
  function whyShort(r) { const s = whyParts(r).join(" · "); return s ? s[0].toUpperCase() + s.slice(1) : ""; }
  function whyThisLead(r) {
    const bd = r.breakdown || {};
    let s = "Ranked " + r.rank + " of " + fmtInt(S.data.items.length) + ": " + whyParts(r).join("; ");
    if (bd.latest_signal_date) s += ". Latest signal " + fmtDate(bd.latest_signal_date);
    return s + ".";
  }

  /* ---------- data ---------- */
  async function load() {
    const res = await fetch("/api/snapshot.json", { credentials: "same-origin" });
    if (!res.ok) throw new Error("snapshot " + res.status);
    S.data = await res.json();
    S.data.items = S.data.items || [];
    for (const r of S.data.items) { for (const k of ["care_types", "signals", "flags", "sources", "lists", "not_found"]) r[k] = r[k] || []; }
    S.byRank = S.data.items.slice().sort((a, b) => a.rank - b.rank);
    S.buyers = groupBuyers();
    $("asof").textContent = "As of " + fmtDate(S.data.generated_at);
    $("loading").classList.add("hidden");
    buildToday();
    buildFilters();
    buildTabs();
    render();
    buildBuyers();
    buildKpis();
    buildFunnel();
    buildHow();
    buildExplore();
    await buildMap().catch(() => { $("map-hint").textContent = "The map could not be drawn. Choose a state from the menu instead."; });
    renderMapSide();
    window.addEventListener("hashchange", route);
    window.addEventListener("scroll", () => $("topbar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });
    route();
  }

  /* ---------- pages ---------- */
  const VIEWS = { "": "dashboard", "#/": "dashboard", "#/leads": "leads", "#/buyers": "buyers", "#/map": "map",
    "#/overview": "overview", "#/funnel": "overview", "#/how": "how", "#/explore": "explore" };
  const VIEW_IDS = ["dashboard", "leads", "buyers", "map", "overview", "how", "explore"];
  function route() {
    const listRoute = /^#\/leads\/([ABC])$/.exec(location.hash);
    const v = listRoute ? "leads" : VIEWS[location.hash] || "dashboard";
    stopMotion();
    S.view = v;
    for (const id of VIEW_IDS) $("view-" + id).classList.toggle("hidden", id !== v);
    document.querySelectorAll(".nav a").forEach((a) => {
      const on = a.dataset.view === v;
      a.classList.toggle("active", on);
      if (on) { a.setAttribute("aria-current", "page"); a.scrollIntoView({ block: "nearest", inline: "nearest" }); }
      else a.removeAttribute("aria-current");
    });
    closePanel();
    if (listRoute) selectTab(listRoute[1]);
    window.scrollTo({ top: 0, behavior: "instant" });
    animateView(v);
  }

  /* ---------- motion: numbers count up once when their page opens; nothing else moves ---------- */
  const frames = new Set();
  function frame(cb) { const id = requestAnimationFrame((t) => { frames.delete(id); cb(t); }); frames.add(id); }
  function stopMotion() {
    frames.forEach(cancelAnimationFrame); frames.clear();
    document.querySelectorAll("[data-n]").forEach((n) => { n.textContent = fmtInt(Number(n.dataset.n)) + (n.dataset.suffix || ""); });
  }
  function countUp(node, delay) {
    const n = Number(node.dataset.n);
    if (!isFinite(n)) return;
    const suffix = node.dataset.suffix || "";
    if (REDUCED || n < 2) { node.textContent = fmtInt(n) + suffix; return; }
    const t0 = performance.now() + (delay || 0), dur = 700;
    node.textContent = "0" + suffix;
    const tick = (t) => { const k = Math.max(0, Math.min(1, (t - t0) / dur)), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmtInt(Math.round(n * e)) + suffix; if (k < 1) frame(tick); };
    frame(tick);
  }
  function animateView(v) {
    const view = $("view-" + v);
    view.classList.remove("enter");
    void view.offsetWidth;                       // restart the CSS transitions on every visit
    view.classList.add("enter");
    view.querySelectorAll("[data-n]").forEach((node, i) => countUp(node, 80 + i * 90));
  }

  /* ---------- 1. This week: how many, as of when, where to start ---------- */
  function topPicks(n) {
    const seen = new Set(), out = [];
    const key = (r) => (r.buyer || "") + "|" + (((r.breakdown || {}).bankruptcy_family || [])[0] || r.property);
    for (const pass of [(r) => r.lists.includes("A") && r.buyer_group === "confirmed", () => true]) {
      for (const r of S.byRank) {
        if (out.length >= n) break;
        if (out.includes(r) || !pass(r) || seen.has(key(r)) || (r.buyer && seen.has("b:" + r.buyer))) continue;
        seen.add(key(r)); if (r.buyer) seen.add("b:" + r.buyer);
        out.push(r);
      }
    }
    return out;
  }
  function narrowSteps() {
    const F = S.data.funnel || [], find = (name) => F.find((f) => f.step === name);
    const n = S.data.items.length;
    return [[find("Buildings tracked"), "buildings tracked", "Every senior-housing and nursing building in the public sources."],
      [find("With a distress signal"), "show a distress signal", "At least one dated signal in the last 36 months. Most stay on the watch list."],
      [{ count: n }, "leads", "Three or more kinds of signal, or one hard event such as a bankruptcy or receivership."],
      [{ count: Math.min(3, n) }, "to start with", "The highest-ranked live deals, one per buyer."]]
      .filter(([f]) => f && f.count != null);
  }
  function buildNarrow(target) {
    const steps = narrowSteps(), max = Math.max(1, ...steps.map(([f]) => f.count));
    const w = (c) => (c > 0 ? Math.max(0.04, Math.log(c + 1) / Math.log(max + 1)) : 0);
    $(target).replaceChildren(...steps.map(([f, label, rule], i) => el("li", { style: "--i:" + i + ";--w:" + w(f.count).toFixed(3) },
      el("span", { class: "n", "data-n": f.count, text: fmtInt(f.count) }), el("span", { class: "l", text: label }),
      el("span", { class: "bar", "aria-hidden": "true" }), el("span", { class: "r", text: rule }))));
  }
  function buildToday() {
    const items = S.data.items, n = items.length;
    const cnt = (k) => items.filter((r) => r.lists.includes(k)).length;
    $("hero-eyebrow").textContent = "Weekly briefing · data as of " + fmtDate(S.data.generated_at, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
    $("hero-count").textContent = fmtInt(n); $("hero-count").dataset.n = n;
    $("hero-sub").replaceChildren("Senior-housing and nursing properties with public signs of distress, ranked. " +
      "Every claim links to its source. What no source states reads ", nf(), ".");
    for (const [id, v] of [["next-a", cnt("A")], ["next-b", cnt("B")], ["next-buyers", S.buyers.length],
      ["next-states", new Set(items.map((r) => r.state).filter(Boolean)).size]]) { $(id).textContent = fmtInt(v); }
    const picks = topPicks(3);
    $("picks").replaceChildren(...picks.map((r, i) => el("article", { class: "pick", tabindex: 0, role: "button", style: "--i:" + i,
      "aria-label": "Open lead: " + r.property, onclick: (e) => openPanel(r, e.currentTarget, picks),
      onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); openPanel(r, e.currentTarget, picks); } } },
      el("p", { class: "pick-rank" }, "Rank " + r.rank, el("span", { class: "sep", text: " · " }), "score " + Math.round(r.score ?? 0)),
      el("h3", { text: r.property || "" }), metaLine(r),
      el("p", { class: "why", text: whyShort(r) }),
      el("div", { class: "pick-facts" }, stageMark(r), buyerLine(r),
        el("span", { class: "buyerline" }, el("span", { class: "k", text: "Phone" }), " ", telLink(r))),
      el("span", { class: "open", text: "Open lead" }))));
    if (!picks.length) $("picks").replaceChildren(el("p", { class: "muted", text: "No leads in this week's data yet. Check back after the next update." }));
    buildNarrow("narrow");
  }

  /* ---------- 4. The numbers ---------- */
  function buildKpis() {
    const items = S.data.items, n = items.length;
    const senior = items.filter((r) => r.care_types.some((t) => SENIOR_TYPES.includes(t))).length;
    const states = new Set(items.map((r) => r.state).filter(Boolean)).size;
    const named = items.filter((r) => r.buyer_group === "confirmed").length;
    const phone = items.filter((r) => (r.contact || {}).phone).length;
    const pct = (x) => (n ? Math.round((100 * x) / n) : 0);
    const rows = [[n, "", "leads on the list", "public sources only"],
      [pct(named), "%", "have a buyer named in a source", fmtInt(named) + " leads"],
      [pct(senior), "%", "are senior housing rather than nursing homes", fmtInt(senior) + " AL / IL / MC / CCRC leads"],
      [pct(phone), "%", "have a public phone number on file", "the rest read NOT FOUND"],
      [states, "", "states with at least one lead", ""]];
    $("kpis").replaceChildren(...rows.map(([num, suffix, label, sub]) => el("div", { class: "kpi" },
      el("dt", {}, el("span", { class: "value", "data-n": num, "data-suffix": suffix, text: fmtInt(num) + suffix })),
      el("dd", {}, label, sub ? el("span", { class: "sub", text: sub }) : null))));
  }
  function buildFunnel() {
    const F = (S.data.funnel || []).filter((f) => !f.list);
    const max = Math.max(1, ...F.map((f) => f.count));
    $("funnel").replaceChildren(...F.map((f, i) => {
      const w = f.count > 0 ? Math.max(0.03, Math.log(f.count) / Math.log(max)) : 0;
      return el("div", { class: "frow" + (f.step === "Shown on Deal Radar" ? " is-final" : ""), style: "--i:" + i + ";--w:" + w.toFixed(4) },
        el("div", { class: "lab" }, el("span", { class: "cnt", text: fmtInt(f.count), "data-n": f.count }), el("span", { class: "t", text: f.step })),
        el("div", { class: "barbox", "aria-hidden": "true" }, el("div", { class: "fbar" })),
        el("div", { class: "rule-t", text: String(f.rule || "").replace("(see Explore)", "(see the watch list)") }));
    }));
  }

  /* ---------- 4. How it works ---------- */
  function buildHow() {
    const steps = [["Collect", "Each week we pull public records and trade news: Medicare ownership and cost data, federal loan records, state licence lists, bankruptcy court dockets and the senior-housing press."],
      ["Read", "Articles and filings are read by an AI model with one instruction: copy the facts exactly as written (name, city, buyer, stage). Anything the text does not state is recorded as NOT FOUND."],
      ["Match", "Every fact is tied to one building and one company, by licence or Medicare number where there is one, otherwise by name and address. Matches made on a name alone are flagged as unconfirmed."],
      ["Rank", "Fixed rules turn the matched facts into dated signals and a score. No model decides the ranking, and each lead shows the points behind its score."]];
    $("pipeline").replaceChildren(...steps.map(([t, d], i) => el("li", { style: "--i:" + i },
      el("span", { class: "num", text: String(i + 1) }), el("h3", { text: t }), el("p", { text: d }))));
    buildNarrow("how-funnel");
    const src = S.data.sources_table || [];
    $("sources-table").replaceChildren(
      el("thead", {}, el("tr", {}, ["Source", "What it tells us", "Records", "Refreshed"].map((h, i) => el("th", { text: h, class: i === 2 ? "num" : null })))),
      el("tbody", {}, src.map((r) => el("tr", { class: "static" }, el("td", { class: "prop", text: r.source }), el("td", { text: r.provides }),
        el("td", { class: "num", text: fmtInt(r.records) + " " + (r.records === 1 ? String(r.unit).replace(/ies$/, "y").replace(/s$/, "") : r.unit) }),
        el("td", { text: r.refresh })))));
    const R = S.data.rules || {};
    const sw = R.stage_weight || {}, sf = R.size_fit || {}, rc = R.recency || {}, cp = R.closed_sale_penalty || {};
    const rule = (title, text) => el("div", { class: "rule" }, el("h3", { text: title }), el("p", { text }));
    $("rules").replaceChildren(el("div", { class: "rules" },
      rule("Getting on the list", "A building needs " + (R.main_min_signals || 3) + " or more different kinds of distress signal, or one hard event: " +
        (R.hard_events || []).slice(0, 6).map(sigName).join(", ").toLowerCase() + " and similar. Everything else with a signal stays on the watch list."),
      rule("Distress stage", "Points for how far along the distress is: in a sale " + sw.sale + ", late " + sw.late + ", deepening " + sw.middle + ", early " + sw.early +
        ". A court-supervised sale adds " + R.court_sale_priority + " so live sales rise to the top. A sale that closed more than " + cp.after_months + " months ago loses " + cp.points + "."),
      rule("Buyer", "A buyer named in a source adds " + R.buyer_named_bonus + " points; a candidate (a proposed or stalking-horse bidder) adds " + R.buyer_candidate_bonus +
        ". A named buyer counts only if the change is under " + R.buyer_named_max_months + " months old. A buyer sharing the seller's brand is flagged as a possible insider."),
      rule("Deal size", "Buildings with " + sf.min_units + " to " + sf.max_units + " units or beds, a stand-in for a $3–30M loan, add " + sf.points +
        " points. A very large bankruptcy counts as one deal: only its top " + R.family_top_n + " properties keep full points."),
      rule("Recency", "Up to " + rc.max_points + " points: full marks within " + rc.full_points_within_months + " months of the latest signal, falling to zero after " +
        rc.zero_points_after_months + " months. Events older than " + R.lead_window_months + " months are context only."),
      rule("Weak matches", "A court case matched to a building by name alone is marked unconfirmed and kept out of the top " + R.name_only_top +
        " until an address, licence or Medicare record confirms it.")));
    $("glossary").replaceChildren(...GLOSSARY.flatMap(([t, d]) => [el("dt", { text: t }), el("dd", { text: d })]));
  }

  /* ---------- watch list ---------- */
  const X = { page: 1 };
  function buildExplore() {
    const rows = S.data.explore || [];
    const states = [...new Set(rows.map((r) => r.state).filter(Boolean))].sort();
    const sigs = [...new Set(rows.flatMap((r) => r.signals || []))].sort();
    $("x-state").replaceChildren(option("", "All states"), ...states.map((c) => option(c, STATES[c] || c)));
    $("x-care").replaceChildren(option("", "All care types"), ...CARE_TYPES.map((c) => option(c, c)));
    $("x-signal").replaceChildren(option("", "All signals"), ...sigs.map((c) => option(c, sigName(c))));
    for (const id of ["x-search", "x-state", "x-care", "x-signal"]) $(id).addEventListener("input", () => { X.page = 1; renderExplore(); });
    $("x-reset").addEventListener("click", () => { for (const id of ["x-search", "x-state", "x-care", "x-signal"]) $(id).value = ""; X.page = 1; renderExplore(); });
    $("x-thead").replaceChildren(el("tr", {}, [["Property", ""], ["City / state", ""], ["Care", ""], ["Size", "num"],
      ["Signals", ""], ["Why it is not on the lead list", ""], ["Score", "num"], ["Sources", ""]].map(([h, c]) => el("th", { text: h, class: c }))));
    renderExplore();
  }
  function exploreRows() {
    const q = $("x-search").value.trim().toLowerCase(), st = $("x-state").value, care = $("x-care").value, sig = $("x-signal").value;
    return (S.data.explore || []).filter((r) => (!st || r.state === st) && (!care || (r.care_types || []).includes(care))
      && (!sig || (r.signals || []).includes(sig)) && (!q || [r.property, r.city, r.buyer].join(" ").toLowerCase().includes(q)));
  }
  function renderExplore() {
    const rows = exploreRows();
    X.rows = rows;
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    X.page = Math.min(X.page, pages);
    $("x-count").textContent = fmtInt(rows.length) + " of " + fmtInt((S.data.explore || []).length) + " watch-list buildings";
    $("x-tbody").replaceChildren(...rows.slice((X.page - 1) * PAGE_SIZE, X.page * PAGE_SIZE).map((r) => el("tr", { class: "static" },
      el("td", { class: "prop", text: r.property }),
      el("td", {}, place(r) || nf()),
      el("td", {}, (r.care_types || []).length ? r.care_types.join(" / ") : nf()),
      el("td", { class: "num" }, size(r) != null ? fmtInt(size(r)) : nf()),
      el("td", {}, el("div", { class: "chips" }, (r.signals || []).map(signalChip))),
      el("td", { class: "reason", text: r.reason }),
      el("td", { class: "num", text: r.score == null ? "–" : String(Math.round(r.score)) }),
      el("td", {}, el("div", { class: "chips" }, (r.sources || []).slice(0, 3).map((u) => { const s = safeUrl(u); return s ? ext(s, hostOf(s)) : null; }),
        (r.sources || []).length > 3 ? el("span", { class: "muted", text: "+" + (r.sources.length - 3) }) : null)))));
    $("x-empty").classList.toggle("hidden", rows.length > 0);
    $("x-table").classList.toggle("hidden", rows.length === 0);
    $("x-pager").replaceChildren(...pager(X.page, pages, rows.length, (p) => { X.page = p; renderExplore(); }));
  }
  function pager(page, pages, count, go) {
    if (pages <= 1) return [];
    return [el("span", { class: "muted", text: "Page " + page + " of " + pages }),
      el("div", { class: "btns" },
        el("button", { class: "btn", type: "button", disabled: page <= 1, onclick: () => go(page - 1) }, "Previous"),
        el("button", { class: "btn", type: "button", disabled: page >= pages, onclick: () => go(page + 1) }, "Next"))];
  }

  /* ---------- 2. Leads: filters ---------- */
  function option(value, label) { return el("option", { value, text: label }); }
  const FILTER_IDS = ["f-search", "f-care", "f-stage", "f-buyer"];
  function buildFilters() {
    const present = [...new Set(S.data.items.map((r) => r.state).filter(Boolean))].sort((a, b) => (STATES[a] || a).localeCompare(STATES[b] || b));
    const stateOpts = () => [option("", "All states"), ...present.map((c) => option(c, STATES[c] || c))];
    $("f-state").replaceChildren(...stateOpts());
    $("m-state").replaceChildren(...stateOpts());
    $("f-care").replaceChildren(option("", "All care types"), option("senior", "Senior housing (AL / IL / MC / CCRC)"),
      option("SNF", "Skilled nursing (SNF)"), ...SENIOR_TYPES.map((c) => option(c, c + " only")));
    $("f-stage").replaceChildren(option("", "All stages"), ...STAGES.map((s) => option(s, STAGE_LABEL[s])));
    $("f-buyer").replaceChildren(option("", "Any buyer status"), option("confirmed", "Buyer named"), option("candidate", "Candidate (not final)"),
      option("inferred", "Unverified"), option("none", "No buyer found"));
    for (const id of FILTER_IDS) $(id).addEventListener("input", () => { S.page = 1; S.sel = -1; render(); });
    $("f-state").addEventListener("input", (e) => setState(e.target.value));
    $("m-state").addEventListener("input", (e) => setState(e.target.value));
    const reset = () => { for (const id of FILTER_IDS) $(id).value = ""; setState(""); };
    $("f-reset").addEventListener("click", reset);
    $("empty-reset").addEventListener("click", reset);
    $("mode-brief").addEventListener("click", () => setMode("brief"));
    $("mode-table").addEventListener("click", () => setMode("table"));
  }
  function setMode(m) {
    S.mode = m;
    $("mode-brief").setAttribute("aria-pressed", String(m === "brief"));
    $("mode-table").setAttribute("aria-pressed", String(m === "table"));
    render();
  }
  function setState(code) {
    S.state = code; S.page = 1; S.sel = -1;
    $("f-state").value = code; $("m-state").value = code;
    d3.selectAll("#map path.state").classed("selected", (f) => CODE_BY_NAME[f.properties.name] === code);
    render();
    renderMapSide();
  }
  const careMatch = (r, care) => !care || (care === "senior" ? r.care_types.some((t) => SENIOR_TYPES.includes(t)) : r.care_types.includes(care));
  function filtered() {
    const q = $("f-search").value.trim().toLowerCase();
    const care = $("f-care").value, stage = $("f-stage").value, bg = $("f-buyer").value;
    if (S.tab === "C") return (S.data.list_c || []).filter((c) => !q || [c.buyer, c.example_homes].join(" ").toLowerCase().includes(q));
    return S.data.items.filter((r) => (S.tab === "all" || r.lists.includes(S.tab))
      && (!S.state || r.state === S.state) && careMatch(r, care)
      && (!stage || r.stage === stage) && (!bg || r.buyer_group === bg)
      && (!q || [r.property, r.city, r.state, STATES[r.state], r.buyer].join(" ").toLowerCase().includes(q)));
  }
  const filtersActive = () => FILTER_IDS.some((id) => $(id).value) || !!S.state;

  /* ---------- tabs ---------- */
  function selectTab(key) { S.tab = key; S.page = 1; S.sel = -1; S.sort = { key: "rank", dir: 1 }; buildTabs(); render(); }
  function buildTabs() {
    const counts = { all: S.data.items.length, A: 0, B: 0, C: (S.data.list_c || []).length };
    for (const r of S.data.items) for (const l of r.lists) counts[l] = (counts[l] || 0) + 1;
    $("tabs").replaceChildren(...TABS.map((t, i) => el("button", {
      class: "tab", role: "tab", type: "button", "aria-selected": String(S.tab === t.key), tabindex: S.tab === t.key ? 0 : -1,
      onclick: () => { selectTab(t.key); $("tabs").querySelector('[aria-selected="true"]').focus({ preventScroll: true }); },
      onkeydown: (e) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        const next = e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
        selectTab(TABS[next].key);
        $("tabs").querySelector('[aria-selected="true"]').focus({ preventScroll: true });
      },
    }, el("span", { class: "tl", text: t.label }), el("span", { class: "count", text: fmtInt(counts[t.key] || 0) }),
      t.sub ? el("span", { class: "ts", text: t.sub }) : null)));
    for (const id of ["f-state", "f-care", "f-stage", "f-buyer"]) $(id).classList.toggle("hidden", S.tab === "C");
  }

  /* ---------- leads: brief rows (default) and sortable table ---------- */
  const LEAD_COLS = [
    { key: "rank", label: "Rank", sort: (r) => r.rank, num: true },
    { key: "property", label: "Property", sort: (r) => r.property || "" },
    { key: "state", label: "City / state", sort: (r) => (r.state || "") + (r.city || "") },
    { key: "units", label: "Size", sort: (r) => size(r) ?? -1, num: true },
    { key: "care", label: "Care" },
    { key: "stage", label: "Stage", sort: (r) => STAGES.indexOf(r.stage) },
    { key: "buyer", label: "Buyer", sort: (r) => r.buyer || "~" },
    { key: "score", label: "Score", sort: (r) => r.score ?? 0, num: true },
    { key: "contact", label: "Phone", sort: (r) => (r.contact || {}).phone || "~" },
  ];
  const C_COLS = [
    { key: "rank", label: "Rank", sort: (c) => c.rank, num: true },
    { key: "buyer", label: "Buyer", sort: (c) => c.buyer },
    { key: "homes", label: "Properties on the list", sort: (c) => c.homes_in_list, num: true },
    { key: "examples", label: "Examples" },
    { key: "source", label: "Source" },
  ];
  function cell(col, r) {
    switch (col.key) {
      case "rank": return el("td", { class: "num rank", text: String(r.rank) });
      case "property": return el("td", { class: "prop", text: r.property || "" });
      case "state": return el("td", {}, place(r) || nf());
      case "care": return el("td", {}, careText(r) || nf());
      case "units": return el("td", { class: "num" }, size(r) != null ? fmtInt(size(r)) : nf());
      case "stage": return el("td", {}, stageMark(r));
      case "buyer": return el("td", { class: "buyer" }, buyerChip(r.buyer_group), r.buyer ? el("span", { class: "bname", text: r.buyer }) : null);
      case "score": return el("td", { class: "num strong", text: r.score == null ? "–" : String(Math.round(r.score)) });
      case "contact": return el("td", { class: "nowrap" }, telLink(r));
      default: return el("td");
    }
  }
  function cCell(col, c) {
    switch (col.key) {
      case "rank": return el("td", { class: "num rank", text: String(c.rank) });
      case "buyer": return el("td", { class: "prop", text: c.buyer });
      case "homes": return el("td", { class: "num", text: fmtInt(c.homes_in_list) });
      case "examples": return el("td", { text: c.example_homes });
      case "source": { const u = safeUrl(c.public_source_link); return el("td", {}, u ? ext(u, hostOf(u)) : nf()); }
      default: return el("td");
    }
  }
  function rowProps(r, i) {
    return { tabindex: 0, "data-i": i, class: i === S.sel ? "sel" : null,
      onclick: (e) => { S.sel = i; openPanel(r, e.currentTarget, S.rows); },
      onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) { e.preventDefault(); S.sel = i; openPanel(r, e.currentTarget, S.rows); } } };
  }
  function briefRow(r, i) {
    const p = rowProps(r, i);
    p.class = "lead" + (i === S.sel ? " sel" : ""); p.role = "button"; p["aria-label"] = "Open lead " + r.rank + ": " + r.property;
    return el("article", p,
      el("span", { class: "rank", text: String(r.rank) }),
      el("div", { class: "main" }, el("h3", { text: r.property || "" }), metaLine(r), el("p", { class: "why", text: whyShort(r) })),
      el("div", { class: "status" }, stageMark(r), buyerLine(r)),
      scoreMark(r.score));
  }
  function render() {
    if (!S.data) return;
    const isC = S.tab === "C", table = isC || S.mode === "table";
    const cols = isC ? C_COLS : LEAD_COLS;
    let rows = filtered();
    const col = cols.find((c) => c.key === S.sort.key && c.sort) || cols[0];
    rows = rows.slice().sort((a, b) => { const x = col.sort(a), y = col.sort(b); return (x > y ? 1 : x < y ? -1 : 0) * S.sort.dir; });
    S.rows = rows;
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    S.page = Math.min(S.page, pages);
    const start = (S.page - 1) * PAGE_SIZE, slice = rows.slice(start, start + PAGE_SIZE);
    $("mode-brief").parentNode.classList.toggle("hidden", isC);
    $("brief").classList.toggle("hidden", table || !rows.length);
    $("table-wrap").classList.toggle("hidden", !table || !rows.length);
    if (table) {
      $("thead").replaceChildren(el("tr", {}, cols.map((c) => el("th", {
        class: (c.num ? "num" : ""), scope: "col", "aria-sort": S.sort.key === c.key ? (S.sort.dir > 0 ? "ascending" : "descending") : null },
        c.sort ? el("button", { type: "button", class: "sortbtn", onclick: () => {
          S.sort = { key: c.key, dir: S.sort.key === c.key ? -S.sort.dir : (["score", "units", "homes"].includes(c.key) ? -1 : 1) }; S.sel = -1; render(); } },
          c.label, el("span", { class: "arrow", "aria-hidden": "true", text: S.sort.key === c.key ? (S.sort.dir > 0 ? " ▲" : " ▼") : "" })) : c.label))));
      $("tbody").replaceChildren(...slice.map((r, k) => el("tr", isC ? { class: "static" } : rowProps(r, start + k), cols.map((c) => (isC ? cCell(c, r) : cell(c, r))))));
      $("brief").replaceChildren();
    } else {
      $("brief").replaceChildren(...slice.map((r, k) => briefRow(r, start + k)));
      $("tbody").replaceChildren();
    }
    const total = isC ? (S.data.list_c || []).length : S.data.items.filter((r) => S.tab === "all" || r.lists.includes(S.tab)).length;
    $("result-count").textContent = (rows.length === total ? fmtInt(total) : fmtInt(rows.length) + " of " + fmtInt(total)) + (isC ? " repeat buyers" : " leads") +
      (S.state && !isC ? " in " + (STATES[S.state] || S.state) : "") + (isC ? "" : ", best first");
    $("f-reset").classList.toggle("hidden", !filtersActive());
    const empty = $("empty");
    empty.classList.toggle("hidden", rows.length > 0);
    if (isC && total === 0) empty.querySelector(".state-title").textContent = "No repeat buyers yet. Buyers named on two or more properties will appear here.";
    else empty.querySelector(".state-title").textContent = isC ? "No buyers match this search." : "No leads match these filters.";
    $("pager").replaceChildren(...pager(S.page, pages, rows.length, (p) => { S.page = p; S.sel = -1; render(); $("lists").scrollIntoView({ block: "start" }); }));
  }
  function moveSel(d) {
    if (S.view !== "leads" || S.tab === "C" || !S.rows || !S.rows.length) return;
    S.sel = Math.max(0, Math.min(S.rows.length - 1, S.sel + d));
    S.page = Math.floor(S.sel / PAGE_SIZE) + 1;
    render();
    const node = document.querySelector('#lists [data-i="' + S.sel + '"]');
    if (node) { node.focus({ preventScroll: true }); node.scrollIntoView({ block: "nearest" }); }
  }

  /* ---------- 3. Buyers: who is taking these properties over ---------- */
  function groupBuyers() {
    const m = new Map();
    for (const r of S.data.items) {
      if (!r.buyer) continue;
      if (!m.has(r.buyer)) m.set(r.buyer, { name: r.buyer, leads: [], groups: {} });
      const g = m.get(r.buyer); g.leads.push(r); g.groups[r.buyer_group] = (g.groups[r.buyer_group] || 0) + 1;
    }
    const out = [...m.values()];
    S.repeat = new Map((S.data.list_c || []).map((c) => [String(c.buyer || "").trim().toLowerCase(), c]));
    for (const g of out) {
      g.repeat = S.repeat.get(g.name.trim().toLowerCase()) || null;
      g.leads.sort((a, b) => a.rank - b.rank);
      g.group = ["confirmed", "candidate", "inferred"].find((k) => g.groups[k]) || "inferred";
      g.mixed = Object.keys(g.groups).length > 1;
      g.states = [...new Set(g.leads.map((r) => r.state).filter(Boolean))].sort();
      g.care = [...new Set(g.leads.flatMap((r) => r.care_types))];
      const bd = (g.leads.find((r) => (r.breakdown || {}).buyer_type) || {}).breakdown || {};
      g.type = bd.buyer_type ? pretty(bd.buyer_type) : null;
      g.insider = g.leads.some((r) => r.flags.includes("buyer_possible_insider"));
    }
    return out.sort((a, b) => b.leads.length - a.leads.length || a.leads[0].rank - b.leads[0].rank);
  }
  function buildBuyers() {
    $("b-status").replaceChildren(option("", "Any status"), option("confirmed", "Named"), option("candidate", "Candidate (not final)"), option("inferred", "Unverified"));
    for (const id of ["b-search", "b-status"]) $(id).addEventListener("input", renderBuyers);
    renderBuyers();
  }
  function leadLink(r, ctx) {
    return el("button", { class: "leadlink", type: "button", onclick: (e) => openPanel(r, e.currentTarget, ctx) },
      el("span", { class: "rk", text: String(r.rank) }), el("span", { class: "nm", text: r.property || "" }),
      el("span", { class: "pl", text: place(r) }), el("span", { class: "st", text: STAGE_LABEL[r.stage] || "" }));
  }
  function renderBuyers() {
    const q = $("b-search").value.trim().toLowerCase(), st = $("b-status").value;
    const rows = S.buyers.filter((g) => (!st || g.group === st) && (!q || g.name.toLowerCase().includes(q) || g.leads.some((r) => (r.property || "").toLowerCase().includes(q))));
    $("b-count").textContent = (rows.length === S.buyers.length ? fmtInt(rows.length) : fmtInt(rows.length) + " of " + fmtInt(S.buyers.length)) +
      " buyers tied to " + plural(rows.reduce((a, g) => a + g.leads.length, 0), "lead") + ", most leads first";
    $("buyers").replaceChildren(...rows.map((g) => {
      const n = g.leads.length, SHOW = 4;
      const more = n > SHOW ? el("details", { class: "more" }, el("summary", { text: "Show all " + fmtInt(n) + " leads" }),
        el("div", { class: "leadlinks" }, g.leads.slice(SHOW).map((r) => leadLink(r, g.leads)))) : null;
      return el("article", { class: "buyer-row" },
        el("div", { class: "b-head" },
          el("h3", { text: g.name }),
          el("p", { class: "b-status" }, buyerChip(g.group), g.insider ? el("span", { class: "chip plain", text: "Possible insider", "data-tip": "This buyer shares a brand with the seller, so the sale may be to a related party (inferred)." }) : null,
            g.type ? el("span", { class: "muted", text: g.type }) : null),
          g.group === "candidate" ? el("p", { class: "caveat", text: "Proposed or stalking-horse bidder. Not final: another bidder may win." }) : null),
        el("dl", { class: "b-facts" },
          el("div", {}, el("dt", { text: n === 1 ? "Lead" : "Leads" }), el("dd", { class: "big", text: fmtInt(n) })),
          el("div", {}, el("dt", { text: g.states.length === 1 ? "State" : "States" }), el("dd", {}, g.states.length ? (g.states.length > 8 ? g.states.slice(0, 8).join(", ") + " +" + (g.states.length - 8) : g.states.join(", ")) : nf())),
          el("div", {}, el("dt", { text: "Care" }), el("dd", {}, g.care.length ? g.care.join(" / ") : nf())),
          g.repeat ? el("div", {}, el("dt", { text: "Repeat buyer" }), el("dd", {}, plural(g.repeat.homes_in_list, "property", "properties") + " on record ",
            safeUrl(g.repeat.public_source_link) ? ext(g.repeat.public_source_link, "source") : null)) : null),
        el("div", { class: "b-leads" }, el("div", { class: "leadlinks" }, g.leads.slice(0, SHOW).map((r) => leadLink(r, g.leads))), more));
    }));
    $("b-empty").classList.toggle("hidden", rows.length > 0);
  }

  /* ---------- 1b. Map: where the leads are ---------- */
  async function buildMap() {
    const topo = await d3.json("/static/states-albers-10m.json");
    const states = topojson.feature(topo, topo.objects.states).features;
    const counts = d3.rollup(S.data.items, (v) => v.length, (r) => r.state);
    const max = d3.max([...counts.values()]) || 1;
    const color = d3.scaleSequentialSqrt([0, max], d3.interpolateRgb(token("--map-low"), token("--map-high")));
    const path = d3.geoPath();
    const svg = d3.select("#map");
    const codeOf = (f) => CODE_BY_NAME[f.properties.name], n = (f) => counts.get(codeOf(f)) || 0;
    const label = (f) => f.properties.name + ": " + plural(n(f), "lead");
    svg.append("g").selectAll("path").data(states).join("path")
      .attr("class", (f) => "state" + (n(f) ? "" : " none")).attr("d", path)
      .attr("fill", (f) => (n(f) ? color(n(f)) : token("--map-empty")))
      .attr("tabindex", (f) => (n(f) ? 0 : null)).attr("role", "button").attr("aria-label", label)
      .on("mousemove", (ev, f) => showTip(label(f), ev.clientX, ev.clientY + 8))
      .on("mouseleave", hideTip)
      .on("click", (ev, f) => { ev.stopPropagation(); if (n(f)) setState(S.state === codeOf(f) ? "" : codeOf(f)); })
      .on("keydown", (ev, f) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setState(S.state === codeOf(f) ? "" : codeOf(f)); } });
    svg.append("path").datum(topojson.mesh(topo, topo.objects.states, (a, b) => a !== b)).attr("class", "mesh").attr("d", path);
    $("legend").replaceChildren(el("span", { text: "1 lead" }),
      el("span", { class: "ramp", "aria-hidden": "true" }, [0.05, 0.25, 0.5, 0.75, 1].map((t) => el("i", { style: "background:" + color(t * max) }))),
      el("span", { text: fmtInt(max) + " leads" }), el("span", { class: "none-key" }, el("i", {}), "none"));
  }
  function renderMapSide() {
    const items = S.byRank, st = S.state;
    if (!st) {
      const counts = [...d3.rollup(items, (v) => v.length, (r) => r.state)].filter(([k]) => k).sort((a, b) => b[1] - a[1]);
      $("map-title").textContent = "Where the leads are";
      $("map-sub").textContent = plural(items.length, "lead") + " across " + plural(counts.length, "state") + ". The busiest:";
      $("map-list").replaceChildren(...counts.slice(0, 8).map(([code, c]) => el("button", { class: "leadlink state", type: "button", onclick: () => setState(code) },
        el("span", { class: "nm", text: STATES[code] || code }), el("span", { class: "st", text: plural(c, "lead") }))));
      $("map-open").textContent = "Open all leads";
      return;
    }
    const rows = items.filter((r) => r.state === st);
    const named = rows.filter((r) => r.buyer_group === "confirmed").length;
    $("map-title").textContent = STATES[st] || st;
    $("map-sub").textContent = plural(rows.length, "lead") + ", " + named + " with a named buyer. Highest ranked first:";
    $("map-list").replaceChildren(...rows.slice(0, 8).map((r) => leadLink(r, rows)));
    $("map-open").textContent = rows.length > 8 ? "See all " + rows.length + " in Leads" : "Open in Leads";
  }

  /* ---------- lead detail: why -> signals + sources -> buyer -> contact -> score ---------- */
  function section(n, title, ...kids) { return el("section", { class: "psec" }, el("h3", {}, el("span", { class: "step", text: String(n) }), title), ...kids); }
  function sourceList(r) {
    const urls = r.sources.map(safeUrl).filter(Boolean);
    if (!urls.length) return el("p", {}, "Source link: ", nf());
    return el("ul", { class: "sources" }, urls.map((s) => { const url = new URL(s);
      return el("li", {}, el("a", { href: s, target: "_blank", rel: "noopener noreferrer" },
        el("span", { class: "host", text: url.hostname.replace(/^www\./, "") }),
        el("span", { class: "path", text: decodeURIComponent((url.pathname + url.search).replace(/\/$/, "")).slice(0, 90) || "/" }))); }));
  }
  function openPanel(r, trigger, ctx) {
    if (trigger !== null) S.panelTrigger = trigger || document.activeElement;
    if (ctx) S.ctx = ctx;
    S.ctxIdx = S.ctx.indexOf(r);
    S.open = r;
    const bd = r.breakdown || {};
    $("p-kicker").textContent = "Rank " + r.rank + (r.lists.length ? " · List " + r.lists.join(" and ") : "") + " · score " + (r.score == null ? "–" : Math.round(r.score));
    $("p-title").textContent = r.property || "";
    $("p-sub").replaceChildren(el("span", {}, place(r) || nf(), " · ", sizeText(r) || ["size ", nf()], " · ",
      r.care_types.length ? r.care_types.map((c, i) => [i ? " / " : "", CARE_TIP[c] ? info(CARE_TIP[c], c) : c]) : ["care type ", nf()]));
    $("p-pos").textContent = S.ctxIdx >= 0 && S.ctx.length > 1 ? (S.ctxIdx + 1) + " of " + fmtInt(S.ctx.length) : "";
    $("p-prev").disabled = S.ctxIdx <= 0; $("p-next").disabled = S.ctxIdx < 0 || S.ctxIdx >= S.ctx.length - 1;
    $("p-copy").textContent = "Copy summary";

    // 1. why this lead
    const sections = [section(1, "Why this lead", el("p", { class: "why-big", text: whyThisLead(r) }))];

    // 2. what is happening: signals, dates, and every source link
    const facts = [];
    if (bd.latest_signal_date) facts.push(["Latest signal", fmtDate(bd.latest_signal_date)]);
    if (bd.bankruptcy_family && bd.bankruptcy_family.length) facts.push(["Bankruptcy case", bd.bankruptcy_family.join(", ")]);
    if (r.first_seen) facts.push(["First listed here", fmtDate(r.first_seen)]);
    sections.push(section(2, "What is happening",
      el("p", { class: "stage-row" }, stageMark(r)),
      el("div", { class: "chips" }, r.signals.length ? r.signals.map(signalChip) : nf()),
      facts.length ? el("dl", { class: "facts" }, facts.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])) : null,
      el("h4", { text: plural(r.sources.length, "public source") + " behind these signals" }), sourceList(r)));

    // 3. who is taking it over
    const peers = r.buyer ? (S.buyers.find((g) => g.name === r.buyer) || { leads: [] }).leads.filter((x) => x !== r) : [];
    sections.push(section(3, "Who is taking it over",
      el("p", { class: "buyer-big" }, r.buyer ? el("strong", { text: r.buyer }) : null, " ", buyerChip(r.buyer_group),
        r.flags.includes("buyer_possible_insider") ? el("span", { class: "chip plain", text: "Possible insider", "data-tip": "This buyer shares a brand with the seller, so the sale may be to a related party (inferred)." }) : null),
      r.buyer_group === "candidate" ? el("p", { class: "caveat", text: "Proposed or stalking-horse bidder. Not final: another bidder may win." }) : null,
      r.buyer_group === "inferred" ? el("p", { class: "caveat", text: "This name is older or belongs to a shell company, and no recent source confirms it." }) : null,
      !r.buyer ? el("p", { class: "muted", text: "No public source names a buyer yet. That is the opening: financing is usually arranged once a buyer steps in." }) : null,
      r.buyer ? el("dl", { class: "facts" },
        el("dt", { text: "Type" }), el("dd", {}, bd.buyer_type ? pretty(bd.buyer_type) + (bd.buyer_type_source ? (bd.buyer_type_source === "inferred" ? " (inferred)" : " (per " + pretty(bd.buyer_type_source) + ")") : "") : nf()),
        el("dt", { text: "Parent" }), el("dd", {}, bd.buyer_parent ? bd.buyer_parent + (bd.buyer_parent_basis ? " (" + pretty(bd.buyer_parent_basis) + ")" : "") : nf()),
        el("dt", { text: "Reported" }), el("dd", {}, bd.buyer_date ? fmtDate(bd.buyer_date) + (bd.buyer_date_basis ? " (" + pretty(bd.buyer_date_basis) + ")" : "") : nf()),
        el("dt", { text: "Track record" }), el("dd", {}, [peers.length ? "Also tied to " + plural(peers.length, "other lead") + " on this list" : "No other leads on this list",
          S.repeat.get(r.buyer.trim().toLowerCase()) ? ". Repeat buyer: " + plural(S.repeat.get(r.buyer.trim().toLowerCase()).homes_in_list, "property", "properties") + " on record (List C)" : ""])) : null,
      peers.length ? el("div", { class: "leadlinks" }, peers.slice(0, 3).map((x) => leadLink(x, [r, ...peers])),
        peers.length > 3 ? el("a", { class: "morelink", href: "#/buyers", text: "See all in Buyers" }) : null) : null));

    // 4. who do I contact
    sections.push(section(4, "Who to contact", contactBlock(r)));

    // 5. score breakdown
    const parts = typeof bd.total === "number" ? BD_LABELS.filter(([k]) => typeof bd[k] === "number" && Math.round(bd[k]) !== 0) : [];
    const top = Math.max(1, ...parts.map(([k]) => Math.abs(bd[k])));
    const gaps = r.not_found.map((f) => pretty(f)), flags = r.flags.map(flagText).filter(Boolean);
    sections.push(section(5, "Score breakdown", parts.length ? [
      ...parts.map(([k, label]) => el("div", { class: "bd-row" }, el("span", { text: label }),
        el("div", { class: "track" }, el("div", { class: "fill" + (bd[k] < 0 ? " neg" : ""), style: "width:" + (100 * Math.abs(bd[k])) / top + "%" })),
        el("span", { class: "v", text: (bd[k] > 0 ? "+" : "−") + Math.abs(Math.round(bd[k])) }))),
      el("div", { class: "bd-row total" }, el("span", { text: "Total" }), el("span"), el("span", { class: "v", text: String(Math.round(bd.total ?? r.score ?? 0)) })),
    ] : el("p", { class: "muted", text: "The point-by-point breakdown is not shown for this lead because part of its evidence is not public." }),
      gaps.length ? el("p", { class: "gaps" }, "Not stated in any source: ", gaps.map((g, i) => [i ? ", " : "", el("span", { class: "nf", text: g })])) : null,
      flags.length ? el("p", { class: "muted small", text: "Notes from the rules: " + flags.join("; ") + "." }) : null));

    $("p-body").replaceChildren(...sections);
    $("p-body").scrollTop = 0;
    const wasHidden = $("panel").classList.contains("hidden");
    $("panel").classList.remove("hidden"); $("scrim").classList.remove("hidden");
    $("panel").setAttribute("aria-hidden", "false");
    document.body.classList.add("panel-open");
    document.querySelectorAll(".view, .topbar, .skip-link, .site-foot").forEach((n) => { n.inert = true; });
    if (wasHidden) $("p-close").focus();
  }
  function stepPanel(d) {
    const i = S.ctxIdx + d;
    if (S.ctxIdx < 0 || i < 0 || i >= S.ctx.length) return;
    if (S.ctx === S.rows) { S.sel = i; S.page = Math.floor(i / PAGE_SIZE) + 1; render(); S.panelTrigger = document.querySelector('#lists [data-i="' + i + '"]'); }
    openPanel(S.ctx[i], null);
  }
  function summaryText(r) {
    const c = r.contact || {}, bd = r.breakdown || {};
    const NFT = "NOT FOUND";
    return [r.property + " (" + (place(r) || NFT) + ")",
      "Deal Radar rank " + r.rank + ", score " + Math.round(r.score ?? 0) + (r.lists.length ? ", List " + r.lists.join("/") : ""),
      "Size / care: " + (sizeText(r) || NFT) + ", " + (careText(r) || NFT),
      "Stage: " + (STAGE_LABEL[r.stage] || NFT) + (bd.latest_signal_date ? " (latest signal " + bd.latest_signal_date + ")" : ""),
      "Signals: " + (r.signals.map(sigName).join(", ") || NFT),
      "Buyer: " + (r.buyer ? r.buyer + " (" + BUYER_LABEL[r.buyer_group].toLowerCase() + ")" : NFT),
      "Why: " + whyShort(r),
      "Phone: " + (phoneText(c.phone) || NFT), "Website: " + (c.website || NFT),
      "Address: " + ([c.street, r.city, [r.state, c.zip5].filter(Boolean).join(" ")].filter(Boolean).join(", ") || NFT),
      "Sources:", ...(r.sources.length ? r.sources.map((u) => "  " + u) : ["  " + NFT])].join("\n");
  }
  async function copyText(text, button, idle) {
    try { await navigator.clipboard.writeText(text); button.textContent = "Copied"; }
    catch (e) { button.textContent = "Copy not allowed here"; }
    setTimeout(() => { button.textContent = idle; }, 2000);
  }
  /* ---------- contact (business contact points only; each with its public source) ---------- */
  function contactBlock(r) {
    const c = r.contact || {}, web = safeUrl(c.website), tel = phoneText(c.phone) ? c.phone : null;
    const full = c.street ? [c.street, r.city, [r.state, c.zip5].filter(Boolean).join(" ")].filter(Boolean).join(", ") : null;
    const src = [c.phone_source, c.website_source].map(safeUrl).filter(Boolean);
    const copyBtn = (text) => el("button", { class: "copy", type: "button", text: "Copy", onclick: (e) => copyText(text, e.currentTarget, "Copy") });
    const row = (k, v, copy) => el("div", { class: "crow" }, el("dt", { text: k }), el("dd", {}, v || nf()), copy ? copyBtn(copy) : el("span"));
    return el("div", { class: "contact" },
      tel ? el("a", { class: "btn primary call", href: "tel:+1" + tel }, "Call " + phoneText(tel)) : null,
      el("dl", { class: "contact-rows" },
        row("Phone", tel ? [el("a", { class: "tel", href: "tel:+1" + tel, text: phoneText(tel) }), c.phone_label ? el("span", { class: "muted", text: " " + c.phone_label }) : null] : null, tel ? phoneText(tel) : null),
        row("Website", web ? ext(web, hostOf(web)) : null, web),
        row("Address", full ? ext(mapsUrl(r), full) : null, full),
        row("Buyer's contact", null, null)),
      el("p", { class: "muted small" },
        src.length ? ["The property's main business line, not a person. Source: ", ...src.flatMap((u, i) => [i ? ", " : "", ext(u, hostOf(u))]), c.checked ? ", checked " + fmtDate(c.checked) + "." : "."]
          : "No public phone or website is on file yet. The source links above are the best starting point.",
        full ? null : [" ", ext(mapsUrl(r), "Search for it on a map"), "."]));
  }
  function closePanel() {
    const wasOpen = !$("panel").classList.contains("hidden");
    $("panel").classList.add("hidden"); $("scrim").classList.add("hidden"); $("panel").setAttribute("aria-hidden", "true");
    document.body.classList.remove("panel-open");
    document.querySelectorAll(".view, .topbar, .skip-link, .site-foot").forEach((n) => { n.inert = false; });
    S.open = null;
    if (wasOpen && S.panelTrigger && S.panelTrigger.isConnected) S.panelTrigger.focus({ preventScroll: true });
  }
  $("p-close").addEventListener("click", closePanel);
  $("scrim").addEventListener("click", closePanel);
  $("p-prev").addEventListener("click", () => stepPanel(-1));
  $("p-next").addEventListener("click", () => stepPanel(1));
  $("p-copy").addEventListener("click", (e) => { if (S.open) copyText(summaryText(S.open), e.currentTarget, "Copy summary"); });
  $("retry").addEventListener("click", () => location.reload());
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href^='#/']");
    if (a && a.hash === location.hash) { e.preventDefault(); route(); }
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target.tagName || ""));
    const open = !$("panel").classList.contains("hidden");
    if (e.key === "Escape") { hideTip(); if (open) closePanel(); else if (typing) e.target.blur(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (open) {
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); stepPanel(1); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); stepPanel(-1); }
      else if (e.key === "Tab") {
        const nodes = [...$("panel").querySelectorAll('button:not(:disabled), a[href], summary, [tabindex="0"]')];
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
      return;
    }
    if (typing || !S.data) return;
    if (e.key === "/") { e.preventDefault(); if (S.view !== "leads" && S.view !== "buyers" && S.view !== "explore") location.hash = "#/leads";
      setTimeout(() => $(S.view === "buyers" ? "b-search" : S.view === "explore" ? "x-search" : "f-search").focus(), 0); }
    else if (e.key === "ArrowDown" || e.key === "j") { if (S.view === "leads") { e.preventDefault(); moveSel(1); } }
    else if (e.key === "ArrowUp" || e.key === "k") { if (S.view === "leads") { e.preventDefault(); moveSel(-1); } }
  });

  /* ---------- CSV of the current view ---------- */
  function csvCell(v) { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
  function saveCsv(header, lines, name) {
    const csv = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
    const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: name });
    document.body.append(a); a.click(); a.remove();
  }
  $("x-download").addEventListener("click", () => {
    saveCsv(["property", "city", "state", "care_type", "units_or_beds", "signals", "why_not_main_list", "score", "sources"],
      (X.rows || []).map((r) => [r.property, r.city || "NOT FOUND", r.state || "NOT FOUND", (r.care_types || []).join("/") || "NOT FOUND",
        size(r) ?? "NOT FOUND", (r.signals || []).join(";"), r.reason, r.score, (r.sources || []).join(" ")]), "deal-radar-watch-list.csv");
  });
  $("download").addEventListener("click", () => {
    if (!S.rows) return;
    if (S.tab === "C") {
      const header = ["rank", "buyer", "homes_in_list", "example_homes", "public_source_link"];
      return saveCsv(header, S.rows.map((c) => header.map((k) => c[k])), "deal-radar-list-c.csv");
    }
    const header = ["list", "rank", "property", "city", "state", "address", "phone", "website", "care_type", "units_or_beds", "signals",
      "stage", "buyer", "buyer_status", "score", "why", "sources", "not_found"];
    const lines = S.rows.map((r) => { const c = r.contact || {};
      return [r.lists.join("/"), r.rank, r.property, r.city || "NOT FOUND", r.state || "NOT FOUND", addressText(c) || "NOT FOUND",
        phoneText(c.phone) || "NOT FOUND", c.website || "NOT FOUND", r.care_types.join("/") || "NOT FOUND", size(r) ?? "NOT FOUND",
        r.signals.join(";"), r.stage, r.buyer || "NOT FOUND", r.buyer_group, r.score, whyShort(r), r.sources.join(" "), r.not_found.join(";")]; });
    saveCsv(header, lines, "deal-radar-" + (S.tab === "all" ? "leads" : "list-" + S.tab.toLowerCase()) + (S.state ? "-" + S.state : "") + ".csv");
  });

  load().catch(() => {
    $("asof").textContent = "Data not loaded";
    $("loading").classList.add("hidden");
    $("load-error").classList.remove("hidden");
  });
})();
