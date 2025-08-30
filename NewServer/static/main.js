const stripConfigs = {};

function sendJSON(url, data) {
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
}

function renderParams(card) {
  const effect = card.querySelector('.ws-effect').value;
  const paramsDiv = card.querySelector('.ws-params');
  paramsDiv.innerHTML = '';
  if (effect === 'solid') {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = '#ffffff';
    input.className = 'form-control form-control-color ws-color';
    paramsDiv.appendChild(input);
  } else if (effect === 'triple_wave') {
    for (let i = 0; i < 3; i++) {
      const row = document.createElement('div');
      row.className = 'row g-2 align-items-center mb-2';
      row.innerHTML = `
        <div class="col"><input type="color" class="form-control form-control-color wave-color" value="#ff0000"></div>
        <div class="col"><input type="number" step="0.1" class="form-control wave-freq" placeholder="freq"></div>
        <div class="col"><input type="number" step="0.1" class="form-control wave-vel" placeholder="velocity"></div>`;
      paramsDiv.appendChild(row);
    }
  }
}

function initWSCard(card) {
  const strip = Number(card.dataset.strip);
  stripConfigs[strip] = { strip, effect: 'solid', hex: '#ffffff' };
  card.querySelector('.ws-effect').addEventListener('change', () => renderParams(card));
  renderParams(card);

  card.querySelector('.ws-apply').addEventListener('click', () => {
    const effect = card.querySelector('.ws-effect').value;
    const brightness = Number(card.querySelector('.ws-brightness').value);
    const payload = { strip, effect, brightness };
    if (effect === 'solid') {
      payload.hex = card.querySelector('.ws-color').value;
    } else if (effect === 'triple_wave') {
      const waves = [];
      card.querySelectorAll('.ws-params .row').forEach(row => {
        const hex = row.querySelector('.wave-color').value;
        const freq = parseFloat(row.querySelector('.wave-freq').value) || 0;
        const velocity = parseFloat(row.querySelector('.wave-vel').value) || 0;
        waves.push({ hex, freq, velocity });
      });
      payload.waves = waves;
    }
    stripConfigs[strip] = payload;
    sendJSON('/api/ws/set', payload);
  });

  card.querySelector('.ws-power-on').addEventListener('click', () => {
    sendJSON('/api/ws/power', { strip, on: true });
  });
  card.querySelector('.ws-power-off').addEventListener('click', () => {
    sendJSON('/api/ws/power', { strip, on: false });
  });
}

document.querySelectorAll('#ws-strips .card-body').forEach(initWSCard);

function initWhiteCard(card) {
  const channel = Number(card.dataset.channel);
  card.querySelector('.white-apply').addEventListener('click', () => {
    const effect = card.querySelector('.white-effect').value;
    const brightness = Number(card.querySelector('.white-brightness').value);
    sendJSON('/api/white/set', { channel, effect, brightness });
  });
  card.querySelector('.white-power-on').addEventListener('click', () => {
    sendJSON('/api/white/power', { channel, on: true });
  });
  card.querySelector('.white-power-off').addEventListener('click', () => {
    sendJSON('/api/white/power', { channel, on: false });
  });
}

document.querySelectorAll('#white-channels .card-body').forEach(initWhiteCard);

// OTA check
const otaBtn = document.getElementById('ota-check');
otaBtn.addEventListener('click', () => {
  sendJSON('/api/ota/check', {});
});

// Master brightness
const master = document.getElementById('master-brightness');
master.addEventListener('input', () => {
  const b = Number(master.value);
  Object.values(stripConfigs).forEach(cfg => {
    const payload = { ...cfg, brightness: b };
    sendJSON('/api/ws/set', payload);
  });
});
