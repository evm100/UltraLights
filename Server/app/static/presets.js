const container = document.getElementById('presetList');
if (container) {
  const statusEl = document.getElementById('presetStatus');
  const saveButton = document.getElementById('presetSaveButton');
  const editButton = document.getElementById('presetEditButton');
  const baseUrl = container.dataset.apiBase || '';
  const presetsUrl = baseUrl ? `${baseUrl}/presets` : '';
  const reorderUrl = presetsUrl ? `${presetsUrl}/reorder` : '';
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
  let editing = false;
  let orderDirty = false;

  const ordersEqual = (a, b) => {
    if (!Array.isArray(a) || !Array.isArray(b)) {
      return false;
    }
    if (a.length !== b.length) {
      return false;
    }
    for (let index = 0; index < a.length; index += 1) {
      if (a[index] !== b[index]) {
        return false;
      }
    }
    return true;
  };

  const currentOrder = () => presets.map((preset) => preset.id);
  let lastKnownOrder = currentOrder();

  const dragState = { id: null, index: -1 };
  let currentDropTarget = null;
  let currentDropPosition = null;

  const resetDragState = () => {
    dragState.id = null;
    dragState.index = -1;
  };

  const clearDropIndicators = () => {
    if (currentDropTarget) {
      currentDropTarget.classList.remove('drop-target-before', 'drop-target-after');
    }
    currentDropTarget = null;
    currentDropPosition = null;
  };

  const setDropIndicator = (target, position) => {
    if (currentDropTarget === target && currentDropPosition === position) {
      return;
    }
    clearDropIndicators();
    if (target && position) {
      const cls = position === 'after' ? 'drop-target-after' : 'drop-target-before';
      target.classList.add(cls);
      currentDropTarget = target;
      currentDropPosition = position;
    }
  };

  const findPresetIndex = (id) => presets.findIndex((entry) => entry.id === id);

  const dropPositionForEvent = (event, element) => {
    if (!element) return 'after';
    const rect = element.getBoundingClientRect();
    const horizontal = rect.width >= rect.height;
    if (horizontal) {
      return event.clientX - rect.left > rect.width / 2 ? 'after' : 'before';
    }
    return event.clientY - rect.top > rect.height / 2 ? 'after' : 'before';
  };

  const movePreset = (fromIndex, toIndex) => {
    if (fromIndex < 0 || fromIndex >= presets.length) {
      return false;
    }
    if (toIndex < 0) {
      toIndex = 0;
    }
    if (toIndex > presets.length) {
      toIndex = presets.length;
    }
    if (fromIndex === toIndex || (fromIndex + 1 === toIndex && fromIndex < toIndex)) {
      return false;
    }
    const [moved] = presets.splice(fromIndex, 1);
    let insertIndex = toIndex;
    if (fromIndex < toIndex) {
      insertIndex -= 1;
    }
    if (insertIndex < 0) {
      insertIndex = 0;
    }
    if (insertIndex > presets.length) {
      insertIndex = presets.length;
    }
    presets.splice(insertIndex, 0, moved);
    return insertIndex !== fromIndex;
  };

  const updateEditingUI = () => {
    container.dataset.editing = editing ? 'true' : 'false';
    if (editing) {
      container.classList.add('is-editing');
    } else {
      container.classList.remove('is-editing');
    }
    const deleteButtons = container.querySelectorAll('button.preset-delete');
    deleteButtons.forEach((button) => {
      button.hidden = !editing;
      if (editing) {
        button.removeAttribute('aria-hidden');
        button.removeAttribute('tabindex');
      } else {
        button.setAttribute('aria-hidden', 'true');
        button.setAttribute('tabindex', '-1');
      }
    });
    const presetItems = container.querySelectorAll('.preset-item');
    presetItems.forEach((item) => {
      if (editing) {
        item.setAttribute('draggable', 'true');
        item.classList.add('is-draggable');
      } else {
        item.removeAttribute('draggable');
        item.classList.remove('is-draggable', 'is-dragging', 'drop-target-before', 'drop-target-after');
      }
    });
    if (!editing) {
      clearDropIndicators();
      resetDragState();
    }
    if (editButton) {
      editButton.textContent = editing ? 'Done' : 'Edit';
      editButton.setAttribute('aria-pressed', editing ? 'true' : 'false');
      editButton.title = editing ? 'Finish editing presets' : 'Edit presets';
    }
  };

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
      button.className = 'preset preset-button glow-button rounded-lg';
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
        removeButton.hidden = !editing;
        if (editing) {
          removeButton.removeAttribute('aria-hidden');
          removeButton.removeAttribute('tabindex');
        } else {
          removeButton.setAttribute('aria-hidden', 'true');
          removeButton.setAttribute('tabindex', '-1');
        }
        wrapper.appendChild(removeButton);
      }
      fragment.appendChild(wrapper);
    });
    container.appendChild(fragment);
  };

  const fetchJson = async (url, options = {}) => {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
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
      updateEditingUI();
      lastKnownOrder = currentOrder();
      orderDirty = false;
      sharePresets(presets);
    }
  };

  container.addEventListener('dragstart', (event) => {
    const item = event.target.closest('.preset-item');
    if (!item) {
      return;
    }
    if (!editing) {
      event.preventDefault();
      return;
    }
    const id = item.dataset.presetId;
    if (!id) {
      event.preventDefault();
      return;
    }
    const index = findPresetIndex(id);
    if (index === -1) {
      event.preventDefault();
      return;
    }
    dragState.id = id;
    dragState.index = index;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', id);
    }
    window.requestAnimationFrame(() => {
      item.classList.add('is-dragging');
    });
  });

  container.addEventListener('dragend', (event) => {
    const item = event.target.closest('.preset-item');
    if (item) {
      item.classList.remove('is-dragging');
    }
    clearDropIndicators();
    resetDragState();
  });

  container.addEventListener('dragover', (event) => {
    if (!editing || !dragState.id) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
    const item = event.target.closest('.preset-item');
    if (!item || item.dataset.presetId === dragState.id) {
      clearDropIndicators();
      return;
    }
    const position = dropPositionForEvent(event, item);
    setDropIndicator(item, position);
  });

  container.addEventListener('dragleave', (event) => {
    if (!editing || !dragState.id) {
      return;
    }
    const related = event.relatedTarget;
    if (!related || !container.contains(related)) {
      clearDropIndicators();
    }
  });

  container.addEventListener('drop', (event) => {
    if (!editing || !dragState.id) {
      return;
    }
    event.preventDefault();
    const fromIndex = dragState.index;
    if (fromIndex === -1) {
      clearDropIndicators();
      resetDragState();
      return;
    }
    const activeItem = container.querySelector('.preset-item.is-dragging');
    if (activeItem) {
      activeItem.classList.remove('is-dragging');
    }
    const target = event.target.closest('.preset-item');
    let toIndex = presets.length;
    if (target) {
      const targetId = target.dataset.presetId;
      const targetIndex = targetId ? findPresetIndex(targetId) : -1;
      if (targetIndex !== -1) {
        const position = dropPositionForEvent(event, target);
        toIndex = position === 'after' ? targetIndex + 1 : targetIndex;
      }
    }
    const moved = movePreset(fromIndex, toIndex);
    clearDropIndicators();
    resetDragState();
    if (moved) {
      renderPresets();
      updateEditingUI();
      orderDirty = !ordersEqual(currentOrder(), lastKnownOrder);
      sharePresets(presets);
    }
  });

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

  const persistPresetOrder = async () => {
    const order = currentOrder();
    if (order.length < 2) {
      orderDirty = false;
      lastKnownOrder = order.slice();
      return true;
    }

    const hasChanges = !ordersEqual(order, lastKnownOrder);
    orderDirty = hasChanges;
    if (!hasChanges) {
      return true;
    }

    if (!reorderUrl) {
      setStatus('Saving preset order is not available.', 'error');
      return false;
    }
    
    try {
      if (editButton) {
        editButton.disabled = true;
      }
      setStatus('Saving preset order...', 'info');
      const data = await fetchJson(reorderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order }),
      });
      updateFromResponse(data);
      lastKnownOrder = currentOrder();
      orderDirty = false;
      setStatus('Preset order saved ✓', 'success', true);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update preset order.';
      setStatus(`Failed to update preset order: ${message}`, 'error');
      return false;
    } finally {
      if (editButton) {
        editButton.disabled = false;
      }
    }
  };

  container.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('button.preset-delete');
    if (deleteButton) {
      if (!editing) {
        return;
      }
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

  if (editButton) {
    editButton.addEventListener('click', async (event) => {
      event.preventDefault();
      if (editing) {
        editing = false;
        updateEditingUI();
        const saved = await persistPresetOrder();
        if (!saved) {
          editing = true;
          updateEditingUI();
        }
      } else {
        editing = true;
        updateEditingUI();
      }
    });
  }

  if (saveButton) {
    saveButton.addEventListener('click', (event) => {
      event.preventDefault();
      handleSave();
    });
  }

  renderPresets();
  updateEditingUI();
  setStatus('', 'base');
  sharePresets(presets);
}
