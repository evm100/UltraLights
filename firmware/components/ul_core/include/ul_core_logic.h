/*
 * Pure logic of the device model: output mapping, state merging with the seq
 * rule, and rendering a state into per-channel values. Host-testable; no
 * ESP-IDF headers.
 */
#pragma once

#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One output per channel, keyed by channel id (contracts/README.md "Default mapping"). rev = 0. */
void ul_mapping_default(const ul_board_desc_t *b, ul_config_t *c);

/*
 * Check a mapping against the board: unique keys, the roles each kind needs,
 * channels that exist with the right type, no channel used twice, pixels in
 * range. Fills caps for each output. Returns 0, or -1 with the reason in err.
 */
int ul_mapping_validate(const ul_board_desc_t *b, ul_config_t *c, char *err, size_t errlen);

/* Index of a board channel by id, or -1. */
int ul_board_find_channel(const ul_board_desc_t *b, const char *id);

typedef struct {
    ul_state_msg_t cur;     /* on, level, cct, color, white, effect always meaningful */
    uint64_t last_seq;
    bool seq_seen;          /* RAM only: the first message after boot always applies */
} ul_out_state_t;

/* Defaults for a fresh output: off, level 1, mid CCT, white. */
void ul_state_init(ul_out_state_t *s, const ul_output_def_t *d);

/*
 * Apply a set (live = false) or live (live = true) message. Returns 1 if it
 * was applied, 0 if dropped because seq is not greater than the last applied.
 * Fields the kind doesn't use are ignored; cct is clamped to the output's range.
 */
int ul_state_apply(ul_out_state_t *s, const ul_output_def_t *d, const ul_state_msg_t *m, bool live);

/*
 * cycle outputs: an appliance that steps off -> modes[0] -> ... -> off on each
 * press of its own button (a servo presses it). Positions: 0 = off, i = modes[i-1].
 * cur.mode is the selected mode (kept while off); cur.at is where the appliance is.
 */
void ul_cycle_init(ul_out_state_t *s, const ul_output_def_t *d, const ul_channel_desc_t *ch);
/* ul_state_apply, then keep cur.mode one of the channel's modes. */
int ul_cycle_apply(ul_out_state_t *s, const ul_output_def_t *d, const ul_channel_desc_t *ch,
                   const ul_state_msg_t *m, bool live);
/* Position the state wants: 0 when off, else the selected mode's. */
int ul_cycle_target(const ul_channel_desc_t *ch, const ul_state_msg_t *st);
/* Presses to get from one position to another (always forward, 0..n_modes). */
int ul_cycle_presses(const ul_channel_desc_t *ch, int from, int to);
/* The appliance is really at `at`: take it as both position and target. */
void ul_cycle_sync(ul_out_state_t *s, const ul_channel_desc_t *ch, int at);

/* Perceived level (0..1) to light output (0..1), CIE 1931 lightness. */
float ul_curve_cie(float level);
/* sRGB component (0..255) to linear light (0..1). */
float ul_srgb_to_linear(uint8_t c);

/* Per-role output values, 0..1 (coil: 0 or 1). */
typedef struct {
    float v[UL_ROLE_COUNT];
    float rgbw[4];   /* argb: linear r, g, b, w for the strip */
} ul_render_t;

/*
 * Turn a state into channel values: curve, min_duty and max_level, CCT mix
 * (constant total, interpolated in mireds), sRGB colors in linear light.
 */
void ul_render(const ul_output_def_t *d, const ul_state_msg_t *st, ul_render_t *out);

/* Linear interpolation between two states at t in [0,1] (level, cct, color, white). */
void ul_state_lerp(const ul_state_msg_t *a, const ul_state_msg_t *b, float t, ul_state_msg_t *out);

#ifdef __cplusplus
}
#endif
