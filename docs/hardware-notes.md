# ESP32-C6 LED Node – Voice Session Handoff

Summary of decisions and verified findings from a voice session, for the main development session. Sources: ESP32-C6 datasheet, ESP32-C6-WROOM-1/1U datasheet, ESP32-C6 TRM v1.2 (ch. 37 RMT), ESP-IDF soc\_caps.h (release/v5.3).

## Module choice

- Module: **ESP32-C6-WROOM-1** (PCB antenna) or **-1U** (external antenna). Variants N4/N8/N16 = 4/8/16 MB quad SPI flash. There is no C6 WROVER.
- **No PSRAM** on C6 (confirmed). Internal: 512 KB HP SRAM, 16 KB LP SRAM.
- Module exposes 23 GPIOs (IO0–IO13, IO15–IO23); GPIO14 not exposed. Flash is internal, costs no pins.
- Strapping pins: GPIO8, GPIO9, GPIO15. USB D-/D+ on IO12/IO13. UART0 on GPIO16/17. Realistic free pin budget ≈ 17.
- Peripheral signals route through the GPIO matrix, so strip data pins can be switched between RMT / SPI / PARLIO in firmware without a re-spin.

## PWM

- **LEDC**: 6 channels, 4 timers (20-bit). Max 4 distinct frequencies across the 6 channels. Use for LED dimming.
- **MCPWM**: 1 group, 3 timers, 3 operators, 6 outputs (complementary pairs with dead time), 3 capture inputs, fault inputs. Spare PWM, or motor drive (3 half-bridges = BLDC).
- Relays / solenoids: plain GPIO + low-side MOSFET. Add optional flyback-diode footprint per channel so PWM channels can double as coil drivers.

## Addressable strips (decision: 2 ARGB outputs per node, down from 3)

- RMT: 4 channels; ch0–1 are TX, ch2–3 are RX. No RMT DMA on C6.
- RMT RAM: 192 x 32-bit words, four 48-word blocks. One word = one WS2812 bit.
- Borrowing (TRM 37.3.2.2): a channel can only use blocks of **higher-numbered** channels; max RMT\_MEM\_SIZE for TX ch n is (4 − n).
- **Plan**: ch0 = 1 block (48 words), ch1 = 3 blocks (144 words, takes RX ch2/ch3 RAM). Both TX channels stay usable.
- Refill deadline (half-buffer ping-pong): \~30 µs at 48 words, \~90 µs at 144 words. WS2812 reset threshold: \~50 µs older parts, \~280 µs newer parts.
- Arduino-ESP32 only exposes 1–2 blocks on C6; use ESP-IDF directly for 3 blocks. Verify allocation on hardware.
- Enable RMT **ISR IRAM-safe** option (flash writes disable cache and would stall a flash-resident ISR). Free to enable.
- Fallback: SPI2 (only free SPI) has GDMA, which streams a full frame with no refill interrupts. Costs 3–4 SPI bits per LED bit in RAM. PARLIO (1/2/4/8/16-bit, GDMA) is another option; driver support for LED strips is unverified.
- Frame rate (30 µs per LED + reset): \~300 LEDs ≈ 100 fps, \~500 ≈ 65 fps, \~1000 ≈ 33 fps. Each RMT channel runs in parallel.

## Networking architecture

- Home: plain Wi-Fi per node (few nodes per room). Build this first.
- Larger installs (e.g. cousin's restaurant): ESP-NOW mesh + gateway, for sync and independence from venue router. Same node hardware; firmware differs.
- Gateway: two modules on one board (e.g. 2x C6), one on ESP-NOW and one on Wi-Fi, linked by UART. Avoids the single-radio channel-lock issue. Antennas on board edge, far apart; independent programming for each chip.
- C6 also has 802.15.4 (Thread/Zigbee) as an alternative.

## Power monitoring

- Decision: **single INA226** on main input (0–36 V bus, I2C). LCSC listing from \~$0.43. Wire ALERT pin to a GPIO for overcurrent.
- Per-channel hardware sensing rejected on cost (\~$5–9 per fully loaded board).
- Per-channel estimation instead: calibrate per strip type: current vs duty (PWM) or vs pixel-value sum (ARGB), and vs bus voltage (INA226 reports it). Calibrate on a length similar to installed runs. Resistor-fed strips are voltage-sensitive; constant-current (e.g. WS2811 12 V) less so.
- Optional live check: briefly blank one channel (few ms) and read the drop in total current.
- Store calibration profiles in a database, refine over time, report to users. Compare estimated vs measured total to detect faults (dead segment, bad connection, wrong profile).

## Ideas under consideration (not decided)

Discussed and liked, but still open. Treat as proposals.

- **Optional stepper header**: footprint for a plug-in TMC2209 stepstick module (step, dir, enable, one-wire UART, plus one pin for StallGuard or a limit switch). Keeps motor current and heat off boards that don't need it; a curtain node is the standard board with a driver plugged in.
- **Rough GPIO budget** (\~19 usable after USB and UART0, 3 of them strapping): 2 strip data, 6 PWM/relay, 2 I2C + 1 INA226 alert, 1 status LED, 5 stepper header = \~17. Put strapping pins on boot-safe outputs (status LED, stepper enable).
- **Status WS2812B**: both RMT TX channels are used by strips, so chain it as pixel 0 on one strip's data line, or drive it via SPI.
- **Layer count**: 2-layer is feasible; 4-layer suggested for a solid ground plane under the buck and module. Main care points: wide pours or 2 oz copper on strip current paths, and a copper-free antenna keep-out.

## Validation to do once boards arrive

- Confirm RMT 3-block allocation on ch1 under ESP-IDF.
- Stress test: continuous full-length frames + heavy Wi-Fi (iperf); logic analyser triggered on data-line low gaps > 50 µs. OTA/NVS-write glitches considered acceptable (only during config changes).
