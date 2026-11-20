# ul_argb tests

No host tests yet. Color math (curve, sRGB to linear, max_level) is tested in
`ul_core/test`.

On-target checks still to write:
- with two strips, channel 0 gets 1 RMT block and channel 1 gets 3 (watch the boot log);
- logic analyser on the data line: bit timings per chipset, reset gap of 300 us or more,
  and no gaps over 50 us mid-frame while Wi-Fi is busy and NVS is being written;
- GRB/RGBW byte order on real strips; pixels past `pixels` stay dark.
