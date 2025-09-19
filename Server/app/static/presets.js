const container = document.getElementById('presetList');
if (container) {
  const statusEl = document.getElementById('presetStatus');
  const saveButton = document.getElementById('presetSaveButton');
  const baseUrl = container.dataset.apiBase || '';
  const presetsUrl = baseUrl ? `${baseUrl}/presets` : '';
  const applyUrlFor = (id) => `${baseUrl}/preset/${encodeURIComponent(id)}`;
  let statusTimer = null;

  const statusClasses = {
    base: 'text-sm mb-3 opacity-80',
    info: 'text-sm mb-3 text-slate-200 opacity-80',
    success: 'text-sm mb-3 text-emerald-300',
    error: 'text-sm mb-3 text-rose-300',
  };

  const setStatus = (message, tone = 'base', autoClear = false) => {
    if (!statusEl) return;
    if (statusTimer) {
      window.clearTimeout(statusTimer);
      statusTimer = null;
    }
    if (!message) {
      statusEl.textContent = '';
      statusEl.className = statusClasses.base;
      return;
    }
    const cls = statusClasses[tone] || statusClasses.base;
    statusEl.className = cls;
    statusEl.textContent = message;
    if (autoClear) {
      statusTimer = window.setTimeout(() => {
        statusEl.textContent = '';
        statusEl.className = statusClasses.base;
        statusTimer = null;
      }, 2400);
    }
  };

  const normalizePresets = (list) => {
    if (!Array.isArray(list)) {
      return [];
    }
    const normalized = [];
    list.forEach((preset) => {
      if (!preset || preset.id === undefined || preset.id === null) {
        return;
      }
      const id = String(preset.id);
      const nameValue = preset.name;
      const name =
        nameValue === undefined || nameValue === null || nameValue === ''
          ? id
          : String(nameValue);
      const source = preset.source ? String(preset.source) : '';
      normalized.push({ ...preset, id, name, source });
    });
    return normalized;
  };

  const sharePresets = (list) => {
    const copy = list.map((preset) => ({ ...preset }));
    window.UltraLights = window.UltraLights || {};
    window.UltraLights.presets = copy;
    window.setTimeout(() => {
      document.dispatchEvent(
        new CustomEvent('ultralights:presets-changed', { detail: { presets: copy } }),
      );
    }, 0);
  };

  let initialRaw = [];
  if (container.dataset.initialPresets) {
    try {
      initialRaw = JSON.parse(container.dataset.initialPresets);
    } catch (err) {
      initialRaw = [];
    }
  }
  const initialPresets = normalizePresets(initialRaw);
  let presets = initialPresets;

  const isCustomPreset = (preset) => preset && String(preset.source || '').toLowerCase() === 'custom';

  const renderPresets = () => {
    container.innerHTML = '';
    if (!presets.length) {
      const empty = document.createElement('div');
      empty.className = 'opacity-60';
      empty.textContent = 'No presets configured.';
      container.appendChild(empty);
      return;
    }
    const fragment = document.createDocumentFragment();
    presets.forEach((preset) => {
      const id = preset.id;
      const name = preset.name || id;
      const wrapper = document.createElement('div');
      wrapper.className = 'preset-item';
      wrapper.dataset.presetId = id;
      wrapper.dataset.custom = isCustomPreset(preset) ? 'true' : 'false';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'preset preset-button glass px-4 py-2 rounded-lg hover:ring-2 hover:ring-indigo-400';
      button.dataset.presetId = id;
      button.dataset.custom = wrapper.dataset.custom;
      button.textContent = name;
      button.title = `Apply preset ${name}`;
      wrapper.appendChild(button);
      if (wrapper.dataset.custom === 'true') {
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'preset-delete';
        removeButton.dataset.presetId = id;
        removeButton.setAttribute('aria-label', `Delete preset ${name}`);
        removeButton.textContent = '✕';
        wrapper.appendChild(removeButton);
      }
      fragment.appendChild(wrapper);
    });
    container.appendChild(fragment);
  };

  const fetchJson = async (url, options) => {
    const response = await fetch(url, options);
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      const detail =
        data && (data.detail || data.error || data.message || data.reason);
      const message = detail ? String(detail) : `Request failed (${response.status})`;
      throw new Error(message);
    }
    return data;
  };

  const updateFromResponse = (data) => {
    if (data && Array.isArray(data.presets)) {
      presets = normalizePresets(data.presets);
      renderPresets();
      sharePresets(presets);
    }
  };

  const handleApply = async (id, button) => {
    if (!id || !baseUrl) {
      setStatus('Unable to apply preset at this time.', 'error');
      return;
    }
    try {
      if (button) button.disabled = true;
      setStatus('Applying preset...', 'info');
      await fetchJson(applyUrlFor(id), { method: 'POST' });
      setStatus('Preset applied ✓', 'success', true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to apply preset.';
      setStatus(`Failed to apply preset: ${message}`, 'error');
    } finally {
      if (button) button.disabled = false;
    }
  };

  const handleSave = async () => {
    if (!presetsUrl) {
      setStatus('Saving presets is not available.', 'error');
      return;
    }
    const rawName = window.prompt('Save preset as:');
    if (rawName === null) {
      return;
    }
    const name = String(rawName).trim();
    if (!name) {
      setStatus('Preset name is required.', 'error');
      return;
    }
    try {
      if (saveButton) saveButton.disabled = true;
      setStatus('Saving preset...', 'info');
      const data = await fetchJson(presetsUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      updateFromResponse(data);
      setStatus('Preset saved ✓', 'success', true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save preset.';
      setStatus(`Failed to save preset: ${message}`, 'error');
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  };

  const handleDelete = async (id, button) => {
    if (!id || !presetsUrl) {
      setStatus('Unable to delete preset.', 'error');
      return;
    }
    const preset = presets.find((entry) => entry.id === id);
    const label = preset ? preset.name : id;
    if (!window.confirm(`Delete preset "${label}"?`)) {
      return;
    }
    try {
      if (button) button.disabled = true;
      setStatus('Deleting preset...', 'info');
      const url = `${presetsUrl}?preset_id=${encodeURIComponent(id)}`;
      const data = await fetchJson(url, { method: 'DELETE' });
      updateFromResponse(data);
      setStatus('Preset deleted ✓', 'success', true);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete preset.';
      setStatus(`Failed to delete preset: ${message}`, 'error');
    } finally {
      if (button) button.disabled = false;
    }
  };

  container.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('button.preset-delete');
    if (deleteButton) {
      event.preventDefault();
      event.stopPropagation();
      const id = deleteButton.dataset.presetId;
      if (id) {
        handleDelete(id, deleteButton);
      }
      return;
    }
    const presetButton = event.target.closest('button.preset-button');
    if (presetButton) {
      const id = presetButton.dataset.presetId;
      if (id) {
        handleApply(id, presetButton);
      }
    }
  });

  if (saveButton) {
    saveButton.addEventListener('click', (event) => {
      event.preventDefault();
      handleSave();
    });
  }

  renderPresets();
  setStatus('', 'base');
  sharePresets(presets);
}
