# Effect Parameters and Utilities

This server exposes lighting effects for addressable LED strips and white channels. To avoid duplicating UI logic, the parameter form code is shared across modules.

## Parameter utilities

`Server/app/static/params.js` exports helpers used by the templates:

- `spawnColorPicker(parent, key, value, onChange)` – insert an [`iro.js`](https://iro.js.org) colour picker bound to a `data-param-key` and keep the selected RGB triplet (as JSON) in a hidden `<input>`.
- `renderParams(defs, container, onChange)` – create form controls from an array of effect descriptors. Each descriptor specifies the `type`, optional `label`, and settings such as `min`, `max`, or `value`. Inputs are tagged with a `data-param-key` derived from the descriptor name or index and wired to the `onChange` callback so callers can throttle MQTT messages.
- `collectParams(defs, container)` – look up elements by `data-param-key`, reading numbers directly or expanding stored RGB arrays into the positional parameter list expected by the firmware.

`ws.html` and `white.html` import these utilities with

```html
<script type="module">
import { renderParams, collectParams } from '/static/params.js';
```

This removes duplicate input handling code.

Templates that expose colour parameters (such as `ws.html`) must include the `iro.js` script before importing `params.js`. Modules that only use numeric inputs, like `white.html`, can omit the extra script:

```html
<script src="https://cdn.jsdelivr.net/npm/@jaames/iro@5"></script>
<script type="module" src="/static/params.js"></script>
```

### Descriptor types

Effect descriptors support these `type` values:

- `color` – choose an RGB colour using `iro.js`; the triplet is flattened into the `params` array.
- `slider` – range input producing an integer value.
- `number` – numeric input for floats or free‑form numbers.
- `toggle` – checkbox that appends `1` when checked or `0` when cleared.

## Adding a new effect

1. **Implement the effect in firmware** and register it in the appropriate `registry.c` so that `effects.py` detects its name.
2. **Describe its parameters** in `Server/app/effects.py` by adding an entry to `WS_PARAM_DEFS` or `WHITE_PARAM_DEFS`. Each descriptor corresponds to one positional argument sent over MQTT.
3. The web UI will automatically list the new effect and render the controls using `renderParams`. Use `collectParams` when constructing the message body to obtain the values.

Example descriptor:

```python
WS_PARAM_DEFS["sparkle"] = [
    {"type": "color", "label": "Sparkle Color"},
    {"type": "slider", "label": "Density", "min": 1, "max": 100, "value": 10},
]
```

After reloading the page, the "sparkle" effect appears with a colour picker and slider. No additional template changes are required.
