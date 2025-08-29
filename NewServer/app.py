import json
import os

from flask import Flask, render_template, request, jsonify
import paho.mqtt.client as mqtt

MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
NODE_ID = os.getenv("ULTRALIGHT_NODE", "node")

client = mqtt.Client()
client.connect(MQTT_BROKER)
client.loop_start()

app = Flask(__name__)


def publish(topic, payload):
    client.publish(f"ul/{NODE_ID}/{topic}", json.dumps(payload), qos=1)


@app.route("/")
def index():
    ws_effects = [
        "solid",
        "triple_wave",
        "breathe",
        "rainbow",
        "twinkle",
        "theater_chase",
        "wipe",
        "gradient_scroll",
    ]
    white_effects = [
        "graceful_on",
        "graceful_off",
        "motion_swell",
        "day_night_curve",
        "blink",
    ]
    return render_template(
        "index.html", effects_ws=ws_effects, effects_white=white_effects
    )


@app.route("/api/ws/set", methods=["POST"])
def api_ws_set():
    publish("cmd/ws/set", request.json)
    return jsonify(status="ok")


@app.route("/api/ws/power", methods=["POST"])
def api_ws_power():
    publish("cmd/ws/power", request.json)
    return jsonify(status="ok")


@app.route("/api/white/set", methods=["POST"])
def api_white_set():
    publish("cmd/white/set", request.json)
    return jsonify(status="ok")


@app.route("/api/white/power", methods=["POST"])
def api_white_power():
    publish("cmd/white/power", request.json)
    return jsonify(status="ok")


@app.route("/api/ota/check", methods=["POST"])
def api_ota_check():
    publish("cmd/ota/check", {})
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
