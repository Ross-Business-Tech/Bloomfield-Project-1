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
  const BUYER_GROUPS = [["confirmed", "Confirmed"], ["candidate", "Candidate"], ["inferred", "Inferred"], ["none", "Not found"]];
  const HARD = new Set(["chapter_11", "chapter_7", "receivership", "foreclosure", "deed_in_lieu", "closure",
    "warn_closure", "hud_loan_terminated", "loan_or_lease_default", "court_sale_process", "hud_delinquency",
    "special_servicing", "lender_takeback", "maturity_default"]);
  const TABS = [
    { key: "A", cls: "a", label: "List A · Live deals, named buyer" },
    { key: "B", cls: "b", label: "List B · Pre-sale distress" },
    { key: "C", cls: "c", label: "List C · Repeat buyers" },
  ];
  const BD_LABELS = [["court_sale_priority", "Court sale priority"], ["stage", "Distress stage"], ["buyer", "Buyer"],
    ["recency", "Recency"], ["size_fit", "Size fit ($3–30M)"], ["hud_exit", "HUD exit"], ["market_sale", "Market sale"],
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
  }

  /* ---------- KPIs ---------- */
  function buildKpis() {
    const items = S.data.items, n = items.length;
    const senior = items.filter((r) => r.care_types.some((t) => SENIOR_TYPES.includes(t))).length;
    const states = new Set(items.map((r) => r.state).filter(Boolean)).size;
    const named = items.filter((r) => r.buyer_group === "confirmed").length;
    const cnt = (k) => items.filter((r) => r.lists.includes(k)).length;
    const cards = [
      ["Total leads", fmtInt(n), "public sources only", null],
      ["List A", fmtInt(cnt("A")), "live deals, named buyer", "a"],
      ["List B", fmtInt(cnt("B")), "pre-sale distress", "b"],
      ["List C", fmtInt((S.data.list_c || []).length), "repeat buyers", "c"],
      ["AL / IL / MC / CCRC", n ? Math.round((100 * senior) / n) + "%" : "–", fmtInt(senior) + " senior-housing leads", null],
      ["States covered", fmtInt(states), "with at least one lead", null],
      ["Named buyer", n ? Math.round((100 * named) / n) + "%" : "–", fmtInt(named) + " confirmed in a source", null],
    ];
    $("kpis").replaceChildren(...cards.map(([label, value, sub, dot]) =>
      el("div", { class: "kpi" },
        el("div", { class: "label" }, dot ? el("span", { class: "dot " + dot }) : null, label),
        el("div", { class: "value", text: value }),
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
  function buildTabs() {
    const counts = { A: 0, B: 0, C: (S.data.list_c || []).length };
    for (const r of S.data.items) for (const l of r.lists) counts[l] = (counts[l] || 0) + 1;
    $("tabs").replaceChildren(...TABS.map((t) => el("button", {
      class: "tab " + t.cls, role: "tab", type: "button", "aria-selected": String(S.tab === t.key), "data-tab": t.key,
      onclick: () => { S.tab = t.key; S.page = 1; S.sort = { key: "rank", dir: 1 }; buildTabs(); render(); },
    }, el("span", { class: "dot " + t.cls }), t.label, el("span", { class: "count", text: fmtInt(counts[t.key] || 0) }))));
    const leadFilters = ["f-state", "f-care", "f-stage", "f-buyer"];
    for (const id of leadFilters) $(id).classList.toggle("hidden", S.tab === "C");
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
  const LEAD_COLS = [
    { key: "rank", label: "#", sort: (r) => r.rank },
    { key: "property", label: "Property", sort: (r) => r.property || "" },
    { key: "state", label: "City / state", sort: (r) => (r.state || "") + (r.city || "") },
    { key: "care", label: "Care type" },
    { key: "units", label: "Units", sort: (r) => size(r) ?? -1, num: true },
    { key: "signals", label: "Signals" },
    { key: "stage", label: "Stage", sort: (r) => STAGES.indexOf(r.stage) },
    { key: "buyer", label: "Buyer", sort: (r) => r.buyer || "~" },
    { key: "score", label: "Score", sort: (r) => r.score ?? 0 },
    { key: "sources", label: "Sources", sort: (r) => r.sources.length, num: true },
  ];
  const C_COLS = [
    { key: "rank", label: "#", sort: (c) => c.rank },
    { key: "buyer", label: "Buyer", sort: (c) => c.buyer },
    { key: "homes", label: "Homes in list", sort: (c) => c.homes_in_list, num: true },
    { key: "examples", label: "Example homes" },
    { key: "source", label: "Source" },
  ];

  function cell(col, r) {
    switch (col.key) {
      case "rank": return el("td", { class: "rank", text: String(r.rank) });
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
      case "sources": return el("td", { class: "num" }, r.sources.length ? String(r.sources.length) : nf());
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
    const label = { confirmed: "confirmed", candidate: "candidate", inferred: "inferred", none: "not found" }[r.buyer_group];
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
    const cols = isC ? C_COLS : LEAD_COLS;
    let rows = filtered();
    const col = cols.find((c) => c.key === S.sort.key && c.sort) || cols[0];
    rows = rows.slice().sort((a, b) => { const x = col.sort(a), y = col.sort(b); return (x > y ? 1 : x < y ? -1 : 0) * S.sort.dir; });
    S.rows = rows;
    $("thead").replaceChildren(el("tr", {}, cols.map((c) => el("th", {
      class: (c.sort ? "sortable" : "") + (c.num ? " num" : ""), scope: "col",
      "aria-sort": S.sort.key === c.key ? (S.sort.dir > 0 ? "ascending" : "descending") : null,
      onclick: c.sort ? () => { S.sort = { key: c.key, dir: S.sort.key === c.key ? -S.sort.dir : (c.key === "score" || c.key === "sources" || c.key === "units" || c.key === "homes" ? -1 : 1) }; render(); } : null,
    }, c.label, S.sort.key === c.key ? el("span", { class: "arrow", text: S.sort.dir > 0 ? "▲" : "▼" }) : null))));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    S.page = Math.min(S.page, pages);
    const slice = rows.slice((S.page - 1) * PAGE_SIZE, S.page * PAGE_SIZE);
    $("tbody").replaceChildren(...slice.map((r) => el("tr", isC ? {} : {
      tabindex: 0, onclick: () => openPanel(r), onkeydown: (e) => { if (e.key === "Enter") openPanel(r); },
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
  function openPanel(r) {
    const bd = r.breakdown || {};
    const tabKey = r.lists[0];
    $("p-kicker").replaceChildren(...r.lists.map((l) => el("span", { class: "dot " + l.toLowerCase() })),
      el("span", { text: (r.lists.length ? "List " + r.lists.join(" · ") + " · " : "") + "Rank " + r.rank }));
    $("p-title").textContent = r.property || "";
    $("p-sub").replaceChildren(r.city || r.state ? [r.city, r.state].filter(Boolean).join(", ") : nf());
    const sections = [];

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
    const conf = { confirmed: "Named in a source", candidate: "Candidate (proposed / stalking horse)",
      inferred: "Inferred (parent matched by brand, or a shell / older buyer)", none: "No buyer found" }[r.buyer_group];
    sections.push(section("Buyer", el("dl", { class: "facts" },
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
    $("panel").classList.remove("hidden"); $("scrim").classList.remove("hidden");
    $("panel").setAttribute("aria-hidden", "false");
    $("p-close").focus();
    void tabKey;
  }
  function section(title, ...kids) { return el("div", { class: "section" }, el("h3", { text: title }), ...kids); }
  function closePanel() {
    $("panel").classList.add("hidden"); $("scrim").classList.add("hidden"); $("panel").setAttribute("aria-hidden", "true");
  }
  $("p-close").addEventListener("click", closePanel);
  $("scrim").addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

  /* ---------- CSV of the current view ---------- */
  function csvCell(v) { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
  $("download").addEventListener("click", () => {
    if (!S.rows) return;
    let header, lines;
    if (S.tab === "C") {
      header = ["rank", "buyer", "homes_in_list", "example_homes", "public_source_link"];
      lines = S.rows.map((c) => header.map((k) => c[k]));
    } else {
      header = ["list", "rank", "property", "city", "state", "care_type", "units_or_beds", "signals", "stage", "buyer",
        "buyer_status", "score", "sources", "not_found"];
      lines = S.rows.map((r) => [S.tab, r.rank, r.property, r.city || "NOT FOUND", r.state || "NOT FOUND",
        r.care_types.join("/") || "NOT FOUND", size(r) ?? "NOT FOUND", r.signals.join(";"), r.stage, r.buyer || "NOT FOUND",
        r.buyer_group, r.score, r.sources.join(" "), r.not_found.join(";")]);
    }
    const csv = [header, ...lines].map((l) => l.map(csvCell).join(",")).join("\n");
    const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
      download: "deal-radar-list-" + S.tab.toLowerCase() + (S.state ? "-" + S.state : "") + ".csv" });
    document.body.append(a); a.click(); a.remove();
  });

  load().catch((e) => {
    $("asof").textContent = "Could not load data";
    $("kpis").replaceChildren(el("div", { class: "kpi" }, el("div", { class: "label", text: "Error" }), el("div", { class: "sub", text: String(e.message || e) })));
  });
})();
