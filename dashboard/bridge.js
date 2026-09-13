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
      ".seg:not(.seg-dark) .seg-opt{background:var(--color-accent) !important;color:#fff !important;box-shadow:none !important;transition:color .2s ease .06s}" +
      ".seg:not(.seg-dark) .seg-opt + .seg-opt{border-left-color:rgba(255,255,255,.25)}" +
      ".seg:not(.seg-dark) .seg-opt:has(input:checked){background:var(--color-accent) !important;color:#fff !important;box-shadow:inset 0 0 0 1px rgba(255,255,255,.55) !important}" +
      // the same rising circle the buttons use, on the options you can still pick
      ".seg:not(.seg-dark) .seg-opt{position:relative;isolation:isolate;overflow:hidden}" +
      ".seg:not(.seg-dark) .seg-opt::before{content:'';position:absolute;left:50%;bottom:0;width:165%;aspect-ratio:1;" +
      "border-radius:50%;background:var(--color-surface);transform:translate(-50%,50%) scale(0);pointer-events:none;" +
      "z-index:-1;transition:transform .42s cubic-bezier(.22,1,.36,1)}" +
      ".seg:not(.seg-dark) .seg-opt:not(:has(input:checked)):hover::before{transform:translate(-50%,50%) scale(1)}" +
      ".seg:not(.seg-dark) .seg-opt:not(:has(input:checked)):hover{color:var(--color-accent) !important}" +
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
      // ---- the marketing loader: a toastie doing laps of a ring ----
      // The arm spins; the toastie counter-spins by the same amount so it stays upright as it
      // travels, and adds its own slow wobble on top. Steam drifts on a third, shorter cycle.
      "@keyframes dc-orbit{to{transform:rotate(360deg)}}" +
      "@keyframes dc-unspin{to{transform:rotate(-360deg)}}" +
      "@keyframes dc-wobble{0%,100%{transform:rotate(-9deg)}50%{transform:rotate(9deg)}}" +
      "@keyframes dc-steam{0%{opacity:0;transform:translateY(3px)}40%{opacity:.7}100%{opacity:0;transform:translateY(-6px)}}" +
      "@keyframes dc-track-spin{to{transform:rotate(360deg)}}" +
      ".dc-toastie-orbit{position:relative;width:92px;height:92px}" +
      ".dc-toastie-track{position:absolute;inset:0;border-radius:50%;border:3px solid rgba(var(--dc-glow),.14);" +
      "border-top-color:var(--color-accent);animation:dc-track-spin 1.6s linear infinite}" +
      ".dc-toastie-arm{position:absolute;inset:0;animation:dc-orbit 2.6s linear infinite}" +
      ".dc-toastie{position:absolute;top:-16px;left:50%;margin-left:-17px;" +
      "animation:dc-unspin 2.6s linear infinite,dc-wobble 1.1s ease-in-out infinite}" +
      ".dc-toastie-steam{animation:dc-steam 1.4s ease-out infinite}" +
      "@media (prefers-reduced-motion:reduce){.dc-toastie-arm,.dc-toastie,.dc-toastie-steam" +
      "{animation:none}.dc-toastie-track{animation-duration:3s}}" +
      // ---- tiled cards: MagicBento (reactbits), rebuilt in CSS + the controller below ----
      // The component itself cannot be dropped in: UI/ is a pinned export whose DOM belongs to
      // React, and GSAP is not loadable here. Every effect it ships is reproducible without either —
      // border glow and spotlight are custom properties driven from one rAF loop, tilt/magnetism are
      // a transform, particles and the click ripple are keyframes. Glow colour is the theme accent.
      ":root{--dc-glow:31,74,69}" +
      ".dc-bento{position:relative;overflow:hidden;--dc-glow-x:50%;--dc-glow-y:50%;--dc-glow-intensity:0;" +
      "--dc-glow-radius:220px;transition:transform .28s cubic-bezier(.22,1,.36,1),box-shadow .3s ease}" +
      ".dc-bento:hover{box-shadow:0 6px 22px rgba(var(--dc-glow),.16),0 0 26px rgba(var(--dc-glow),.06)}" +
      // the glow is a ring: a radial gradient masked down to the padding box's edge
      ".dc-bento::after{content:'';position:absolute;inset:0;padding:2px;border-radius:inherit;pointer-events:none;" +
      "z-index:2;background:radial-gradient(var(--dc-glow-radius) circle at var(--dc-glow-x) var(--dc-glow-y)," +
      "rgba(var(--dc-glow),calc(var(--dc-glow-intensity) * .85)) 0%," +
      "rgba(var(--dc-glow),calc(var(--dc-glow-intensity) * .35)) 32%,transparent 62%);" +
      "-webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);-webkit-mask-composite:xor;" +
      "mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);mask-composite:exclude}" +
      ".dc-spotlight{position:fixed;width:680px;height:680px;border-radius:50%;pointer-events:none;z-index:5;" +
      "opacity:0;transform:translate(-50%,-50%);transition:opacity .28s ease;background:radial-gradient(circle," +
      "rgba(var(--dc-glow),.10) 0%,rgba(var(--dc-glow),.05) 25%,rgba(var(--dc-glow),.02) 45%,transparent 70%)}" +
      "@keyframes dc-particle{0%{transform:translate(0,0) scale(0);opacity:0}" +
      "18%{transform:translate(0,0) scale(1);opacity:.85}" +
      "100%{transform:translate(var(--dx),var(--dy)) scale(.5);opacity:0}}" +
      ".dc-particle{position:absolute;width:4px;height:4px;border-radius:50%;pointer-events:none;z-index:3;" +
      "background:rgba(var(--dc-glow),.8);box-shadow:0 0 6px rgba(var(--dc-glow),.45);" +
      "animation:dc-particle var(--dur) ease-out infinite}" +
      "@keyframes dc-ripple{from{transform:scale(0);opacity:.55}to{transform:scale(1);opacity:0}}" +
      ".dc-ripple{position:absolute;border-radius:50%;pointer-events:none;z-index:3;background:radial-gradient(circle," +
      "rgba(var(--dc-glow),.32) 0%,rgba(var(--dc-glow),.16) 30%,transparent 70%);" +
      "animation:dc-ripple .75s cubic-bezier(.22,1,.36,1) forwards}" +
      "@media (prefers-reduced-motion:reduce){.dc-bento{transition:none}.dc-spotlight,.dc-particle{display:none}}" +
      // ---- typography: Outfit only, on three weights ----
      // 856 titles and numbers · 577 everything else · 267 captions and muted lines. The export
      // hard-codes 400/500/600/800 inline on its numbers and status words, and inline styles beat a
      // stylesheet, so those four are mapped onto the scale by matching the style attribute itself.
      ":root{--font-heading:'Outfit',system-ui,sans-serif !important;--font-body:'Outfit',system-ui,sans-serif !important;" +
      "--font-heading-weight:856 !important}" +
      "body,button,input,select,textarea,table{font-family:'Outfit',system-ui,sans-serif}" +
      "body{font-weight:577}" +
      // the export sets Archivo + a width axis on headings; Outfit has neither
      "h1,h2,h3,h4,h5,h6{font-family:'Outfit',system-ui,sans-serif !important;font-weight:856 !important;" +
      "font-stretch:normal !important}" +
      ".card-title,.dialog-title{font-weight:856 !important}" +
      ".card-body,.card-meta,.card-kicker,.dialog-body,small,figcaption{font-weight:267 !important}" +
      "::placeholder{font-weight:267}" +
      // muted colour is this design's marker for a caption or a secondary line
      "[style*='color: var(--color-neutral-400)'],[style*='color: var(--color-neutral-500)']," +
      "[style*='color:var(--color-neutral-400)'],[style*='color:var(--color-neutral-500)']{font-weight:267}" +
      ".btn,.tag,.seg-opt,th{font-weight:577 !important}" +
      // last, so a weight the design stated explicitly wins over the rules above
      "[style*='font-weight: 400'],[style*='font-weight:400']{font-weight:577 !important}" +
      "[style*='font-weight: 500'],[style*='font-weight:500'],[style*='font-weight: 600'],[style*='font-weight:600']," +
      "[style*='font-weight: 700'],[style*='font-weight:700'],[style*='font-weight: 800'],[style*='font-weight:800']" +
      "{font-weight:856 !important}" +
      // ---- changing screens: the sidebar's highlight slides, the new screen rises in ----
      // The highlight is a pseudo-element of the nav list rather than each item's own background, so
      // one pill travels between items instead of one blinking off and another on. Its box comes from
      // custom properties the bridge measures off the active item after every render.
      ".dc-sidebar-nav{position:relative}" +
      ".dc-sidebar-nav::before{content:'';position:absolute;left:var(--dc-nav-left,8px);top:var(--dc-nav-top,0);" +
      "width:var(--dc-nav-w,0);height:var(--dc-nav-h,0);border-radius:var(--radius-md);" +
      "background:var(--color-bg);opacity:0;pointer-events:none;z-index:0}" +
      ".dc-sidebar-nav.dc-nav-ready::before{opacity:1;transition:top .42s cubic-bezier(.34,1.28,.42,1)," +
      "height .3s ease,left .3s ease,width .3s ease,opacity .2s ease}" +
      ".dc-sidebar-nav.dc-nav-none::before{opacity:0}" +  // a screen with no nav item (profile, setup)
      ".dc-sidebar-nav > div{position:relative;z-index:1;background:transparent !important;transition:color .28s ease}" +
      ".dc-sidebar-nav > div:hover{color:var(--color-neutral-100)}" +
      "@keyframes dc-row-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}" +
      ".dc-search-row{animation:dc-row-rise .26s cubic-bezier(.22,1,.36,1) both;transition:background .16s ease}" +
      ".dc-search-row:hover{background:var(--color-neutral-200) !important}" +
      ".dc-cal-day{animation:dc-row-rise .2s cubic-bezier(.22,1,.36,1) both;transition:background .14s ease,color .14s ease}" +
      ".dc-cal-day:hover{background:var(--color-neutral-200)}" +
      // the platform filter icons — the only 18px glyphs in the export — read small next to the cards
      "svg[width='18'][height='18']{width:22px;height:22px}" +
      "button:has(> svg[width='18']){transition:transform .18s cubic-bezier(.22,1,.36,1)}" +
      "button:has(> svg[width='18']):hover{transform:translateY(-1px) scale(1.08)}" +
      "@keyframes dc-screen-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}" +
      ".scrollpane.dc-screen-in{animation:dc-screen-in .34s cubic-bezier(.22,1,.36,1) both}" +
      "@media (prefers-reduced-motion:reduce){.dc-sidebar-nav.dc-nav-ready::before{transition:none}" +
      ".scrollpane.dc-screen-in,.dc-search-row{animation:none}" +
      ".seg:not(.seg-dark) .seg-opt::before,.btn::before{transition:none}}" +
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
      // A card painting its own background is a special panel, not a tile: the dark chart card's
      // hover tooltip is drawn outside its own box, and the bento treatment clips it.
      var cards = document.querySelectorAll(".card.elev-sm:not(.dc-no-bento)");
      for (var c = 0; c < cards.length; c++) {
        if (cards[c].style && cards[c].style.backgroundColor) cards[c].classList.add("dc-no-bento");
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
  var origDidUpdate = P.componentDidUpdate;
  // Shown one at a time beside the mascot while a turn is in flight. Kept in Tony's register.
  var THINKING_WORDS = ["Thinking", "Pondering", "Mulling it over", "Chewing on it", "Turning it over",
    "Noodling on it", "Having a think", "Weighing it up", "Working it out", "Putting it together",
    "Casting an eye over it", "Giving it a moment"];
  function nextWord(current) {
    var pick = current;
    while (pick === current) pick = THINKING_WORDS[Math.floor(Math.random() * THINKING_WORDS.length)];
    return pick;
  }
  // Where the header search can take you. `keys` are the words people actually type for a place
  // that is not called that: "transcript" for the call log, "billing" for the profile.
  var SEARCH_INDEX = [
    { label: "Overview", sub: "Today's calls, bookings and quick actions", screen: "overview", keys: "home dashboard kpi covers containment" },
    { label: "Voice AI calls", sub: "Call log, transcripts and summaries", screen: "calls", keys: "transcript recording phone log outcome" },
    { label: "Marketing", sub: "Trending content scored for your niche", screen: "marketing", keys: "trends tiktok instagram youtube shorts scan scripts" },
    { label: "Callbacks", sub: "People waiting on a call back", screen: "callbacks", keys: "queue waiting unanswered questions review" },
    { label: "Analytics", sub: "Impact, Voice Ops and Insights", screen: "analytics", keys: "metrics report handoff latency cost minutes" },
    { label: "Business context", sub: "Menu and knowledge the agents answer from", screen: "setup", keys: "menu research packet hours allergens prices" },
    { label: "Profile settings", sub: "Your details, plan and usage", screen: "profile", keys: "account billing plan minutes owner timezone" },
    { label: "My saves", sub: "Trends you saved to film", screen: "marketing", keys: "saved bookmarks scripts", act: "saves" },
    { label: "My likes", sub: "Trends you liked", screen: "marketing", keys: "liked hearts", act: "likes" },
    { label: "Refresh trends now", sub: "Re-read the latest scan", screen: "marketing", keys: "reload update rescan", act: "refresh" },
    { label: "Ask the Overlord", sub: "Questions across calls, bookings and trends", keys: "chat assistant help ask", act: "overlord" }
  ];
  function searchMatches(query) {
    var q = query.trim().toLowerCase();
    if (!q) return SEARCH_INDEX.slice(0, 6);
    var hits = [];
    SEARCH_INDEX.forEach(function (row) {
      var label = row.label.toLowerCase();
      var rank = label.indexOf(q) === 0 ? 0 : label.indexOf(q) > 0 ? 1
        : (row.sub + " " + row.keys).toLowerCase().indexOf(q) >= 0 ? 2 : -1;
      if (rank >= 0) hits.push({ row: row, rank: rank });
    });
    hits.sort(function (a, b) { return a.rank - b.rank; });
    return hits.slice(0, 6).map(function (h) { return h.row; });
  }
  function shortWhen(iso) {
    var at = new Date(iso);
    if (isNaN(at)) return "—";
    var hours = Math.round((at - Date.now()) / 3600000);
    if (hours <= 0) return "due now";
    if (hours < 24) return "in " + hours + "h";
    return "in " + Math.round(hours / 24) + "d";
  }
  var TONE_DOT = {
    urgent: "background:#a03a3a", warn: "background:var(--color-accent-500)",
    info: "background:var(--color-accent)", ok: "background:#3a7a4a"
  };
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
    goProfile: function () {},
    searchQuery: "", searchPlaceholder: "Search screens…", searchOpen: false, searchResults: [],
    searchEmpty: false, searchEmptyLabel: "Nothing here by that name.",
    setSearchQuery: function () {}, searchKeyDown: function () {}, openSearch: function () {},
    notifications: [], notificationsOpen: false, notifEmpty: false, notifEmptyLabel: "Nothing waiting.",
    notifHeading: "Needs you", notifTitle: "Notifications", notifBadgeCount: "",
    notifBadgeStyle: "display:none", toggleNotifications: function () {},
    marketingStats: [], marketingStatsNote: "",
    calendarOpen: false, calendarMonth: "", calendarWeekdays: [], calendarDays: [], calendarHint: "",
    calendarApplyLabel: "Apply", calendarApplyStyle: "padding:4px 13px;font-size:11px",
    calendarApply: function () {}, calendarPrev: function () {}, calendarNext: function () {},
    trendsLoading: false, trendsLoadingLabel: "Toasting your trends"
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
      if (menu && menu.parentElement && !menu.parentElement.contains(e.target) && self.state.profileMenuOpen) {
        self.toggleProfileMenu();
      }
      var search = document.querySelector(".dc-search");
      if (self.state.searchOpen && search && !search.contains(e.target)) self.setState({ searchOpen: false });
      var bell = document.querySelector(".dc-bell");
      if (self.state.notificationsOpen && bell && !bell.contains(e.target)) self.setState({ notificationsOpen: false });
      var period = document.querySelector(".dc-period");
      if (self.state.calendarOpen && period && !period.contains(e.target)) self.setState({ calendarOpen: false });
    };
    if (typeof document !== "undefined") document.addEventListener("mousedown", this.__clickOut, true);
    this.__onResize = function () { self.__placeNavPill(); };
    if (typeof window !== "undefined") window.addEventListener("resize", this.__onResize);
    this.__placeNavPill();
    this.__initBento();
    this.__initSparks();
  };
  P.componentDidUpdate = function () {
    if (origDidUpdate) { try { origDidUpdate.apply(this, arguments); } catch (e) { console.error(e); } }
    this.__placeNavPill();
  };
  // Measure the active nav item and hand its box to the sliding highlight. Reads the item's own
  // inline style, because the export gives the nav items no class — only the active one is painted.
  P.__placeNavPill = function () {
    var nav = typeof document !== "undefined" && document.querySelector(".dc-sidebar-nav");
    if (!nav) return;
    var kids = nav.children, active = null;
    for (var i = 0; i < kids.length; i++) {
      if (kids[i].style && kids[i].style.background) active = kids[i];
    }
    nav.classList.toggle("dc-nav-none", !active);
    if (!active) return;
    nav.style.setProperty("--dc-nav-top", active.offsetTop + "px");
    nav.style.setProperty("--dc-nav-left", active.offsetLeft + "px");
    nav.style.setProperty("--dc-nav-w", active.offsetWidth + "px");
    nav.style.setProperty("--dc-nav-h", active.offsetHeight + "px");
    if (!nav.classList.contains("dc-nav-ready")) {
      // next frame, so the first placement lands silently instead of sliding in from the top
      (window.requestAnimationFrame || setTimeout)(function () { nav.classList.add("dc-nav-ready"); });
    }
  };
  P.componentWillUnmount = function () {
    clearInterval(this.__timer);
    if (this.__live) { clearInterval(this.__live.thinkTimer); clearTimeout(this.__live.retryTimer); }
    if (this.__onResize && typeof window !== "undefined") window.removeEventListener("resize", this.__onResize);
    if (this.__bento) {
      document.removeEventListener("mousemove", this.__bento.onMove);
      document.removeEventListener("click", this.__bento.onClick, true);
      this.__bento.spot.remove();
      this.__bento = null;
    }
    if (this.__sparks) {
      document.removeEventListener("click", this.__sparks.onClick, true);
      window.removeEventListener("resize", this.__sparks.size);
      this.__sparks.canvas.remove();
      this.__sparks = null;
    }
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
    vals.refreshTrendsStyle = "padding:5px 14px;font-size:11px;white-space:nowrap;flex:none;margin-left:auto"
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
    this.__headerVals(vals);
    this.__calendarVals(vals);
    // My saves / My likes rows open the trend they name
    vals.savedLikedTrends = (vals.savedLikedTrends || []).map(function (t) {
      return Object.assign({}, t, { onOpen: function () { self.openTrend(t.id); } });
    });
    if (!d) {
      vals.platformGroups = [];
      vals.trendCountLabel = refreshing ? "Refreshing trends…"
        : (live && (live.refreshError || live.error) ? "Backend unavailable: " + (live.refreshError || live.error) : "Loading…");
      vals.showMoreLabel = ""; vals.callLogCountLabel = "Loading calls…";
      vals.trendsLoading = !(live && live.error);
      vals.trendsLoadingLabel = "Warming up the grill";
      vals.freshness = { asOf: live && live.error ? "unavailable" : "loading", timezone: "", staleAfterMinutes: 60 };
      vals.marketingChat = this.__chatRows(vals.marketingChat);
      vals.overlordThread = this.__chatRows(vals.overlordThread);
      this.__headerVals(vals);
    this.__calendarVals(vals);
      return vals;
    }
    var s = this.state;
    var a = (s.analyticsPeriod === "custom" && live.customAnalytics)
      || live.analytics[s.analyticsPeriod] || d.analytics;
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
    }
    vals.trendsLoading = !!refreshing;
    vals.trendsLoadingLabel = "Toasting your trends";
    if (refreshing) vals.trendCountLabel = "Refreshing trends…";
    else if (live.refreshError) vals.trendCountLabel = vals.trendCountLabel + " · refresh failed: " + live.refreshError;
    this.__marketingStats(vals, d.trends);
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
  // The scan's own numbers, as one strip above the agent. Everything here is already in the trends
  // payload — this only decides what is worth a slot and how it reads.
  P.__marketingStats = function (vals, t) {
    var cell = "min-width:0";
    var stats = [{ label: "Showing", value: (t.items || []).length + " trends", style: cell }];
    var credits = t.credits && typeof t.credits.remaining === "number" ? t.credits : null;
    if (credits) {
      stats.push({ label: "Scan credits", value: credits.remaining + " left", style: cell });
      if (credits.spent_this_scan) stats.push({ label: "This scan", value: credits.spent_this_scan + " spent", style: cell });
    } else if (t.offline) {
      stats.push({ label: "Scan credits", value: "Offline", style: cell });
    }
    stats.push({ label: "Saved", value: String(t.savedCount || 0), style: cell });
    if (t.nextScanAt) stats.push({ label: "Next scan", value: shortWhen(t.nextScanAt), style: cell });
    vals.marketingStats = stats;
    vals.marketingStatsNote = t.platformsNote || "";
  };

  // ---- Analytics: the Custom period's calendar ----
  // Two clicks pick a range (first sets the start, second the end); Apply asks the API for
  // period=custom with real dates. Monday-first, because the business runs on a Mon-Fri week.
  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December"];
  function isoDay(date) {
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-"
      + String(date.getDate()).padStart(2, "0");
  }
  function shortDay(iso) {
    var parts = String(iso).split("-");
    return Number(parts[2]) + " " + MONTHS[Number(parts[1]) - 1].slice(0, 3);
  }
  P.__calendarVals = function (vals) {
    var self = this, s = this.state;
    var cursor = s.calMonth ? new Date(s.calMonth + "-01T00:00:00") : new Date();
    cursor.setDate(1);
    var year = cursor.getFullYear(), month = cursor.getMonth();
    var first = new Date(year, month, 1);
    var lead = (first.getDay() + 6) % 7;  // Sunday is 0 in JS; this week starts on Monday
    var length = new Date(year, month + 1, 0).getDate();
    var todayIso = isoDay(new Date()), start = s.calStart || null, end = s.calEnd || null;

    var days = [];
    for (var blank = 0; blank < lead; blank++) days.push({ label: "", style: "visibility:hidden", onClick: function () {} });
    for (var day = 1; day <= length; day++) {
      var iso = isoDay(new Date(year, month, day));
      var inRange = start && end && iso >= start && iso <= end;
      var edge = iso === start || iso === end;
      var future = iso > todayIso;
      var style = "font-size:12px;text-align:center;padding:6px 0;border-radius:8px;"
        + (future ? "color:var(--color-neutral-400);cursor:not-allowed" : "cursor:pointer")
        + (edge ? ";background:var(--color-accent);color:#fff;font-weight:856"
          : inRange ? ";background:var(--color-accent-100);color:var(--color-accent)"
            : iso === todayIso ? ";box-shadow:inset 0 0 0 1px var(--color-accent)" : "");
      days.push({ label: String(day), style: style,
        onClick: (function (picked, blocked) {
          return function () { if (!blocked) self.pickCalendarDay(picked); };
        })(iso, future) });
    }
    vals.calendarOpen = !!s.calendarOpen;
    vals.calendarMonth = MONTHS[month] + " " + year;
    vals.calendarWeekdays = WEEKDAYS;
    vals.calendarDays = days;
    vals.calendarHint = start && end ? shortDay(start) + " – " + shortDay(end)
      : start ? shortDay(start) + " – pick the end" : "Pick a start date";
    vals.calendarApplyLabel = start && end ? "Apply" : "Pick dates";
    vals.calendarApplyStyle = "padding:4px 13px;font-size:11px" + (start && end ? "" : ";opacity:.5;pointer-events:none");
    vals.calendarApply = function () { self.applyCalendar(); };
    vals.calendarPrev = function () { self.shiftCalendar(-1); };
    vals.calendarNext = function () { self.shiftCalendar(1); };
  };
  P.shiftCalendar = function (delta) {
    var s = this.state;
    var cursor = s.calMonth ? new Date(s.calMonth + "-01T00:00:00") : new Date();
    cursor.setDate(1);
    cursor.setMonth(cursor.getMonth() + delta);
    this.setState({ calMonth: isoDay(cursor).slice(0, 7) });
  };
  P.pickCalendarDay = function (iso) {
    var s = this.state;
    if (!s.calStart || (s.calStart && s.calEnd)) return this.setState({ calStart: iso, calEnd: null });
    if (iso < s.calStart) return this.setState({ calStart: iso, calEnd: s.calStart });
    this.setState({ calEnd: iso });
  };
  P.applyCalendar = function () {
    var self = this, s = this.state, live = this.__live;
    if (!s.calStart || !s.calEnd) return;
    this.setState({ calendarOpen: false, analyticsPeriod: "custom" });
    api("/api/dashboard/analytics?period=custom&start=" + encodeURIComponent(s.calStart)
      + "&end=" + encodeURIComponent(s.calEnd))
      .then(function (a) { live.customAnalytics = a; self.setState({}); })
      .catch(function (e) { console.error("[dashboard] custom analytics", e); });
  };

  // ---- MagicBento + ClickSpark, ported (see the CSS block at the top of this file) ----
  // Every tiled card on every screen. The two big marketing panels are excluded: tilting a card you
  // are typing into is not a feature.
  var BENTO_SELECTOR = ".card.elev-sm:not(.mkt-chat):not(.dc-mkt-stats):not(.dc-no-bento)";
  var SPOT_RADIUS = 340, PARTICLES = 8, MOBILE_BREAKPOINT = 768;
  function bentoOff() {
    return (typeof window !== "undefined" && window.innerWidth <= MOBILE_BREAKPOINT) || reducedMotion();
  }
  P.__initBento = function () {
    if (this.__bento || typeof document === "undefined") return;
    var self = this, frame = null, latest = null;
    var spot = document.createElement("div");
    spot.className = "dc-spotlight";
    document.body.appendChild(spot);
    var onMove = function (e) {
      latest = { x: e.clientX, y: e.clientY };
      if (frame) return;
      frame = requestAnimationFrame(function () { frame = null; self.__bentoFrame(latest, spot); });
    };
    var onClick = function (e) { self.__bentoRipple(e); };
    document.addEventListener("mousemove", onMove, { passive: true });
    document.addEventListener("click", onClick, true);
    this.__bento = { spot: spot, onMove: onMove, onClick: onClick, hovered: null };
  };
  // One pass per frame: proximity glow for every card, tilt and magnetism for the one under the
  // cursor, and the spotlight's own position and strength. Ported from MagicBento's GSAP tweens --
  // the transitions live in CSS instead, so this only writes values.
  P.__bentoFrame = function (at, spot) {
    var bento = this.__bento;
    if (!at || !bento) return;
    if (bentoOff()) { spot.style.opacity = 0; return; }
    var cards = document.querySelectorAll(BENTO_SELECTOR);
    var proximity = SPOT_RADIUS * 0.5, fade = SPOT_RADIUS * 0.75, nearest = Infinity, hovered = null;
    var rects = [];  // measure everything before writing anything, or each write forces a layout
    for (var r = 0; r < cards.length; r++) rects.push(cards[r].getBoundingClientRect());
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i], rect = rects[i];
      if (!card.classList.contains("dc-bento")) card.classList.add("dc-bento");
      if (rect.bottom < -200 || rect.top > window.innerHeight + 200) {
        card.style.setProperty("--dc-glow-intensity", "0");
        continue;
      }
      var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      var gap = Math.max(0, Math.hypot(at.x - cx, at.y - cy) - Math.max(rect.width, rect.height) / 2);
      nearest = Math.min(nearest, gap);
      var glow = gap <= proximity ? 1 : gap <= fade ? (fade - gap) / (fade - proximity) : 0;
      card.style.setProperty("--dc-glow-x", ((at.x - rect.left) / rect.width * 100) + "%");
      card.style.setProperty("--dc-glow-y", ((at.y - rect.top) / rect.height * 100) + "%");
      card.style.setProperty("--dc-glow-intensity", String(glow));
      card.style.setProperty("--dc-glow-radius", SPOT_RADIUS + "px");
      var inside = at.x >= rect.left && at.x <= rect.right && at.y >= rect.top && at.y <= rect.bottom;
      if (inside) {
        hovered = card;
        var dx = at.x - cx, dy = at.y - cy;
        card.style.transform = "perspective(900px) rotateX(" + (-(dy / (rect.height / 2)) * 5).toFixed(2)
          + "deg) rotateY(" + ((dx / (rect.width / 2)) * 5).toFixed(2) + "deg) translate3d("
          + (dx * 0.03).toFixed(1) + "px," + (dy * 0.03).toFixed(1) + "px,0)";
      } else if (card.style.transform) {
        card.style.transform = "";
      }
    }
    if (hovered !== bento.hovered) {
      this.__bentoParticles(bento.hovered, false, null);
      this.__bentoParticles(hovered, true, hovered ? rects[[].indexOf.call(cards, hovered)] : null);
      bento.hovered = hovered;
    }
    var strength = nearest <= proximity ? 1 : nearest <= fade ? (fade - nearest) / (fade - proximity) : 0;
    spot.style.left = at.x + "px";
    spot.style.top = at.y + "px";
    spot.style.opacity = String(strength);
  };
  P.__bentoParticles = function (card, on, rect) {
    if (!card) return;
    var old = card.querySelectorAll(".dc-particle");
    for (var i = 0; i < old.length; i++) old[i].remove();
    if (!on || bentoOff()) return;
    rect = rect || card.getBoundingClientRect();
    for (var n = 0; n < PARTICLES; n++) {
      var dot = document.createElement("div");
      dot.className = "dc-particle";
      dot.style.left = (Math.random() * rect.width) + "px";
      dot.style.top = (Math.random() * rect.height) + "px";
      dot.style.setProperty("--dx", ((Math.random() - 0.5) * 70).toFixed(0) + "px");
      dot.style.setProperty("--dy", ((Math.random() - 0.5) * 70).toFixed(0) + "px");
      dot.style.setProperty("--dur", (2.2 + Math.random() * 1.8).toFixed(2) + "s");
      dot.style.animationDelay = (n * 0.09).toFixed(2) + "s";
      card.appendChild(dot);
    }
  };
  P.__bentoRipple = function (e) {
    if (bentoOff() || !e.target || !e.target.closest) return;
    var card = e.target.closest(BENTO_SELECTOR);
    if (!card) return;
    var rect = card.getBoundingClientRect();
    var x = e.clientX - rect.left, y = e.clientY - rect.top;
    var reach = Math.max(Math.hypot(x, y), Math.hypot(x - rect.width, y),
      Math.hypot(x, y - rect.height), Math.hypot(x - rect.width, y - rect.height));
    var ripple = document.createElement("div");
    ripple.className = "dc-ripple";
    ripple.style.width = ripple.style.height = (reach * 2) + "px";
    ripple.style.left = (x - reach) + "px";
    ripple.style.top = (y - reach) + "px";
    ripple.addEventListener("animationend", function () { ripple.remove(); });
    card.appendChild(ripple);
  };

  // ClickSpark: one fixed canvas over the whole app, drawn only while sparks are alive rather than
  // on a permanent rAF loop.
  var SPARK = { color: "#1f4a45", size: 11, radius: 16, count: 8, duration: 420 };
  P.__initSparks = function () {
    if (this.__sparks || typeof document === "undefined") return;
    var canvas = document.createElement("canvas");
    canvas.style.cssText = "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:70";
    document.body.appendChild(canvas);
    var live = [], running = false, ctx = canvas.getContext("2d");
    var size = function () {
      var ratio = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * ratio;
      canvas.height = window.innerHeight * ratio;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    size();
    var draw = function (now) {
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
      live = live.filter(function (s) {
        var t = (now - s.at) / SPARK.duration;
        if (t >= 1) return false;
        var eased = t * (2 - t);  // ease-out, the component's default
        var far = eased * SPARK.radius, len = SPARK.size * (1 - eased);
        ctx.strokeStyle = SPARK.color;
        ctx.globalAlpha = 1 - eased;
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(s.x + far * Math.cos(s.angle), s.y + far * Math.sin(s.angle));
        ctx.lineTo(s.x + (far + len) * Math.cos(s.angle), s.y + (far + len) * Math.sin(s.angle));
        ctx.stroke();
        return true;
      });
      if (live.length) requestAnimationFrame(draw);
      else { running = false; ctx.clearRect(0, 0, window.innerWidth, window.innerHeight); }
    };
    var onClick = function (e) {
      if (reducedMotion()) return;
      var now = performance.now();
      for (var i = 0; i < SPARK.count; i++) {
        live.push({ x: e.clientX, y: e.clientY, angle: (2 * Math.PI * i) / SPARK.count, at: now });
      }
      if (!running) { running = true; requestAnimationFrame(draw); }
    };
    document.addEventListener("click", onClick, true);
    window.addEventListener("resize", size);
    this.__sparks = { canvas: canvas, onClick: onClick, size: size };
  };

  // ---- header: search over the app's own screens, and the bell ----
  P.__headerVals = function (vals) {
    var self = this, s = this.state, live = this.__live, d = live && live.data;
    var query = s.searchQuery || "";
    var rows = searchMatches(query);
    vals.searchQuery = query;
    vals.searchPlaceholder = "Search screens…";
    vals.searchOpen = !!s.searchOpen;
    vals.searchEmpty = rows.length === 0;
    vals.searchEmptyLabel = "Nothing in the app by that name.";
    vals.searchResults = rows.map(function (row, i) {
      return { label: row.label, sub: row.sub,
        // the stagger is what makes the list feel like it unrolls rather than blinks in
        style: "padding:8px 12px;border-radius:var(--radius-md);cursor:pointer;animation-delay:" + (i * 32) + "ms"
          + (i === (s.searchIndex || 0) ? ";background:var(--color-neutral-200)" : ""),
        onClick: function () { self.goSearchResult(row); } };
    });
    vals.setSearchQuery = function (e) { self.setState({ searchQuery: e.target.value, searchOpen: true, searchIndex: 0 }); };
    vals.openSearch = function () { self.setState({ searchOpen: true }); };
    vals.searchKeyDown = function (e) { self.searchKeyDown(e, rows); };

    var notes = (d && d.notifications) || [];
    var waiting = notes.filter(function (n) { return n.tone === "urgent" || n.tone === "warn"; }).length;
    vals.notifications = notes.map(function (n) {
      return { title: n.title, sub: n.sub,
        style: "padding:8px 12px;border-radius:var(--radius-md);cursor:pointer",
        dotStyle: "display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;vertical-align:middle;"
          + (TONE_DOT[n.tone] || TONE_DOT.info),
        onClick: function () { self.setState({ notificationsOpen: false }); if (n.screen) self.setScreen(n.screen); } };
    });
    vals.notificationsOpen = !!s.notificationsOpen;
    vals.notifEmpty = notes.length === 0;
    vals.notifEmptyLabel = d ? "Nothing waiting." : "Still loading your data…";
    vals.notifHeading = waiting ? "Needs you" : "Nothing urgent";
    vals.notifTitle = waiting ? waiting + " thing" + (waiting === 1 ? "" : "s") + " need you" : "Notifications";
    vals.notifBadgeCount = waiting || "";
    vals.notifBadgeStyle = waiting
      ? "position:absolute;top:-3px;right:-3px;min-width:16px;height:16px;padding:0 4px;border-radius:999px;"
        + "background:var(--color-accent-500);color:var(--color-accent-900);font-size:10px;line-height:16px;"
        + "text-align:center;font-weight:600;pointer-events:none"
      : "display:none";
    vals.toggleNotifications = function () {
      self.setState(function (st) { return { notificationsOpen: !st.notificationsOpen, searchOpen: false }; });
    };
  };
  P.goSearchResult = function (row) {
    this.setState({ searchOpen: false, searchQuery: "", searchIndex: 0 });
    if (row.screen) this.setScreen(row.screen);
    if (row.act === "saves" || row.act === "likes") this.openSavedLikedModal(row.act);
    else if (row.act === "refresh") this.refreshTrends();
    else if (row.act === "overlord" && !this.state.overlordOpen) this.toggleOverlord();
  };
  P.searchKeyDown = function (e, rows) {
    var s = this.state, index = s.searchIndex || 0;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      var next = index + (e.key === "ArrowDown" ? 1 : -1);
      this.setState({ searchOpen: true, searchIndex: (next + rows.length) % Math.max(1, rows.length) });
    } else if (e.key === "Enter") {
      if (rows[index]) this.goSearchResult(rows[index]);
    } else if (e.key === "Escape") {
      this.setState({ searchOpen: false, searchQuery: "" });
    }
  };

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
  var origSetScreen = P.setScreen;
  P.setScreen = function (screen) {
    if (this.state.screen !== screen) {
      var pane = typeof document !== "undefined" && document.querySelector(".scrollpane:not(.mkt-scroll)");
      if (pane) {
        pane.classList.remove("dc-screen-in");
        void pane.offsetWidth;  // reflow, so the same animation replays on the next screen too
        pane.classList.add("dc-screen-in");
        pane.scrollTop = 0;
      }
    }
    return origSetScreen.apply(this, arguments);
  };
  P.setChartRange = function (range) { this.setState({ chartRange: range }); };
  P.setAnalyticsPeriod = function (p) {
    var self = this, live = this.__live;
    if (p === "custom") {  // the Custom option is the calendar's own button
      return this.setState(function (s) {
        return { analyticsPeriod: "custom", calendarOpen: true,
          calMonth: s.calMonth || (s.calStart || isoDay(new Date())).slice(0, 7) };
      });
    }
    this.setState({ analyticsPeriod: p, calendarOpen: false });
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
