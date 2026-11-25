"""Level and color math. Same meaning as on a node: level is perceived brightness (CIE 1931
lightness), color is sRGB at full brightness, and the device turns both into raw channel values."""

RGB = tuple[int, int, int]
FRGB = tuple[float, float, float]
BLACK: FRGB = (0.0, 0.0, 0.0)


def cie(level: float) -> float:
    """Perceived lightness 0..1 to relative luminance 0..1."""
    lum = max(0.0, min(1.0, level)) * 100.0
    return lum / 903.3 if lum <= 8.0 else ((lum + 16.0) / 116.0) ** 3


def target(on: bool, level: float, color: RGB) -> FRGB:
    """What the LEDs should show, before rounding."""
    if not on or level <= 0:
        return BLACK
    y = cie(level)
    return (color[0] * y, color[1] * y, color[2] * y)


def lerp(a: FRGB, b: FRGB, f: float) -> FRGB:
    f = max(0.0, min(1.0, f))
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def to_rgb(c: FRGB) -> RGB:
    """Round to 8 bits. A lit color never rounds to black: its brightest channel keeps 1."""
    r = (round(c[0]), round(c[1]), round(c[2]))
    if r == (0, 0, 0) and max(c) > 0.05:
        i = max(range(3), key=lambda k: c[k])
        r = tuple(1 if k == i else 0 for k in range(3))  # type: ignore[assignment]
    return (min(255, r[0]), min(255, r[1]), min(255, r[2]))
