<script>
  // Tall brightness slider: drag anywhere on it, fill rises from the bottom.
  let { value = 0, oninput = () => {}, onchange = () => {}, label = "Brightness", fill = "rgba(244,239,232,0.92)", disabled = false } = $props();
  let el;
  let dragging = false;

  function at(e) {
    const r = el.getBoundingClientRect();
    return Math.min(1, Math.max(0, 1 - (e.clientY - r.top) / r.height));
  }
  function down(e) {
    if (disabled) return;
    dragging = true;
    el.setPointerCapture(e.pointerId);
    oninput(round(at(e)));
  }
  function move(e) {
    if (dragging) oninput(round(at(e)));
  }
  function up(e) {
    if (!dragging) return;
    dragging = false;
    onchange(round(at(e)));
  }
  function key(e) {
    const step = e.shiftKey ? 0.1 : 0.01;
    let v = value;
    if (e.key === "ArrowUp" || e.key === "ArrowRight") v += step;
    else if (e.key === "ArrowDown" || e.key === "ArrowLeft") v -= step;
    else if (e.key === "Home") v = 0;
    else if (e.key === "End") v = 1;
    else return;
    e.preventDefault();
    onchange(round(Math.min(1, Math.max(0, v))));
  }
  const round = (v) => Math.round(v * 100) / 100;
</script>

<div
  class="vs"
  class:disabled
  bind:this={el}
  role="slider"
  tabindex="0"
  aria-label={label}
  aria-valuemin="0"
  aria-valuemax="100"
  aria-valuenow={Math.round(value * 100)}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  onkeydown={key}
>
  <div class="fill" style={`height:${value * 100}%;background:${fill}`}><span class="grip"></span></div>
</div>

<style>
  .vs {
    position: relative; width: min(42vw, 170px); height: min(46vh, 330px); border-radius: 28px; overflow: hidden;
    background: rgba(255, 255, 255, 0.14); border: 1px solid rgba(255, 255, 255, 0.18);
    touch-action: none; cursor: ns-resize; box-shadow: var(--shadow);
  }
  .vs.disabled { opacity: 0.5; cursor: default; }
  .fill { position: absolute; left: 0; right: 0; bottom: 0; transition: height 0.06s linear; }
  .grip { position: absolute; top: 10px; left: 50%; width: 28px; height: 4px; margin-left: -14px; border-radius: 4px; background: rgba(0, 0, 0, 0.45); }
</style>
