/* Brightness curve equalizer for room pages. */

(function () {
  const container = document.getElementById('brightnessCurve');
  if (!container) return;

  const canvas = document.getElementById('brightnessCurveCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const apiBase = container.dataset.apiBase || '';
  const initialCurve = JSON.parse(container.dataset.curve || '{}');
  const nodeList = JSON.parse(container.dataset.nodes || '[]');
  const enableToggle = document.getElementById('brightnessCurveEnabled');
  const saveButton = document.getElementById('brightnessCurveSave');
  const statusEl = document.getElementById('brightnessCurveStatus');
  const resetButton = document.getElementById('brightnessCurveReset');

  let points = (initialCurve.points || []).map(p => ({ hour: p.hour, brightness: p.brightness }));
  let enabled = !!initialCurve.enabled;
  let dirty = false;
  let dragging = -1;
  let statusTimeout = null;

  const PAD_LEFT = 48;
  const PAD_RIGHT = 20;
  const PAD_TOP = 20;
  const PAD_BOTTOM = 36;
  const POINT_RADIUS = 10;
  const MIN_GAP_HOURS = 0.5;

  if (enableToggle) enableToggle.checked = enabled;

  function dpr() { return window.devicePixelRatio || 1; }

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const w = rect.width;
    const h = 260;
    const r = dpr();
    canvas.width = w * r;
    canvas.height = h * r;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(r, 0, 0, r, 0, 0);
    draw();
  }

  function graphW() { return parseFloat(canvas.style.width) - PAD_LEFT - PAD_RIGHT; }
  function graphH() { return parseFloat(canvas.style.height) - PAD_TOP - PAD_BOTTOM; }

  function hourToX(h) { return PAD_LEFT + (h / 24) * graphW(); }
  function brightnessToY(b) { return PAD_TOP + (1 - b / 255) * graphH(); }
  function xToHour(x) { return Math.max(0, Math.min(24, ((x - PAD_LEFT) / graphW()) * 24)); }
  function yToBrightness(y) { return Math.max(0, Math.min(255, Math.round((1 - (y - PAD_TOP) / graphH()) * 255))); }

  function catmullRom(p0, p1, p2, p3, t) {
    const t2 = t * t, t3 = t2 * t;
    return 0.5 * (
      2 * p1 +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    );
  }

  function interpolate(hour) {
    if (!points.length) return 0;
    const pts = points.slice().sort((a, b) => a.hour - b.hour);
    const n = pts.length;
    if (n === 1) return pts[0].brightness;
    const hours = pts.map(p => p.hour);
    const vals = pts.map(p => p.brightness);
    const h = ((hour % 24) + 24) % 24;

    let seg = -1;
    for (let i = 0; i < n - 1; i++) {
      if (hours[i] <= h && h <= hours[i + 1]) { seg = i; break; }
    }
    let result;
    if (seg === -1) {
      const h0 = hours[n - 1], h1 = hours[0] + 24;
      const hc = h < hours[0] ? h + 24 : h;
      const span = h1 - h0;
      const t = span > 0 ? (hc - h0) / span : 0;
      result = catmullRom(
        vals[n >= 2 ? n - 2 : n - 1], vals[n - 1],
        vals[0], vals[n >= 2 ? 1 : 0], t
      );
    } else {
      const span = hours[seg + 1] - hours[seg];
      const t = span > 0 ? (h - hours[seg]) / span : 0;
      const p0 = seg === 0 ? vals[n - 1] : vals[seg - 1];
      const p3 = seg + 2 >= n ? vals[0] : vals[seg + 2];
      result = catmullRom(p0, vals[seg], vals[seg + 1], p3, t);
    }
    return Math.max(0, Math.min(255, Math.round(result)));
  }

  function draw() {
    const w = parseFloat(canvas.style.width);
    const h = parseFloat(canvas.style.height);
    ctx.clearRect(0, 0, w, h);

    // Grid
    ctx.strokeStyle = 'rgba(148,163,184,0.15)';
    ctx.lineWidth = 1;
    for (let hr = 0; hr <= 24; hr += 4) {
      const x = hourToX(hr);
      ctx.beginPath(); ctx.moveTo(x, PAD_TOP); ctx.lineTo(x, PAD_TOP + graphH()); ctx.stroke();
    }
    for (let pct = 0; pct <= 100; pct += 25) {
      const y = brightnessToY(pct / 100 * 255);
      ctx.beginPath(); ctx.moveTo(PAD_LEFT, y); ctx.lineTo(PAD_LEFT + graphW(), y); ctx.stroke();
    }

    // Axis labels
    ctx.fillStyle = 'rgba(148,163,184,0.7)';
    ctx.font = '11px Inter, system-ui, sans-serif';
    ctx.textAlign = 'center';
    for (let hr = 0; hr <= 24; hr += 4) {
      const label = hr === 0 ? '12a' : hr === 4 ? '4a' : hr === 8 ? '8a' : hr === 12 ? '12p' : hr === 16 ? '4p' : hr === 20 ? '8p' : '12a';
      ctx.fillText(label, hourToX(hr), PAD_TOP + graphH() + 20);
    }
    ctx.textAlign = 'right';
    for (let pct = 0; pct <= 100; pct += 25) {
      ctx.fillText(pct + '%', PAD_LEFT - 8, brightnessToY(pct / 100 * 255) + 4);
    }

    // Current time line
    const now = new Date();
    const nowHour = now.getHours() + now.getMinutes() / 60;
    const nowX = hourToX(nowHour);
    ctx.strokeStyle = 'rgba(251,191,36,0.5)';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(nowX, PAD_TOP); ctx.lineTo(nowX, PAD_TOP + graphH()); ctx.stroke();
    ctx.setLineDash([]);

    // Current brightness dot
    const nowBrightness = interpolate(nowHour);
    const nowY = brightnessToY(nowBrightness);
    ctx.fillStyle = 'rgba(251,191,36,0.9)';
    ctx.beginPath(); ctx.arc(nowX, nowY, 5, 0, Math.PI * 2); ctx.fill();

    // Curve for each node (colored lines)
    const steps = Math.max(200, Math.round(graphW()));
    if (nodeList.length > 0) {
      nodeList.forEach((node, ni) => {
        ctx.strokeStyle = node.color || '#ffffff';
        ctx.lineWidth = nodeList.length > 1 ? 2 : 3;
        ctx.globalAlpha = 0.45;
        ctx.beginPath();
        for (let s = 0; s <= steps; s++) {
          const hr = (s / steps) * 24;
          const val = interpolate(hr);
          const x = hourToX(hr);
          const y = brightnessToY(val);
          if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      });
    }

    // Main curve
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 3;
    ctx.shadowColor = 'rgba(124,58,237,0.4)';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    for (let s = 0; s <= steps; s++) {
      const hr = (s / steps) * 24;
      const val = interpolate(hr);
      const x = hourToX(hr);
      const y = brightnessToY(val);
      if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Fill under curve
    ctx.fillStyle = 'rgba(124,58,237,0.08)';
    ctx.beginPath();
    for (let s = 0; s <= steps; s++) {
      const hr = (s / steps) * 24;
      const x = hourToX(hr);
      const y = brightnessToY(interpolate(hr));
      if (s === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.lineTo(hourToX(24), PAD_TOP + graphH());
    ctx.lineTo(hourToX(0), PAD_TOP + graphH());
    ctx.closePath();
    ctx.fill();

    // Control points
    points.forEach((p, i) => {
      const x = hourToX(p.hour);
      const y = brightnessToY(p.brightness);

      // Glow
      ctx.shadowColor = dragging === i ? 'rgba(124,58,237,0.7)' : 'rgba(124,58,237,0.3)';
      ctx.shadowBlur = dragging === i ? 16 : 8;

      // Outer ring
      ctx.fillStyle = dragging === i ? 'rgba(124,58,237,0.9)' : 'rgba(30,41,59,0.9)';
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(x, y, POINT_RADIUS, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      ctx.shadowBlur = 0;

      // Label
      ctx.fillStyle = '#e2e8f0';
      ctx.font = 'bold 9px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      const pct = Math.round(p.brightness / 255 * 100);
      ctx.fillText(pct + '%', x, y - POINT_RADIUS - 6);

      const hourLabel = formatHour(p.hour);
      ctx.font = '9px Inter, system-ui, sans-serif';
      ctx.fillStyle = 'rgba(148,163,184,0.8)';
      ctx.fillText(hourLabel, x, y + POINT_RADIUS + 14);
    });
  }

  function formatHour(h) {
    const hr = Math.floor(h) % 24;
    const min = Math.round((h - Math.floor(h)) * 60);
    const suffix = hr >= 12 ? 'p' : 'a';
    const display = hr % 12 || 12;
    return min ? display + ':' + String(min).padStart(2, '0') + suffix : display + suffix;
  }

  function canvasPos(e) {
    const rect = canvas.getBoundingClientRect();
    const touch = e.touches ? e.touches[0] : e;
    return { x: touch.clientX - rect.left, y: touch.clientY - rect.top };
  }

  function hitTest(pos) {
    for (let i = 0; i < points.length; i++) {
      const x = hourToX(points[i].hour);
      const y = brightnessToY(points[i].brightness);
      const dx = pos.x - x, dy = pos.y - y;
      if (dx * dx + dy * dy <= (POINT_RADIUS + 6) * (POINT_RADIUS + 6)) return i;
    }
    return -1;
  }

  function onPointerDown(e) {
    const pos = canvasPos(e);
    const idx = hitTest(pos);
    if (idx >= 0) {
      dragging = idx;
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    }
  }

  function onPointerMove(e) {
    if (dragging < 0) {
      const pos = canvasPos(e);
      canvas.style.cursor = hitTest(pos) >= 0 ? 'grab' : 'default';
      return;
    }
    e.preventDefault();
    const pos = canvasPos(e);
    let hour = xToHour(pos.x);
    const brightness = yToBrightness(pos.y);

    // Constrain: don't cross neighbors
    const sorted = points.map((p, i) => ({ ...p, idx: i })).sort((a, b) => a.hour - b.hour);
    const sortedIdx = sorted.findIndex(s => s.idx === dragging);
    const prevHour = sortedIdx > 0 ? sorted[sortedIdx - 1].hour + MIN_GAP_HOURS : 0;
    const nextHour = sortedIdx < sorted.length - 1 ? sorted[sortedIdx + 1].hour - MIN_GAP_HOURS : 24;
    hour = Math.max(prevHour, Math.min(nextHour, hour));

    // Snap to half hours
    hour = Math.round(hour * 2) / 2;
    hour = Math.max(prevHour, Math.min(nextHour, hour));

    points[dragging].hour = hour;
    points[dragging].brightness = brightness;
    markDirty();
    draw();
  }

  function onPointerUp() {
    if (dragging >= 0) {
      dragging = -1;
      canvas.style.cursor = 'default';
      draw();
    }
  }

  canvas.addEventListener('mousedown', onPointerDown);
  canvas.addEventListener('mousemove', onPointerMove);
  window.addEventListener('mouseup', onPointerUp);
  canvas.addEventListener('touchstart', onPointerDown, { passive: false });
  canvas.addEventListener('touchmove', onPointerMove, { passive: false });
  window.addEventListener('touchend', onPointerUp);

  function markDirty() {
    if (!dirty) {
      dirty = true;
      updateSaveState();
    }
    setStatus('Unsaved changes', 'text-amber-300');
  }

  function updateSaveState() {
    if (saveButton) {
      saveButton.disabled = !dirty;
      saveButton.classList.toggle('motion-button--dirty', dirty);
    }
  }

  function setStatus(msg, cls) {
    if (!statusEl) return;
    if (statusTimeout) { clearTimeout(statusTimeout); statusTimeout = null; }
    statusEl.textContent = msg || '';
    statusEl.className = 'text-sm mt-3 opacity-80' + (cls ? ' ' + cls : '');
  }

  if (enableToggle) {
    enableToggle.addEventListener('change', () => {
      enabled = enableToggle.checked;
      markDirty();
    });
  }

  if (resetButton) {
    resetButton.addEventListener('click', () => {
      points = [
        { hour: 0, brightness: 10 },
        { hour: 7, brightness: 128 },
        { hour: 12, brightness: 200 },
        { hour: 18, brightness: 255 },
        { hour: 22, brightness: 50 },
      ];
      markDirty();
      draw();
    });
  }

  if (saveButton) {
    saveButton.addEventListener('click', async () => {
      if (!dirty) return;
      saveButton.disabled = true;
      setStatus('Saving...', 'text-slate-200 opacity-80');
      try {
        const res = await fetch(apiBase + '/brightness-curve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ points, enabled }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          throw new Error((data && data.detail) || 'Failed to save');
        }
        const data = await res.json();
        if (data.points) points = data.points;
        if (data.enabled !== undefined) enabled = data.enabled;
        if (enableToggle) enableToggle.checked = enabled;
        dirty = false;
        updateSaveState();
        setStatus('Saved', 'text-emerald-300');
        statusTimeout = setTimeout(() => setStatus(''), 2200);
        draw();
      } catch (err) {
        setStatus(err.message || 'Failed to save', 'text-rose-300');
        saveButton.disabled = false;
      }
    });
  }

  window.addEventListener('resize', resize);
  updateSaveState();
  resize();

  // Refresh current-time indicator every minute
  setInterval(() => { draw(); }, 60000);
})();
