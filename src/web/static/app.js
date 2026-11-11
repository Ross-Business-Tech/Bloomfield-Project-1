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
  const STAGES = ["early", "middle", "late", "sale", "done"];
  const STAGE_LABEL = { early: "Early distress", middle: "Deepening", late: "Late stage", sale: "In a sale", done: "Sale approved / closed" };
  // "Sale approved · Oct 13, 2026" / "Closed / sold · May 1, 2026" (the date comes from the public source; never guessed)
  function stageText(r) {
    const s = r.sale_status;
    if (!s) return STAGE_LABEL[r.stage] || "";
    return (s.status === "closed" ? "Closed / sold" : "Sale approved") + " · " + (!s.date ? "date NOT FOUND" : /^\d{4}-\d{2}$/.test(s.date)
      ? new Date(s.date + "-15T12:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" }) : fmtDate(s.date));
  }
  const STAGE_TIP = { early: "Early distress: warning signs such as losses or low occupancy, with no legal action yet.",
    middle: "Deepening: several signals at once, or a regulator or lender has stepped in.",
    late: "Late stage: a court, receiver or lender now controls what happens to the property.",
    sale: "In a sale: the property is being sold, usually through a court process, or a sale was recently reported.",
    done: "Sale approved: a court order approved the sale, closing not confirmed. Closed / sold: a public source reports the sale closed. The date is the order or closing date in that source." };
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
  const FLAG_TEXT = { sale_approved: "a court order approved the sale", sale_closed: "a public source reports the sale closed",
    closing_unverified: "closing not verified in a public source", sale_date_is_report_date: "the sale date is the date of the report",
    debtor_link_unverified: "the link between this building and the bankrupt company is not verified, so the bankruptcy is not scored",
    buyer_operating_transferee: "the named party took over operations; the real-estate buyer is not verified",
    buyer_court_approved_purchaser: "the buyer is the purchaser approved by the court",
    buyer_chain_perf_missing: "no public quality record for the buyer's other properties",
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
    ["recency", "How recent the latest signal is"], ["size_fit", "Size fits a $3–30M loan"], ["care_type_fit", "Care-type fit (assumption)"], ["hud_exit", "HUD loan ended"], ["market_sale", "Market sale reported"],
    ["buyer_chain_quality", "Buyer's quality record"], ["closed_sale_penalty", "Sale closed over a year ago"]];
  const PAGE_SIZE = 25;

  /* Headline counts come from the snapshot's summary block; the browser only counts when the block is missing. */
  const SUM = (key, fallback) => { const v = ((S.data || {}).summary || {})[key]; return typeof v === "number" ? v : fallback; };
  const S = { data: null, tab: "all", state: "", page: 1, sort: { key: "rank", dir: 1 }, mode: "table", sel: -1, ctx: [], ctxIdx: -1 };
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
      el("span", { text: stageText(r) }));
  }
  function buyerChip(group) {
    if (group === "none") return el("span", { class: "nf", text: "NOT FOUND", "data-tip": BUYER_TIP.none });
    return el("span", { class: "chip buyer-" + group, text: BUYER_LABEL[group] || group, "data-tip": BUYER_TIP[group] });
  }
  function buyerLine(r) {
    return el("span", { class: "buyerline" }, el("span", { class: "k", text: "Buyer" }), " ", buyerChip(r.buyer_group),
      r.buyer ? buyerNameEl(r) : null);
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
    else if (r.buyer && r.buyer_group === "candidate") parts.push("candidate buyer (not final)");
    else if (!r.buyer) parts.push("no buyer named yet");
    if (bd.size_fit > 0) parts.push("fits a $3–30M loan");
    if (bd.care_type_fit > 0) parts.push("senior housing (care-type fit, an assumption)");
    if (bd.closed_sale_penalty < 0) parts.push("sale closed over a year ago");
    return parts;
  }
  function whyShort(r) { const s = whyParts(r).join(" · "); return s ? s[0].toUpperCase() + s.slice(1) : ""; }
  function whyThisLead(r) {
    const bd = r.breakdown || {};
    let s = "Ranked " + r.rank + " of " + fmtInt(S.data.items.length) + ": " + whyParts(r).join("; ");
    if (bd.latest_signal_date) s += ". Latest signal " + fmtDate(bd.latest_signal_date);
    if (bd.buyer_diversity_cap) s += ". Shown just below the top 20 because that buyer already has two leads there";
    return s + ".";
  }

  /* ---------- data ---------- */
  async function load() {
    const res = await fetch("/api/snapshot.json", { credentials: "same-origin" });
    if (!res.ok) throw new Error("snapshot " + res.status);
    S.data = await res.json();
    S.data.items = S.data.items || [];
    for (const r of S.data.items) {
      for (const k of ["care_types", "signals", "flags", "sources", "lists", "not_found"]) r[k] = r[k] || [];
      if (/\(unnamed\)/i.test(r.buyer || "") && r.buyer_group === "confirmed") r.buyer_group = "candidate";     // a placeholder is never a named buyer
    }
    S.byRank = S.data.items.slice().sort((a, b) => a.rank - b.rank);
    S.buyers = groupBuyers();
    $("asof").textContent = "As of " + fmtDate(S.data.generated_at);
    $("loading").classList.add("hidden");
    buildFilters();
    buildTabs();
    render();
    buildBuyers();
    buildKpis();
    buildFunnel();
    buildHow();
    buildExplore();
    buildSearch();
    await buildMap().catch(() => { $("stage").replaceChildren(el("p", { class: "state-title", text: "The map could not be drawn. Use search, or open Leads." })); });
    const barH = () => document.documentElement.style.setProperty("--bar-h", $("topbar").offsetHeight + "px");    // sticky group headers sit under the bar
    barH(); window.addEventListener("resize", barH);
    window.addEventListener("hashchange", route);
    route();
  }

  /* ---------- pages ---------- */
  const VIEWS = { "#/leads": "leads", "#/buyers": "buyers", "#/overview": "overview", "#/funnel": "overview", "#/how": "how", "#/explore": "explore" };
  const VIEW_IDS = ["dashboard", "leads", "buyers", "overview", "how", "explore"];
  function route() {
    const h = location.hash;
    const listRoute = /^#\/leads\/([ABC])$/.exec(h), buyerQ = /^#\/buyers\?q=(.*)$/.exec(h);
    const v = listRoute ? "leads" : buyerQ ? "buyers" : VIEWS[h] || "dashboard";
    const changed = S.view !== v;
    if (changed) stopMotion();
    S.view = v;
    document.body.dataset.view = v;
    for (const id of VIEW_IDS) $("view-" + id).classList.toggle("hidden", id !== v);
    const about = v === "how" || v === "overview";
    $("subnav").classList.toggle("hidden", !about);
    document.querySelectorAll(".nav a, .subnav a").forEach((a) => {
      const on = a.dataset.view === v || (a.dataset.view === "help" && about);
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    hideTip();
    if (v !== "dashboard") hidePanel();
    if (listRoute) selectTab(listRoute[1]);
    if (buyerQ) { try { $("b-search").value = decodeURIComponent(buyerQ[1]); } catch (e) { $("b-search").value = ""; } renderBuyers(); }
    if (v === "dashboard") { parseMapHash(h); renderMap(); }
    if (changed) { window.scrollTo({ top: 0, behavior: "instant" }); animateView(v); }
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

  /* ---------- the narrowing (How it works): many buildings become a short list ---------- */
  function narrowSteps() {
    const F = S.data.funnel || [], find = (name) => F.find((f) => f.step === name);
    const n = S.data.items.length;
    return [[find("Buildings tracked"), "buildings tracked", "Every senior-housing and nursing building in the public sources."],
      [find("With a distress signal"), "show a distress signal", "At least one dated signal in the last 36 months. Most stay on the watch list."],
      [{ count: n }, "leads", "Three or more kinds of signal, or one hard event such as a bankruptcy or receivership."],
      [{ count: Math.min(20, n) }, "at the top", "The highest-ranked leads: live sales with a buyer in the picture."]]
      .filter(([f]) => f && f.count != null);
  }
  function buildNarrow(target) {
    const steps = narrowSteps(), max = Math.max(1, ...steps.map(([f]) => f.count));
    const w = (c) => (c > 0 ? Math.max(0.04, Math.log(c + 1) / Math.log(max + 1)) : 0);
    $(target).replaceChildren(...steps.map(([f, label, rule], i) => el("li", { style: "--i:" + i + ";--w:" + w(f.count).toFixed(3) },
      el("span", { class: "n", "data-n": f.count, text: fmtInt(f.count) }), el("span", { class: "l", text: label }),
      el("span", { class: "bar", "aria-hidden": "true" }), el("span", { class: "r", text: rule }))));
  }

  /* ---------- 4. The numbers ---------- */
  function buildKpis() {
    const items = S.data.items, n = SUM("leads", items.length);
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
  // "533 leads = List A 241 + List B 234 + 58 other (...). List C = ..." from the snapshot's summary block
  function reconLine(m) {
    if (!m) return "";
    const o = m.other_breakdown || {};
    const parts = [[o.candidate, "stalking-horse candidates"], [o.stale, "older ownership changes"],
      [o.sale_no_public_buyer, "sales under way with no public buyer"],
      [o.sale_done_no_public_buyer, "sales approved or closed with no public buyer"], [o.shell_or_closed, "shell or closed"]]
      .filter(([n]) => n > 0).map(([n, t]) => fmtInt(n) + " " + t);
    return fmtInt(m.leads) + " leads = List A " + fmtInt(m.list_a) + " (buyer named) + List B " + fmtInt(m.list_b) +
      " (no buyer yet) + " + fmtInt(m.other) + " other" + (parts.length ? " (" + parts.join(", ") + ")" : "") +
      ". List C = " + fmtInt(m.list_c_buyers) + " repeat buyers, linked to " + fmtInt(m.list_c_linked_leads) + " of these leads.";
  }
  function buildFunnel() {
    $("funnel-recon").textContent = reconLine(S.data.summary);
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
    const ctf = R.care_type_fit || {}, dv = R.buyer_diversity || {};
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
      rule("Care-type fit (assumption)", "Assisted living, independent living, memory care and CCRC buildings add " + (ctf.points || 0) +
        " points. This weighting is an assumption still to be confirmed."),
      rule("Top 20 mix", "To keep the top of each list varied, at most " + (dv.per_buyer || 2) + " leads of one buyer appear in its top " + (dv.top_n || 20) +
        "; further leads of that buyer keep their score and are shown just below rank " + (dv.top_n || 20) + "."),
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
    $("f-care").replaceChildren(option("", "All care types"), option("senior", "Senior housing (AL / IL / MC / CCRC)"),
      option("SNF", "Skilled nursing (SNF)"), ...SENIOR_TYPES.map((c) => option(c, c + " only")));
    $("f-stage").replaceChildren(option("", "All stages"), ...STAGES.map((s) => option(s, STAGE_LABEL[s])));
    $("f-buyer").replaceChildren(option("", "Any buyer status"), option("confirmed", "Buyer named"), option("candidate", "Candidate (not final)"),
      option("inferred", "Unverified"), option("none", "No buyer found"));
    for (const id of FILTER_IDS) $(id).addEventListener("input", () => { S.page = 1; S.sel = -1; render(); });
    $("f-state").addEventListener("input", (e) => setState(e.target.value));
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
    $("f-state").value = code;
    render();
  }
  /* Buyer provenance notes (PR #17), built only from snapshot fields: flags, breakdown.buyer_parent(+basis),
     breakdown.buyer_facilities_basis. No buyer names live in this file. */
  const BASIS = { cms_chain: "CMS chain", cms: "CMS", source: "stated in a source", inferred: "inferred" };
  const basisText = (b) => BASIS[b] || pretty(b);
  function megaNote(r) {
    if (!r.flags.includes("buyer_mega_platform")) return null;
    const b = (r.breakdown || {}).buyer_facilities_basis;
    return "mega-platform" + (b === "inferred" ? ", size inferred" : b ? ", size per " + basisText(b) : "");
  }
  function parentNote(r) {
    const bd = r.breakdown || {};
    return bd.buyer_parent ? "parent: " + bd.buyer_parent + (bd.buyer_parent_basis ? ", " + basisText(bd.buyer_parent_basis) : "") : null;
  }
  const roleNote = (r) => r.flags.includes("buyer_operating_transferee") ? "operating transferee; real-estate buyer not verified"
    : r.flags.includes("buyer_court_approved_purchaser") ? "court-approved purchaser; closing not verified" : null;
  const buyerNotes = (r) => [parentNote(r), megaNote(r), roleNote(r)].filter(Boolean).map((t) => "(" + t + ")").join(" ");
  const buyerNameEl = (r, cls) => el("span", { class: cls || "bname", title: r.buyer }, r.buyer, buyerNotes(r) ? el("span", { class: "bnote", text: " " + buyerNotes(r) }) : null);
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
    const have = { A: 0, B: 0 };
    for (const r of S.data.items) for (const l of r.lists) have[l] = (have[l] || 0) + 1;
    const counts = { all: SUM("leads", S.data.items.length), A: SUM("list_a", have.A), B: SUM("list_b", have.B), C: SUM("list_c_buyers", (S.data.list_c || []).length) };
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
    }, el("span", { class: "tl", text: t.label }), el("span", { class: "count", text: fmtInt(counts[t.key] || 0) + (t.key === "C" ? " buyers" : "") }),
      t.sub ? el("span", { class: "ts", text: t.sub }) : null)));
    for (const id of ["f-state", "f-care", "f-stage", "f-buyer"]) $(id).classList.toggle("hidden", S.tab === "C");
  }

  /* ---------- leads: brief rows (default) and sortable table ---------- */
  const LEAD_COLS = [
    { key: "rank", label: "Rank", sort: (r) => listRank(r), num: true },
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
    { key: "homes", label: "Homes acquired (36 mo)", sort: (c) => c.homes_in_list, num: true },
    { key: "examples", label: "Examples" },
    { key: "source", label: "Source" },
  ];
  function listRank(r) {             // List A / B: position in that list (its own display order)
    return (S.tab === "A" && r.rank_a) || (S.tab === "B" && r.rank_b) || r.rank;
  }
  function cell(col, r) {
    switch (col.key) {
      case "rank": return el("td", { class: "num rank", text: String(listRank(r)) });
      case "property": return el("td", { class: "prop", text: r.property || "" });
      case "state": return el("td", {}, place(r) || nf());
      case "care": return el("td", {}, careText(r) || nf());
      case "units": return el("td", { class: "num" }, size(r) != null ? fmtInt(size(r)) : nf());
      case "stage": return el("td", {}, stageMark(r));
      case "buyer": return el("td", { class: "buyer" }, buyerChip(r.buyer_group), r.buyer ? buyerNameEl(r) : null);
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
      el("span", { class: "rank", text: String(listRank(r)) }),
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

  /* ---------- Buyers: who is taking these properties over ---------- */
  const B_SECTIONS = [["confirmed", "Named"], ["candidate", "Candidate · not final"], ["inferred", "Unverified"]];
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
      g.states = [...new Set(g.leads.map((r) => r.state).filter(Boolean))].sort();
      g.care = [...new Set(g.leads.flatMap((r) => r.care_types))];
      const bd = (g.leads.find((r) => (r.breakdown || {}).buyer_type) || {}).breakdown || {};
      g.type = bd.buyer_type ? pretty(bd.buyer_type) : null;
      g.insider = g.leads.some((r) => r.flags.includes("buyer_possible_insider"));
      g.notes = [...new Set(g.leads.map(buyerNotes).filter(Boolean))];
    }
    return out.sort((a, b) => b.leads.length - a.leads.length || a.leads[0].rank - b.leads[0].rank);
  }
  function buildBuyers() {
    $("b-status").replaceChildren(option("", "All groups"), ...B_SECTIONS.map(([k, l]) => option(k, l)), option("C", "List C · repeat buyers"));
    for (const id of ["b-search", "b-status"]) $(id).addEventListener("input", renderBuyers);
    renderBuyers();
  }
  function leadLink(r, ctx) {
    return el("button", { class: "leadlink", type: "button", onclick: (e) => openPanel(r, e.currentTarget, ctx) },
      el("span", { class: "rk", text: String(r.rank) }), el("span", { class: "nm", text: r.property || "" }),
      el("span", { class: "pl", text: [cityName(r.city), r.state].filter(Boolean).join(", ") }), el("span", { class: "st", text: stageText(r) }));
  }
  function buyerBlock(g) {
    const n = g.leads.length, SHOW = 3;
    const states = g.states.length > 6 ? plural(g.states.length, "state") : g.states.join(", ");
    return el("article", { class: "buyer-block" },
      el("h3", {}, g.name, g.notes.length === 1 ? el("span", { class: "bnote", text: " " + g.notes[0] }) : null),
      el("p", { class: "b-meta" }, plural(n, "lead"), states ? " · " + states : "", g.care.length ? " · " + g.care.join(" / ") : "", g.type ? " · " + g.type : "",
        g.repeat ? " · List C: " + plural(g.repeat.acquisitions_36mo ?? g.repeat.homes_in_list, "home") + " acquired" : "",
        g.insider ? [" ", el("span", { class: "chip plain", text: "Possible insider", "data-tip": "This buyer shares a brand with the seller, so the sale may be to a related party (inferred)." })] : null),
      el("div", { class: "leadlinks" }, g.leads.slice(0, SHOW).map((r) => leadLink(r, g.leads))),
      n > SHOW ? el("details", { class: "more" }, el("summary", { text: "All " + fmtInt(n) + " leads" }), el("div", { class: "leadlinks" }, g.leads.slice(SHOW).map((r) => leadLink(r, g.leads)))) : null);
  }
  function repeatBlock(c) {
    const u = safeUrl(c.public_source_link), homes = c.acquisitions_36mo ?? c.homes_in_list;
    return el("article", { class: "buyer-block" },
      el("h3", { text: c.buyer || "" }),
      el("p", { class: "b-meta" }, homes != null ? plural(homes, "home") + " acquired in 36 months" : ["homes acquired ", nf()],
        c.buyer_type && c.buyer_type !== "NOT FOUND" ? " · " + pretty(c.buyer_type) : "", c.latest_date && c.latest_date !== "NOT FOUND" ? " · latest " + fmtDate(c.latest_date) : "",
        u ? [" · ", ext(u, hostOf(u))] : null),
      c.example_homes ? el("p", { class: "b-ex", text: c.example_homes }) : null);
  }
  function renderBuyers() {
    const q = $("b-search").value.trim().toLowerCase(), st = $("b-status").value, sum = S.data.summary || {};
    const match = (g) => !q || g.name.toLowerCase().includes(q) || g.leads.some((r) => (r.property || "").toLowerCase().includes(q));
    const sections = [];
    for (const [key, label] of B_SECTIONS) {
      if (st && st !== key) continue;
      const rows = S.buyers.filter((g) => g.group === key && match(g));
      if (rows.length) sections.push({ id: "b-" + key, label, n: rows.length, unit: plural(rows.length, "buyer").replace(/^[\d,]+ /, ""), tip: BUYER_TIP[key], kids: rows.map(buyerBlock) });
    }
    if (!st || st === "C") {
      const all = S.data.list_c || [], rows = all.filter((c) => !q || [c.buyer, c.example_homes].join(" ").toLowerCase().includes(q));
      const CAP = 25;
      if (rows.length) sections.push({ id: "b-C", label: "List C · repeat buyers", n: q ? rows.length : sum.list_c_buyers ?? all.length, unit: "buyers",
        tip: "List C: buyers that took over two or more homes in the last 36 months, from public ownership records. " +
          (sum.list_c_linked_leads != null ? fmtInt(sum.list_c_linked_leads) + " of today's leads are linked to one of them." : ""),
        kids: [...rows.slice(0, CAP).map(repeatBlock), rows.length > CAP ? el("details", { class: "more" }, el("summary", { text: "All " + fmtInt(rows.length) + " repeat buyers" }), rows.slice(CAP).map(repeatBlock)) : null] });
    }
    $("b-jump").replaceChildren(...sections.map((s) => el("a", { class: "jump", href: "#" + s.id, onclick: (e) => { e.preventDefault(); $(s.id).scrollIntoView({ block: "start" }); } },
      s.label, el("b", { text: fmtInt(s.n) }))));
    $("buyers").replaceChildren(...sections.map((s) => el("section", { class: "b-section", id: s.id, "aria-label": s.label },
      el("h2", { class: "b-sec-head" }, el("span", { class: "t", text: s.label }), el("span", { class: "n", text: fmtInt(s.n) + " " + s.unit }), s.tip ? info(s.tip, "", "b-info") : null),
      s.kids.filter(Boolean))));
    $("b-empty").classList.toggle("hidden", sections.length > 0);
  }

  /* ---------- the map is the product: L0 USA > L1 state > L2 city > L3 property card > L4 research ----------
     The URL hash holds the whole state (#/map/PA/pittsburgh/r4/research?l=AB), so Back and Esc go up one level. */
  const M = { layers: new Set(), st: "", city: "", lead: null, research: false, k: 1, ready: false, rows: [] };
  const NOSTATE = "--", NOCITY = "-";
  const LAYERS = [["A", "Live deals"], ["B", "Pre-sale"]];
  const leadKey = (r) => (r.lead_id ? String(r.lead_id) : "r" + r.rank);     // stable id from the snapshot; rank only if it is missing
  const cityKey = (r) => (r.city ? r.city.trim().toLowerCase() : NOCITY);
  const cityName = (c) => String(c || "").toLowerCase().replace(/(^|[\s\-'.(])([a-z])/g, (m, a, b) => a + b.toUpperCase());
  const inLayers = (r) => !M.layers.size || [...M.layers].some((l) => r.lists.includes(l));
  function mapHash(o) {
    o = Object.assign({ st: M.st, city: M.city, lead: M.lead, research: false, layers: M.layers }, o);
    let h = "#/map";
    if (o.st) { h += "/" + o.st;
      if (o.city) { h += "/" + encodeURIComponent(o.city);
        if (o.lead) h += "/" + leadKey(o.lead) + (o.research ? "/research" : ""); } }
    const l = [...o.layers].sort().join("");
    return h + (l ? "?l=" + l : "");
  }
  const leadHash = (r, o) => mapHash(Object.assign({ st: r.state || NOSTATE, city: cityKey(r), lead: r }, o));
  function parseMapHash(h) {
    const [path, query] = h.replace(/^#\/?(map)?\/?/, "").split("?");
    let seg = [];
    try { seg = path ? path.split("/").map(decodeURIComponent) : []; } catch (e) { seg = []; }
    M.layers = new Set((((/(?:^|&)l=([AB]+)/.exec(query || "")) || [])[1] || "").split("").filter(Boolean));
    M.st = seg[0] && (STATES[seg[0]] || seg[0] === NOSTATE) ? seg[0] : "";
    M.city = M.st ? seg[1] || "" : "";
    M.lead = M.city && seg[2] ? S.byRank.find((r) => leadKey(r) === seg[2]) || null : null;
    M.research = !!M.lead && seg[3] === "research";
  }
  const go = (h) => { if (location.hash === h) route(); else location.hash = h; };
  /* Coordinates: used only when the snapshot carries them. Never estimated. */
  let projection = null;
  function coord(r) {
    if (r._xy !== undefined) return r._xy;
    const c = r.contact || {};
    const lat = Number(r.lat ?? r.latitude ?? c.lat ?? c.latitude), lon = Number(r.lon ?? r.lng ?? r.longitude ?? c.lon ?? c.lng ?? c.longitude);
    const has = [r.lat, r.latitude, c.lat, c.latitude].some((v) => v != null && v !== "") && isFinite(lat) && isFinite(lon) && (lat || lon);
    r._xy = has && projection ? projection([lon, lat]) || null : null;
    return r._xy;
  }
  const stageColor = (r) => token("--stage-" + (STAGES.includes(r.stage) ? r.stage : "early"));
  const pings = (r) => r.stage === "late" || r.stage === "sale" || r.buyer_group === "confirmed";

  async function buildMap() {
    const topo = await d3.json("/static/states-albers-10m.json");
    projection = d3.geoAlbersUsa().scale(1300).translate([487.5, 305]);      // the projection the bundled state shapes use
    const states = topojson.feature(topo, topo.objects.states).features;
    M.path = d3.geoPath();
    M.feat = Object.fromEntries(states.map((f) => [CODE_BY_NAME[f.properties.name], f]));
    const svg = d3.select("#map");
    M.zoom = svg.append("g").attr("class", "zoom").attr("transform", "translate(0,0) scale(1)");
    const codeOf = (f) => CODE_BY_NAME[f.properties.name];
    M.states = M.zoom.append("g").selectAll("path").data(states).join("path").attr("class", "state").attr("d", M.path).attr("role", "button")
      .on("mousemove", (ev, f) => showTip(f.properties.name + " · " + plural((M.counts && M.counts.get(codeOf(f))) || 0, "lead"), ev.clientX, ev.clientY + 8))
      .on("mouseleave", hideTip)
      .on("click", (ev, f) => { ev.stopPropagation(); hideTip(); if (M.counts.get(codeOf(f))) go(mapHash({ st: codeOf(f), city: "", lead: null })); })
      .on("keydown", (ev, f) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); if (M.counts.get(codeOf(f))) go(mapHash({ st: codeOf(f), city: "", lead: null })); } });
    M.zoom.append("path").datum(topojson.mesh(topo, topo.objects.states, (a, b) => a !== b)).attr("class", "mesh").attr("d", M.path);
    M.marks = M.zoom.append("g").attr("class", "marks");
    svg.on("click", () => { if (M.lead) go(mapHash({ lead: null })); });
    window.addEventListener("resize", () => { if (S.view === "dashboard") renderMap(); });
    M.ready = true;
  }

  function renderMap() {
    if (!S.data) return;
    const all = S.byRank.filter(inLayers);
    const stRows = M.st ? all.filter((r) => (M.st === NOSTATE ? !r.state : r.state === M.st)) : all;
    const rows = M.city ? stRows.filter((r) => cityKey(r) === M.city) : stRows;
    M.rows = rows;
    const level = M.lead ? 3 : M.city ? 2 : M.st ? 1 : 0;
    $("view-dashboard").dataset.level = level;
    const stName = M.st === NOSTATE ? "State not found" : STATES[M.st] || "";
    const ctName = M.city === NOCITY ? "City not found" : cityName(M.city);

    // breadcrumb + title
    const crumb = (label, hash, here) => (here ? el("span", { class: "here", "aria-current": "page", text: label }) : el("a", { href: hash, text: label }));
    const parts = [crumb("USA", mapHash({ st: "", city: "", lead: null }), level === 0)];
    if (level >= 1) parts.push(crumb(stName, mapHash({ city: "", lead: null }), level === 1));
    if (level >= 2) parts.push(crumb(ctName, mapHash({ lead: null }), level === 2));
    if (level >= 3) parts.push(crumb(M.lead.property || "Property", "", true));
    $("crumbs").replaceChildren(...parts.flatMap((p, i) => (i ? [el("span", { class: "sep", "aria-hidden": "true", text: "›" }), p] : [p])));
    $("m-title").textContent = level === 0 ? "United States" : level === 1 ? stName : ctName + (M.st !== NOSTATE ? ", " + M.st : "");

    // three numbers, never more
    const named = rows.filter((r) => r.buyer_group === "confirmed").length;
    const mid = level === 0 ? [new Set(rows.map((r) => r.state).filter(Boolean)).size, "states"]
      : level === 1 ? [new Set(rows.map(cityKey)).size, "cities"] : [rows.filter((r) => phoneText((r.contact || {}).phone)).length, "with phone"];
    const nLeads = level === 0 && !M.layers.size ? SUM("leads", rows.length) : rows.length;
    $("hud").replaceChildren(...[[nLeads, nLeads === 1 ? "lead" : "leads"], mid, [named, "buyer named"]].map(([n, label]) =>
      el("div", {}, el("dt", { text: label }), el("dd", { "data-n": n, text: fmtInt(n) }))));

    // layer toggles
    const base = M.st ? S.byRank.filter((r) => (M.st === NOSTATE ? !r.state : r.state === M.st) && (!M.city || cityKey(r) === M.city)) : S.byRank;
    $("layers").replaceChildren(...LAYERS.map(([key, label]) => {
      const on = M.layers.has(key), n = M.st ? base.filter((r) => r.lists.includes(key)).length : SUM(key === "A" ? "list_a" : "list_b", base.filter((r) => r.lists.includes(key)).length);
      return el("button", { class: "layer", type: "button", "aria-pressed": String(on), "aria-label": "List " + key + ", " + label + ": " + n + " leads",
        onclick: () => { const next = new Set(M.layers); if (on) next.delete(key); else next.add(key); go(mapHash({ layers: next, lead: null })); } },
        el("b", { text: key }), el("span", { text: label }), el("i", { text: fmtInt(n) }));
    }), el("a", { class: "layer link", href: "#/buyers", "aria-label": "List C: " + SUM("list_c_buyers", (S.data.list_c || []).length) + " repeat buyers, shown in Buyers",
      onclick: () => { $("b-status").value = "C"; renderBuyers(); } },
      el("b", { text: "C" }), el("span", { text: fmtInt(SUM("list_c_buyers", (S.data.list_c || []).length)) + " buyers" }), el("i", { "aria-hidden": "true", text: "→" })));

    if (M.ready) drawMap(all, stRows, rows, level);
    renderSide(stRows, rows, level);
    renderCard();
    if (M.research) openPanel(M.lead, null, rows); else hidePanel();
  }

  function drawMap(all, stRows, rows, level) {
    const codeOf = (f) => CODE_BY_NAME[f.properties.name];
    const counts = d3.rollup(all, (v) => v.length, (r) => r.state);
    M.counts = counts;
    const max = d3.max([...counts.values()]) || 1;
    const color = d3.scaleSequentialSqrt([0, max], d3.interpolateRgb(token("--map-low"), token("--map-high")));
    const n = (f) => counts.get(codeOf(f)) || 0;
    M.states.attr("fill", (f) => (n(f) ? color(n(f)) : token("--map-empty")))
      .classed("none", (f) => !n(f)).classed("selected", (f) => codeOf(f) === M.st).classed("dim", (f) => !!M.feat[M.st] && codeOf(f) !== M.st)
      .attr("tabindex", (f) => (n(f) && level === 0 ? 0 : null)).attr("aria-label", (f) => f.properties.name + ": " + plural(n(f), "lead"));

    // where to fly: the whole country, a state, or the pins of a city
    const W = 975, H = 610, feat = M.feat[M.st];
    const pins = level >= 2 ? rows.map((r) => [r, coord(r)]).filter((x) => x[1]) : [];
    let b = null;
    if (pins.length) {
      const xs = pins.map((p) => p[1][0]), ys = pins.map((p) => p[1][1]), pad = 10;     // never closer than about a 60-mile window: positions are ZIP centres
      b = [[Math.min(...xs) - pad, Math.min(...ys) - pad], [Math.max(...xs) + pad, Math.max(...ys) + pad]];
    } else if (feat) b = M.path.bounds(feat);
    let k = 1, tx = 0, ty = 0;
    if (b) { k = Math.min(40, 0.88 / Math.max((b[1][0] - b[0][0]) / W, (b[1][1] - b[0][1]) / H));
      tx = W / 2 - (k * (b[0][0] + b[1][0])) / 2; ty = H / 2 - (k * (b[0][1] + b[1][1])) / 2; }
    M.k = k;
    M.zoom.transition().duration(REDUCED ? 0 : 750).ease(d3.easeCubicInOut).attr("transform", "translate(" + tx + "," + ty + ") scale(" + k + ")");

    // marks are sized in screen pixels, whatever the zoom
    const box = $("map").getBoundingClientRect();
    const u = (1 / Math.max(0.01, Math.min(box.width / W, box.height / H))) / k;
    const key = (ev, fn) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); } };
    M.marks.selectAll("*").remove();
    M.hasPins = pins.length > 0;
    M.hasBubbles = false;
    if (level === 1) {
      const byCity = d3.groups(stRows.filter(coord), cityKey).map(([c, v]) => ({ c, v, x: d3.mean(v, (r) => coord(r)[0]), y: d3.mean(v, (r) => coord(r)[1]) }))
        .sort((a, b2) => b2.v.length - a.v.length);
      M.hasBubbles = byCity.length > 0;
      const g = M.marks.selectAll("g").data(byCity).join("g").attr("class", "bubble").attr("transform", (d) => "translate(" + d.x + "," + d.y + ")")
        .attr("tabindex", 0).attr("role", "button").attr("aria-label", (d) => cityName(d.c) + ": " + plural(d.v.length, "lead"))
        .on("click", (ev, d) => { ev.stopPropagation(); hideTip(); go(mapHash({ city: d.c, lead: null })); })
        .on("keydown", (ev, d) => key(ev, () => go(mapHash({ city: d.c, lead: null }))))
        .on("mousemove", (ev, d) => showTip(cityName(d.c) + " · " + plural(d.v.length, "lead"), ev.clientX, ev.clientY + 8)).on("mouseleave", hideTip);
      const rad = (d) => Math.min(26, 7 + 4 * Math.sqrt(d.v.length)) * u;
      g.append("circle").attr("r", rad);
      g.filter((d) => d.v.length > 1).append("text").attr("dy", "0.35em").attr("font-size", 12 * u).text((d) => d.v.length);
    } else if (pins.length) {
      // other cities stay on screen as quiet dots, so you can hop sideways without going back up
      const others = d3.groups(stRows.filter((r) => coord(r) && cityKey(r) !== M.city), cityKey)
        .map(([c, v]) => ({ c, v, x: d3.mean(v, (r) => coord(r)[0]), y: d3.mean(v, (r) => coord(r)[1]) }));
      M.marks.selectAll("g.ctx").data(others).join("g").attr("class", "ctx").attr("transform", (d) => "translate(" + d.x + "," + d.y + ")")
        .on("click", (ev, d) => { ev.stopPropagation(); hideTip(); go(mapHash({ city: d.c, lead: null })); })
        .on("mousemove", (ev, d) => showTip(cityName(d.c) + " · " + plural(d.v.length, "lead"), ev.clientX, ev.clientY + 8)).on("mouseleave", hideTip)
        .append("circle").attr("r", 5 * u);
      // range rings give the scale: 1 degree of latitude is about 69 miles, 22.7 map units in this projection
      const cx = d3.mean(pins, (p) => p[1][0]), cy = d3.mean(pins, (p) => p[1][1]), mile = (1300 * Math.PI / 180) / 69.17;
      const rings = M.marks.selectAll("g.ring").data([5, 15, 30]).join("g").attr("class", "ring").attr("transform", "translate(" + cx + "," + cy + ")");
      rings.append("circle").attr("r", (d) => d * mile);
      rings.append("text").attr("y", (d) => -d * mile - 4 * u).attr("font-size", 11 * u).text((d) => d + " mi");
      const rad = (r) => (size(r) ? Math.max(4, Math.min(12, 1.5 + Math.sqrt(size(r)) * 0.62)) : 4) * u;
      // Leads in one ZIP code share one centre point. Fan them out a few screen pixels so each can be clicked;
      // the offset is a display device, not a location (the legend says positions are approximate).
      const spot = d3.groups(pins, (p) => p[1][0].toFixed(3) + "," + p[1][1].toFixed(3));
      for (const [, grp] of spot) grp.forEach((p, i) => { const n2 = grp.length, a = (2 * Math.PI * i) / n2, d = n2 > 1 ? (10 + 2.5 * n2) * u : 0;
        p[2] = [p[1][0] + d * Math.cos(a), p[1][1] + d * Math.sin(a)]; });
      M.marks.selectAll("circle.zip").data(spot.filter(([, grp]) => grp.length > 1)).join("circle").attr("class", "zip")
        .attr("cx", (d) => d[1][0][1][0]).attr("cy", (d) => d[1][0][1][1]).attr("r", (d) => (23 + 2.5 * d[1].length) * u);
      const sorted = pins.slice().sort((a, b2) => (size(b2[0]) || 0) - (size(a[0]) || 0));          // small pins stay on top
      const g = M.marks.selectAll("g.pin").data(sorted).join("g").attr("class", (d) => "pin" + (d[0] === M.lead ? " sel" : "") + (size(d[0]) ? "" : " nosize"))
        .attr("transform", (d) => "translate(" + d[2][0] + "," + d[2][1] + ")").attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", (d) => d[0].property + ", " + (sizeText(d[0]) || "size not found") + ", " + stageText(d[0]))
        .on("click", (ev, d) => { ev.stopPropagation(); hideTip(); go(leadHash(d[0])); })
        .on("keydown", (ev, d) => key(ev, () => go(leadHash(d[0]))))
        .on("mousemove", (ev, d) => showTip(d[0].property + " · " + (sizeText(d[0]) || "size not found") + (d[0].coord_source === "zip_centroid" ? " · approx. position" : ""), ev.clientX, ev.clientY + 8)).on("mouseleave", hideTip);
      g.filter((d) => pings(d[0])).append("circle").attr("class", "ping").attr("r", (d) => rad(d[0])).attr("stroke", (d) => stageColor(d[0])).attr("stroke-width", 1.5 * u);
      g.append("circle").attr("class", "dot").attr("r", (d) => rad(d[0])).attr("fill", (d) => stageColor(d[0])).attr("stroke-width", (d) => (d[0] === M.lead ? 3 : 1.25) * u);
    }

    const bare = level >= 1 && !M.hasBubbles && !M.hasPins && !!feat;
    let note = $("stage").querySelector(".stage-note");
    if (!note) { note = el("p", { class: "stage-note" }); $("stage").append(note); }
    note.classList.toggle("hidden", !bare);
    note.replaceChildren("Map positions ", nf());

    // legend: one row of marks, no sentences
    const approx = stRows.some((r) => r.coord_source === "zip_centroid" && coord(r));
    const approxNote = approx ? el("span", { class: "lg approx", text: "Positions = ZIP-code centres (approximate)", "data-tip": "Each lead is placed at the centre of its ZIP code, not at its street address. Leads without a known ZIP are in the list only." }) : null;
    const ramp = el("span", { class: "ramp", "aria-hidden": "true" }, [0.05, 0.25, 0.5, 0.75, 1].map((t) => el("i", { style: "background:" + color(t * max) })));
    $("legend").replaceChildren(...(M.hasPins
      ? [el("span", { class: "lg" }, STAGES.map((s) => el("span", { class: "lg-item" }, el("i", { class: "dotkey", style: "background:" + token("--stage-" + s) }), STAGE_LABEL[s]))),
         el("span", { class: "lg" }, el("i", { class: "dotkey s" }), el("i", { class: "dotkey l" }), "size = units / beds"), approxNote]
      : [el("span", { class: "lg" }, "1", ramp, fmtInt(max) + " leads"), M.hasBubbles ? approxNote : null].filter(Boolean)));
  }

  function leadRow(r) {
    return el("a", { class: "lrow" + (r === M.lead ? " sel" : ""), href: leadHash(r), "aria-current": r === M.lead ? "true" : null },
      el("span", { class: "nm", text: r.property || "" }),
      el("span", { class: "pl" }, cityName(r.city) || nf(), " · ", sizeText(r) || ["size ", nf()]),
      el("span", { class: "chips" }, stageMark(r), buyerChip(r.buyer_group),
        phoneText((r.contact || {}).phone) ? el("span", { class: "ph tnum", text: phoneText(r.contact.phone) }) : null));
  }
  function renderSide(stRows, rows, level) {
    const side = $("side");
    side.classList.toggle("hidden", level === 0);
    if (level === 0) return side.replaceChildren();
    const kids = [];
    if (level === 1 && !M.hasBubbles) {
      const cities = d3.groups(stRows, cityKey).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
      const cityRow = ([c, v]) => el("a", { class: "crow-city", href: mapHash({ city: c, lead: null }) },
        el("span", { class: "nm", text: c === NOCITY ? "City not found" : cityName(c) }), el("span", { class: "bar", style: "--w:" + (v.length / cities[0][1].length).toFixed(3) }), el("b", { text: fmtInt(v.length) }));
      kids.push(el("h2", {}, "Cities", el("span", { class: "n", text: fmtInt(cities.length) })),
        el("div", { class: "citylist" }, cities.slice(0, 6).map(cityRow)),
        cities.length > 6 ? el("details", { class: "more" }, el("summary", { text: "All " + cities.length + " cities" }), el("div", { class: "citylist" }, cities.slice(6).map(cityRow))) : null);
    }
    kids.push(el("h2", {}, "Leads", el("span", { class: "n", text: fmtInt(rows.length) })));
    const CAP = 20, selIdx = rows.indexOf(M.lead);
    kids.push(el("div", { class: "lrows" }, rows.slice(0, CAP).map(leadRow)),
      rows.length > CAP ? el("details", { class: "more", open: selIdx >= CAP }, el("summary", { text: "All " + fmtInt(rows.length) + " leads" }),
        el("div", { class: "lrows" }, rows.slice(CAP).map(leadRow))) : null);
    if (!rows.length) kids.push(el("p", { class: "muted", text: "No leads here with these lists on." }));
    const top = side.scrollTop;
    side.replaceChildren(...kids.filter(Boolean));
    side.scrollTop = M.sideKey === M.st + "/" + M.city ? top : 0;
    M.sideKey = M.st + "/" + M.city;
    const sel = side.querySelector(".lrow.sel");
    if (sel && sel.scrollIntoView && window.innerWidth > 960) sel.scrollIntoView({ block: "nearest" });
  }

  /* L3: the card. Two decisions only: call, or research. */
  function renderCard() {
    const card = $("card"), r = M.lead;
    card.classList.toggle("hidden", !r);
    if (!r) return card.replaceChildren();
    const tel = phoneText((r.contact || {}).phone) ? r.contact.phone : null, bd = r.breakdown || {};
    card.replaceChildren(...[
      el("button", { class: "btn quiet icon x", type: "button", "aria-label": "Close card", onclick: (e) => { e.stopPropagation(); go(mapHash({ lead: null })); } }, "✕"),
      el("p", { class: "eyebrow", text: "Rank " + r.rank + " · score " + (r.score == null ? "–" : Math.round(r.score)) }),
      el("h2", { text: r.property || "" }),
      el("p", { class: "meta" }, [cityName(r.city), r.state].filter(Boolean).join(", ") || nf(), " · ", sizeText(r) || ["size ", nf()], " · ", careText(r) || ["care ", nf()]),
      el("p", { class: "why", text: whyShort(r) }),
      el("div", { class: "chips" }, stageMark(r), buyerChip(r.buyer_group), bd.size_fit > 0 ? el("span", { class: "chip plain", text: "fits $3–30M", "data-tip": "Size fit: " + sizeText(r) + " is inside the range the rules treat as a $3–30M loan. No dollar value is estimated." }) : null),
      r.buyer ? buyerNameEl(r, "bname p") : null,
      el("div", { class: "card-actions" },
        tel ? el("a", { class: "btn primary big", href: "tel:+1" + tel }, "Call ", el("span", { class: "tnum", text: phoneText(tel) }))
          : el("span", { class: "btn big nfbtn", role: "note" }, "Phone ", nf()),
        el("a", { class: "btn big", href: leadHash(r, { research: true }), text: "Research" }))].filter(Boolean));
    card.onclick = (e) => e.stopPropagation();
  }

  /* ---------- search: property / buyer / city / state, jumps straight to that layer ---------- */
  const Q = { items: [], active: -1 };
  function buildSearch() {
    const idx = [], none = new Set();
    const byState = d3.rollup(S.byRank, (v) => v.length, (r) => r.state);
    for (const [st, n] of byState) if (st) idx.push({ t: "State", label: STATES[st] || st, sub: plural(n, "lead"), hash: mapHash({ st, city: "", lead: null, layers: none }) });
    for (const [st, cities] of d3.rollup(S.byRank, (v) => v.length, (r) => r.state, cityKey)) for (const [c, n] of cities)
      if (st && c !== NOCITY) idx.push({ t: "City", label: cityName(c) + ", " + st, sub: plural(n, "lead"), hash: mapHash({ st, city: c, lead: null, layers: none }) });
    for (const r of S.byRank) idx.push({ t: "Property", label: r.property || "", sub: [cityName(r.city), r.state].filter(Boolean).join(", "), hash: leadHash(r, { layers: none }) });
    for (const g of S.buyers) idx.push({ t: "Buyer", label: g.name, sub: plural(g.leads.length, "lead"), hash: "#/buyers?q=" + encodeURIComponent(g.name) });
    for (const x of idx) x.key = x.label.toLowerCase();
    const input = $("q"), list = $("q-results");
    const close = () => { list.classList.add("hidden"); input.setAttribute("aria-expanded", "false"); Q.items = []; Q.active = -1; };
    const pick = (x) => { input.value = ""; close(); input.blur(); go(x.hash); };
    const paint = () => {
      list.replaceChildren(...(Q.items.length ? Q.items.map((x, i) => el("li", { role: "option", "aria-selected": String(i === Q.active), class: i === Q.active ? "on" : null,
        onmousedown: (e) => { e.preventDefault(); pick(x); } }, el("span", { class: "t", text: x.t }), el("span", { class: "l", text: x.label }), el("span", { class: "s", text: x.sub })))
        : [el("li", { class: "none", text: "No match" })]));
      list.classList.remove("hidden"); input.setAttribute("aria-expanded", "true");
    };
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) return close();
      const order = { State: 0, City: 1, Property: 2, Buyer: 3 };
      Q.items = idx.filter((x) => x.key.includes(q)).sort((a, b) => (a.key.startsWith(q) ? 0 : 1) - (b.key.startsWith(q) ? 0 : 1) || order[a.t] - order[b.t] || a.key.length - b.key.length).slice(0, 8);
      Q.active = Q.items.length ? 0 : -1;
      paint();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (!Q.items.length) return; e.preventDefault();
        Q.active = (Q.active + (e.key === "ArrowDown" ? 1 : Q.items.length - 1)) % Q.items.length; paint(); }
      else if (e.key === "Enter" && Q.items[Q.active]) { e.preventDefault(); pick(Q.items[Q.active]); }
      else if (e.key === "Escape") { e.stopPropagation(); input.value = ""; close(); input.blur(); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }

  /* ---------- lead detail: why -> signals + sources -> buyer -> contact -> score ---------- */
  function section(n, title, ...kids) { return el("section", { class: "psec" }, el("h3", {}, el("span", { class: "step", text: String(n) }), title), ...kids); }
  function sourceList(r) {
    const urls = r.sources.map(safeUrl).filter(Boolean);
    if (!urls.length) return el("p", {}, "Source link: ", nf());
    const labels = r.source_labels || [];
    return el("ul", { class: "sources" }, urls.map((s) => { const url = new URL(s);
      return el("li", {}, el("a", { href: s, target: "_blank", rel: "noopener noreferrer" },
        el("span", { class: "host", text: labels[r.sources.indexOf(s)] || url.hostname.replace(/^www\./, "") }),
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
      el("p", { class: "buyer-big" }, r.buyer ? el("strong", { text: r.buyer }) : null, buyerNotes(r) ? el("span", { class: "bnote", text: buyerNotes(r) }) : null, " ", buyerChip(r.buyer_group),
        r.flags.includes("buyer_possible_insider") ? el("span", { class: "chip plain", text: "Possible insider", "data-tip": "This buyer shares a brand with the seller, so the sale may be to a related party (inferred)." }) : null),
      r.buyer_group === "candidate" ? el("p", { class: "caveat", text: "Proposed or stalking-horse bidder. Not final: another bidder may win." }) : null,
      r.buyer_group === "inferred" ? el("p", { class: "caveat", text: "This name is older or belongs to a shell company, and no recent source confirms it." }) : null,
      !r.buyer ? el("p", { class: "muted", text: "No public source names a buyer yet. That is the opening: financing is usually arranged once a buyer steps in." }) : null,
      r.buyer ? el("dl", { class: "facts" },
        el("dt", { text: "Type" }), el("dd", {}, bd.buyer_type ? pretty(bd.buyer_type) + (bd.buyer_type_source ? (bd.buyer_type_source === "inferred" ? " (inferred)" : " (per " + pretty(bd.buyer_type_source) + ")") : "") : nf()),
        el("dt", { text: "Parent" }), el("dd", {}, bd.buyer_parent ? bd.buyer_parent + (bd.buyer_parent_basis ? " (" + basisText(bd.buyer_parent_basis) + ")" : "") : nf()),
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
    document.querySelectorAll(".view, .topbar, .subnav, .skip-link").forEach((n) => { n.inert = true; });
    if (wasHidden) $("p-close").focus();
  }
  function stepPanel(d) {
    const i = S.ctxIdx + d;
    if (S.ctxIdx < 0 || i < 0 || i >= S.ctx.length) return;
    if (S.view === "dashboard") return go(leadHash(S.ctx[i], { research: true }));
    if (S.ctx === S.rows) { S.sel = i; S.page = Math.floor(i / PAGE_SIZE) + 1; render(); S.panelTrigger = document.querySelector('#lists [data-i="' + i + '"]'); }
    openPanel(S.ctx[i], null);
  }
  function summaryText(r) {
    const c = r.contact || {}, bd = r.breakdown || {};
    const NFT = "NOT FOUND";
    return [r.property + " (" + (place(r) || NFT) + ")",
      "Deal Radar rank " + r.rank + ", score " + Math.round(r.score ?? 0) + (r.lists.length ? ", List " + r.lists.join("/") : ""),
      "Size / care: " + (sizeText(r) || NFT) + ", " + (careText(r) || NFT),
      "Stage: " + (stageText(r) || NFT) + (bd.latest_signal_date ? " (latest signal " + bd.latest_signal_date + ")" : ""),
      "Signals: " + (r.signals.map(sigName).join(", ") || NFT),
      "Buyer: " + (r.buyer ? r.buyer + (buyerNotes(r) ? " " + buyerNotes(r) : "") + " [" + BUYER_LABEL[r.buyer_group] + "]" : NFT),
      "Why: " + whyShort(r),
      "Phone: " + (phoneText(c.phone) || NFT), "Website: " + (c.website || NFT),
      "Address: " + ([c.street, r.city, [r.state, c.zip5].filter(Boolean).join(" ")].filter(Boolean).join(", ") || NFT),
      "Sources:", ...(r.sources.length ? r.sources.map((u, i) => "  " + ((r.source_labels || [])[i] ? r.source_labels[i] + ": " : "") + u) : ["  " + NFT])].join("\n");
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
  function closePanel() {            // the user closes research: on the map that means "up one level"
    if (S.view === "dashboard" && M.research) return go(mapHash({ research: false }));
    hidePanel();
  }
  function hidePanel() {
    const wasOpen = !$("panel").classList.contains("hidden");
    $("panel").classList.add("hidden"); $("scrim").classList.add("hidden"); $("panel").setAttribute("aria-hidden", "true");
    document.body.classList.remove("panel-open");
    document.querySelectorAll(".view, .topbar, .subnav, .skip-link").forEach((n) => { n.inert = false; });
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
    if (a && a.hash === location.hash && S.data) { e.preventDefault(); route(); }
  });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target.tagName || ""));
    const open = !$("panel").classList.contains("hidden");
    if (e.key === "Escape") { hideTip();
      if (open) closePanel(); else if (typing) e.target.blur();
      else if (S.view === "dashboard" && M.st) go(M.lead ? mapHash({ lead: null }) : M.city ? mapHash({ city: "", lead: null }) : mapHash({ st: "", city: "", lead: null }));
      return; }
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
    if (e.key === "/") { e.preventDefault(); $("q").focus(); }
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
      return [r.lists.join("/"), listRank(r), r.property, r.city || "NOT FOUND", r.state || "NOT FOUND", addressText(c) || "NOT FOUND",
        phoneText(c.phone) || "NOT FOUND", c.website || "NOT FOUND", r.care_types.join("/") || "NOT FOUND", size(r) ?? "NOT FOUND",
        r.signals.join(";"), r.sale_status ? stageText(r) : r.stage, r.buyer || "NOT FOUND", r.buyer_group, r.score, whyShort(r), r.sources.join(" | "), r.not_found.join(";")]; });
    saveCsv(header, lines, "deal-radar-" + (S.tab === "all" ? "leads" : "list-" + S.tab.toLowerCase()) + (S.state ? "-" + S.state : "") + ".csv");
  });

  load().catch(() => {
    $("asof").textContent = "Data not loaded";
    $("loading").classList.add("hidden");
    $("load-error").classList.remove("hidden");
  });
})();
