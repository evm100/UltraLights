#include <math.h>
#include <stdio.h>
#include <string.h>
#include "ul_contract.h"
#include "ul_core_logic.h"

int ul_board_find_channel(const ul_board_desc_t *b, const char *id)
{
    for (int i = 0; i < b->n_channels; i++) {
        if (strcmp(b->ch[i].id, id) == 0) {
            return i;
        }
    }
    return -1;
}

static void init_def(ul_output_def_t *d, const char *key, ul_kind_t kind)
{
    memset(d, 0, sizeof(*d));
    snprintf(d->key, sizeof(d->key), "%s", key);
    d->kind = kind;
    d->cct_warm = 2700;
    d->cct_cool = 6500;
    d->max_level = 1.0f;
    d->caps = ul_kind_caps(kind);
}

void ul_mapping_default(const ul_board_desc_t *b, ul_config_t *c)
{
    memset(c, 0, sizeof(*c));
    for (int i = 0; i < b->n_channels && c->n < UL_MAX_OUTPUTS; i++) {
        const ul_channel_desc_t *ch = &b->ch[i];
        ul_output_def_t *d = &c->out[c->n++];
        switch (ch->type) {
        case UL_CH_PWM:
            init_def(d, ch->id, UL_KIND_WHITE);
            strcpy(d->ch[UL_ROLE_W], ch->id);
            break;
        case UL_CH_ARGB:
            init_def(d, ch->id, UL_KIND_ARGB);
            strcpy(d->ch[UL_ROLE_DATA], ch->id);
            d->pixels = ch->pixels;
            break;
        case UL_CH_SWITCH:
            init_def(d, ch->id, UL_KIND_RELAY);
            strcpy(d->ch[UL_ROLE_COIL], ch->id);
            break;
        case UL_CH_MOTION:
            init_def(d, ch->id, UL_KIND_MOTION);
            strcpy(d->ch[UL_ROLE_IN], ch->id);
            break;
        }
        if (ch->name[0]) {
            snprintf(d->name, sizeof(d->name), "%s", ch->name);
        }
    }
    if (ul_mapping_validate(b, c, NULL, 0) != 0) {
        c->n = 0; /* cannot happen for a board that passed its own checks */
    }
}

/* Roles each kind needs, and the channel type each role needs. */
static uint32_t kind_roles(ul_kind_t k)
{
#define R(x) (1u << UL_ROLE_##x)
    switch (k) {
    case UL_KIND_WHITE: return R(W);
    case UL_KIND_CCT: return R(WARM) | R(COOL);
    case UL_KIND_RGB: return R(R) | R(G) | R(B);
    case UL_KIND_RGBW: return R(R) | R(G) | R(B) | R(W);
    case UL_KIND_RGBCCT: return R(R) | R(G) | R(B) | R(WARM) | R(COOL);
    case UL_KIND_ARGB: return R(DATA);
    case UL_KIND_RELAY:
    case UL_KIND_SOLENOID: return R(COIL);
    case UL_KIND_MOTION: return R(IN);
    default: return 0;
    }
#undef R
}

static ul_ch_type_t role_type(ul_role_t r)
{
    switch (r) {
    case UL_ROLE_DATA: return UL_CH_ARGB;
    case UL_ROLE_COIL: return UL_CH_SWITCH;
    case UL_ROLE_IN: return UL_CH_MOTION;
    default: return UL_CH_PWM;
    }
}

#define FAIL(...) do { if (err && errlen) snprintf(err, errlen, __VA_ARGS__); return -1; } while (0)

int ul_mapping_validate(const ul_board_desc_t *b, ul_config_t *c, char *err, size_t errlen)
{
    bool used[UL_MAX_CHANNELS] = {0};
    for (int i = 0; i < c->n; i++) {
        ul_output_def_t *d = &c->out[i];
        for (int k = 0; k < i; k++) {
            if (strcmp(c->out[k].key, d->key) == 0) {
                FAIL("output %s: duplicate key", d->key);
            }
        }
        uint32_t need = kind_roles(d->kind);
        for (int r = 0; r < UL_ROLE_COUNT; r++) {
            bool has = d->ch[r][0] != '\0';
            bool wanted = need & (1u << r);
            if (has != wanted) {
                FAIL("output %s: kind %s %s role %s", d->key, ul_kind_name(d->kind),
                     wanted ? "needs" : "has no", ul_role_name((ul_role_t)r));
            }
            if (!has) {
                continue;
            }
            int ci = ul_board_find_channel(b, d->ch[r]);
            if (ci < 0) {
                FAIL("output %s: channel %s not on board", d->key, d->ch[r]);
            }
            if (b->ch[ci].type != role_type((ul_role_t)r)) {
                FAIL("output %s: channel %s is %s, role %s needs %s", d->key, d->ch[r],
                     ul_ch_type_name(b->ch[ci].type), ul_role_name((ul_role_t)r),
                     ul_ch_type_name(role_type((ul_role_t)r)));
            }
            if (used[ci]) {
                FAIL("output %s: channel %s already used", d->key, d->ch[r]);
            }
            used[ci] = true;
            if (r == UL_ROLE_DATA) {
                if (d->pixels == 0) {
                    d->pixels = b->ch[ci].pixels;
                } else if (d->pixels > b->ch[ci].pixels) {
                    FAIL("output %s: %u pixels but channel %s has %u", d->key, d->pixels,
                         d->ch[r], b->ch[ci].pixels);
                }
            }
        }
        if (d->cct_warm >= d->cct_cool && (d->kind == UL_KIND_CCT || d->kind == UL_KIND_RGBCCT)) {
            FAIL("output %s: cct_range must be [warm, cool] with warm < cool", d->key);
        }
        if (d->kind == UL_KIND_SOLENOID && d->max_on_ms == 0) {
            FAIL("output %s: solenoid needs max_on_ms", d->key);
        }
        /* Phase 1 drivers render solid colors only. */
        d->caps = ul_kind_caps(d->kind) & ~(uint32_t)UL_CAP_EFFECT;
    }
    return 0;
}

/* ---------- state ---------- */

static uint16_t clamp_cct(const ul_output_def_t *d, uint16_t k)
{
    return k < d->cct_warm ? d->cct_warm : k > d->cct_cool ? d->cct_cool : k;
}

void ul_state_init(ul_out_state_t *s, const ul_output_def_t *d)
{
    memset(s, 0, sizeof(*s));
    ul_state_msg_t *c = &s->cur;
    c->on = false;
    c->level = 1.0f;
    c->color[0] = c->color[1] = c->color[2] = 255;
    c->white = 0;
    c->fields = UL_F_ON | UL_F_SEQ;
    uint32_t caps = d->caps;
    if (caps & UL_CAP_LEVEL) {
        c->fields |= UL_F_LEVEL;
    }
    if (caps & UL_CAP_CCT) {
        /* Middle of the range in mireds. */
        float m = 0.5f * (1e6f / d->cct_warm + 1e6f / d->cct_cool);
        c->cct = clamp_cct(d, (uint16_t)lroundf(1e6f / m));
        c->fields |= UL_F_CCT;
    }
    if (caps & UL_CAP_COLOR) {
        c->fields |= UL_F_COLOR;
    }
    if (caps & UL_CAP_WHITE) {
        c->fields |= UL_F_WHITE;
    }
}

int ul_state_apply(ul_out_state_t *s, const ul_output_def_t *d, const ul_state_msg_t *m, bool live)
{
    (void)live;
    if (m->fields & UL_F_SEQ) {
        if (s->seq_seen && m->seq <= s->last_seq) {
            return 0;
        }
        s->last_seq = m->seq;
        s->seq_seen = true;
        s->cur.seq = m->seq;
    }
    ul_state_msg_t *c = &s->cur;
    uint32_t caps = d->caps;
    if (m->fields & UL_F_ON) {
        c->on = m->on;
    }
    if ((m->fields & UL_F_LEVEL) && (caps & UL_CAP_LEVEL)) {
        c->level = m->level;
    }
    if ((m->fields & UL_F_CCT) && (caps & UL_CAP_CCT)) {
        c->cct = clamp_cct(d, m->cct);
    }
    if ((m->fields & UL_F_COLOR) && (caps & UL_CAP_COLOR)) {
        memcpy(c->color, m->color, 3);
    }
    if ((m->fields & UL_F_WHITE) && (caps & (UL_CAP_WHITE | UL_CAP_CCT))) {
        c->white = m->white;
    }
    if ((m->fields & UL_F_EFFECT) && (caps & UL_CAP_EFFECT)) {
        memcpy(c->effect, m->effect, sizeof(c->effect));
        c->fields |= UL_F_EFFECT;
    }
    if (m->fields & UL_F_SRC) {
        memcpy(c->src, m->src, sizeof(c->src));
    }
    return 1;
}

/* ---------- rendering ---------- */

float ul_curve_cie(float level)
{
    if (level <= 0) {
        return 0;
    }
    if (level >= 1) {
        return 1;
    }
    float l = level * 100.0f;
    if (l <= 8.0f) {
        return l / 903.3f;
    }
    float t = (l + 16.0f) / 116.0f;
    return t * t * t;
}

float ul_srgb_to_linear(uint8_t c)
{
    float v = c / 255.0f;
    return v <= 0.04045f ? v / 12.92f : powf((v + 0.055f) / 1.055f, 2.4f);
}

void ul_render(const ul_output_def_t *d, const ul_state_msg_t *st, ul_render_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!st->on) {
        return;
    }
    if (d->kind == UL_KIND_RELAY || d->kind == UL_KIND_SOLENOID) {
        out->v[UL_ROLE_COIL] = 1.0f;
        return;
    }
    if (d->kind == UL_KIND_MOTION) {
        return;
    }
    float level = st->level < 0 ? 0 : st->level > 1 ? 1 : st->level;
    float y = d->curve == UL_CURVE_LINEAR ? level : ul_curve_cie(level);
    if (y <= 0) {
        return;
    }
    y = d->min_duty + (1.0f - d->min_duty) * y;
    y *= d->max_level;

    float rgb[3];
    for (int i = 0; i < 3; i++) {
        rgb[i] = ul_srgb_to_linear(st->color[i]);
    }
    /* CCT share of the cool channel, linear in mireds. */
    float cool = 0;
    if (d->kind == UL_KIND_CCT || d->kind == UL_KIND_RGBCCT) {
        float mw = 1e6f / d->cct_warm, mc = 1e6f / d->cct_cool, m = 1e6f / (st->cct ? st->cct : d->cct_warm);
        cool = (mw - m) / (mw - mc);
        cool = cool < 0 ? 0 : cool > 1 ? 1 : cool;
    }
    switch (d->kind) {
    case UL_KIND_WHITE:
        out->v[UL_ROLE_W] = y;
        break;
    case UL_KIND_CCT:
        out->v[UL_ROLE_WARM] = y * (1.0f - cool);
        out->v[UL_ROLE_COOL] = y * cool;
        break;
    case UL_KIND_RGB:
    case UL_KIND_RGBW:
    case UL_KIND_RGBCCT:
        out->v[UL_ROLE_R] = y * rgb[0];
        out->v[UL_ROLE_G] = y * rgb[1];
        out->v[UL_ROLE_B] = y * rgb[2];
        if (d->kind == UL_KIND_RGBW) {
            out->v[UL_ROLE_W] = y * st->white;
        } else if (d->kind == UL_KIND_RGBCCT) {
            /* Provisional: `white` sets the intensity of the CCT pair. */
            out->v[UL_ROLE_WARM] = y * st->white * (1.0f - cool);
            out->v[UL_ROLE_COOL] = y * st->white * cool;
        }
        break;
    case UL_KIND_ARGB:
        out->rgbw[0] = y * rgb[0];
        out->rgbw[1] = y * rgb[1];
        out->rgbw[2] = y * rgb[2];
        out->rgbw[3] = 0;
        break;
    default:
        break;
    }
}

void ul_state_lerp(const ul_state_msg_t *a, const ul_state_msg_t *b, float t, ul_state_msg_t *out)
{
    *out = *b;
    /* Fading on or off is a level fade from or to zero. */
    float la = a->on ? a->level : 0, lb = b->on ? b->level : 0;
    out->on = a->on || b->on;
    out->level = la + (lb - la) * t;
    out->cct = (uint16_t)lroundf(a->cct + ((float)b->cct - a->cct) * t);
    for (int i = 0; i < 3; i++) {
        out->color[i] = (uint8_t)lroundf(a->color[i] + ((float)b->color[i] - a->color[i]) * t);
    }
    out->white = a->white + (b->white - a->white) * t;
    if (t >= 1.0f) {
        *out = *b;
    }
}
