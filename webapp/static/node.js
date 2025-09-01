function initNodePage() {
    const nodeId = document.body.dataset.node;
    const brightnessInput = document.getElementById('brightness');
    const statusDiv = document.getElementById('status');

    function send(cmd, payload) {
        fetch(`/api/nodes/${nodeId}/${cmd}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    }

    document.querySelectorAll('.strip .send').forEach(btn => {
        btn.addEventListener('click', e => {
            const container = e.target.closest('.strip');
            const strip = parseInt(container.dataset.strip);
            const effect = container.querySelector('.effect').value;
            const payload = { strip: strip, effect: effect, brightness: parseInt(brightnessInput.value) };
            if (effect === 'solid') {
                const hex = container.querySelector('.color').value;
                payload.hex = hex;
            }
            send('ws/set', payload);
        });
    });

    document.querySelectorAll('.strip .power').forEach(btn => {
        btn.addEventListener('click', e => {
            const container = e.target.closest('.strip');
            const strip = parseInt(container.dataset.strip);
            const on = btn.dataset.on === 'true';
            send('ws/power', { strip: strip, on: on });
            btn.dataset.on = (!on).toString();
        });
    });

    document.querySelectorAll('.white .send').forEach(btn => {
        btn.addEventListener('click', e => {
            const container = e.target.closest('.white');
            const channel = parseInt(container.dataset.channel);
            const effect = container.querySelector('.effect').value;
            const payload = { channel: channel, effect: effect, brightness: parseInt(brightnessInput.value) };
            send('white/set', payload);
        });
    });

    document.querySelectorAll('.sensor .set-cooldown').forEach(btn => {
        btn.addEventListener('click', e => {
            const container = e.target.closest('.sensor');
            const seconds = parseInt(container.querySelector('.cooldown').value);
            send('sensor/cooldown', { seconds: seconds });
        });
    });

    document.getElementById('ota').addEventListener('click', () => {
        send('ota/check', {});
    });

    async function pollStatus() {
        const res = await fetch(`/api/nodes/${nodeId}/status`);
        const data = await res.json();
        if (data.connected) {
            statusDiv.textContent = 'Connected';
            statusDiv.className = 'status connected';
        } else {
            statusDiv.textContent = 'Disconnected';
            statusDiv.className = 'status disconnected';
        }
    }

    setInterval(pollStatus, 3000);
    pollStatus();
}
