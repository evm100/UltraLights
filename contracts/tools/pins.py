"""Reference pin-rule check for a board descriptor, using chips.json.

The node builder (web) and ul_board (firmware) implement the same rules and
are tested against contracts/examples/board-rules/*.json, whose expected
results are listed in each file's "_expect".
"""
import json
from pathlib import Path

SERVO_HZ = 50  # below the pwm minimum (100 Hz), so it never shares a timer with a light

CHIPS = json.loads((Path(__file__).resolve().parent.parent / "chips.json").read_text())


def check_board(board: dict) -> dict:
    """Return {"errors": [...], "warnings": [...]} as stable codes "<code>:<detail>"."""
    chip = CHIPS[board["chip"]]
    errors, warnings = [], []
    used = {}

    def claim(gpio, who, is_reset=False):
        g = str(gpio)
        if gpio > chip["gpio_max"]:
            errors.append(f"no_such_pin:{gpio}")
        elif g in chip["unusable"]:
            errors.append(f"unusable_pin:{gpio}")
        elif g in chip["caution"] and not (is_reset and gpio == chip["boot_button"]):
            warnings.append(f"caution_pin:{gpio}")
        if gpio in used:
            errors.append(f"pin_conflict:{gpio}")
        used[gpio] = who

    ids = set()
    pwm, argb, freqs = 0, 0, set()
    for ch in board["channels"]:
        if ch["id"] in ids:
            errors.append(f"duplicate_id:{ch['id']}")
        ids.add(ch["id"])
        claim(ch["gpio"], ch["id"])
        if ch["type"] == "pwm":
            pwm += 1
            freqs.add(ch.get("hz", 19000))
        elif ch["type"] == "servo":
            # Servos share LEDC with PWM: a channel each, and one 50 Hz timer between them.
            pwm += 1
            freqs.add(SERVO_HZ)
        elif ch["type"] == "argb":
            argb += 1
    if "reset_button" in board:
        claim(board["reset_button"]["gpio"], "reset_button", is_reset=True)
    else:
        warnings.append("no_reset_button:")
    if "status_led" in board:
        claim(board["status_led"]["gpio"], "status_led")

    lim = chip["limits"]
    if pwm > lim["pwm"]:
        errors.append(f"too_many_pwm:{pwm}")
    if len(freqs) > lim["pwm_freqs"]:
        errors.append(f"too_many_pwm_freqs:{len(freqs)}")
    if argb > lim["argb"]:
        errors.append(f"too_many_argb:{argb}")
    return {"errors": sorted(set(errors)), "warnings": sorted(set(warnings))}
