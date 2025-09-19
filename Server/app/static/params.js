function clampColorChannel(value) {
  const num = Number(value);
  if (Number.isNaN(num)) return 0;
  if (!Number.isFinite(num)) return 0;
  if (num <= 0) return 0;
  if (num >= 255) return 255;
  return Math.round(num);
}

export function spawnColorPicker(parent, key, value, onChange, initialValues) {
  const pickerDiv = document.createElement('div');
  pickerDiv.className = 'w-32 h-32';
  parent.appendChild(pickerDiv);
  const input = document.createElement('input');
  input.type = 'hidden';
  input.dataset.paramKey = key;
  parent.appendChild(input);
  let initial = null;
  if (Array.isArray(initialValues) && initialValues.length >= 3) {
    initial = initialValues.slice(0, 3).map(clampColorChannel);
  }
  if (!initial && Array.isArray(value) && value.length >= 3) {
    initial = value.slice(0, 3).map(clampColorChannel);
  }
  const colorOption = initial
    ? { r: initial[0], g: initial[1], b: initial[2] }
    : value || '#ffffff';
  const picker = new iro.ColorPicker(pickerDiv, { color: colorOption, width: 128 });
  const suppressInitial = Array.isArray(initialValues) && initialValues.length > 0;
  let skipNotify = suppressInitial;
  const update = () => {
    const { r, g, b } = picker.color.rgb;
    input.value = JSON.stringify([r, g, b]);
    if (!skipNotify && onChange) onChange();
    skipNotify = false;
  };
  picker.on('color:change', update);
  update();
  return input;
}

export function renderParams(defs, container, onChange, initialValues) {
  container.innerHTML = '';
  let valueIndex = 0;
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
    let initial;
    if (Array.isArray(initialValues)) {
      if (d.type === 'color') {
        const slice = initialValues.slice(valueIndex, valueIndex + 3);
        valueIndex += 3;
        if (slice.length === 3) {
          initial = slice;
        }
      } else {
        initial = initialValues[valueIndex];
        valueIndex += 1;
      }
    }
    if (d.type === 'color') {
      input = spawnColorPicker(wrap, key, d.value, onChange, initial);
    } else {
      input = document.createElement('input');
      if (d.type === 'slider') {
        input.type = 'range';
        input.min = d.min;
        input.max = d.max;
        const val = initial !== undefined && initial !== null ? Number(initial) : d.value;
        if (val !== undefined && val !== null && !Number.isNaN(Number(val))) {
          input.value = String(val);
        } else {
          input.value = d.value !== undefined ? d.value : '';
        }
        input.addEventListener('input', onChange);
      } else if (d.type === 'toggle') {
        input.type = 'checkbox';
        if (initial !== undefined && initial !== null) {
          input.checked = Boolean(initial);
        } else {
          input.checked = !!d.value;
        }
        input.addEventListener('change', onChange);
      } else {
        input.type = 'number';
        if (d.min !== undefined) input.min = d.min;
        if (d.max !== undefined) input.max = d.max;
        if (d.step !== undefined) input.step = d.step;
        const val = initial !== undefined && initial !== null ? Number(initial) : d.value;
        if (val !== undefined && val !== null && !Number.isNaN(Number(val))) {
          input.value = String(val);
        } else {
          input.value = d.value !== undefined ? d.value : '';
        }
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
