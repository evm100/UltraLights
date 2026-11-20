<script>
  // Horizontal slider with a gradient track and a round thumb (Kelvin, hue, saturation).
  let { value = 0, min = 0, max = 1, step = 0.01, track = "rgba(255,255,255,0.2)", thumb = "#fff", label = "",
        oninput = () => {}, onchange = () => {}, disabled = false } = $props();
  let el;
  let dragging = false;
  const frac = $derived((value - min) / (max - min));

  function at(e) {
    const r = el.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return Math.round((min + f * (max - min)) / step) * step;
  }
  function down(e) {
    if (disabled) return;
    dragging = true;
    el.setPointerCapture(e.pointerId);
    oninput(at(e));
  }
  const move = (e) => dragging && oninput(at(e));
  function up(e) {
    if (!dragging) return;
    dragging = false;
    onchange(at(e));
  }
  function key(e) {
    let v = value;
    const s = (e.shiftKey ? 10 : 1) * step;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") v += s;
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") v -= s;
    else return;
    e.preventDefault();
    onchange(Math.min(max, Math.max(min, v)));
  }
</script>

<div
  class="hs"
  class:disabled
  bind:this={el}
  role="slider"
  tabindex="0"
  aria-label={label}
  aria-valuemin={min}
  aria-valuemax={max}
  aria-valuenow={value}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  onkeydown={key}
>
  <div class="track" style={`background:${track}`}></div>
  <div class="thumb" style={`left:${frac * 100}%;background:${thumb}`}></div>
</div>

<style>
  .hs { position: relative; height: 34px; touch-action: none; cursor: pointer; }
  .hs.disabled { opacity: 0.45; cursor: default; }
  .track { position: absolute; left: 0; right: 0; top: 15px; height: 4px; border-radius: 4px; }
  .thumb {
    position: absolute; top: 7px; width: 20px; height: 20px; margin-left: -10px; border-radius: 50%;
    border: 3px solid rgba(20, 17, 15, 0.85); box-shadow: 0 0 0 1.5px rgba(255, 255, 255, 0.9);
  }
</style>
