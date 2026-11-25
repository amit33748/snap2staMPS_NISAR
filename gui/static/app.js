(() => {
  "use strict";

  let appState = null;
  let leafletMap = null;
  let aoiRectLayer = null;
  let drawnItems = null;
  let jobTimer = null;
  let activeJobTarget = "run";
  let lastOffset = 0;

  const $ = (id) => document.getElementById(id);

  function showToast(message, kind = "info") {
    const banner = $("toast-banner");
    banner.textContent = message;
    banner.className = "wb-toast" + (kind === "ok" ? " is-ok" : kind === "err" ? " is-err" : "");
    banner.hidden = false;
    window.clearTimeout(banner._hideTimer);
    banner._hideTimer = window.setTimeout(() => {
      banner.hidden = true;
    }, 5500);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function switchTab(tabKey) {
    document.querySelectorAll(".wb-tab").forEach((btn) => {
      const selected = btn.dataset.tab === tabKey;
      btn.classList.toggle("is-active", selected);
      btn.setAttribute("aria-selected", String(selected));
    });
    document.querySelectorAll(".wb-panel").forEach((panel) => {
      const active = panel.id === `panel-${tabKey}`;
      panel.classList.toggle("is-active", active);
      panel.hidden = !active;
    });
    if (tabKey === "setup" && leafletMap) {
      window.setTimeout(() => leafletMap.invalidateSize(), 120);
    }
    if (tabKey === "inspect") {
      refreshBaselineChart();
    }
  }

  function initTabs() {
    const tabButtons = Array.from(document.querySelectorAll(".wb-tab"));
    tabButtons.forEach((btn, idx) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
      btn.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
          ev.preventDefault();
          const dir = ev.key === "ArrowRight" ? 1 : -1;
          const next = tabButtons[(idx + dir + tabButtons.length) % tabButtons.length];
          next.focus();
          switchTab(next.dataset.tab);
        }
      });
    });
  }

  function getFormBounds() {
    return [
      parseFloat($("cfg-lonmin").value),
      parseFloat($("cfg-latmin").value),
      parseFloat($("cfg-lonmax").value),
      parseFloat($("cfg-latmax").value),
    ];
  }

  function setFormBounds(lonmin, latmin, lonmax, latmax) {
    $("cfg-lonmin").value = lonmin.toFixed(4);
    $("cfg-latmin").value = latmin.toFixed(4);
    $("cfg-lonmax").value = lonmax.toFixed(4);
    $("cfg-latmax").value = latmax.toFixed(4);
    updateAoiOverlayAndCaption();
  }

  function updateAoiOverlayAndCaption() {
    const [lonmin, latmin, lonmax, latmax] = getFormBounds();
    const caption = $("aoi-summary-caption");
    if ([lonmin, latmin, lonmax, latmax].some(Number.isNaN)) {
      caption.textContent = "Configured Box: invalid coordinates";
      return;
    }
    const areaDeg2 = Math.abs((lonmax - lonmin) * (latmax - latmin));
    caption.textContent = `Configured Box: ${lonmin.toFixed(4)} to ${lonmax.toFixed(4)} E, ${latmin.toFixed(4)} to ${latmax.toFixed(4)} N (${areaDeg2.toFixed(4)} deg^2)`;

    if (!leafletMap || lonmin >= lonmax || latmin >= latmax) {
      return;
    }
    const latLngBounds = L.latLngBounds([latmin, lonmin], [latmax, lonmax]);
    if (aoiRectLayer) {
      aoiRectLayer.setBounds(latLngBounds);
    } else {
      aoiRectLayer = L.rectangle(latLngBounds, {
        color: "#c84b28",
        weight: 2,
        fillColor: "#c84b28",
        fillOpacity: 0.15,
      }).addTo(leafletMap);
    }
    aoiRectLayer.bindTooltip(`AOI: ${lonmin.toFixed(3)}..${lonmax.toFixed(3)} E, ${latmin.toFixed(3)}..${latmax.toFixed(3)} N`);
  }

  function initIndiaMap(indiaCfg) {
    if (leafletMap) return;
    const center = (indiaCfg && indiaCfg.center) || [22.5, 79.0];
    const zoom = (indiaCfg && indiaCfg.zoom) || 5;

    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    });

    const openTopo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
      maxZoom: 17,
      attribution: "Map data: &copy; OpenStreetMap contributors, SRTM | Style: &copy; OpenTopoMap",
    });

    const osmHot = L.tileLayer("https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors, HOT",
    });

    const esriSat = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{x}/{y}",
      {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri",
      }
    );

    leafletMap = L.map("india-map", {
      center,
      zoom,
      layers: [osm],
    });

    L.control.scale({ imperial: false }).addTo(leafletMap);
    L.control.layers(
      {
        "OpenStreetMap (Standard)": osm,
        "OpenTopoMap (Terrain)": openTopo,
        "OSM Humanitarian": osmHot,
        "Satellite Imagery": esriSat,
      },
      null,
      { position: "topright", collapsed: false }
    ).addTo(leafletMap);

    drawnItems = new L.FeatureGroup();
    leafletMap.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
      edit: { featureGroup: drawnItems, edit: false, remove: true },
      draw: {
        polyline: false,
        polygon: false,
        circle: false,
        marker: false,
        circlemarker: false,
        rectangle: {
          shapeOptions: { color: "#c84b28", weight: 2 },
        },
      },
    });
    leafletMap.addControl(drawControl);

    leafletMap.on(L.Draw.Event.CREATED, (event) => {
      drawnItems.clearLayers();
      drawnItems.addLayer(event.layer);
      const b = event.layer.getBounds();
      setFormBounds(b.getWest(), b.getSouth(), b.getEast(), b.getNorth());
      showToast("Updated AOI coordinates from drawn rectangle. Click Save Configuration File to persist.", "info");
    });

    $("btn-india-view").addEventListener("click", () => {
      leafletMap.setView(center, zoom);
    });

    $("btn-fit-aoi").addEventListener("click", () => {
      const [lonmin, latmin, lonmax, latmax] = getFormBounds();
      if (lonmin < lonmax && latmin < latmax) {
        leafletMap.fitBounds([[latmin, lonmin], [latmax, lonmax]], { padding: [28, 28], maxZoom: 12 });
      }
    });

    ["cfg-lonmin", "cfg-latmin", "cfg-lonmax", "cfg-latmax"].forEach((id) => {
      $(id).addEventListener("change", updateAoiOverlayAndCaption);
    });
  }

  function renderMastheadAndRibbon(state) {
    const cfg = state.config;
    const ws = state.workspace;
    $("chip-workspace").textContent = cfg.project_folder || "Not configured";
    $("chip-master").textContent = cfg.master || ws.master_date || "Auto";
    $("chip-track").textContent = cfg.track || "Any";
    $("chip-pol").textContent = cfg.polarisation || "HH";

    const ribbon = $("stage-ribbon");
    ribbon.innerHTML = state.stages
      .map((st) => {
        const cls = st.done ? "is-done" : st.active ? "is-active" : "";
        return `
          <div class="wb-stage ${cls}">
            <div>
              <div class="wb-stage-name">${escapeHtml(st.label)}</div>
              <div class="wb-stage-sub">${escapeHtml(st.sub)}</div>
            </div>
            <div class="wb-stage-count">${escapeHtml(st.count)}</div>
          </div>`;
      })
      .join("");
  }

  function renderDoctor(doctor) {
    const hasErr = doctor.some((d) => d.status === "err");
    const hasWarn = doctor.some((d) => d.status === "warn");
    $("doctor-summary").textContent = hasErr
      ? "Action needed: missing environment items listed in Details."
      : hasWarn
      ? "Ready with warnings: open Details to inspect items."
      : "All environment checks passed (isolated Python, packages, SNAP GPT, XML graphs, disk).";

    const drawer = $("doctor-drawer");
    if (hasErr) {
      drawer.hidden = false;
      $("btn-toggle-doctor").setAttribute("aria-expanded", "true");
    }

    const labelMap = { ok: "READY", warn: "CHECK", err: "MISSING" };
    $("doctor-rows").innerHTML = doctor
      .map(
        (d) => `
        <tr>
          <td><strong>${escapeHtml(d.component)}</strong></td>
          <td><span class="wb-badge ${escapeHtml(d.status)}">${labelMap[d.status] || "INFO"}</span></td>
          <td class="wb-mono">${escapeHtml(d.detail)}</td>
        </tr>`
      )
      .join("");
  }

  function populateConfigForm(cfg) {
    $("example-notice").hidden = !cfg.used_example;
    $("cfg-project-folder").value = cfg.project_folder || "";
    $("cfg-snap-folder").value = cfg.snap_folder || "";
    $("cfg-snap-home").value = cfg.snap_home || "";
    $("cfg-lonmin").value = Number(cfg.lonmin).toFixed(4);
    $("cfg-latmin").value = Number(cfg.latmin).toFixed(4);
    $("cfg-lonmax").value = Number(cfg.lonmax).toFixed(4);
    $("cfg-latmax").value = Number(cfg.latmax).toFixed(4);
    $("cfg-provider").value = cfg.provider || "ASF";
    $("cfg-frequency").value = cfg.frequency || "L";
    $("cfg-pol").value = cfg.polarisation || "HH";
    $("cfg-track").value = cfg.track || "";
    $("cfg-start").value = cfg.start || "";
    $("cfg-end").value = cfg.end || "";
    $("cfg-master").value = cfg.master || "";
    $("cfg-overwrite").value = cfg.overwrite || "N";
    $("cfg-cpu").value = cfg.cpu || 16;
    $("cfg-cache").value = cfg.cache || "32G";
    $("cfg-dem").value = cfg.dem_name || "Copernicus 30m Global DEM";
    $("cfg-asf-user").value = cfg.asf_user || "";
    $("cfg-asf-pass").value = "";
    $("lbl-asf-pass").textContent = cfg.has_asf_pass
      ? "ASF Password (leave blank to keep stored)"
      : "ASF Password";
    $("cfg-bh-user").value = cfg.bhoonidhi_user || "";
    $("cfg-bh-pass").value = "";
    $("lbl-bh-pass").textContent = cfg.has_bh_pass
      ? "Bhoonidhi Password (leave blank to keep stored)"
      : "Bhoonidhi Password";
    updateAoiOverlayAndCaption();
  }

  function renderRunControls(state) {
    const container = $("step-buttons");
    container.innerHTML = "";
    state.steps.forEach((step) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "wb-btn wb-btn-secondary";
      btn.textContent = step.label;
      btn.title = step.detail;
      btn.disabled = Boolean(state.job && state.job.running);
      btn.addEventListener("click", () => startJob(step.key, "run"));
      container.appendChild(btn);
    });

    $("step-guide-rows").innerHTML = state.steps
      .map((s) => `<tr><td><strong>${escapeHtml(s.label)}</strong></td><td>${escapeHtml(s.detail)}</td></tr>`)
      .join("");

    const sel = $("log-source-select");
    const prevVal = sel.value || "__live__";
    sel.innerHTML = '<option value="__live__">Live Session Console</option>';
    (state.logs || []).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    if (Array.from(sel.options).some((o) => o.value === prevVal)) {
      sel.value = prevVal;
    }
  }

  function refreshBaselineChart() {
    $("baseline-chart-img").src = `/api/baseline-chart.png?t=${Date.now()}`;
  }

  function renderInspect(state) {
    const ws = state.workspace;
    $("inspect-empty").hidden = ws.exists;
    $("inspect-content").hidden = !ws.exists;
    if (!ws.exists) return;

    refreshBaselineChart();
    $("inspect-master-note").textContent = `Master: ${ws.master_date || "Pending"}`;

    if (ws.baselines && ws.baselines.length) {
      $("baseline-rows").innerHTML = ws.baselines
        .map((row) => {
          const badge = row.is_master
            ? '<span class="wb-badge info">MASTER</span>'
            : '<span class="wb-badge ok">IFG</span>';
          const bperp = `${row.bperp_m >= 0 ? "+" : ""}${Number(row.bperp_m).toFixed(2)} m`;
          const btemp = `${row.btemp_days >= 0 ? "+" : ""}${row.btemp_days} d`;
          return `
            <tr>
              <td class="wb-mono">${escapeHtml(row.date)}</td>
              <td>${badge}</td>
              <td class="wb-mono">${bperp}</td>
              <td class="wb-mono">${btemp}</td>
              <td class="wb-mono">${Number(row.coherence).toFixed(4)}</td>
            </tr>`;
        })
        .join("");
    } else {
      $("baseline-rows").innerHTML =
        '<tr><td colspan="5" class="wb-caption">No baselines_table.txt found yet. Run Automaster first.</td></tr>';
    }

    const summary = $("stamps-export-summary");
    if (ws.insar_dir) {
      const badge = ws.stamps_export_ready
        ? '<span class="wb-badge ok">EXPORT READY</span>'
        : '<span class="wb-badge warn">INCOMPLETE</span>';
      summary.innerHTML = `${badge} &nbsp; <span class="wb-mono">${escapeHtml(ws.insar_dir)}</span> (dem / diff0 [${ws.stamps_diff_count}] / geo / rslc)`;
    } else {
      summary.innerHTML = '<span class="wb-caption">No <code>INSAR_&lt;MASTER&gt;</code> directory exported yet.</span>';
    }

    const pSel = $("preview-select");
    const prevChoice = pSel.value;
    pSel.innerHTML = "";
    if (ws.previews && ws.previews.length) {
      ws.previews.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        pSel.appendChild(opt);
      });
      if (ws.previews.includes(prevChoice)) pSel.value = prevChoice;
      loadSelectedPreview();
    } else {
      $("preview-image").hidden = true;
      $("preview-empty-msg").hidden = false;
    }
  }

  function loadSelectedPreview() {
    const name = $("preview-select").value;
    const img = $("preview-image");
    const msg = $("preview-empty-msg");
    if (!name) {
      img.hidden = true;
      msg.hidden = false;
      return;
    }
    img.src = `/api/preview/${encodeURIComponent(name)}?t=${Date.now()}`;
    img.hidden = false;
    msg.hidden = true;
  }

  function setRunningButtons(isRunning) {
    $("btn-run-all").disabled = isRunning;
    $("btn-install-deps").disabled = isRunning;
    $("btn-stop-job").disabled = !isRunning;
    document.querySelectorAll("#step-buttons button").forEach((b) => {
      b.disabled = isRunning;
    });
  }

  async function pollJob() {
    try {
      const res = await fetch(`/api/job?offset=${lastOffset}`);
      if (!res.ok) return;
      const snap = await res.json();
      const targetPre = activeJobTarget === "deps" ? $("deps-console") : $("run-console");

      if (snap.lines && snap.lines.length) {
        if (lastOffset === 0) targetPre.textContent = "";
        targetPre.textContent += (targetPre.textContent ? "\n" : "") + snap.lines.join("\n");
        targetPre.scrollTop = targetPre.scrollHeight;
        lastOffset = snap.total_lines;
      }

      if (snap.running) {
        setRunningButtons(true);
        if (activeJobTarget === "deps") {
          $("deps-status-pill").textContent = "Installing...";
        } else {
          $("run-status-note").textContent = `Running: ${snap.label}...`;
        }
      } else {
        window.clearInterval(jobTimer);
        jobTimer = null;
        setRunningButtons(false);
        const ok = snap.exit_code === 0;
        if (activeJobTarget === "deps") {
          $("deps-status-pill").textContent = ok ? "Completed (0)" : `Exited (${snap.exit_code})`;
        } else {
          $("run-status-note").textContent = ok
            ? `${snap.label} completed cleanly (exit 0).`
            : `${snap.label} exited with code ${snap.exit_code}.`;
        }
        showToast(
          ok ? `${snap.label} finished cleanly.` : `${snap.label} exited with code ${snap.exit_code}.`,
          ok ? "ok" : "err"
        );
        await fetchState(false);
      }
    } catch (_) {
      /* ignore transient poll hiccup */
    }
  }

  async function startJob(stepKey, target) {
    activeJobTarget = target;
    lastOffset = 0;

    if (target === "deps") {
      $("doctor-drawer").hidden = false;
      $("btn-toggle-doctor").setAttribute("aria-expanded", "true");
      $("deps-console-wrap").hidden = false;
      $("deps-console").textContent = "Starting dependency installer...";
      $("deps-status-pill").textContent = "Starting...";
    } else {
      $("log-source-select").value = "__live__";
      $("run-console").textContent = "Launching process...";
      $("run-status-note").textContent = "Starting...";
    }

    try {
      const res = await fetch(`/api/run/${encodeURIComponent(stepKey)}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || "Could not launch step.", "err");
        return;
      }
      setRunningButtons(true);
      if (jobTimer) window.clearInterval(jobTimer);
      jobTimer = window.setInterval(pollJob, 400);
      pollJob();
    } catch (err) {
      showToast(`Network error: ${err}`, "err");
    }
  }

  async function fetchState(resetForm = true) {
    const res = await fetch("/api/state");
    if (!res.ok) {
      showToast("Failed to load workbench state from server.", "err");
      return;
    }
    appState = await res.json();
    initIndiaMap(appState.india_map);
    renderMastheadAndRibbon(appState);
    renderDoctor(appState.doctor);
    if (resetForm) populateConfigForm(appState.config);
    renderRunControls(appState);
    renderInspect(appState);

    if (appState.job && appState.job.running && !jobTimer) {
      activeJobTarget = appState.job.step_key === "install_deps" ? "deps" : "run";
      lastOffset = 0;
      setRunningButtons(true);
      jobTimer = window.setInterval(pollJob, 400);
      pollJob();
    }
  }

  function wireEvents() {
    $("btn-toggle-doctor").addEventListener("click", () => {
      const drawer = $("doctor-drawer");
      drawer.hidden = !drawer.hidden;
      $("btn-toggle-doctor").setAttribute("aria-expanded", String(!drawer.hidden));
    });

    $("btn-refresh-doctor").addEventListener("click", () => fetchState(false));
    $("btn-install-deps").addEventListener("click", () => startJob("install_deps", "deps"));

    $("config-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const [lonmin, latmin, lonmax, latmax] = getFormBounds();
      if (lonmin >= lonmax || latmin >= latmax) {
        showToast("Invalid AOI bounds: Lon Min must be < Lon Max and Lat Min < Lat Max.", "err");
        return;
      }
      const btn = $("btn-save-config");
      btn.disabled = true;
      btn.textContent = "Saving...";
      try {
        const payload = {
          project_folder: $("cfg-project-folder").value.trim(),
          snap_folder: $("cfg-snap-folder").value.trim(),
          snap_home: $("cfg-snap-home").value.trim(),
          lonmin,
          latmin,
          lonmax,
          latmax,
          provider: $("cfg-provider").value,
          frequency: $("cfg-frequency").value,
          polarisation: $("cfg-pol").value,
          track: $("cfg-track").value.trim(),
          start: $("cfg-start").value.trim(),
          end: $("cfg-end").value.trim(),
          master: $("cfg-master").value.trim(),
          overwrite: $("cfg-overwrite").value,
          cpu: parseInt($("cfg-cpu").value || "16", 10),
          cache: $("cfg-cache").value.trim(),
          dem_name: $("cfg-dem").value,
          asf_user: $("cfg-asf-user").value.trim(),
          asf_pass: $("cfg-asf-pass").value,
          bhoonidhi_user: $("cfg-bh-user").value.trim(),
          bhoonidhi_pass: $("cfg-bh-pass").value,
        };
        const res = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) {
          showToast(data.detail || "Failed to save configuration.", "err");
        } else {
          appState = data.state;
          renderMastheadAndRibbon(appState);
          renderDoctor(appState.doctor);
          populateConfigForm(appState.config);
          renderInspect(appState);
          showToast(`Saved configuration to ${data.path}`, "ok");
        }
      } catch (err) {
        showToast(`Save error: ${err}`, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "Save Configuration File";
      }
    });

    $("btn-run-all").addEventListener("click", () => startJob("all", "run"));

    $("btn-stop-job").addEventListener("click", async () => {
      await fetch("/api/stop", { method: "POST" });
      showToast("Sent stop signal to active process.", "info");
    });

    $("log-source-select").addEventListener("change", async () => {
      const choice = $("log-source-select").value;
      if (choice === "__live__") {
        lastOffset = 0;
        pollJob();
        return;
      }
      const res = await fetch(`/api/logs/${encodeURIComponent(choice)}`);
      if (res.ok) {
        const data = await res.json();
        $("run-console").textContent = data.content || "(empty log file)";
      }
    });

    $("btn-copy-log").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("run-console").textContent);
        showToast("Copied console log to clipboard.", "ok");
      } catch (_) {
        showToast("Clipboard copy unavailable in this browser context.", "err");
      }
    });

    $("btn-clear-log").addEventListener("click", () => {
      $("run-console").textContent = "";
    });

    $("btn-refresh-inspect").addEventListener("click", () => fetchState(false));
    $("preview-select").addEventListener("change", loadSelectedPreview);
    $("btn-gen-quicklook").addEventListener("click", async () => {
      const btn = $("btn-gen-quicklook");
      btn.disabled = true;
      btn.textContent = "Generating...";
      try {
        const res = await fetch("/api/quicklook", { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          showToast(data.detail || "Could not generate quicklook.", "err");
        } else {
          appState = data.state;
          renderInspect(appState);
          showToast(`Generated ${data.path}`, "ok");
        }
      } finally {
        btn.disabled = false;
        btn.textContent = "Generate Quicklook";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    wireEvents();
    fetchState(true);
  });
})();
