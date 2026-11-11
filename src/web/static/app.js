/* Deal Radar front-end. Reads /api/snapshot.json (same-origin, behind the site password). Every value from the data
   is written with textContent / attributes, never parsed as HTML. */
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
  const BUYER_GROUPS = [["confirmed", "Confirmed"], ["candidate", "Candidate · not final"], ["inferred", "Inferred"], ["none", "Not found"]];
  const HARD = new Set(["chapter_11", "chapter_7", "receivership", "foreclosure", "deed_in_lieu", "closure",
    "warn_closure", "hud_loan_terminated", "loan_or_lease_default", "court_sale_process", "hud_delinquency",
    "special_servicing", "lender_takeback", "maturity_default"]);
  const TABS = [
    { key: "A", cls: "a", label: "List A · Live deals, named buyer" },
    { key: "B", cls: "b", label: "List B · Pre-sale distress" },
    { key: "C", cls: "c", label: "List C · Repeat buyers" },
  ];
  const BD_LABELS = [["court_sale_priority", "Court sale priority"], ["stage", "Distress stage"], ["buyer", "Buyer"],
    ["recency", "Recency"], ["size_fit", "Size fit ($3–30M)"], ["care_type_fit", "Care-type fit (assumption)"], ["hud_exit", "HUD exit"], ["market_sale", "Market sale"],
    ["buyer_chain_quality", "Buyer chain quality"], ["closed_sale_penalty", "Closed-sale penalty"]];
  const PAGE_SIZE = 25;

  const S = { data: null, tab: "A", state: "", page: 1, sort: { key: "rank", dir: 1 } };
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
    for (const kid of kids.flat()) if (kid != null) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return n;
  }
  const nf = () => el("span", { class: "nf", text: "NOT FOUND" });
  const pretty = (s) => String(s || "").replace(/_/g, " ");
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
  const fmtInt = (n) => Number(n).toLocaleString("en-US");
  const size = (r) => r.units || r.beds || null;
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const phoneText = (d) => { d = String(d || "").replace(/\D/g, ""); return d.length === 10 ? "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6) : null; };
  const addressText = (c) => [c.street, c.zip5].filter(Boolean).join(", ");
  const mapsUrl = (r) => { const c = r.contact || {};
    const q = [r.property, c.street, r.city, r.state, c.zip5].filter(Boolean).join(", ");
    return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q); };
  function telLink(r) {
    const d = (r.contact || {}).phone;
    return d ? el("a", { class: "tel", href: "tel:+1" + d, text: phoneText(d), onclick: (e) => e.stopPropagation() }) : nf();
  }

  /* ---------- data ---------- */
  async function load() {
    const res = await fetch("/api/snapshot.json", { credentials: "same-origin" });
    if (!res.ok) throw new Error("snapshot " + res.status);
    S.data = await res.json();
    const d = new Date(S.data.generated_at);
    $("asof").textContent = "Data as of " + (isNaN(d) ? S.data.generated_at :
      d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" }));
    buildKpis();
    buildFilters();
    buildTabs();
    await buildMap();
    render();
    buildHow();
    buildFunnel();
    buildExplore();
    window.addEventListener("hashchange", route);
    window.addEventListener("scroll", () => $("topbar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });
    route();
  }

  /* ---------- pages ---------- */
  const VIEWS = { "": "dashboard", "#/": "dashboard", "#/overview": "overview", "#/funnel": "overview", "#/how": "how", "#/explore": "explore" };
  function route() {
    const listRoute = /^#\/leads(?:\/([ABC]))?$/.exec(location.hash);
    const v = VIEWS[location.hash] || "dashboard";
    stopOverviewMotion();
    S.view = v;
    for (const id of ["dashboard", "overview", "how", "explore"]) $("view-" + id).classList.toggle("hidden", id !== v);
    document.querySelectorAll(".nav a").forEach((a) => {
      a.classList.toggle("active", a.dataset.view === v);
      if (a.dataset.view === v) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    $("download").classList.toggle("off", v === "how" || v === "overview");   // keeps its place: the header never shifts
    closePanel();
    if (listRoute) {
      // Entry links open the full list. Ordinary tab changes keep the user's filters.
      $("f-reset").click();
      selectTab(listRoute[1] || S.tab);
      $("lists").scrollIntoView({ behavior: REDUCED ? "instant" : "smooth", block: "start" });
      $("lists").focus({ preventScroll: true });
    } else window.scrollTo({ top: 0, behavior: "instant" });
    if (v === "overview") animateOverview();
  }

  function buildHow() {
    const steps = [["1", "Collect", "Public records and news: Medicare data, HUD loans, state licence lists, bankruptcy courts, trade press."],
      ["2", "Read with AI", "Articles are read by an AI model that copies facts exactly as written (name, city, buyer, stage); anything not stated is NOT FOUND."],
      ["3", "Link", "Every fact is matched to one building and one company (by licence or Medicare number, else name + address)."],
      ["4", "Score", "Rules turn the linked facts into dated signals and a score; see below."],
      ["5", "Lists", "Main list, then List A (live deals), List B (pre-sale distress), List C (repeat buyers)."]];
    $("pipeline").replaceChildren(...steps.flatMap(([k, t, d], i) => [
      i ? el("div", { class: "arrow-r", "aria-hidden": "true", text: "→" }) : null,
      el("div", { class: "step" }, el("div", { class: "k", text: "Step " + k }), el("div", { class: "t", text: t }), el("div", { class: "d", text: d }))])
      .filter(Boolean));
    const src = S.data.sources_table || [];
    $("sources-table").replaceChildren(
      el("thead", {}, el("tr", {}, ["Source", "What it provides", "Records", "Refresh"].map((h) => el("th", { text: h })))),
      el("tbody", {}, src.map((r) => el("tr", {}, el("td", { class: "prop", text: r.source }), el("td", { text: r.provides }),
        el("td", { class: "num", text: fmtInt(r.records) + " " + (r.records === 1 ? r.unit.replace(/s$/, "").replace(/ies$/, "y") : r.unit) }),
        el("td", { class: "muted", text: r.refresh })))));
    const R = S.data.rules || {};
    const sw = R.stage_weight || {}, sf = R.size_fit || {}, rc = R.recency || {}, cp = R.closed_sale_penalty || {};
    const ctf = R.care_type_fit || {}, dv = R.buyer_diversity || {};
    const rule = (title, ...body) => el("div", { class: "rule" }, el("h3", { text: title }), ...body);
    $("rules").replaceChildren(el("div", { class: "rules" },
      rule("Main list", el("p", { text: "A building makes the main list with " + (R.main_min_signals || 3) +
        " or more different distress signals, or one hard event. Everything else with a signal goes to the watch list (Explore)." })),
      rule("Hard events", el("p", { text: "One of these is enough on its own:" }),
        el("ul", {}, (R.hard_events || []).map((h) => el("li", { text: pretty(h) })))),
      rule("Distress stage", el("p", { text: "Points by how far along the distress is: sale " + sw.sale + ", late " + sw.late +
        ", middle " + sw.middle + ", early " + sw.early + ". A court-supervised sale adds " + R.court_sale_priority +
        " so live sales rise to the top; a sale that closed more than " + cp.after_months + " months ago loses " + cp.points + "." })),
      rule("Buyer", el("p", { text: "A buyer named in a source adds " + R.buyer_named_bonus + " points; a candidate (proposed or stalking-horse bidder) adds " +
        R.buyer_candidate_bonus + ". A named buyer counts only if the change is under " + R.buyer_named_max_months +
        " months old. The seller is never its own buyer, and a buyer with the seller's brand is flagged as a possible insider." })),
      rule("Deal size fit", el("p", { text: "Buildings with " + sf.min_units + "–" + sf.max_units + " units or beds (a proxy for a $3–30M loan) add " +
        sf.points + " points. Very large bankruptcies are shown as one deal; only their top " + R.family_top_n + " homes keep full points." })),
      rule("Care-type fit (assumption)", el("p", { text: "Assisted living, independent living, memory care and CCRC buildings add " +
        (ctf.points || 0) + " points. This weighting is an assumption still to be confirmed." })),
      rule("Top 20 mix", el("p", { text: "To keep the top of each list varied, at most " + (dv.per_buyer || 2) + " leads of one buyer appear in its top " +
        (dv.top_n || 20) + "; further leads of that buyer keep their score and are shown just below rank " + (dv.top_n || 20) + "." })),
      rule("Recency", el("p", { text: "Up to " + rc.max_points + " points: full within " + rc.full_points_within_months +
        " months of the latest signal, falling to zero after " + rc.zero_points_after_months + " months. Events older than " +
        R.lead_window_months + " months are context only." })),
      rule("Weak links", el("p", { text: "A court case matched to a building by name alone is marked unconfirmed and kept out of the top " +
        R.name_only_top + " until an address, licence or Medicare record confirms it." }))));
  }

  function buildFunnel() {
    const F = (S.data.funnel || []).filter((f) => !f.list);
    const max = d3.max(F, (f) => f.count) || 1;
    const w = d3.scaleLog().domain([1, max]).range([0.03, 1]).clamp(true);
    const shade = d3.interpolateRgb("#adc6df", "#0b1f3a");
    $("funnel").replaceChildren(...F.map((f, i) => {
      const final = f.step === "Shown on Deal Radar", color = shade(i / Math.max(1, F.length - 1));
      return el("div", { class: "frow" + (final ? " is-final" : ""), style: "--i:" + i },
        el("div", { class: "lab" }, el("span", { class: "cnt", text: fmtInt(f.count), "data-n": f.count }),
          el("span", { class: "t", text: f.step })),
        el("div", { class: "barbox", "aria-hidden": "true" }, el("div", { class: "fbar",
          style: "--w:" + (f.count > 0 ? w(f.count).toFixed(4) : "0") + ";--bar-color:" + color +
            ";--bar-top:" + d3.interpolateRgb(color, "#fff")(0.15) })),
        el("div", { class: "rule-t", text: f.rule }));
    }));
    $("funnel-recon").textContent = reconLine(S.data.summary);
  }

  // "533 leads = List A 241 (buyer named) + List B 234 (no buyer yet) + 58 other (...). List C = ..." from live counts
  function reconLine(m) {
    if (!m) return "";
    const o = m.other_breakdown || {};
    const parts = [[o.candidate, "stalking-horse candidates"], [o.stale, "older ownership changes"],
      [o.sale_no_public_buyer, "sales under way with no public buyer"], [o.shell_or_closed, "shell or closed"]]
      .filter(([n]) => n > 0).map(([n, t]) => fmtInt(n) + " " + t);
    return fmtInt(m.leads) + " leads = List A " + fmtInt(m.list_a) + " (buyer named) + List B " + fmtInt(m.list_b) +
      " (no buyer yet) + " + fmtInt(m.other) + " other" + (parts.length ? " (" + parts.join(", ") + ")" : "") +
      ". List C = " + fmtInt(m.list_c_buyers) + " repeat buyers, linked to " + fmtInt(m.list_c_linked_leads) + " of these leads.";
  }

  /* Replay on each Overview visit; cancel pending work when leaving or quickly switching pages. */
  let overviewObservers = [];
  const overviewFrames = new Set();
  function motionFrame(callback) {
    const id = requestAnimationFrame((t) => { overviewFrames.delete(id); callback(t); });
    overviewFrames.add(id);
  }
  function stopOverviewMotion() {
    overviewObservers.forEach((observer) => observer.disconnect());
    overviewObservers = [];
    overviewFrames.forEach(cancelAnimationFrame);
    overviewFrames.clear();
    // a cancelled count-up must never leave a number frozen mid-way (e.g. 400 of 533): snap to the real value
    document.querySelectorAll("#kpis [data-n], #funnel [data-n]").forEach((node) => {
      const n = Number(node.dataset.n);
      if (isFinite(n)) node.textContent = fmtInt(n) + (node.dataset.suffix || "");
    });
  }
  function countUp(node, delay = 0) {
    const n = Number(node.dataset.n);
    if (!isFinite(n)) return;
    const suffix = node.dataset.suffix || "";
    if (REDUCED || n < 2) { node.textContent = fmtInt(n) + suffix; return; }
    const t0 = performance.now() + delay, dur = 800;
    node.textContent = "0" + suffix;
    const tick = (t) => { const k = Math.max(0, Math.min(1, (t - t0) / dur)), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmtInt(Math.round(n * e)) + suffix; if (k < 1) motionFrame(tick); };
    motionFrame(tick);
  }
  function animateOverview() {
    const view = $("view-overview");
    view.classList.remove("overview-enter");
    for (const id of ["kpis", "funnel"]) $(id).classList.remove("in");
    // Commit the reset before restarting CSS animations, including repeated clicks on Overview.
    void view.offsetWidth;
    view.classList.add("overview-enter");
    for (const id of ["kpis", "funnel"]) {
      const group = $(id);
      const go = () => {
        group.classList.add("in");
        group.querySelectorAll("[data-n]").forEach((node, i) => countUp(node, id === "kpis" ? 100 + i * 40 : 360 + i * 100));
      };
      if (REDUCED || !("IntersectionObserver" in window)) { go(); continue; }
      const observer = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { observer.disconnect(); go(); }
      }, { threshold: 0.1 });
      overviewObservers.push(observer);
      observer.observe(group);
    }
  }

  /* ---------- explore (watch list) ---------- */
  const X = { page: 1 };
  function buildExplore() {
    const rows = S.data.explore || [];
    const states = [...new Set(rows.map((r) => r.state).filter(Boolean))].sort();
    const sigs = [...new Set(rows.flatMap((r) => r.signals))].sort();
    $("x-state").replaceChildren(option("", "All states"), ...states.map((c) => option(c, STATES[c] || c)));
    $("x-care").replaceChildren(option("", "All care types"), ...CARE_TYPES.map((c) => option(c, c)));
    $("x-signal").replaceChildren(option("", "All signals"), ...sigs.map((c) => option(c, pretty(c))));
    for (const id of ["x-search", "x-state", "x-care", "x-signal"]) $(id).addEventListener("input", () => { X.page = 1; renderExplore(); });
    $("x-reset").addEventListener("click", () => { for (const id of ["x-search", "x-state", "x-care", "x-signal"]) $(id).value = ""; X.page = 1; renderExplore(); });
    $("x-thead").replaceChildren(el("tr", {}, [["Property", ""], ["City / state", "c-state"], ["Care type", "c-care"], ["Units", "c-units"],
      ["Signals", "c-signals"], ["Why not on the main list", "c-reason"], ["Score", "c-score"], ["Sources", "c-sources"]]
      .map(([h, c]) => el("th", { text: h, class: c }))));
    renderExplore();
  }
  function exploreRows() {
    const q = $("x-search").value.trim().toLowerCase(), st = $("x-state").value, care = $("x-care").value, sig = $("x-signal").value;
    return (S.data.explore || []).filter((r) => (!st || r.state === st) && (!care || r.care_types.includes(care))
      && (!sig || r.signals.includes(sig)) && (!q || [r.property, r.city, r.buyer].join(" ").toLowerCase().includes(q)));
  }
  function renderExplore() {
    const rows = exploreRows();
    X.rows = rows;
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    X.page = Math.min(X.page, pages);
    $("x-count").textContent = fmtInt(rows.length) + " of " + fmtInt((S.data.explore || []).length) + " watch-list buildings";
    $("x-tbody").replaceChildren(...rows.slice((X.page - 1) * PAGE_SIZE, X.page * PAGE_SIZE).map((r) => el("tr", { class: "static" },
      el("td", { class: "prop", text: r.property }),
      el("td", { class: "loc" }, r.city || r.state ? [r.city, r.state].filter(Boolean).join(", ") : nf()),
      el("td", {}, r.care_types.length ? r.care_types.join(" / ") : nf()),
      el("td", { class: "num" }, size(r) != null ? fmtInt(size(r)) : nf()),
      el("td", {}, el("div", { class: "chips" }, r.signals.map((x) => el("span", { class: "chip", text: pretty(x) })))),
      el("td", { class: "reason", text: r.reason }),
      el("td", {}, scoreBar(r.score)),
      el("td", {}, el("div", { class: "chips" }, r.sources.slice(0, 3).map((u) => { const s = safeUrl(u);
        return s ? el("a", { href: s, target: "_blank", rel: "noopener noreferrer", text: hostOf(s) }) : null; }),
        r.sources.length > 3 ? el("span", { class: "muted", text: "+" + (r.sources.length - 3) }) : null)))));
    $("x-empty").classList.toggle("hidden", rows.length > 0);
    $("x-pager").replaceChildren(el("span", { class: "muted", text: rows.length ? "Page " + X.page + " of " + pages : "" }),
      el("div", { class: "btns" },
        el("button", { class: "btn", type: "button", disabled: X.page <= 1, onclick: () => { X.page--; renderExplore(); } }, "Previous"),
        el("button", { class: "btn", type: "button", disabled: X.page >= pages, onclick: () => { X.page++; renderExplore(); } }, "Next")));
  }

  /* ---------- "Why this lead" (rules in code, no AI) ---------- */
  function whyThisLead(r) {
    const bd = r.breakdown || {}, parts = [];
    const hard = (bd.hard_events || r.signals.filter((x) => HARD.has(x))).map(pretty);
    if (bd.court_sale_priority > 0) parts.push("a court-supervised sale is under way");
    else if (hard.length) parts.push((hard.length > 1 ? "hard events (" : "a hard event (") + hard.join(", ") + ")");
    else parts.push(r.signals.length + " distress signals (" + r.signals.map(pretty).join(", ") + ")");
    if (r.buyer && r.buyer_group === "confirmed") parts.push("a named buyer, " + r.buyer);
    else if (r.buyer && r.buyer_group === "candidate") parts.push("a candidate buyer (not final), " + r.buyer);
    if (bd.size_fit > 0) parts.push("a size that fits a $3–30M loan");
    if (bd.care_type_fit > 0) parts.push("assisted living / memory care (care-type fit, assumption)");
    if (bd.closed_sale_penalty < 0) parts.push("although its sale closed over a year ago");
    let s = "Ranked #" + r.rank + " because of " + (parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0]);
    if (bd.latest_signal_date) s += "; latest signal " + bd.latest_signal_date;
    if (bd.buyer_diversity_cap) s += ". Shown just below the top 20 because that buyer already has two leads there";
    return s + ".";
  }

  /* ---------- KPIs ---------- */
  function buildKpis() {
    const items = S.data.items, n = items.length;
    const senior = items.filter((r) => r.care_types.some((t) => SENIOR_TYPES.includes(t))).length;
    const states = new Set(items.map((r) => r.state).filter(Boolean)).size;
    const named = items.filter((r) => r.buyer_group === "confirmed").length;
    const cnt = (k) => items.filter((r) => r.lists.includes(k)).length;
    const cards = [
      ["Total leads", fmtInt(n), "public sources only", null, n, ""],
      ["List A", fmtInt(cnt("A")), "live deals, named buyer", "a", cnt("A"), ""],
      ["List B", fmtInt(cnt("B")), "pre-sale distress", "b", cnt("B"), ""],
      ["List C", fmtInt((S.data.list_c || []).length), "repeat buyers" + (S.data.summary ? " · linked to " +
        fmtInt(S.data.summary.list_c_linked_leads) + " leads" : ""), "c", (S.data.list_c || []).length, ""],
      ["Senior housing", n ? Math.round((100 * senior) / n) + "%" : "–", fmtInt(senior) + " AL / IL / MC / CCRC leads", null, n ? Math.round((100 * senior) / n) : null, "%"],
      ["States covered", fmtInt(states), "with at least one lead", null, states, ""],
      ["Named buyer", n ? Math.round((100 * named) / n) + "%" : "–", fmtInt(named) + " confirmed in a source", null, n ? Math.round((100 * named) / n) : null, "%"],
    ];
    $("kpis").replaceChildren(...cards.map(([label, value, sub, dot, num, suffix], i) =>
      el(dot ? "a" : "div", { class: "kpi", style: "--i:" + i,
        href: dot ? "#/leads/" + dot.toUpperCase() : null, "aria-label": dot ? label + ": " + sub : null },
        el("div", { class: "label" }, dot ? el("span", { class: "dot " + dot }) : null, label),
        el("div", { class: "value", text: value, "data-n": num, "data-suffix": suffix }),
        el("div", { class: "sub", text: sub }))));
  }

  /* ---------- filters ---------- */
  function option(value, label) { return el("option", { value, text: label }); }
  function buildFilters() {
    const present = [...new Set(S.data.items.map((r) => r.state).filter(Boolean))].sort();
    $("f-state").replaceChildren(option("", "All states"), ...present.map((c) => option(c, (STATES[c] || c))));
    $("f-care").replaceChildren(option("", "All care types"), ...CARE_TYPES.map((c) => option(c, c)));
    $("f-stage").replaceChildren(option("", "All stages"), ...STAGES.map((s) => option(s, s[0].toUpperCase() + s.slice(1))));
    $("f-buyer").replaceChildren(option("", "All buyer statuses"), ...BUYER_GROUPS.map(([v, l]) => option(v, l)));
    for (const id of ["f-search", "f-care", "f-stage", "f-buyer", "f-min", "f-max"])
      $(id).addEventListener("input", () => { S.page = 1; render(); });
    $("f-state").addEventListener("input", (e) => setState(e.target.value));
    $("f-reset").addEventListener("click", () => {
      for (const id of ["f-search", "f-care", "f-stage", "f-buyer", "f-min", "f-max"]) $(id).value = "";
      setState("");
    });
    $("clear-state").addEventListener("click", () => setState(""));
  }
  function setState(code) {
    S.state = code; S.page = 1;
    $("f-state").value = code;
    $("clear-state").classList.toggle("hidden", !code);
    d3.selectAll("#map path.state").classed("selected", (f) => CODE_BY_NAME[f.properties.name] === code);
    render();
  }
  function filtered() {
    const q = $("f-search").value.trim().toLowerCase();
    const care = $("f-care").value, stage = $("f-stage").value, bg = $("f-buyer").value;
    const min = parseFloat($("f-min").value), max = parseFloat($("f-max").value);
    if (S.tab === "C") {
      return (S.data.list_c || []).filter((c) => !q || [c.buyer, c.example_homes].join(" ").toLowerCase().includes(q));
    }
    return S.data.items.filter((r) => r.lists.includes(S.tab)
      && (!S.state || r.state === S.state)
      && (!care || r.care_types.includes(care))
      && (!stage || r.stage === stage)
      && (!bg || r.buyer_group === bg)
      && (isNaN(min) || (r.score ?? 0) >= min)
      && (isNaN(max) || (r.score ?? 0) <= max)
      && (!q || [r.property, r.city, r.state, r.buyer].join(" ").toLowerCase().includes(q)));
  }

  /* ---------- tabs ---------- */
  function selectTab(key) {
    S.tab = key; S.page = 1; S.sort = { key: "rank", dir: 1 }; buildTabs(); render();
  }
  function buildTabs() {
    const counts = { A: 0, B: 0, C: (S.data.list_c || []).length };
    for (const r of S.data.items) for (const l of r.lists) counts[l] = (counts[l] || 0) + 1;
    $("tabs").replaceChildren(...TABS.map((t) => el("button", {
      class: "tab " + t.cls, role: "tab", type: "button", "aria-selected": String(S.tab === t.key), "data-tab": t.key,
      tabindex: S.tab === t.key ? 0 : -1,
      onclick: () => { selectTab(t.key); $("tabs").querySelector('[aria-selected="true"]').focus({ preventScroll: true }); },
      onkeydown: (e) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        const i = TABS.findIndex((x) => x.key === t.key);
        const next = e.key === "Home" ? 0 : e.key === "End" ? 2 : (i + (e.key === "ArrowRight" ? 1 : 2)) % 3;
        selectTab(TABS[next].key);
        $("tabs").querySelector('[aria-selected="true"]').focus({ preventScroll: true });
      },
    }, el("span", { class: "dot " + t.cls }), t.label, el("span", { class: "count", text: fmtInt(counts[t.key] || 0) + (t.key === "C" ? " buyers" : "") }))));
    for (const id of ["f-state", "f-care", "f-stage"]) $(id).classList.toggle("hidden", S.tab === "C");
    $("f-buyer").classList.toggle("hidden", S.tab !== "A");
    document.querySelector(".range").classList.toggle("hidden", S.tab === "C");
  }

  /* ---------- map ---------- */
  async function buildMap() {
    const topo = await d3.json("/static/states-albers-10m.json");
    const states = topojson.feature(topo, topo.objects.states).features;
    const counts = d3.rollup(S.data.items, (v) => v.length, (r) => r.state);
    const max = d3.max([...counts.values()]) || 1;
    const color = d3.scaleSequentialSqrt([0, max], d3.interpolateRgb("#eef2f7", "#0b1f3a"));
    const path = d3.geoPath();
    const svg = d3.select("#map");
    const tip = $("tooltip");
    svg.append("g").selectAll("path").data(states).join("path")
      .attr("class", "state").attr("d", path)
      .attr("fill", (f) => { const c = counts.get(CODE_BY_NAME[f.properties.name]) || 0; return c ? color(c) : "#f3f4f6"; })
      .attr("tabindex", 0)
      .attr("aria-label", (f) => f.properties.name + ": " + (counts.get(CODE_BY_NAME[f.properties.name]) || 0) + " leads")
      .on("mousemove", (ev, f) => {
        const c = counts.get(CODE_BY_NAME[f.properties.name]) || 0;
        const box = document.querySelector(".map-wrap").getBoundingClientRect();
        tip.textContent = f.properties.name + " · " + fmtInt(c) + (c === 1 ? " lead" : " leads");
        tip.style.left = (ev.clientX - box.left) + "px"; tip.style.top = (ev.clientY - box.top) + "px";
        tip.classList.remove("hidden");
      })
      .on("mouseleave", () => tip.classList.add("hidden"))
      .on("click", (ev, f) => { const code = CODE_BY_NAME[f.properties.name]; setState(S.state === code ? "" : code); })
      .on("keydown", (ev, f) => { if (ev.key === "Enter") { const code = CODE_BY_NAME[f.properties.name]; setState(S.state === code ? "" : code); } });
    svg.append("path").datum(topojson.mesh(topo, topo.objects.nation)).attr("class", "nation").attr("d", path);
    const grad = "linear-gradient(90deg, " + [0, 0.25, 0.5, 0.75, 1].map((t) => color(t * max)).join(", ") + ")";
    $("legend").replaceChildren(el("span", { text: "0" }), el("span", { class: "bar", style: "background:" + grad }),
      el("span", { text: fmtInt(max) + " leads" }));
  }

  /* ---------- table ---------- */
  // Widths live in CSS (.c-<key>) and are fixed, so List A and List B line up column for column; Property flexes.
  const LEAD_COLS = [
    { key: "rank", label: "#", sort: (r) => listRank(r) },
    { key: "property", label: "Property", sort: (r) => r.property || "" },
    { key: "state", label: "City / state", sort: (r) => (r.state || "") + (r.city || "") },
    { key: "care", label: "Care type" },
    { key: "units", label: "Units", sort: (r) => size(r) ?? -1, num: true },
    { key: "signals", label: "Signals" },
    { key: "stage", label: "Stage", sort: (r) => STAGES.indexOf(r.stage) },
    { key: "buyer", label: "Buyer", sort: (r) => r.buyer || "~", onlyTab: "A" },   // List B never has a buyer: column left out
    { key: "score", label: "Score", sort: (r) => r.score ?? 0 },
    { key: "contact", label: "Phone", sort: (r) => (r.contact || {}).phone || "~" },
  ];
  const C_COLS = [
    { key: "rank", label: "#", sort: (c) => c.rank },
    { key: "buyer", label: "Buyer", sort: (c) => c.buyer },
    { key: "homes", label: "Homes acquired (36 mo)", sort: (c) => c.homes_in_list, num: true },
    { key: "examples", label: "Example homes" },
    { key: "source", label: "Source" },
  ];

  function listRank(r) {             // List A / B tables: position in that list (its own display order)
    return (S.tab === "A" && r.rank_a) || (S.tab === "B" && r.rank_b) || r.rank;
  }

  function cell(col, r) {
    switch (col.key) {
      case "rank": return el("td", { class: "rank", text: String(listRank(r)) });
      case "property": return el("td", { class: "prop", text: r.property || "" });
      case "state": return el("td", { class: "loc" }, r.city || r.state ? [r.city || "", r.city && r.state ? ", " : "", r.state || ""].join("") : nf());
      case "care": return el("td", {}, r.care_types.length ? r.care_types.join(" / ") : nf());
      case "units": return el("td", { class: "num" }, size(r) != null ? fmtInt(size(r)) : nf());
      case "signals": return el("td", {}, el("div", { class: "chips" }, r.signals.slice(0, 4).map((s) =>
        el("span", { class: "chip" + (HARD.has(s) ? " hard" : ""), text: pretty(s) })),
        r.signals.length > 4 ? el("span", { class: "chip", text: "+" + (r.signals.length - 4) }) : null));
      case "stage": return el("td", { class: "stage", text: r.stage || "" });
      case "buyer": return el("td", { class: "buyer" }, r.buyer ? el("span", { class: "bname", text: r.buyer }) : nf(), " ",
        statusBadge(r), r.flags.includes("buyer_possible_insider") ? el("span", { class: "badge insider", text: "possible insider" }) : null);
      case "score": return el("td", {}, scoreBar(r.score));
      case "contact": return el("td", {}, telLink(r));
      default: return el("td");
    }
  }
  function cCell(col, c) {
    switch (col.key) {
      case "rank": return el("td", { class: "rank", text: String(c.rank) });
      case "buyer": return el("td", { class: "prop", text: c.buyer });
      case "homes": return el("td", { class: "num", text: fmtInt(c.homes_in_list) });
      case "examples": return el("td", { text: c.example_homes });
      case "source": { const u = safeUrl(c.public_source_link);
        return el("td", {}, u ? el("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: hostOf(u) }) : nf()); }
      default: return el("td");
    }
  }
  function statusBadge(r) {
    const label = { confirmed: "confirmed", candidate: "Candidate · not final", inferred: "inferred", none: "not found" }[r.buyer_group];
    return el("span", { class: "badge " + r.buyer_group, text: label, title: "status in source: " + r.buyer_status });
  }
  function scoreBar(v) {
    const max = S.maxScore || 1;
    return el("div", { class: "scorebar" },
      el("div", { class: "track" }, el("div", { class: "fill", style: "width:" + Math.max(2, Math.min(100, (100 * (v || 0)) / max)) + "%" })),
      el("span", { class: "v", text: v == null ? "–" : String(Math.round(v)) }));
  }
  function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return u; } }

  function render() {
    if (!S.data) return;
    S.maxScore = d3.max(S.data.items, (r) => r.score) || 1;
    const isC = S.tab === "C";
    const cols = isC ? C_COLS : LEAD_COLS.filter((c) => !c.onlyTab || c.onlyTab === S.tab);
    $("colgroup").replaceChildren(...cols.map((c) => el("col", { class: "c-" + c.key })));
    $("f-buyer").classList.toggle("hidden", isC || S.tab === "B");
    if (S.tab === "B") $("f-buyer").value = "";
    let rows = filtered();
    const col = cols.find((c) => c.key === S.sort.key && c.sort) || cols[0];
    rows = rows.slice().sort((a, b) => { const x = col.sort(a), y = col.sort(b); return (x > y ? 1 : x < y ? -1 : 0) * S.sort.dir; });
    S.rows = rows;
    $("thead").replaceChildren(el("tr", {}, cols.map((c) => el("th", {
      class: (c.sort ? "sortable" : "") + (c.num ? " num" : ""), scope: "col",
      "aria-sort": S.sort.key === c.key ? (S.sort.dir > 0 ? "ascending" : "descending") : null,
      onclick: c.sort ? () => { S.sort = { key: c.key, dir: S.sort.key === c.key ? -S.sort.dir : (c.key === "score" || c.key === "units" || c.key === "homes" ? -1 : 1) }; render(); } : null,
    }, c.label, S.sort.key === c.key ? el("span", { class: "arrow", text: S.sort.dir > 0 ? "▲" : "▼" }) : null))));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    S.page = Math.min(S.page, pages);
    const slice = rows.slice((S.page - 1) * PAGE_SIZE, S.page * PAGE_SIZE);
    $("tbody").replaceChildren(...slice.map((r) => el("tr", isC ? { class: "static" } : {
      tabindex: 0, onclick: (e) => openPanel(r, e.currentTarget), onkeydown: (e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) { e.preventDefault(); openPanel(r, e.currentTarget); }
      },
    }, cols.map((c) => (isC ? cCell(c, r) : cell(c, r))))));
    const total = isC ? (S.data.list_c || []).length : S.data.items.filter((r) => r.lists.includes(S.tab)).length;
    $("result-count").textContent = fmtInt(rows.length) + " of " + fmtInt(total) + (isC ? " buyers" : " leads") + (S.state && !isC ? " in " + (STATES[S.state] || S.state) : "");
    const thinC = isC && total < 5;
    $("notice").classList.toggle("hidden", !thinC || total === 0);
    $("notice").textContent = "Repeat-buyer list expands in next update.";
    const empty = $("empty");
    empty.classList.toggle("hidden", rows.length > 0);
    empty.replaceChildren(el("strong", { text: isC && total === 0 ? "Repeat-buyer list expands in next update." : "No leads match these filters." }),
      el("span", { text: isC && total === 0 ? "Buyers named on two or more homes will appear here." : "Try clearing a filter or the state on the map." }));
    $("table").classList.toggle("hidden", rows.length === 0);
    $("pager").replaceChildren(el("span", { class: "muted", text: rows.length ? "Page " + S.page + " of " + pages : "" }),
      el("div", { class: "btns" },
        el("button", { class: "btn", type: "button", disabled: S.page <= 1, onclick: () => { S.page--; render(); } }, "Previous"),
        el("button", { class: "btn", type: "button", disabled: S.page >= pages, onclick: () => { S.page++; render(); } }, "Next")));
  }

  /* ---------- detail panel ---------- */
  function openPanel(r, trigger) {
    S.panelTrigger = trigger || document.activeElement;
    const bd = r.breakdown || {};
    const tabKey = r.lists[0];
    $("p-kicker").replaceChildren(...r.lists.map((l) => el("span", { class: "dot " + l.toLowerCase() })),
      el("span", { text: (r.lists.length ? "List " + r.lists.join(" · ") + " · " : "") + "Rank " + r.rank }));
    $("p-title").textContent = r.property || "";
    $("p-sub").replaceChildren(r.city || r.state ? [r.city, r.state].filter(Boolean).join(", ") : nf());
    const sections = [contactBlock(r), el("div", { class: "why" }, el("strong", { text: "Why this lead" }), whyThisLead(r))];

    // facts (NOT FOUND highlighted)
    const facts = [["City", r.city], ["State", r.state && (STATES[r.state] || r.state)],
      ["Care type", r.care_types.join(" / ")], ["Units / beds", size(r) != null ? fmtInt(size(r)) : null],
      ["Stage", r.stage], ["Score", r.score != null ? String(Math.round(r.score)) : null]];
    sections.push(section("Property", el("dl", { class: "facts" }, facts.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", {}, v ? String(v) : nf())]))));

    // score breakdown
    const parts = BD_LABELS.filter(([k]) => typeof bd[k] === "number" && Math.round(bd[k]) !== 0);
    const top = Math.max(1, ...parts.map(([k]) => Math.abs(bd[k])));
    sections.push(section("Score breakdown", parts.length ? [
      ...parts.map(([k, label]) => el("div", { class: "bd-row" }, el("span", { text: label }),
        el("div", { class: "track" }, el("div", { class: "fill" + (bd[k] < 0 ? " neg" : ""), style: "width:" + (100 * Math.abs(bd[k])) / top + "%" })),
        el("span", { class: "v", text: (bd[k] > 0 ? "+" : "") + Math.round(bd[k]) }))),
      el("div", { class: "bd-row" }, el("strong", { text: "Total" }), el("span"), el("strong", { class: "v", text: String(Math.round(bd.total ?? r.score ?? 0)) })),
      bd.stage_from ? el("p", { class: "muted", text: "Stage from: " + pretty(bd.stage_from) }) : null,
    ] : el("p", { class: "muted", text: "Score parts are not shown for this lead (some of its evidence is not public)." })));

    // signals timeline (dated items the public data carries)
    const events = [];
    if (bd.latest_signal_date) events.push([bd.latest_signal_date, "Latest distress signal", r.signals.map(pretty).join(", ")]);
    if (bd.hard_events && bd.hard_events.length) events.push([bd.latest_signal_date || null, "Hard events on record", bd.hard_events.map(pretty).join(", ")]);
    if (bd.buyer_date) events.push([bd.buyer_date, "Buyer reported", (r.buyer || "") + (bd.buyer_date_basis ? " (" + pretty(bd.buyer_date_basis) + ")" : "")]);
    if (bd.bankruptcy_family && bd.bankruptcy_family.length) events.push([null, "Bankruptcy case", bd.bankruptcy_family.join(", ")]);
    if (r.first_seen) events.push([r.first_seen, "First listed in Deal Radar", ""]);
    if (r.last_updated) events.push([r.last_updated, "Last updated", ""]);
    events.sort((a, b) => String(b[0] || "").localeCompare(String(a[0] || "")));
    sections.push(section("Signals",
      el("div", { class: "chips", style: "margin-bottom:12px" }, r.signals.map((s) => el("span", { class: "chip" + (HARD.has(s) ? " hard" : ""), text: pretty(s) }))),
      events.length ? el("ul", { class: "timeline" }, events.map(([when, what, detail]) =>
        el("li", {}, el("div", { class: "when" }, when || nf()), el("div", { text: what }), detail ? el("div", { class: "what", text: detail }) : null)))
        : el("p", { class: "muted", text: "No dated events in the public data." })));

    // buyer block
    const conf = { confirmed: "Named in a source", candidate: "Candidate · not final (proposed / stalking-horse bidder)",
      inferred: "Inferred (parent matched by brand, or a shell / older buyer)", none: "No buyer found" }[r.buyer_group];
    if (r.buyer || r.lists.includes("A")) sections.push(section("Buyer", el("dl", { class: "facts" },
      el("dt", { text: "Name" }), el("dd", {}, r.buyer || nf()),
      el("dt", { text: "Status" }), el("dd", {}, statusBadge(r), r.flags.includes("buyer_possible_insider") ? [" ", el("span", { class: "badge insider", text: "possible insider" })] : null),
      el("dt", { text: "Type" }), el("dd", {}, bd.buyer_type ? pretty(bd.buyer_type) + (bd.buyer_type_source ? " (" + bd.buyer_type_source + ")" : "") : nf()),
      el("dt", { text: "Confidence" }), el("dd", { text: conf }),
      el("dt", { text: "Parent" }), el("dd", {}, bd.buyer_parent ? bd.buyer_parent + (bd.buyer_parent_basis ? " (" + pretty(bd.buyer_parent_basis) + ")" : "") : nf()),
      el("dt", { text: "Insider flag" }), el("dd", { text: r.flags.includes("buyer_possible_insider") ? "Same brand as the seller (inferred)" : "No" }))));

    // every source link
    sections.push(section("Sources (" + r.sources.length + ")", r.sources.length
      ? el("ul", { class: "sources" }, r.sources.map((u) => { const s = safeUrl(u); if (!s) return null;
          const url = new URL(s);
          return el("li", {}, el("a", { href: s, target: "_blank", rel: "noopener noreferrer" },
            el("span", { class: "host", text: url.hostname.replace(/^www\./, "") }),
            el("span", { class: "path", text: (url.pathname + url.search).slice(0, 120) }))); }))
      : nf()));

    // flags
    if (r.not_found.length || r.flags.length) sections.push(section("Gaps and flags", el("div", { class: "chips" },
      r.not_found.map((f) => el("span", { class: "nf", text: pretty(f) + ": NOT FOUND" })),
      r.flags.filter((f) => f !== "buyer_possible_insider").map((f) => el("span", { class: "chip", text: pretty(f) })))));

    $("p-body").replaceChildren(...sections);
    $("p-body").scrollTop = 0;
    $("panel").classList.remove("hidden"); $("scrim").classList.remove("hidden");
    $("panel").setAttribute("aria-hidden", "false");
    document.body.classList.add("panel-open");
    document.querySelectorAll(".view, .topbar, .skip-link").forEach((n) => { n.inert = true; });
    $("p-close").focus();
    void tabKey;
  }
  /* ---------- contact (business contact points only; each with its public source) ---------- */
  function copyButton(text) {
    return el("button", { class: "copy", type: "button", text: "Copy", "aria-live": "polite", onclick: async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
      catch { b.textContent = "Select to copy"; }
      setTimeout(() => { b.textContent = "Copy"; }, 2000);
    } });
  }
  function contactBlock(r) {
    const c = r.contact || {}, web = safeUrl(c.website), tel = c.phone, addr = addressText(c);
    const act = (label, href, primary) => el("a", { class: "btn " + (primary ? "primary" : "solid") + (href ? "" : " off"),
      href: href || null, target: href && !href.startsWith("tel:") ? "_blank" : null, rel: "noopener noreferrer", "aria-disabled": href ? null : "true", text: label });
    const row = (k, v, copy) => [el("dt", { text: k }), el("dd", {}, v || nf()), el("span", {}, v && copy ? copyButton(copy) : null)];
    const src = [c.phone_source, c.website_source].map(safeUrl).filter(Boolean);
    return el("div", { class: "contact" }, el("h3", { text: "Contact this property" }),
      el("div", { class: "contact-actions" },
        act("Call", tel ? "tel:+1" + tel : null, true),
        act("Website", web, false), act("Map", mapsUrl(r), false)),
      el("dl", { class: "contact-rows" },
        ...[row("Phone", tel ? el("a", { class: "tel", href: "tel:+1" + tel, text: phoneText(tel) + (c.phone_label ? "  " + c.phone_label : "") }) : null, tel ? phoneText(tel) : null)]
          .map((x) => el("div", { class: "crow" }, x)),
        el("div", { class: "crow" }, row("Website", web ? el("a", { href: web, target: "_blank", rel: "noopener noreferrer", text: hostOf(web) }) : null, web)),
        el("div", { class: "crow" }, row("Address", c.street ? [c.street, r.city, [r.state, c.zip5].filter(Boolean).join(" ")].filter(Boolean).join(", ") : null, c.street ? [r.property, addr, r.city, r.state].filter(Boolean).join(", ") : null))),
      el("p", { class: "contact-foot" }, src.length
        ? ["Source: ", ...src.flatMap((u, i) => [i ? ", " : "", el("a", { href: u, target: "_blank", rel: "noopener noreferrer", text: hostOf(u) })]),
           c.checked ? " (checked " + c.checked + ")" : ""]
        : "No public contact details on file yet. Check the source links below."));
  }
  function section(title, ...kids) { return el("div", { class: "section" }, el("h3", { text: title }), ...kids); }
  function closePanel() {
    const wasOpen = !$("panel").classList.contains("hidden");
    $("panel").classList.add("hidden"); $("scrim").classList.add("hidden"); $("panel").setAttribute("aria-hidden", "true");
    document.body.classList.remove("panel-open");
    document.querySelectorAll(".view, .topbar, .skip-link").forEach((n) => { n.inert = false; });
    if (wasOpen && S.panelTrigger && S.panelTrigger.isConnected) S.panelTrigger.focus({ preventScroll: true });
  }
  $("p-close").addEventListener("click", closePanel);
  $("scrim").addEventListener("click", closePanel);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (a && a.hash === location.hash && (/^#\/leads(?:\/[ABC])?$/.test(a.hash) || a.hash === "#/overview")) {
      e.preventDefault(); route();
    }
  });
  document.addEventListener("keydown", (e) => {
    if ($("panel").classList.contains("hidden")) return;
    if (e.key === "Escape") closePanel();
    if (e.key === "Tab") {
      const nodes = [...$("panel").querySelectorAll('button:not(:disabled), a[href], [tabindex="0"]')];
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  /* ---------- CSV of the current view ---------- */
  function csvCell(v) { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
  $("download").addEventListener("click", () => {
    if (!S.rows) return;
    let header, lines;
    if (S.view === "explore") {
      header = ["property", "city", "state", "care_type", "units_or_beds", "signals", "why_not_main_list", "score", "sources"];
      lines = (X.rows || []).map((r) => [r.property, r.city || "NOT FOUND", r.state || "NOT FOUND", r.care_types.join("/") || "NOT FOUND",
        size(r) ?? "NOT FOUND", r.signals.join(";"), r.reason, r.score, r.sources.join(" ")]);
      const csv = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
      const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "deal-radar-watch-list.csv" });
      document.body.append(a); a.click(); a.remove();
      return;
    }
    if (S.tab === "C") {
      header = ["rank", "buyer", "homes_in_list", "example_homes", "public_source_link"];
      lines = S.rows.map((c) => header.map((k) => c[k]));
    } else {
      header = ["list", "rank", "property", "city", "state", "address", "phone", "website", "care_type", "units_or_beds", "signals",
        "stage", ...(S.tab === "A" ? ["buyer", "buyer_status"] : []), "score", "sources", "not_found"];
      lines = S.rows.map((r) => { const c = r.contact || {};
        return [S.tab, listRank(r), r.property, r.city || "NOT FOUND", r.state || "NOT FOUND", addressText(c) || "NOT FOUND",
          phoneText(c.phone) || "NOT FOUND", c.website || "NOT FOUND", r.care_types.join("/") || "NOT FOUND", size(r) ?? "NOT FOUND",
          r.signals.join(";"), r.stage, ...(S.tab === "A" ? [r.buyer || "NOT FOUND", r.buyer_group] : []), r.score,
          r.sources.join(" "), r.not_found.join(";")]; });
    }
    const csv = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
    const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
      download: "deal-radar-list-" + S.tab.toLowerCase() + (S.state ? "-" + S.state : "") + ".csv" });
    document.body.append(a); a.click(); a.remove();
  });

  load().catch(() => {
    $("asof").textContent = "Could not load data";
    $("load-error").textContent = "The property data could not be loaded. Please refresh the page or contact your team.";
    $("load-error").classList.remove("hidden");
  });
})();
