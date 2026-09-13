/* Dashboard bridge — appended by dashboard/ui.py inside the page's <script data-dc-script> block.
 * Runs after `class Component extends DCLogic` in the same scope (support.js evalDcLogic), so it can
 * patch the prototype: load live data on mount, override the literals renderVals() hard-codes, and
 * route every button to the API. The design file itself is never edited (plan 0005). */
;(function () {
  if (typeof Component === "undefined") return;
  var P = Component.prototype;
  var origRender = P.renderVals;
  var origMount = P.componentDidMount;
  var origUnmount = P.componentWillUnmount;
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
    businessLocation: "", headerBadge: "Connecting…"
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
  };
  P.componentWillUnmount = function () {
    clearInterval(this.__timer);
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
    }
    this.setState(patch);
  };

  P.renderVals = function () {
    var live = this.__live, d = live && live.data;
    if (!d) {
      this.callsData = []; this.callbacksData = []; this.trendsData = [PLACEHOLDER_TREND]; this.unansweredDefs = [];
      this.menuRows = []; this.packetFields = []; this.callVolumeValues = [0, 0, 0, 0, 0, 0, 0]; this.callVolumeDates = ["", "", "", "", "", "", ""];
    }
    var vals = origRender.call(this);
    Object.assign(vals, DEFAULTS);
    var self = this;
    if (!d) {
      vals.platformGroups = []; vals.trendCountLabel = live && live.error ? "Backend unavailable: " + live.error : "Loading…";
      vals.showMoreLabel = ""; vals.callLogCountLabel = "Loading calls…";
      vals.freshness = { asOf: live && live.error ? "unavailable" : "loading", timezone: "", staleAfterMinutes: 60 };
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
    vals.callVolumeLabels = ov.chart.labels;
    vals.quickActions = ov.quickActions.map(function (q) {
      return Object.assign({}, q, {
        tagStyle: q.tag === "High" ? "background:var(--color-neutral-800);color:var(--color-neutral-300)" : "background:var(--color-neutral-200);color:var(--color-neutral-700)",
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
    }
    if (vals.scriptModalTrend) {
      var t = live.angles[s.scriptModalTrendId];
      var card = d.trends.items.filter(function (x) { return x.id === s.scriptModalTrendId; })[0];
      vals.scriptModalTrend = t ? { title: t.title, angles: t.angles, guide: t.guide }
        : { title: card ? card.title : "Trend", angles: ["Generating three angles from the transcript…"], guide: "One moment — the marketing agent is writing the breakdown." };
    }
    vals.overlordQuickQuestions = (vals.overlordQuickQuestions || []).map(function (q) {
      var question = { "Bookings last week?": "How many bookings did the AI take this week?", "What's trending?": "What's trending this week?",
        "What are people calling about?": "What are people calling about most?" }[q.label] || q.label;
      return { label: q.label, onClick: function () { self.askOverlord(question); } };
    });
    return vals;
  };

  // ---- actions → API ----
  P.__replaceLast = function (key, text) {
    this.setState(function (s) {
      var arr = (s[key] || []).slice();
      var idx = -1;
      arr.forEach(function (m, i) { if (m.pending) idx = i; });
      if (idx >= 0) arr[idx] = { from: "agent", text: text }; else arr.push({ from: "agent", text: text });
      return (function (o) { o[key] = arr; return o; })({});
    });
  };
  P.sendChat = function () {
    var text = (this.state.chatDraft || "").trim();
    if (!text) return;
    var self = this;
    this.setState(function (s) { return { marketingChat: s.marketingChat.concat([{ from: "user", text: text }, { from: "agent", text: "…", pending: true }]), chatDraft: "" }; });
    post("/api/marketing/chat", { message: text, thread_id: "ui" })
      .then(function (r) { self.__replaceLast("marketingChat", r.reply + (r.quality_warning ? " (" + r.quality_warning + ")" : "")); })
      .catch(function (e) { self.__replaceLast("marketingChat", "The marketing agent is unavailable right now: " + errText(e)); });
  };
  P.askOverlord = function (q) {
    var self = this;
    this.setState(function (s) { return { overlordThread: s.overlordThread.concat([{ from: "user", text: q }, { from: "agent", text: "…", pending: true }]) }; });
    post("/api/overlord/ask", { question: q })
      .then(function (r) { self.__replaceLast("overlordThread", r.answer); })
      .catch(function (e) { self.__replaceLast("overlordThread", "I can't reach the records right now: " + errText(e)); });
  };
  P.likeTrend = function (id) {
    var self = this, live = this.__live;
    if (id === "_none") return;
    this.setState(function (s) { return { likedTrends: Object.assign({}, s.likedTrends, (function (o) { o[id] = true; return o; })({})), scriptModalTrendId: id }; });
    if (live.angles[id] || live.pending[id]) return;
    live.pending[id] = true;
    var card = (this.trendsData || []).filter(function (x) { return x.id === id; })[0];
    post("/api/marketing/posts/" + encodeURIComponent(id) + "/like").then(function (r) {
      live.angles[id] = { title: card ? card.title : id, angles: r.angles || [], guide: r.breakdown || "No filming breakdown was returned." };
    }).catch(function (e) {
      live.angles[id] = { title: card ? card.title : id, angles: [], guide: "Could not generate angles: " + errText(e) };
      self.setState(function (s) { var l = Object.assign({}, s.likedTrends); delete l[id]; return { likedTrends: l }; });
    }).then(function () { delete live.pending[id]; self.setState({}); });
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
})();
