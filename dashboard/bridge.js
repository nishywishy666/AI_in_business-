/* Dashboard bridge — appended by dashboard/ui.py inside the page's <script data-dc-script> block.
 * Runs after `class Component extends DCLogic` in the same scope (support.js evalDcLogic), so it can
 * patch the prototype: load live data on mount, override the literals renderVals() hard-codes, and
 * route every button to the API. The design file itself is never edited (plan 0005). */
;(function () {
  if (typeof Component === "undefined") return;
  var P = Component.prototype;
  // Every segmented control (analytics tabs, period, call filters, Trending for you/globally): solid
  // theme-green segments with white text, matching the buttons. Injected here because UI/ is
  // read-only. `.seg-dark` — the 7D/30D switch on the dark chart card — keeps its own treatment.
  if (typeof document !== "undefined" && !document.getElementById("dashboard-bridge-style")) {
    var css = document.createElement("style");
    css.id = "dashboard-bridge-style";
    css.textContent =
      ".seg:not(.seg-dark){border-color:var(--color-accent);background:var(--color-accent);overflow:hidden}" +
      ".seg:not(.seg-dark) .seg-opt{background:var(--color-accent) !important;color:#fff !important;box-shadow:none !important;transition:background .18s ease}" +
      ".seg:not(.seg-dark) .seg-opt + .seg-opt{border-left-color:rgba(255,255,255,.25)}" +
      ".seg:not(.seg-dark) .seg-opt:has(input:checked){background:var(--color-accent) !important;color:#fff !important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.55) !important}" +
      ".seg:not(.seg-dark) .seg-opt:not(:has(input:checked)):hover{background:color-mix(in srgb,#fff 12%,var(--color-accent)) !important}" +
      // ---- buttons: the segmented control's solid-green look, globally, with a PillNav hover ----
      // Rest: deep-green pill, white label. Hover: a white circle rises from the bottom edge and
      // fills the pill (the PillNav effect, done with a pseudo-element instead of GSAP + extra spans,
      // because the export's DOM is React's and must not be restructured), and the label turns green.
      // !important throughout: several of these buttons carry inline colour from the design's own
      // renderVals(), and inline styles otherwise win.
      ".btn{position:relative;isolation:isolate;overflow:hidden;" +
      "background:var(--color-accent) !important;color:#fff !important;border-color:var(--color-accent) !important;" +
      "transition:color .2s ease .06s,transform .18s cubic-bezier(.22,1,.36,1),box-shadow .2s ease}" +
      ".btn::before{content:'';position:absolute;left:50%;bottom:0;width:165%;aspect-ratio:1;border-radius:50%;" +
      "background:var(--color-surface);transform:translate(-50%,50%) scale(0);pointer-events:none;z-index:-1;" +
      "transition:transform .42s cubic-bezier(.22,1,.36,1)}" +
      ".btn-icon::before{width:260%}" +  // a square button needs a bigger circle to cover its corners
      ".btn:hover::before,.btn:focus-visible::before{transform:translate(-50%,50%) scale(1)}" +
      ".btn:hover,.btn:focus-visible{color:var(--color-accent) !important}" +
      ".btn:hover{transform:translateY(-1px);box-shadow:var(--shadow-sm)}" +
      ".btn:active{transform:translateY(0) scale(.975)}" +
      ".btn:disabled::before{display:none}" +
      // the sidebar is itself the accent colour, so a solid-green button there would vanish
      ".dc-sidebar .btn{background:transparent !important;border-color:rgba(255,255,255,.45) !important}" +
      // ---- the agents' thinking state: mascot + animated ellipsis ----
      "@keyframes dc-dots{0%{content:'.'}33%{content:'..'}66%{content:'...'}}" +
      ".dc-dots::after{content:'.';animation:dc-dots 1.3s steps(1,end) infinite}" +
      "@keyframes dc-mascot-bob{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-3px) rotate(2deg)}}" +
      ".dc-chat-mascot{animation:dc-mascot-bob 2.4s ease-in-out infinite}" +
      "@media (prefers-reduced-motion:reduce){.dc-dots::after{animation:none;content:'…'}" +
      ".dc-chat-mascot{animation:none}}" +
      // ---- motion: modals, floating popups, sidebar collapse, expanded call row ----
      // Entry is pure CSS (the node mounts, the animation plays once). Exit needs the JS wrappers at
      // the bottom of this file, which hold the node for one beat with .dc-closing before React drops it.
      "@keyframes dc-fade{from{opacity:0}to{opacity:1}}" +
      "@keyframes dc-dialog-in{from{opacity:0;transform:translateY(14px) scale(.965)}to{opacity:1;transform:none}}" +
      "@keyframes dc-menu-in{from{opacity:0;transform:translateY(-8px) scale(.97)}to{opacity:1;transform:none}}" +
      "@keyframes dc-panel-in{from{opacity:0;transform:translateY(16px) scale(.94)}to{opacity:1;transform:none}}" +
      "@keyframes dc-row-in{from{opacity:0;transform:translateY(-8px);padding-top:0;padding-bottom:0}" +
      "to{opacity:1;transform:none;padding-top:18px;padding-bottom:18px}}" +
      ".dialog-backdrop{animation:dc-fade .16s ease-out both}" +
      ".dialog-backdrop > .dialog{animation:dc-dialog-in .26s cubic-bezier(.22,1,.36,1) both}" +
      // the two floating popups are the only .elev-lg cards, so they animate even before the observer
      // has tagged them; the tagged classes (same specificity, declared after) set the real origin
      ".card.elev-lg{animation:dc-menu-in .16s cubic-bezier(.22,1,.36,1) both;transform-origin:top center}" +
      ".card.dc-profile-menu{transform-origin:top center}" +
      ".card.dc-overlord-panel{animation:dc-panel-in .22s cubic-bezier(.22,1,.36,1) both;transform-origin:bottom right}" +
      // Exit uses its own @keyframes rather than animation-direction:reverse — the entry animation has
      // already finished by then, and only a change of animation-name restarts one.
      "@keyframes dc-fade-out{to{opacity:0}}" +
      "@keyframes dc-dialog-out{to{opacity:0;transform:translateY(10px) scale(.97)}}" +
      "@keyframes dc-menu-out{to{opacity:0;transform:translateY(-6px) scale(.97)}}" +
      "@keyframes dc-panel-out{to{opacity:0;transform:translateY(12px) scale(.95)}}" +
      "@keyframes dc-row-out{to{opacity:0;transform:translateY(-8px);padding-top:0;padding-bottom:0}}" +
      ".dc-closing{pointer-events:none}" +
      ".dialog-backdrop.dc-closing{animation:dc-fade-out .16s ease-in both}" +
      ".dialog-backdrop.dc-closing > .dialog{animation:dc-dialog-out .16s ease-in both}" +
      ".card.dc-profile-menu.dc-closing{animation:dc-menu-out .14s ease-in both}" +
      ".card.dc-overlord-panel.dc-closing{animation:dc-panel-out .16s ease-in both}" +
      ".table td[colspan].dc-closing{animation:dc-row-out .16s ease-in both}" +
      // the sidebar carries its own inline transition:width .15s — slow it down and ease it
      ".dc-sidebar{transition:width .3s cubic-bezier(.22,1,.36,1) !important;" +
      // the nav list scrolls, the collapse button under it stays pinned in view
      "position:sticky;top:0;height:100vh;max-height:100vh;align-self:flex-start}" +
      ".dc-sidebar > div{flex:none}" +
      ".dc-sidebar > div.dc-sidebar-nav{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;scrollbar-width:thin}" +
      // the transcript row: a table cell cannot animate its own height, so the reveal is the padding
      // opening up under a fading, sliding body — reads as a collapse without breaking table layout
      ".table td[colspan]{animation:dc-row-in .28s cubic-bezier(.22,1,.36,1) both}" +
      "@media (prefers-reduced-motion:reduce){" +
      ".dialog-backdrop,.dialog-backdrop > .dialog,.card.elev-lg,.card.dc-overlord-panel,.table td[colspan]" +
      "{animation:none !important}.dc-sidebar{transition:none !important}" +
      // the hover still swaps to white, it just arrives without the rise
      ".btn,.btn::before{transition:none !important}}";
    document.head.appendChild(css);
    // The two floating popups and the sidebar are plain divs in the export, told apart by the inline
    // styles the design's renderVals() gives them (support.js turns the style string into React's
    // style object, so these read back off el.style).
    var tagMotionTargets = function () {
      var pops = document.querySelectorAll(".card.elev-lg");
      for (var i = 0; i < pops.length; i++) {
        var st = pops[i].style;
        if (st.top === "76px") pops[i].classList.add("dc-profile-menu");
        else if (st.bottom === "64px") pops[i].classList.add("dc-overlord-panel");
      }
      if (document.querySelector(".dc-sidebar")) return;  // found once; skip the wide scan per mutation
      var divs = document.querySelectorAll("div[style]");
      for (var j = 0; j < divs.length; j++) {
        var d = divs[j].style;
        if (d.flexDirection === "column" && (d.width === "64px" || d.width === "220px")) {
          divs[j].classList.add("dc-sidebar");
          var kids = divs[j].children;
          for (var k = 0; k < kids.length; k++) {
            if (kids[k].style && kids[k].style.flex === "1") kids[k].classList.add("dc-sidebar-nav");
          }
          return;
        }
      }
    };
    new MutationObserver(tagMotionTargets).observe(document.body || document.documentElement, { childList: true, subtree: true });
    tagMotionTargets();
  }
  var origRender = P.renderVals;
  var origMount = P.componentDidMount;
  var origUnmount = P.componentWillUnmount;
  // Shown one at a time beside the mascot while a turn is in flight. Kept in Tony's register.
  var THINKING_WORDS = ["Thinking", "Pondering", "Mulling it over", "Chewing on it", "Turning it over",
    "Noodling on it", "Having a think", "Weighing it up", "Working it out", "Putting it together",
    "Casting an eye over it", "Giving it a moment"];
  function nextWord(current) {
    var pick = current;
    while (pick === current) pick = THINKING_WORDS[Math.floor(Math.random() * THINKING_WORDS.length)];
    return pick;
  }
  var MAX_CHAT_RETRIES = 4;  // ~4 cooldowns before the question gives up and reports back
  var PLACEHOLDER_TREND = { id: "_none", niche: true, platform: "", score: 0, title: "No scan yet", why: "" };
  var BAND_STYLE = {
    "Good": { icon: "✓", style: "font-size:11px;font-weight:600;color:#3a7a4a" },
    "Watch": { icon: "△", style: "font-size:11px;font-weight:600;color:var(--color-accent-600)" },
    "Needs attention": { icon: "⚠", style: "font-size:11px;font-weight:600;color:#a03a3a" },
    "No data": { icon: "·", style: "font-size:11px;font-weight:600;color:var(--color-neutral-500)" }
  };
  var DEFAULTS = {
    kpiBookings: "–", kpiCovers: "–", kpiCalls: "–", kpiContainment: "–", kpiContainmentMeta: "loading…",
    kpiOutside: "–", kpiOutsidePct: "–", kpiOutsideMeta: "", menuMetaLabel: "loading…", menuCountLabel: "Menu",
    dataSourceLabel: "loading…", usageMinutesLabel: "…", usageMinutesPct: 0, ownerName: "Owner", ownerSub: "",
    businessLocation: "", headerBadge: "Connecting…",
    refreshTrendsLabel: "Refresh now", refreshTrendsStyle: "padding:3px 10px;font-size:11px;white-space:nowrap;flex:none",
    refreshTrends: function () {},
    overlordDraft: "", overlordPlaceholder: "Ask about calls, bookings or trends…",
    setOverlordDraft: function () {}, overlordKeyDown: function () {}, sendOverlord: function () {},
    chartRange7: true, chartRange30: false, setChartRange7: function () {}, setChartRange30: function () {},
    chartDeltaArrow: "↑", chartDeltaLabel: "vs first day in last 7 days",
    goProfile: function () {}
  };

  function api(path, opts) {
    var init = Object.assign({ headers: { "content-type": "application/json" } }, opts || {});
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) {
          var err = new Error(body.error || body.detail || r.statusText || ("HTTP " + r.status));
          err.status = r.status; err.body = body; throw err;
        }
        return body;
      });
    });
  }
  function post(path, data) { return api(path, { method: "POST", body: JSON.stringify(data || {}) }); }
  function fromIds(ids) { var m = {}; (ids || []).forEach(function (id) { m[id] = true; }); return m; }
  function errText(e) { return (e && e.body && (e.body.note || e.body.error || e.body.detail)) || (e && e.message) || String(e); }

  P.componentDidMount = function () {
    if (origMount) { try { origMount.call(this); } catch (e) { console.error(e); } }
    this.__live = { analytics: {}, angles: {}, pending: {} };
    var self = this;
    this.__refresh();
    this.__timer = setInterval(function () { self.__refresh(); }, 60000);
    // Click-out for the profile menu. The menu and its trigger share one positioned parent, so
    // "outside" is anything not inside that parent — which keeps the trigger's own click a plain
    // toggle instead of an open-then-immediately-close.
    this.__clickOut = function (e) {
      var menu = document.querySelector(".dc-profile-menu");
      if (!menu || !menu.parentElement || menu.parentElement.contains(e.target)) return;
      if (self.state.profileMenuOpen) self.toggleProfileMenu();
    };
    if (typeof document !== "undefined") document.addEventListener("mousedown", this.__clickOut, true);
  };
  P.componentWillUnmount = function () {
    clearInterval(this.__timer);
    if (this.__live) { clearInterval(this.__live.thinkTimer); clearTimeout(this.__live.retryTimer); }
    if (this.__clickOut && typeof document !== "undefined") document.removeEventListener("mousedown", this.__clickOut, true);
    if (origUnmount) { try { origUnmount.call(this); } catch (e) { console.error(e); } }
  };

  P.__refresh = function () {
    var self = this;
    var period = this.state.analyticsPeriod || "today";
    return api("/api/dashboard/bootstrap?period=" + encodeURIComponent(period)).then(function (d) {
      self.__apply(d, period);
    }).catch(function (e) {
      console.error("[dashboard] refresh failed", e);
      if (self.__live) { self.__live.error = errText(e); self.setState({}); }
    });
  };

  P.__apply = function (d, period) {
    var live = this.__live || (this.__live = { analytics: {}, angles: {}, pending: {} });
    live.data = d; live.error = null; live.loadedAt = Date.now();
    live.analytics[d.analytics.period.key] = d.analytics;
    this.callsData = d.calls;
    this.callbacksData = d.callbacks;
    this.trendsData = d.trends.items.length ? d.trends.items : [PLACEHOLDER_TREND];
    this.unansweredDefs = d.gaps;
    this.menuRows = d.setup.menuRows;
    this.packetFields = d.setup.packetFields;
    this.callVolumeValues = d.overview.chart.values;
    this.callVolumeDates = d.overview.chart.dates;
    var reviews = {};
    d.gaps.forEach(function (g) { if (g.review) reviews[g.id] = g.review; });
    var patch = {
      likedTrends: fromIds(d.trends.likedIds), savedTrends: fromIds(d.trends.savedIds),
      gapReviews: reviews, callbackStatus: {},
      notifPrefs: d.settings.notifPrefs || this.state.notifPrefs, packetConfirmed: !!d.settings.packetConfirmed,
      menuUploaded: d.setup.menuRows.length > 0
    };
    if (!live.greeted) {
      patch.overlordThread = [{ from: "agent", text: d.overlord.greeting }];
      patch.marketingChat = [{ from: "agent", text: d.trends.greeting }];
      live.greeted = true;
      live.greeting = d.trends.greeting;
    } else if (live.greeting !== d.trends.greeting && (this.state.marketingChat || []).length <= 1) {
      // the scan landed after the panel first rendered; refresh the untouched opening line
      patch.marketingChat = [{ from: "agent", text: d.trends.greeting }];
      live.greeting = d.trends.greeting;
    }
    this.setState(patch);
  };

  P.renderVals = function () {
    var live = this.__live, d = live && live.data;
    if (!d) {
      this.callsData = []; this.callbacksData = []; this.trendsData = [PLACEHOLDER_TREND]; this.unansweredDefs = [];
      this.menuRows = []; this.packetFields = []; this.callVolumeValues = [0, 0, 0, 0, 0, 0, 0]; this.callVolumeDates = ["", "", "", "", "", "", ""];
    } else {
      // 7D/30D: the design derives the whole chart (path, grid, hover, delta) from these two arrays,
      // so switching the series here is all the toggle has to do. Must happen before origRender.
      var series = (this.state.chartRange === "30d" && d.overview.chart30) || d.overview.chart;
      this.callVolumeValues = series.values;
      this.callVolumeDates = series.dates;
      live.series = series;
    }
    var vals = origRender.call(this);
    Object.assign(vals, DEFAULTS);
    var self = this;
    // "Refresh now" beside the trend count (anchor patched in by dashboard/ui.py). Bound on both
    // paths, because the case it exists for is the one where no data has loaded yet.
    var refreshing = !!(live && live.refreshing);
    vals.refreshTrendsLabel = refreshing ? "Refreshing…" : "Refresh now";
    vals.refreshTrendsStyle = "padding:3px 10px;font-size:11px;white-space:nowrap;flex:none"
      + (refreshing ? ";opacity:.6;cursor:progress" : "");
    vals.refreshTrends = function () { self.refreshTrends(); };
    // Overlord chat box (anchor patched in by dashboard/ui.py) — bound on both paths so the panel is
    // usable before the first bootstrap lands.
    vals.overlordDraft = this.state.overlordDraft || "";
    vals.setOverlordDraft = function (e) { self.setOverlordDraft(e); };
    vals.overlordKeyDown = function (e) { self.overlordKeyDown(e); };
    vals.sendOverlord = function () { self.sendOverlord(); };
    // 7D/30D switch — bound on both paths so a click before the first load still takes
    var thirty = this.state.chartRange === "30d";
    vals.chartRange7 = !thirty; vals.chartRange30 = thirty;
    vals.setChartRange7 = function () { self.setChartRange("7d"); };
    vals.setChartRange30 = function () { self.setChartRange("30d"); };
    vals.goProfile = function () { self.setScreen("profile"); };
    // My saves / My likes rows open the trend they name
    vals.savedLikedTrends = (vals.savedLikedTrends || []).map(function (t) {
      return Object.assign({}, t, { onOpen: function () { self.openTrend(t.id); } });
    });
    if (!d) {
      vals.platformGroups = [];
      vals.trendCountLabel = refreshing ? "Refreshing trends…"
        : (live && (live.refreshError || live.error) ? "Backend unavailable: " + (live.refreshError || live.error) : "Loading…");
      vals.showMoreLabel = ""; vals.callLogCountLabel = "Loading calls…";
      vals.freshness = { asOf: live && live.error ? "unavailable" : "loading", timezone: "", staleAfterMinutes: 60 };
      vals.marketingChat = this.__chatRows(vals.marketingChat);
      vals.overlordThread = this.__chatRows(vals.overlordThread);
      return vals;
    }
    var s = this.state;
    var a = live.analytics[s.analyticsPeriod] || d.analytics;
    var ov = d.overview;

    // ---- header / identity ----
    Object.assign(vals, ov.kpis, {
      menuMetaLabel: d.setup.menuMetaLabel, menuCountLabel: d.setup.menuCountLabel,
      dataSourceLabel: a.dataSourceLabel + (a.freshness.stale ? " · stale" : ""),
      usageMinutesLabel: d.usage.label, usageMinutesPct: d.usage.pct,
      ownerName: d.setup.ownerName, ownerSub: d.setup.ownerSub, businessLocation: d.setup.businessLocation,
      headerBadge: live.error ? "Refresh failed · showing last data" : (d.business.demo ? "Demo data" : "Live"),
      profileFields: d.setup.profileFields
    });
    if (s.screen === "overview") vals.pageTitle = "Welcome, " + d.setup.firstName;
    vals.freshness = Object.assign({}, d.analytics.freshness, live.error ? { asOf: "unavailable (" + live.error + ")" } : {});

    // ---- overview ----
    vals.routeLegend = ov.routeMix.legend; vals.donutStyle = ov.routeMix.donutStyle; vals.donutTotal = ov.routeMix.total;
    var chartSeries = live.series || ov.chart, delta = parseFloat(vals.chartDeltaPct);
    vals.callVolumeLabels = chartSeries.labels;
    vals.chartDeltaArrow = delta < 0 ? "↓" : delta > 0 ? "↑" : "↔";
    vals.chartDeltaLabel = "vs first day in last " + (chartSeries.days || chartSeries.values.length) + " days";
    // 30 dashed grid lines is noise; keep the ones the surviving x-labels sit under
    if (thirty) vals.chartGridX = (vals.chartGridX || []).filter(function (_, i) { return i % 5 === 0; });
    vals.quickActions = ov.quickActions.map(function (q) {
      return Object.assign({}, q, {
        // themed pills: amber for the one that wants attention, accent green for the rest
        tagStyle: q.tag === "High" ? "background:var(--color-accent-400);color:var(--color-accent-900);border-color:var(--color-accent-400)"
          : "background:var(--color-accent);color:#fff;border-color:var(--color-accent)",
        onClick: function () { self.setScreen(q.screen); }
      });
    });

    // ---- calls (per-row overrides of what the template derives from ids) ----
    var byId = {};
    d.calls.forEach(function (c) { byId[c.id] = c; });
    function enrich(row) {
      var src = byId[row.id];
      if (!src) return row;
      row.ttfb = src.ttfb; row.metrics = src.metrics; row.summaryText = src.summaryText;
      if (row.transcript) row.transcript.forEach(function (line, i) { if (src.transcript[i]) line.ts = src.transcript[i].ts; });
      return row;
    }
    (vals.calls || []).forEach(enrich);
    if (vals.summaryModalCall) enrich(vals.summaryModalCall);
    vals.callLogCountLabel = "Showing " + (vals.calls || []).length + " of " + d.calls.length + " " + ov.callLogCountLabel;
    vals.voiceOpsTiles = a.voiceOpsTiles;

    // ---- analytics ----
    vals.impactTiles = a.impactTiles;
    vals.voiceOpsBandTiles = a.voiceOpsBandTiles.map(function (t) {
      var b = BAND_STYLE[t.band] || BAND_STYLE["No data"];
      return Object.assign({}, t, { bandWord: t.band, bandIcon: b.icon, bandStyle: b.style });
    });
    vals.handoffReasonBars = a.handoffReasonBars;
    vals.openCallbacksList = a.openCallbacksList;
    vals.oldestCallbackWait = a.oldestCallbackWait;

    // ---- marketing ----
    if (d.trends.empty || !d.trends.items.length) {
      vals.platformGroups = []; vals.showMoreLabel = "";
      vals.trendCountLabel = d.trends.note || "No scan yet";
      vals.savedLikedTrends = []; vals.savedCount = 0; vals.likedCount = 0;
    } else {
      vals.trendCountLabel = vals.trendCountLabel + " · scan " + d.trends.scanId;
      if (d.trends.platformsNote) vals.trendCountLabel = vals.trendCountLabel + " · " + d.trends.platformsNote;
    }
    if (refreshing) vals.trendCountLabel = "Refreshing trends…";
    else if (live.refreshError) vals.trendCountLabel = vals.trendCountLabel + " · refresh failed: " + live.refreshError;
    if (vals.scriptModalTrend) {
      var t = live.angles[s.scriptModalTrendId];
      var card = d.trends.items.filter(function (x) { return x.id === s.scriptModalTrendId; })[0];
      vals.scriptModalTrend = t ? { title: t.title, angles: t.angles, guide: t.guide }
        : { title: card ? card.title : "Trend", angles: ["Generating three angles from the transcript…"], guide: "One moment — the marketing agent is writing the breakdown." };
    }
    vals.marketingChat = this.__chatRows(vals.marketingChat);
    vals.overlordThread = this.__chatRows(vals.overlordThread);
    vals.overlordQuickQuestions = (vals.overlordQuickQuestions || []).map(function (q) {
      var question = { "Bookings last week?": "How many bookings did the AI take this week?", "What's trending?": "What's trending this week?",
        "What are people calling about?": "What are people calling about most?" }[q.label] || q.label;
      return { label: q.label, onClick: function () { self.askOverlord(question); } };
    });
    return vals;
  };

  // ---- the thinking state ----
  // A turn in flight is not a message: it is the mascot, one of THINKING_WORDS, and an animated
  // ellipsis, in place of the design's "…" bubble. While a free model is only rate-limited (not out
  // of quota for the day) the question stays open and this keeps running until the retry lands.
  P.__chatRows = function (rows) {
    var live = this.__live, word = (live && live.thinkWord) || THINKING_WORDS[0];
    return (rows || []).map(function (m) {
      var side = m.from === "user" ? "flex-end" : "flex-start";
      var row = { rowStyle: "display:flex;align-items:flex-end;gap:8px;align-self:" + side + ";max-width:92%",
        mascotStyle: "display:none", dotsStyle: "display:none" };
      if (!m.pending) return Object.assign({}, m, row);
      return Object.assign({}, m, row, {
        text: word,
        mascotStyle: "width:34px;height:34px;flex:none;object-fit:contain;border-radius:50%;padding:2px;"
          + "background:var(--color-neutral-900)",
        dotsStyle: "",
        bubbleStyle: "align-self:flex-end;font-size:12px;font-style:italic;color:var(--color-neutral-600);"
          + "background:transparent;padding:6px 0;max-width:none"
      });
    });
  };
  P.__startThinking = function () {
    var self = this, live = this.__live;
    if (!live || live.thinkTimer) return;
    live.thinkWord = nextWord(null);
    live.thinkTimer = setInterval(function () {
      live.thinkWord = nextWord(live.thinkWord);
      self.setState({});
    }, 2400);
  };
  P.__stopThinking = function (stillPending) {
    var live = this.__live;
    if (!live || !live.thinkTimer || stillPending) return;
    clearInterval(live.thinkTimer);
    live.thinkTimer = null;
  };

  // ---- actions → API ----
  P.__replaceLast = function (key, text) {
    var self = this;
    this.setState(function (s) {
      var arr = (s[key] || []).slice();
      var idx = -1;
      arr.forEach(function (m, i) { if (m.pending) idx = i; });
      if (idx >= 0) arr[idx] = { from: "agent", text: text }; else arr.push({ from: "agent", text: text });
      return (function (o) { o[key] = arr; return o; })({});
    }, function () {  // after the update, so the check sees the thread this turn just settled
      var open = ((self.state.marketingChat || []).concat(self.state.overlordThread || []))
        .some(function (m) { return m.pending; });
      self.__stopThinking(open);
    });
  };
  // What the agents can honestly answer from right now. Asking before the data is in produced
  // confusing failures ("marketing data unavailable"), so the chat answers for itself instead.
  P.__dataState = function () {
    var live = this.__live, d = live && live.data;
    if (!d) {
      return { ready: false, marketing: false,
        text: live && live.error ? "I can't reach your dashboard data right now (" + live.error + "). I'll answer as soon as it's back."
          : "Your data hasn't loaded yet — give me a moment and ask again." };
    }
    if (live.refreshing) return { ready: true, marketing: false, text: "I'm still analysing the latest scan — ask me again in a moment." };
    var t = d.trends;
    if (t.empty) {
      return { ready: true, marketing: false,
        text: "No trend data has loaded yet" + (t.note ? " — " + t.note : ". The first scan is scheduled; ask me once it lands.") };
    }
    if (!t.items.length) return { ready: true, marketing: false, text: "The scan has landed but I'm still analysing it — ask me again in a moment." };
    return { ready: true, marketing: true };
  };
  P.__answerLocally = function (key, question, text) {
    this.setState(function (s) {
      return { marketingChat: key === "marketingChat" ? s.marketingChat.concat([{ from: "user", text: question }, { from: "agent", text: text }]) : s.marketingChat,
        overlordThread: key === "overlordThread" ? s.overlordThread.concat([{ from: "user", text: question }, { from: "agent", text: text }]) : s.overlordThread,
        chatDraft: key === "marketingChat" ? "" : s.chatDraft, overlordDraft: key === "overlordThread" ? "" : s.overlordDraft };
    });
  };
  P.sendChat = function () {
    var text = (this.state.chatDraft || "").trim();
    if (!text) return;
    var self = this;
    var state = this.__dataState();
    if (!state.marketing) return this.__answerLocally("marketingChat", text, state.text);
    this.setState(function (s) { return { marketingChat: s.marketingChat.concat([{ from: "user", text: text }, { from: "agent", text: "…", pending: true }]), chatDraft: "" }; });
    this.__startThinking();
    // `retry_after` means every free model is briefly rate-limited, not that the answer failed: hold
    // the turn open, keep the mascot thinking, and ask again when the cooldown is up.
    var live = this.__live, tries = 0;
    var ask = function () {
      post("/api/marketing/chat", { message: text, thread_id: "ui", retry: tries > 0 }).then(function (r) {
        if (r.retry_after && tries < MAX_CHAT_RETRIES) {
          tries++;
          live.retryTimer = setTimeout(ask, Math.min(90, Math.max(5, r.retry_after)) * 1000);
          return;
        }
        self.__replaceLast("marketingChat", r.reply + (r.quality_warning ? " (" + r.quality_warning + ")" : ""));
      }).catch(function (e) {
        self.__replaceLast("marketingChat", "The marketing agent is unavailable right now: " + errText(e));
      });
    };
    ask();
  };
  P.setOverlordDraft = function (e) { this.setState({ overlordDraft: e.target.value }); };
  P.overlordKeyDown = function (e) { if (e.key === "Enter") this.sendOverlord(); };
  P.sendOverlord = function () {
    var text = (this.state.overlordDraft || "").trim();
    if (!text) return;
    this.setState({ overlordDraft: "" });
    this.askOverlord(text);
  };
  P.askOverlord = function (q) {
    var self = this;
    var state = this.__dataState();  // the overlord reads the records, so it only needs the bootstrap
    if (!state.ready) return this.__answerLocally("overlordThread", q, state.text);
    this.setState(function (s) { return { overlordThread: s.overlordThread.concat([{ from: "user", text: q }, { from: "agent", text: "…", pending: true }]), overlordDraft: "" }; });
    this.__startThinking();
    post("/api/overlord/ask", { question: q })
      .then(function (r) { self.__replaceLast("overlordThread", r.answer); })
      .catch(function (e) { self.__replaceLast("overlordThread", "I can't reach the records right now: " + errText(e)); });
  };
  // Manual re-read of the trend scan: the bridge otherwise only polls every 60s, and a brief that
  // failed to build once stays failed until the server is asked again. Read-only on the backend.
  P.refreshTrends = function () {
    var self = this, live = this.__live || (this.__live = { analytics: {}, angles: {}, pending: {} });
    if (live.refreshing) return;
    live.refreshing = true; live.refreshError = null; this.setState({});
    post("/api/dashboard/trends/refresh").then(function () {
      return self.__refresh();  // the forced read already warmed the cache; pull the whole payload
    }).catch(function (e) {
      live.refreshError = errText(e);
    }).then(function () { live.refreshing = false; self.setState({}); });
  };
  P.__fetchAngles = function (id, onError) {
    var self = this, live = this.__live;
    if (live.angles[id] || live.pending[id]) return;
    live.pending[id] = true;
    var card = (this.trendsData || []).filter(function (x) { return x.id === id; })[0];
    post("/api/marketing/posts/" + encodeURIComponent(id) + "/like").then(function (r) {
      live.angles[id] = { title: card ? card.title : id, angles: r.angles || [], guide: r.breakdown || "No filming breakdown was returned." };
    }).catch(function (e) {
      live.angles[id] = { title: card ? card.title : id, angles: [], guide: "Could not generate angles: " + errText(e) };
      if (onError) onError(e);
    }).then(function () { delete live.pending[id]; self.setState({}); });
  };
  P.likeTrend = function (id) {
    var self = this;
    if (id === "_none") return;
    this.setState(function (s) { return { likedTrends: Object.assign({}, s.likedTrends, (function (o) { o[id] = true; return o; })({})), scriptModalTrendId: id }; });
    this.__fetchAngles(id, function () {  // the like did not stick: drop the optimistic heart
      self.setState(function (s) { var l = Object.assign({}, s.likedTrends); delete l[id]; return { likedTrends: l }; });
    });
  };
  // Opening a row in My saves / My likes: show its script card without also liking it.
  P.openTrend = function (id) {
    if (id === "_none") return;
    this.setState({ savedLikedModal: null, scriptModalTrendId: id });
    this.__fetchAngles(id);
  };
  P.saveTrend = function (id) {
    var self = this;
    if (id === "_none") return;
    this.setState(function (s) { return { savedTrends: Object.assign({}, s.savedTrends, (function (o) { o[id] = true; return o; })({})) }; });
    post("/api/dashboard/trends/" + encodeURIComponent(id) + "/save").then(function () {
      self.setState(function (s) { return { likedTrends: Object.assign({}, s.likedTrends, (function (o) { o[id] = true; return o; })({})) }; });
    }).catch(function (e) {
      self.setState(function (s) { var m = Object.assign({}, s.savedTrends); delete m[id]; return { savedTrends: m }; });
      alert("Could not save this trend: " + errText(e));
    });
  };
  P.saveScriptToLibrary = function () {
    var id = this.state.scriptModalTrendId;
    this.setState({ scriptModalTrendId: null });
    if (id) this.saveTrend(id);
  };
  P.markCallbackDone = function (id) {
    var self = this;
    this.setState(function (s) { return { callbackStatus: Object.assign({}, s.callbackStatus, (function (o) { o[id] = "done"; return o; })({})) }; });
    post("/api/dashboard/callbacks/" + encodeURIComponent(id) + "/status", { status: "done" })
      .then(function () { return self.__refresh(); })
      .catch(function (e) { alert("Could not update the callback: " + errText(e)); self.__refresh(); });
  };
  P.__reviewGap = function (id, status) {
    var self = this;
    var gap = (this.unansweredDefs || []).filter(function (g) { return g.id === id; })[0];
    this.setState(function (s) { return { gapReviews: Object.assign({}, s.gapReviews, (function (o) { o[id] = status; return o; })({})) }; });
    post("/api/dashboard/gaps/" + encodeURIComponent(id) + "/review", { status: status, question: gap ? gap.text : null })
      .catch(function (e) { alert("Could not save the review: " + errText(e)); self.__refresh(); });
  };
  P.approveGap = function (id) { this.__reviewGap(id, "approved"); };
  P.dismissGap = function (id) { this.__reviewGap(id, "dismissed"); };
  P.setChartRange = function (range) { this.setState({ chartRange: range }); };
  P.setAnalyticsPeriod = function (p) {
    var self = this, live = this.__live;
    this.setState({ analyticsPeriod: p });
    if (live && !live.analytics[p]) {
      api("/api/dashboard/analytics?period=" + encodeURIComponent(p)).then(function (a) { live.analytics[p] = a; self.setState({}); })
        .catch(function (e) { console.error("[dashboard] analytics", e); });
    }
  };
  P.toggleNotif = function (key) {
    var next = Object.assign({}, this.state.notifPrefs);
    next[key] = !next[key];
    this.setState({ notifPrefs: next });
    post("/api/dashboard/settings", { notifPrefs: next }).catch(function (e) { console.error("[dashboard] settings", e); });
  };
  P.confirmPacket = function () {
    this.setState({ packetConfirmed: true });
    post("/api/dashboard/settings", { packetConfirmed: true }).catch(function (e) { console.error("[dashboard] settings", e); });
  };

  // ---- exit animations ---------------------------------------------------------------------------
  // React unmounts a closed popup on the very next render, so there is nothing left to animate out.
  // These wrappers hold it for one beat: tag the live node with .dc-closing, let the CSS above play,
  // then run the original close. Wrapped last so they sit outside this file's own overrides too.
  var CLOSE_MS = 170;
  function reducedMotion() {
    return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion:reduce)").matches;
  }
  function closeAfterAnimation(selector, run) {
    var el = typeof document !== "undefined" && document.querySelector(selector);
    if (!el || el.classList.contains("dc-closing") || reducedMotion()) return run();
    el.classList.add("dc-closing");
    setTimeout(run, CLOSE_MS);
  }
  function wrapClose(name, selector, isOpen) {
    var orig = P[name];
    if (typeof orig !== "function") return;
    P[name] = function () {
      var self = this, args = arguments;
      if (isOpen && !isOpen(this.state, args)) return orig.apply(self, args);  // a toggle on its way open
      closeAfterAnimation(selector, function () { orig.apply(self, args); });
    };
  }
  wrapClose("closeSummaryModal", ".dialog-backdrop");
  wrapClose("closeSavedLikedModal", ".dialog-backdrop");
  wrapClose("closeScriptModal", ".dialog-backdrop");
  wrapClose("saveScriptToLibrary", ".dialog-backdrop");
  wrapClose("toggleOverlord", ".dc-overlord-panel", function (s) { return s.overlordOpen; });
  wrapClose("toggleProfileMenu", ".dc-profile-menu", function (s) { return s.profileMenuOpen; });
  // only when this click closes the row; expanding a different row swaps straight over
  wrapClose("toggleCall", ".table td[colspan]", function (s, args) { return s.expandedCallId === args[0]; });
})();
