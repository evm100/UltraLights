/* The setup page. %-placeholders are filled with snprintf in ul_provision.c, in this order:
 * notice block (may be ""), current DNS value, device id, board line, firmware version, MAC.
 * Every value is HTML-escaped first. */
#pragma once

static const char UL_SETUP_PAGE[] =
"<!doctype html><html lang=en><head><meta charset=utf-8>"
"<meta name=viewport content='width=device-width,initial-scale=1'>"
"<title>UltraLights setup</title><style>"
"body{font:16px system-ui,sans-serif;margin:0;background:#101418;color:#e8edf2}"
"main{max-width:420px;margin:0 auto;padding:24px 16px}"
"h1{font-size:22px;margin:0 0 4px}p{color:#9aa7b4;margin:0 0 20px;font-size:14px}"
"label{display:block;font-size:13px;color:#9aa7b4;margin:16px 0 6px}"
"input,select{width:100%%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2c3640;"
"background:#18202a;color:#e8edf2;font-size:16px}"
"button{margin-top:24px;width:100%%;padding:14px;border:0;border-radius:10px;background:#f0b24a;"
"color:#101418;font-size:16px;font-weight:600}"
".info{margin-top:28px;font-size:12px;color:#6f7d8b;line-height:1.6}"
".warn{background:#3a2a12;color:#f0c27a;padding:10px 12px;border-radius:10px;font-size:13px}"
"</style></head><body><main>"
"<h1>UltraLights setup</h1><p>Connect this node to your Wi-Fi.</p>"
"%s"
"<form method=post action=/save>"
"<label for=net>Wi-Fi network</label>"
"<select id=net onchange=\"document.getElementById('ssid').value=this.value\"><option value=''>Scanning...</option></select>"
"<input id=ssid name=ssid maxlength=32 required placeholder='or type the network name' style='margin-top:8px'>"
"<label for=psk>Password</label><input id=psk name=psk type=password maxlength=64 placeholder='leave empty for an open network'>"
"<label for=dns>DNS server (optional)</label><input id=dns name=dns inputmode=decimal maxlength=15 value='%s' placeholder='e.g. 192.168.1.99'>"
"<button type=submit>Save and connect</button></form>"
"<div class=info>Node %s<br>Board %s<br>Firmware %s<br>MAC %s</div>"
"</main><script>"
"fetch('/scan').then(r=>r.json()).then(l=>{var s=document.getElementById('net');s.innerHTML='<option value=\"\">Choose a network</option>';"
"l.forEach(n=>{var o=document.createElement('option');o.value=n.ssid;o.textContent=n.ssid+(n.secure?' \\u{1F512}':'')+' ('+n.rssi+' dBm)';s.appendChild(o)})})"
".catch(()=>{document.getElementById('net').innerHTML='<option value=\"\">Scan failed, type the name below</option>'})"
"</script></body></html>";

static const char UL_SAVED_PAGE[] =
"<!doctype html><html lang=en><head><meta charset=utf-8>"
"<meta name=viewport content='width=device-width,initial-scale=1'><title>Saved</title>"
"<style>body{font:16px system-ui,sans-serif;background:#101418;color:#e8edf2;margin:0}"
"main{max-width:420px;margin:0 auto;padding:24px 16px}p{color:#9aa7b4}</style></head><body><main>"
"<h1>Saved</h1><p>The node restarts and joins <b>%s</b>. This setup network will disappear; "
"reconnect your phone to your usual Wi-Fi.</p></main></body></html>";
