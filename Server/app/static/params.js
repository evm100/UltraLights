export function spawnColorPicker(parent, key, value, onChange) {
  const pickerDiv = document.createElement('div');
  pickerDiv.className = 'w-32 h-32';
  parent.appendChild(pickerDiv);
  const input = document.createElement('input');
  input.type = 'hidden';
  input.dataset.paramKey = key;
  parent.appendChild(input);
  const picker = new iro.ColorPicker(pickerDiv, { color: value || '#ffffff', width: 128 });
  const update = () => {
    const { r, g, b } = picker.color.rgb;
    input.value = JSON.stringify([r, g, b]);
    if (onChange) onChange();
  };
  picker.on('color:change', update);
  update();
  return input;
}

export function renderParams(defs, container, onChange) {
  container.innerHTML = '';
  defs.forEach((d, idx) => {
    const key = d.name || idx;
    const wrap = document.createElement('div');
    if (d.label) {
      const lab = document.createElement('label');
      lab.className = 'text-xs opacity-70';
      lab.textContent = d.label;
      wrap.appendChild(lab);
    }
    let input;
    if (d.type === 'color') {
      input = spawnColorPicker(wrap, key, d.value, onChange);
    } else {
      input = document.createElement('input');
      if (d.type === 'slider') {
        input.type = 'range';
        input.min = d.min;
        input.max = d.max;
        input.value = d.value;
        input.addEventListener('input', onChange);
      } else if (d.type === 'toggle') {
        input.type = 'checkbox';
        input.checked = !!d.value;
        input.addEventListener('change', onChange);
      } else {
        input.type = 'number';
        if (d.min !== undefined) input.min = d.min;
        if (d.max !== undefined) input.max = d.max;
        if (d.step !== undefined) input.step = d.step;
        input.value = d.value !== undefined ? d.value : '';
        input.addEventListener('input', onChange);
      }
      input.dataset.paramKey = key;
      wrap.appendChild(input);
    }
    container.appendChild(wrap);
  });
}

export function collectParams(defs, container) {
  const out = [];
  defs.forEach((d, idx) => {
    const key = d.name || idx;
    const input = container.querySelector(`[data-param-key="${key}"]`);
    if (!input) return;
    if (d.type === 'color') {
      const rgb = JSON.parse(input.value);
      out.push(...rgb);
    } else if (d.type === 'slider') {
      out.push(parseInt(input.value, 10));
    } else if (d.type === 'toggle') {
      out.push(input.checked ? 1 : 0);
    } else {
      out.push(parseFloat(input.value));
    }
  });
  return out;
}
