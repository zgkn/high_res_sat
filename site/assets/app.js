/* Sentinel-2 viewer: run gallery, zoom/pan viewer, compare slider, map overlay.
   Data comes from data.js (window.S2_RUNS), written by scripts/build_site.py. */
(function () {
  "use strict";

  var RUNS = (window.S2_RUNS || []).slice();
  var PRODUCT = { truecolour: "True colour", swir: "SWIR (B12/B8A/B4)" };
  var SHORT = { truecolour: "True colour", swir: "SWIR" };
  var SWIR_KEY = "SWIR key: burn scars dark red/brown, active fire bright orange, vegetation green, " +
    "cloud white, thin smoke mostly transparent.";

  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function pct(v) { return v == null ? "n/a" : v.toFixed(1) + "%"; }

  function areaKey(r) { return r.bbox.map(function (v) { return +v.toFixed(4); }).join(","); }
  function lonTxt(v) { return Math.abs(v).toFixed(2) + "°" + (v < 0 ? "W" : "E"); }
  function latTxt(v) { return Math.abs(v).toFixed(2) + "°" + (v < 0 ? "S" : "N"); }
  function bboxTxt(b) { return lonTxt(b[0]) + "–" + lonTxt(b[2]) + ", " + latTxt(b[1]) + "–" + latTxt(b[3]); }
  function areaName(r) { return r.label || bboxTxt(r.bbox); }
  function byId(id) { return RUNS.filter(function (r) { return r.id === id; })[0]; }

  // ---------------------------------------------------------------- state
  var S = { run: null, mode: "view", prod: "truecolour", left: null, right: null, area: "all", sat: "all", open: {} };

  function readHash() {
    var o = {};
    location.hash.replace(/^#/, "").split("&").forEach(function (p) {
      var kv = p.split("=");
      if (kv[0]) o[kv[0]] = decodeURIComponent(kv[1] || "");
    });
    return o;
  }
  function writeHash() {
    var h = "run=" + encodeURIComponent(S.run) + "&mode=" + S.mode + "&p=" + S.prod;
    try { history.replaceState(null, "", "#" + h); } catch (e) { /* file:// or sandbox */ }
  }

  // ---------------------------------------------------------------- run list
  // Runs are grouped by area of interest: the label if one was given, else the bounding box. (Compare and
  // the map still match on the exact box, so overlays line up.)
  function groupKey(r) { return r.label ? "L:" + r.label.trim().toLowerCase() : "B:" + areaKey(r); }
  function groupName(r) { return r.label ? r.label.trim() : bboxTxt(r.bbox); }
  function satKey(r) { return r.source || "sentinel-2"; }
  function satLabel(r) { return r.satellite || "Sentinel-2"; }
  function unique(a) { return a.filter(function (v, i) { return a.indexOf(v) === i; }); }

  function renderList() {
    var groups = {}, order = [];
    RUNS.forEach(function (r) {
      var k = groupKey(r);
      if (!groups[k]) { groups[k] = { name: groupName(r), runs: [] }; order.push(k); }
      groups[k].runs.push(r);
    });
    var sel = $("#areaFilter");
    sel.innerHTML = '<option value="all">All areas (' + order.length + ")</option>" + order.map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(groups[k].name) + " (" + groups[k].runs.length + ")</option>";
    }).join("");
    if (!groups[S.area]) S.area = "all";
    sel.value = S.area;

    var sats = unique(RUNS.map(satKey)), satNames = { "sentinel-2": "Sentinel-2", "landsat": "Landsat 8/9" };
    var ss = $("#satFilter");
    ss.hidden = sats.length < 2;
    ss.innerHTML = '<option value="all">All satellites</option>' + sats.map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(satNames[k] || k) + "</option>";
    }).join("");
    if (S.sat !== "all" && sats.indexOf(S.sat) < 0) S.sat = "all";
    ss.value = S.sat;

    var html = "";
    order.forEach(function (k) {
      if (S.area !== "all" && k !== S.area) return;
      var g = groups[k];
      var runs = g.runs.filter(function (r) { return S.sat === "all" || satKey(r) === S.sat; });
      if (!runs.length) return;
      var hasSel = runs.some(function (r) { return r.id === S.run; });
      var open = S.open[k] !== undefined ? S.open[k] : (hasSel || order.length === 1 || S.area !== "all");
      html += '<details class="group" data-key="' + esc(k) + '"' + (open ? " open" : "") + "><summary>" +
        '<span class="gname">' + esc(g.name) + "</span>" +
        '<span class="gmeta">' + runs.length + " run" + (runs.length === 1 ? "" : "s") + " &middot; " +
        esc(unique(runs.map(satLabel)).join(", ")) + "</span></summary>";
      runs.forEach(function (r) {
        html += '<button type="button" class="card" data-id="' + esc(r.id) + '"' +
          (r.id === S.run ? ' aria-current="true"' : "") + ">" +
          '<img loading="lazy" alt="" src="' + esc(r.thumb) + '">' +
          '<div><div class="d">' + esc(r.date) + "</div>" +
          '<div class="m">' + esc(satLabel(r)) + " &middot; cloud " + pct(r.cloud_scl) + " &middot; coverage " + pct(r.coverage) + "</div>" +
          "<div>" + r.composites.map(function (c) { return '<span class="chip">' + esc(SHORT[c] || c) + "</span>"; }).join("") +
          (r.fallback ? '<span class="chip warn">Fallback</span>' : "") + "</div></div></button>";
      });
      html += "</details>";
    });
    $("#runList").innerHTML = html || '<div class="empty">No runs match these filters.</div>';
  }

  // ---------------------------------------------------------------- toolbar
  // An area of interest here = one label (or box) with one exact box, since that is what timelines, compare
  // and the map can line up. A label used with several boxes is listed once per box.
  function aoiKey(r) { return groupKey(r) + "|" + areaKey(r); }
  function aoiList() {
    var map = {}, order = [], perGroup = {};
    RUNS.forEach(function (r) {   // RUNS is newest first, so runs[0] is the latest image of the area
      var k = aoiKey(r);
      if (!map[k]) { map[k] = { key: k, gk: groupKey(r), name: groupName(r), box: bboxTxt(r.bbox), runs: [] }; order.push(k); }
      map[k].runs.push(r);
    });
    order.forEach(function (k) { perGroup[map[k].gk] = (perGroup[map[k].gk] || 0) + 1; });
    return order.map(function (k) {
      var a = map[k];
      if (perGroup[a.gk] > 1) a.name += " (" + a.box + ")";
      return a;
    });
  }
  function pickArea(key) {   // show the newest image of that area, keeping the current mode
    var a = aoiList().filter(function (x) { return x.key === key; })[0];
    if (!a) return;
    var r = a.runs.filter(function (x) { return x.images[S.prod]; })[0] || a.runs[0];
    selectRun(r.id);
  }
  function imageEntries(r) {
    // every (run, product) of the same area, newest first
    var k = areaKey(r), out = [];
    RUNS.forEach(function (x) {
      if (areaKey(x) !== k) return;
      x.composites.forEach(function (c) {
        if (x.images[c]) out.push({ key: x.id + "|" + c, run: x, prod: c });
      });
    });
    return out;
  }
  function entLabel(e) {
    return e.run.date + " · " + (SHORT[e.prod] || e.prod) + (e.run.source === "landsat" ? " · " + e.run.satellite : "");
  }
  function entry(key) {
    var p = (key || "").split("|"), r = byId(p[0]);
    return r && r.images[p[1]] ? { run: r, prod: p[1] } : null;
  }

  function renderToolbar() {
    var r = byId(S.run), aois = aoiList(), cur = aoiKey(r);
    $("#aoiSel").innerHTML = aois.map(function (a) {
      return '<option value="' + esc(a.key) + '">' + esc(a.name) + " (" + a.runs.length + ")</option>";
    }).join("");
    $("#aoiSel").value = cur;
    Array.prototype.forEach.call(document.querySelectorAll("#modeTabs button"), function (b) {
      b.setAttribute("aria-selected", b.dataset.mode === S.mode ? "true" : "false");
    });
    $("#prodBar").hidden = S.mode === "compare";
    $("#prodBar").innerHTML = r.composites.map(function (c) {
      return '<button type="button" data-prod="' + c + '" aria-pressed="' + (c === S.prod) + '">' +
        esc(SHORT[c] || c) + "</button>";
    }).join("");
    $("#cmpBar").hidden = S.mode !== "compare";
    $("#opBar").hidden = S.mode !== "map";
    if (S.mode === "compare") {
      var opts = imageEntries(r).map(function (e) {
        return '<option value="' + esc(e.key) + '">' + esc(entLabel(e)) + "</option>";
      }).join("");
      $("#selL").innerHTML = opts; $("#selR").innerHTML = opts;
      $("#selL").value = S.left; $("#selR").value = S.right;
    }
  }

  // ---------------------------------------------------------------- info panel
  function renderInfo() {
    var r = byId(S.run), links = [];
    r.composites.forEach(function (c) {
      if (r.png[c]) links.push('<a href="' + esc(r.png[c]) + '" target="_blank" rel="noopener">' +
        esc(SHORT[c]) + " PNG (annotated)</a>");
      if (r.images[c]) links.push('<a href="' + esc(r.images[c]) + '" target="_blank" rel="noopener">' +
        esc(SHORT[c]) + " WebP</a>");
    });
    if (r.run_url) links.push('<a href="' + esc(r.run_url) + '" target="_blank" rel="noopener">Workflow run (GeoTIFF in artifacts, 30 days)</a>');
    var usesSwir = S.mode === "compare"
      ? [S.left, S.right].some(function (k) { return /\|swir$/.test(k || ""); })
      : S.prod === "swir";
    $("#info").innerHTML =
      "<h2>" + esc(areaName(r)) + " &middot; " + esc(r.date) + "</h2>" +
      (r.fallback ? '<div class="warnbox"><b>Fallback.</b> ' + esc(r.reason) + "</div>" : "") +
      '<div class="facts">' +
      "<div><b>Area</b>" + esc(bboxTxt(r.bbox)) + "</div>" +
      "<div><b>Satellite</b>" + esc(r.satellite || "Sentinel-2") + "</div>" +
      "<div><b>" + (r.source === "landsat" ? "Path/rows" : "Tiles") + "</b>" + esc(r.tiles.join(", ")) + "</div>" +
      "<div><b>Coverage of area</b>" + pct(r.coverage) + "</div>" +
      "<div><b>Cloud, scene metadata</b>" + pct(r.cloud_meta) + "</div>" +
      "<div><b>Cloud, SCL in area</b>" + pct(r.cloud_scl) + "</div>" +
      "<div><b>Cloud shadow, SCL</b>" + pct(r.shadow_scl) + "</div>" +
      "<div><b>Image size</b>" + r.size[0] + " &times; " + r.size[1] + " px</div>" +
      "<div><b>Search</b>" + esc(r.inputs.start_date) + " to " + esc(r.inputs.end_date) + ", " +
      r.scenes_found + " scene" + (r.scenes_found === 1 ? "" : "s") + " on " + r.dates_considered +
      " date" + (r.dates_considered === 1 ? "" : "s") + "</div></div>" +
      '<div class="links">' + links.join("") + "</div>" +
      (usesSwir ? '<div class="key">' + esc(SWIR_KEY) + "</div>" : "") +
      '<div class="note">SCL cloud can mislabel haze, smoke or bright bare ground, so check the picture.</div>';
  }

  // ---------------------------------------------------------------- pan/zoom stage
  var stage = $("#stage"), pzL = $("#pzL"), pzR = $("#pzR"), imgL = $("#imgL"), imgR = $("#imgR");
  var layerR = $("#layerR"), divider = $("#divider");
  var T = { k: 1, x: 0, y: 0 }, fit = { w: 0, h: 0 }, dim = { w: 1, h: 1 }, dpos = 0.5, geo = null;
  var MAXK = 12;

  function layout() {
    var cw = stage.clientWidth, ch = stage.clientHeight;
    if (!cw || !ch) return;
    var f = Math.min(cw / dim.w, ch / dim.h);
    fit.w = dim.w * f; fit.h = dim.h * f;
    [pzL, pzR].forEach(function (p) { p.style.width = fit.w + "px"; p.style.height = fit.h + "px"; });
    clampT(); apply();
  }
  function clampT() {
    var cw = stage.clientWidth, ch = stage.clientHeight, sw = fit.w * T.k, sh = fit.h * T.k;
    T.x = sw <= cw ? (cw - sw) / 2 : clamp(T.x, cw - sw, 0);
    T.y = sh <= ch ? (ch - sh) / 2 : clamp(T.y, ch - sh, 0);
  }
  function apply() {
    var t = "translate(" + T.x + "px," + T.y + "px) scale(" + T.k + ")";
    pzL.style.transform = t; pzR.style.transform = t;
    stage.classList.toggle("pix", T.k * fit.w / dim.w > 3);
    var px = dpos * stage.clientWidth;
    divider.style.left = px + "px";
    divider.setAttribute("aria-valuenow", Math.round(dpos * 100));
    layerR.style.clipPath = "inset(0 0 0 " + px + "px)";
  }
  function zoomAt(cx, cy, factor) {
    var k2 = clamp(T.k * factor, 1, MAXK), r = k2 / T.k;
    T.x = cx - (cx - T.x) * r; T.y = cy - (cy - T.y) * r; T.k = k2;
    clampT(); apply();
  }
  function resetView() { T.k = 1; T.x = 0; T.y = 0; clampT(); apply(); }

  function loadImg(img, url) {
    return new Promise(function (resolve) {
      if (!url) { img.removeAttribute("src"); resolve(); return; }
      img.onload = img.onerror = function () { resolve(); };
      img.src = url;
    });
  }
  function showStage(leftE, rightE) {
    var compare = !!rightE;
    layerR.hidden = !compare; divider.hidden = !compare;
    $("#tagL").hidden = !compare; $("#tagR").hidden = !compare;
    if (compare) {
      $("#tagL").textContent = entLabel(leftE);
      $("#tagR").textContent = entLabel(rightE);
    }
    geo = { bbox: leftE.run.bbox };
    dim.w = leftE.run.size[0]; dim.h = leftE.run.size[1];
    $("#loading").classList.remove("done");
    Promise.all([loadImg(imgL, leftE.run.images[leftE.prod]),
                 loadImg(imgR, compare ? rightE.run.images[rightE.prod] : null)]).then(function () {
      $("#loading").classList.add("done");
    });
    layout();
  }

  function toLonLat(clientX, clientY) {
    if (!geo || !fit.w) return null;
    var rect = stage.getBoundingClientRect();
    var fx = (clientX - rect.left - T.x) / T.k / fit.w, fy = (clientY - rect.top - T.y) / T.k / fit.h;
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
    var b = geo.bbox;
    return [b[0] + fx * (b[2] - b[0]), b[3] - fy * (b[3] - b[1])];
  }

  var ptrs = {}, nptr = 0, drag = null, pinch = null;
  function dist() {
    var ids = Object.keys(ptrs), a = ptrs[ids[0]], b = ptrs[ids[1]];
    return Math.hypot(a.x - b.x, a.y - b.y) || 1;
  }
  stage.addEventListener("pointerdown", function (e) {
    if (e.target.closest(".zoom")) return;
    try { stage.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    if (!ptrs[e.pointerId]) nptr++;
    ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (nptr === 2) { pinch = { d0: dist() }; drag = null; return; }
    var compare = S.mode === "compare";
    var onHandle = !!e.target.closest("#divider");
    drag = { kind: compare && (onHandle || T.k === 1) ? "divider" : "pan",
             sx: e.clientX, sy: e.clientY, x0: T.x, y0: T.y };
    if (drag.kind === "divider") moveDivider(e.clientX);
  });
  function moveDivider(clientX) {
    var rect = stage.getBoundingClientRect();
    dpos = clamp((clientX - rect.left) / rect.width, 0.02, 0.98);
    apply();
  }
  stage.addEventListener("pointermove", function (e) {
    if (ptrs[e.pointerId]) ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (pinch && nptr === 2) {
      var ids = Object.keys(ptrs), a = ptrs[ids[0]], b = ptrs[ids[1]], rect = stage.getBoundingClientRect();
      var d = dist(), factor = d / pinch.d0;
      pinch.d0 = d;
      zoomAt((a.x + b.x) / 2 - rect.left, (a.y + b.y) / 2 - rect.top, factor);
      return;
    }
    if (drag) {
      if (drag.kind === "divider") moveDivider(e.clientX);
      else { T.x = drag.x0 + e.clientX - drag.sx; T.y = drag.y0 + e.clientY - drag.sy; clampT(); apply(); }
    }
    var ll = toLonLat(e.clientX, e.clientY);
    $("#readout").textContent = ll ? lonTxt(ll[0]) + "  " + latTxt(ll[1]) : "";
  });
  function endPtr(e) {
    if (ptrs[e.pointerId]) { delete ptrs[e.pointerId]; nptr--; }
    if (nptr < 2) pinch = null;
    if (nptr === 0) drag = null;
  }
  stage.addEventListener("pointerup", endPtr);
  stage.addEventListener("pointercancel", endPtr);
  stage.addEventListener("pointerleave", function () { $("#readout").textContent = ""; });
  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    var rect = stage.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
  }, { passive: false });
  stage.addEventListener("dblclick", function (e) {
    if (e.target.closest(".zoom") || e.target.closest("#divider")) return;
    var rect = stage.getBoundingClientRect();
    if (T.k >= MAXK - 0.01) resetView(); else zoomAt(e.clientX - rect.left, e.clientY - rect.top, 2);
  });
  divider.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") { dpos = clamp(dpos - 0.02, 0.02, 0.98); apply(); e.preventDefault(); }
    if (e.key === "ArrowRight") { dpos = clamp(dpos + 0.02, 0.02, 0.98); apply(); e.preventDefault(); }
  });
  function zoomCentre(f) { zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, f); }
  $("#zin").addEventListener("click", function () { zoomCentre(1.6); });
  $("#zout").addEventListener("click", function () { zoomCentre(1 / 1.6); });
  $("#zreset").addEventListener("click", resetView);
  window.addEventListener("resize", function () { if (S.mode !== "map") layout(); });

  // ---------------------------------------------------------------- map
  var map = null, overlay = null, overlayKey = null, aoiGroup = null;
  function ensureMap() {
    if (map) return;
    map = L.map("map", { worldCopyJump: false });
    var osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" });
    var esri = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" });
    osm.addTo(map);
    aoiGroup = L.layerGroup().addTo(map);   // outline + centre of the area of interest, switchable
    L.control.layers({ "OpenStreetMap": osm, "Esri imagery": esri, "No basemap": L.layerGroup() },
      { "Area of interest outline": aoiGroup }, { collapsed: true }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);
  }
  // Dashed outline of the exact box (dark halo under a bright line so it reads on imagery and on the
  // basemap), a dot at the centre and a label with the area name and the centre coordinates.
  function drawAoi(r) {
    var b = r.bbox, bounds = [[b[1], b[0]], [b[3], b[2]]];
    var c = [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2];
    aoiGroup.clearLayers();
    L.rectangle(bounds, { color: "#000", weight: 4, opacity: 0.45, fill: false, interactive: false }).addTo(aoiGroup);
    L.rectangle(bounds, { color: "#ffd400", weight: 2, fill: false, dashArray: "7 5", interactive: false }).addTo(aoiGroup);
    L.circleMarker(c, { radius: 5, color: "#000", weight: 1, fillColor: "#ffd400", fillOpacity: 1, interactive: false })
      .bindTooltip(areaName(r) + " \u00b7 " + latTxt(c[0]) + ", " + lonTxt(c[1]),
        { permanent: true, direction: "right", offset: [8, 0], className: "aoi-tip" })
      .addTo(aoiGroup);
  }
  function showMap(r) {
    ensureMap();
    drawAoi(r);
    var key = r.id + "|" + S.prod, b = r.bbox;
    // The image is linear in degrees; Leaflet stretches it linearly in Web Mercator. Over boxes of a
    // few degrees near the equator the difference is a few pixels at most.
    var bounds = [[b[1], b[0]], [b[3], b[2]]];
    if (overlayKey !== key) {
      if (overlay) map.removeLayer(overlay);
      overlay = L.imageOverlay(r.images[S.prod], bounds, { opacity: $("#opacity").value / 100, interactive: false });
      overlay.addTo(map);
      var newRun = !overlayKey || overlayKey.split("|")[0] !== r.id;
      overlayKey = key;
      map.invalidateSize();
      if (newRun) map.fitBounds(bounds, { padding: [20, 20] });
    } else map.invalidateSize();
  }
  $("#opacity").addEventListener("input", function () { if (overlay) overlay.setOpacity(this.value / 100); });

  // ---------------------------------------------------------------- timeline
  // Frames = every published run with the selected run's exact box that has the chosen image type, oldest
  // first (all satellites unless the satellite filter is set). Scrub, step or play to watch the area change;
  // zoom and position are kept from frame to frame.
  var tl = { frames: [], idx: 0, cloud: 100, speed: 700, timer: null, cache: {}, down: false };

  function tlClass(r) { var c = r.cloud_scl; return c == null ? "na" : c <= 20 ? "good" : c <= 40 ? "ok" : "bad"; }
  function tlFrames() {
    var k = areaKey(byId(S.run));
    return RUNS.filter(function (x) {
      return areaKey(x) === k && x.images[S.prod] && (S.sat === "all" || satKey(x) === S.sat) &&
        (x.cloud_scl == null || x.cloud_scl <= tl.cloud);
    }).sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? -1 : 1;
      return (a.generated || "") < (b.generated || "") ? -1 : 1;
    });
  }
  function tlPos(i) {  // 0..1 along the true date axis (a lone frame sits in the middle)
    var f = tl.frames, n = f.length;
    if (n < 2) return 0.5;
    var t0 = Date.parse(f[0].date), t1 = Date.parse(f[n - 1].date);
    return t1 === t0 ? 0.5 : (Date.parse(f[i].date) - t0) / (t1 - t0);
  }
  function tlX(i) { return 2 + 96 * tlPos(i); }  // % of the track width, padded so dots stay inside

  function tlDraw() {
    var f = tl.frames, dots = "";
    f.forEach(function (r, i) {
      var same = 0;
      for (var j = 0; j < i; j++) if (f[j].date === r.date) same++;   // same-day frames stack downwards
      dots += '<span class="tl-dot ' + tlClass(r) + (r.fallback ? " fb" : "") + '" data-i="' + i +
        '" style="left:' + tlX(i) + "%;top:" + (36 + same * 14) + 'px"></span>';
    });
    $("#tlDots").innerHTML = dots;
    $("#tlStart").textContent = f.length ? f[0].date : "";
    $("#tlEnd").textContent = f.length > 1 ? f[f.length - 1].date : "";
    var cur = byId(S.run), others = RUNS.filter(function (x) {
      return groupKey(x) === groupKey(cur) && areaKey(x) !== areaKey(cur);
    }).length;
    $("#tlNote").innerHTML = !f.length
      ? "No images match these filters. Try a different cloud limit or satellite."
      : f.length < 2
        ? "Only one image for this area and box so far. Run the workflow again with the same box for more dates."
        : f.length + " images, dot colour = cloud inside the box: green &le; 20%, amber &le; 40%, red above; a ring marks a fallback pick." +
          (others ? " " + others + " other run" + (others === 1 ? "" : "s") + " of this area use a different box and are not on this slider." : "");
    $("#tlPlay").disabled = $("#tlPrev").disabled = $("#tlNext").disabled = f.length < 2;
  }

  function tlUpdate() {
    var f = tl.frames, r = f[tl.idx];
    if (!r) { $("#tlLabel").textContent = ""; return; }
    $("#tlHandle").style.left = tlX(tl.idx) + "%";
    Array.prototype.forEach.call(document.querySelectorAll("#tlDots .tl-dot"), function (d) {
      d.classList.toggle("on", +d.dataset.i === tl.idx);
    });
    $("#tlLabel").textContent = r.date + " · " + satLabel(r) + " · cloud " + pct(r.cloud_scl) +
      (r.fallback ? " · fallback" : "") + "  (" + (tl.idx + 1) + "/" + f.length + ")";
    var t = $("#tlTrack");
    t.setAttribute("aria-valuemax", f.length - 1); t.setAttribute("aria-valuenow", tl.idx);
    t.setAttribute("aria-valuetext", r.date + ", " + satLabel(r));
    var tag = $("#tagL"); tag.hidden = false; $("#tagR").hidden = true;
    tag.textContent = r.date + " · " + satLabel(r) + " · cloud " + pct(r.cloud_scl);
  }

  function tlPreload(url) {
    if (!tl.cache[url]) { var im = new Image(); im.src = url; tl.cache[url] = im; }
    return tl.cache[url];
  }
  function tlSet(i) {
    var f = tl.frames;
    if (!f.length) return;
    tl.idx = clamp(i, 0, f.length - 1);
    var r = f[tl.idx], url = r.images[S.prod], im = tlPreload(url);
    S.run = r.id;
    var show = function () { if (tl.frames[tl.idx] === r) imgL.src = url; };
    if (im.complete) show(); else im.addEventListener("load", show, { once: true });
    if (dim.w !== r.size[0] || dim.h !== r.size[1]) { dim.w = r.size[0]; dim.h = r.size[1]; layout(); }
    geo = { bbox: r.bbox };
    [tl.idx + 1, tl.idx - 1, tl.idx + 2].forEach(function (j) { if (f[j]) tlPreload(f[j].images[S.prod]); });
    Array.prototype.forEach.call(document.querySelectorAll("#runList .card"), function (c) {
      if (c.dataset.id === r.id) c.setAttribute("aria-current", "true"); else c.removeAttribute("aria-current");
    });
    renderInfo(); writeHash(); tlUpdate();
  }

  function tlRebuild() {   // after a change of run, product or filter: rebuild the frames, keep the nearest date
    var curDate = Date.parse(byId(S.run).date), curId = S.run;
    tl.frames = tlFrames();
    tlDraw();
    if (!tl.frames.length) { tlPlay(false); return; }
    var best = 0, bd = Infinity;
    tl.frames.forEach(function (r, i) {
      var d = Math.abs(Date.parse(r.date) - curDate);
      if (d < bd || (d === bd && r.id === curId)) { bd = d; best = i; }
    });
    tlSet(best);
  }

  function tlPlay(on) {
    if (tl.timer) { clearTimeout(tl.timer); tl.timer = null; }
    var b = $("#tlPlay");
    b.innerHTML = on ? "&#10074;&#10074;" : "&#9654;";
    b.setAttribute("aria-label", on ? "Pause" : "Play");
    if (!on || tl.frames.length < 2) return;
    tl.frames.forEach(function (r) { tlPreload(r.images[S.prod]); });   // so playback does not stall
    (function step() {
      tl.timer = setTimeout(function () { tlSet((tl.idx + 1) % tl.frames.length); step(); }, tl.speed);
    })();
  }

  function tlNearest(clientX) {
    var rect = $("#tlTrack").getBoundingClientRect(), x = (clientX - rect.left) / rect.width * 100, best = 0, bd = Infinity;
    tl.frames.forEach(function (r, i) { var d = Math.abs(tlX(i) - x); if (d < bd) { bd = d; best = i; } });
    return best;
  }
  var tlTrack = $("#tlTrack");
  tlTrack.addEventListener("pointerdown", function (e) {
    if (!tl.frames.length) return;
    tl.down = true;
    try { tlTrack.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    tlPlay(false); tlSet(tlNearest(e.clientX));
  });
  tlTrack.addEventListener("pointermove", function (e) {
    if (!tl.down) return;
    var i = tlNearest(e.clientX); if (i !== tl.idx) tlSet(i);
  });
  tlTrack.addEventListener("pointerup", function () { tl.down = false; });
  tlTrack.addEventListener("pointercancel", function () { tl.down = false; });
  tlTrack.addEventListener("keydown", function (e) {
    var k = e.key;
    if (k === "ArrowLeft" || k === "ArrowDown") tlSet(tl.idx - 1);
    else if (k === "ArrowRight" || k === "ArrowUp") tlSet(tl.idx + 1);
    else if (k === "Home") tlSet(0);
    else if (k === "End") tlSet(tl.frames.length - 1);
    else if (k === " ") tlPlay(!tl.timer);
    else return;
    if (k !== " ") tlPlay(false);
    e.preventDefault();
  });
  $("#tlPlay").addEventListener("click", function () { tlPlay(!tl.timer); });
  $("#tlPrev").addEventListener("click", function () { tlPlay(false); tlSet(tl.idx - 1); });
  $("#tlNext").addEventListener("click", function () { tlPlay(false); tlSet(tl.idx + 1); });
  $("#tlSpeed").addEventListener("change", function () { tl.speed = +this.value; if (tl.timer) tlPlay(true); });
  $("#tlCloud").addEventListener("change", function () { tl.cloud = +this.value; tlPlay(false); tlRebuild(); });

  // ---------------------------------------------------------------- orchestration
  function defaults(r) {
    if (r.composites.indexOf(S.prod) < 0) S.prod = r.composites[0];
    var list = imageEntries(r), me = r.id + "|";
    var left = (r.composites.indexOf("truecolour") >= 0 ? me + "truecolour" : me + r.composites[0]);
    var right = r.composites.indexOf("swir") >= 0 && left !== me + "swir" ? me + "swir" : null;
    if (!right) {
      var other = list.filter(function (e) { return e.run.id !== r.id && e.prod === left.split("|")[1]; })[0] ||
                  list.filter(function (e) { return e.key !== left; })[0];
      right = other ? other.key : left;
    }
    if (!entry(S.left) || areaKey(entry(S.left).run) !== areaKey(r)) S.left = left;
    if (!entry(S.right) || areaKey(entry(S.right).run) !== areaKey(r)) S.right = right;
  }

  function render() {
    var r = byId(S.run);
    defaults(r);
    renderList(); renderToolbar(); renderInfo();
    var isMap = S.mode === "map", isTl = S.mode === "timeline";
    stage.hidden = isMap; $("#map").hidden = !isMap; $("#timeline").hidden = !isTl;
    if (!isTl) tlPlay(false);
    if (isMap) showMap(r);
    else if (S.mode === "compare") showStage(entry(S.left), entry(S.right));
    else showStage({ run: r, prod: S.prod }, null);
    if (isTl) tlRebuild();
    writeHash();
  }

  function selectRun(id) { tlPlay(false); S.run = id; S.open[groupKey(byId(id))] = true; resetViewSoon(); render(); }
  function resetViewSoon() { T.k = 1; T.x = 0; T.y = 0; }

  $("#runList").addEventListener("click", function (e) {
    var c = e.target.closest(".card");
    if (c) selectRun(c.dataset.id);
  });
  $("#aoiSel").addEventListener("change", function () { pickArea(this.value); });
  $("#areaFilter").addEventListener("change", function () {
    S.area = this.value;
    if (S.area === "all") { renderList(); return; }
    // choosing an area in the list also shows it: the newest image of that area
    var first = RUNS.filter(function (r) { return groupKey(r) === S.area; })[0];
    if (first && groupKey(byId(S.run)) !== S.area) pickArea(aoiKey(first)); else renderList();
  });
  $("#satFilter").addEventListener("change", function () { S.sat = this.value; renderList(); if (S.mode === "timeline") { tlPlay(false); tlRebuild(); } });
  // <details> toggles don't bubble, so listen in the capture phase to remember which groups are open
  $("#runList").addEventListener("toggle", function (e) {
    var d = e.target; if (d.classList && d.classList.contains("group")) S.open[d.dataset.key] = d.open;
  }, true);
  $("#modeTabs").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    S.mode = b.dataset.mode; resetViewSoon(); render();
  });
  $("#prodBar").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    S.prod = b.dataset.prod; render();
  });
  $("#selL").addEventListener("change", function () { S.left = this.value; render(); });
  $("#selR").addEventListener("change", function () { S.right = this.value; render(); });

  // ---------------------------------------------------------------- start
  if (!RUNS.length) {
    $("main").innerHTML = '<p class="empty">No runs published yet. Run the <b>Sentinel-2 true colour</b> ' +
      "workflow with <i>publish</i> ticked and this page will fill in.</p>";
    return;
  }
  var h = readHash();
  S.run = byId(h.run) ? h.run : RUNS[0].id;
  S.open[groupKey(byId(S.run))] = true;
  if (/^(view|compare|map|timeline)$/.test(h.mode || "")) S.mode = h.mode;
  if (h.p) S.prod = h.p;
  $("#subtitle").textContent = RUNS.length + " run" + (RUNS.length === 1 ? "" : "s") +
    ", newest first. Scroll or pinch to zoom, drag to pan.";
  render();
})();
