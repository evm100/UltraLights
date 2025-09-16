#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include <math.h>
#include <string.h>
#include <stdbool.h>

#define BLACK_ICE_MAX_STRIPS 2
#define BLACK_ICE_LAYERS 256
#define BLACK_ICE_DEFAULT_SHIMMER 1.0f

// Black Ice – shimmering crystalline frost with bright crackle highlights.
// This effect keeps multiple high-resolution layers of fracture intensity and
// sparkle energy in PSRAM to create a deep, animated texture. The large
// buffers allow rich detail while keeping the ESP32's internal RAM free.

typedef struct {
    float shimmer;            // how active the crystalline shimmer is
    float base[3];            // base ice colour
    float fracture_colour[3]; // crack highlight colour
    float sparkle_colour[3];  // diamond sparkle colour
    float* fracture;          // BLACK_ICE_LAYERS * capacity fracture energy field
    float* scratch;           // scratch buffer for fracture simulation
    float* sparkle;           // sparkle persistence per cell
    int capacity;             // allocated columns in the buffers
    bool params_set;          // whether custom params were supplied
    bool seeded;              // whether the fields have been initialised
    uint32_t rng;             // PRNG state
} black_ice_state_t;

static black_ice_state_t s_black_ice[BLACK_ICE_MAX_STRIPS];

static inline float clampf(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static uint32_t xorshift32(uint32_t* state) {
    uint32_t x = *state;
    if (x == 0) x = 0x12345678u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

static float frand(uint32_t* state) {
    return (xorshift32(state) >> 8) * (1.0f / 16777216.0f);
}

static float* black_ice_alloc_cells(size_t cells) {
    float* ptr = heap_caps_calloc(cells, sizeof(float), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!ptr) {
        ptr = heap_caps_calloc(cells, sizeof(float), MALLOC_CAP_8BIT);
    }
    return ptr;
}

static bool ensure_capacity(black_ice_state_t* st, int width) {
    if (width <= 0) {
        return false;
    }
    if (width <= st->capacity && st->fracture && st->scratch && st->sparkle) {
        return true;
    }
    size_t cells = (size_t)width * BLACK_ICE_LAYERS;
    float* fracture = black_ice_alloc_cells(cells);
    float* scratch = black_ice_alloc_cells(cells);
    float* sparkle = black_ice_alloc_cells(cells);
    if (!fracture || !scratch || !sparkle) {
        if (fracture) heap_caps_free(fracture);
        if (scratch) heap_caps_free(scratch);
        if (sparkle) heap_caps_free(sparkle);
        return false;
    }
    if (st->fracture) heap_caps_free(st->fracture);
    if (st->scratch) heap_caps_free(st->scratch);
    if (st->sparkle) heap_caps_free(st->sparkle);
    st->fracture = fracture;
    st->scratch = scratch;
    st->sparkle = sparkle;
    st->capacity = width;
    st->seeded = false;
    return true;
}

static void set_default_palette(black_ice_state_t* st) {
    // Deep midnight blue ice with pale cyan cracks and white sparkles.
    st->shimmer = BLACK_ICE_DEFAULT_SHIMMER;
    st->base[0] = 0.015f; st->base[1] = 0.070f; st->base[2] = 0.160f;   // #04122a
    st->fracture_colour[0] = 0.400f; st->fracture_colour[1] = 0.780f; st->fracture_colour[2] = 0.980f; // #66c7fa
    st->sparkle_colour[0] = 0.980f; st->sparkle_colour[1] = 0.995f; st->sparkle_colour[2] = 1.000f;   // #fbfeff
    st->params_set = false;
}

static void seed_fields(black_ice_state_t* st) {
    if (!st->fracture || !st->scratch || !st->sparkle || st->capacity <= 0) {
        return;
    }
    size_t stride = (size_t)st->capacity;
    size_t cells = stride * BLACK_ICE_LAYERS;
    for (size_t i = 0; i < cells; ++i) {
        float n = frand(&st->rng);
        st->fracture[i] = n * n * 0.45f;
        st->sparkle[i] = frand(&st->rng) * 0.10f;
    }
    // Relax the initial field a little to form softly connected fracture veins.
    for (int iter = 0; iter < 12; ++iter) {
        for (int y = 0; y < BLACK_ICE_LAYERS; ++y) {
            int row = y * (int)stride;
            int above = (y == 0 ? (BLACK_ICE_LAYERS - 1) : y - 1) * (int)stride;
            int below = (y == BLACK_ICE_LAYERS - 1 ? 0 : y + 1) * (int)stride;
            for (int x = 0; x < (int)stride; ++x) {
                int left = (x == 0) ? (int)stride - 1 : x - 1;
                int right = (x == (int)stride - 1) ? 0 : x + 1;
                float v = st->fracture[row + x];
                float avg = (v * 2.0f + st->fracture[row + left] + st->fracture[row + right] +
                             st->fracture[above + x] + st->fracture[below + x]) * (1.0f / 6.0f);
                st->scratch[row + x] = avg;
            }
        }
        memcpy(st->fracture, st->scratch, cells * sizeof(float));
    }
    memset(st->scratch, 0, cells * sizeof(float));
    st->seeded = true;
}

void black_ice_init(void) {
    for (int i = 0; i < BLACK_ICE_MAX_STRIPS; ++i) {
        black_ice_state_t* st = &s_black_ice[i];
        if (!st->params_set) {
            set_default_palette(st);
        }
        if (st->rng == 0) {
            st->rng = 0xB5297A4Du ^ (uint32_t)(i + 1) * 0x9E3779B9u;
        }
        if (st->fracture && st->capacity > 0) {
            memset(st->fracture, 0, (size_t)st->capacity * BLACK_ICE_LAYERS * sizeof(float));
        }
        if (st->scratch && st->capacity > 0) {
            memset(st->scratch, 0, (size_t)st->capacity * BLACK_ICE_LAYERS * sizeof(float));
        }
        if (st->sparkle && st->capacity > 0) {
            memset(st->sparkle, 0, (size_t)st->capacity * BLACK_ICE_LAYERS * sizeof(float));
        }
        st->seeded = false;
    }
}

static void apply_colour_params(float dest[3], const cJSON* params, int start_idx) {
    for (int i = 0; i < 3; ++i) {
        const cJSON* item = cJSON_GetArrayItem(params, start_idx + i);
        if (item && cJSON_IsNumber(item)) {
            dest[i] = clampf((float)item->valuedouble / 255.0f, 0.0f, 1.0f);
        }
    }
}

void black_ice_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip >= BLACK_ICE_MAX_STRIPS) return;
    if (!params || !cJSON_IsArray(params)) return;
    if (cJSON_GetArraySize(params) < 10) return; // shimmer + three colours

    black_ice_state_t* st = &s_black_ice[strip];

    const cJSON* shimmer_item = cJSON_GetArrayItem(params, 0);
    if (shimmer_item && cJSON_IsNumber(shimmer_item)) {
        float shimmer = (float)shimmer_item->valuedouble;
        if (shimmer > 10.0f) {
            shimmer *= 0.01f;
        }
        st->shimmer = clampf(shimmer, 0.1f, 3.0f);
    }

    apply_colour_params(st->base, params, 1);
    apply_colour_params(st->fracture_colour, params, 4);
    apply_colour_params(st->sparkle_colour, params, 7);
    st->params_set = true;
}

void black_ice_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    if (pixels <= 0) return;
    int strip = ul_ws_effect_current_strip();
    if (strip < 0 || strip >= BLACK_ICE_MAX_STRIPS) return;

    black_ice_state_t* st = &s_black_ice[strip];
    if (!ensure_capacity(st, pixels)) return;

    if (!st->params_set) {
        set_default_palette(st);
    }

    if (!st->seeded) {
        seed_fields(st);
    }

    float* current = st->fracture;
    float* next = st->scratch;
    float* sparkle = st->sparkle;
    int stride = st->capacity;
    float shimmer = st->shimmer;

    const float decay_base = 0.0032f + 0.0008f * shimmer;

    for (int y = 0; y < BLACK_ICE_LAYERS; ++y) {
        int row = y * stride;
        int above = (y == 0 ? (BLACK_ICE_LAYERS - 1) : y - 1) * stride;
        int below = (y == BLACK_ICE_LAYERS - 1 ? 0 : y + 1) * stride;
        float depth_factor = 1.0f - (float)y / (float)BLACK_ICE_LAYERS;
        float swirl = sinf((float)frame_idx * 0.0065f + (float)y * 0.19f);
        int shift = (int)lroundf(swirl * 4.0f);
        if (shift > pixels - 1) shift = pixels - 1;
        if (shift < -(pixels - 1)) shift = -(pixels - 1);
        for (int x = 0; x < pixels; ++x) {
            int left = (x == 0) ? pixels - 1 : x - 1;
            int right = (x == pixels - 1) ? 0 : x + 1;
            int flow = x + shift;
            if (flow < 0) flow += pixels;
            else if (flow >= pixels) flow -= pixels;

            float v = current[row + x];
            float blend = current[row + left] + current[row + right];
            float cross = current[above + flow] + current[below + flow];
            float local = current[row + flow];
            float target = v * 0.52f + blend * 0.16f + cross * 0.10f + local * 0.12f;
            float ridges = sinf((float)x * 0.045f + (float)y * 0.09f + (float)frame_idx * 0.0045f);
            float perturb = (frand(&st->rng) - 0.5f) * 0.10f + ridges * 0.08f * depth_factor;
            float next_val = target + perturb;
            next_val -= decay_base * (0.7f + 0.3f * depth_factor);

            float injection_prob = (0.0006f + 0.0018f * shimmer) * (0.35f + depth_factor * 0.65f);
            if (frand(&st->rng) < injection_prob) {
                float burst = 0.45f + 0.75f * frand(&st->rng);
                next_val += burst * (0.4f + depth_factor * 0.6f);
            }

            next_val = clampf(next_val, 0.0f, 1.6f);
            next[row + x] = next_val;

            float glimmer = sparkle[row + x];
            glimmer *= 0.72f + depth_factor * 0.23f;
            if (glimmer < 0.0f) glimmer = 0.0f;
            float sparkle_prob = (0.012f + 0.020f * shimmer) * (0.45f + depth_factor * 0.55f);
            if (next_val > 0.62f && frand(&st->rng) < sparkle_prob) {
                glimmer = 1.0f + frand(&st->rng) * 0.6f;
            } else if (frand(&st->rng) < 0.0008f * shimmer) {
                glimmer += frand(&st->rng) * 0.3f;
            }
            sparkle[row + x] = clampf(glimmer, 0.0f, 1.5f);
        }
        if (pixels < stride) {
            memset(&next[row + pixels], 0, (size_t)(stride - pixels) * sizeof(float));
            memset(&sparkle[row + pixels], 0, (size_t)(stride - pixels) * sizeof(float));
        }
    }

    st->scratch = current;
    st->fracture = next;
    current = st->fracture;

    const float weight_norm = 2.0f / (float)(BLACK_ICE_LAYERS * (BLACK_ICE_LAYERS + 1));

    for (int x = 0; x < pixels; ++x) {
        float fracture_sum = 0.0f;
        float sparkle_sum = 0.0f;
        for (int y = 0; y < BLACK_ICE_LAYERS; ++y) {
            float weight = (float)(y + 1);
            fracture_sum += current[y * stride + x] * weight;
            sparkle_sum += st->sparkle[y * stride + x] * weight;
        }
        float crack_strength = clampf(fracture_sum * weight_norm * 1.45f, 0.0f, 1.0f);
        float shimmer_strength = clampf(sparkle_sum * weight_norm * 1.20f, 0.0f, 1.0f);

        float frost = clampf(powf(crack_strength, 1.25f), 0.0f, 1.0f);
        float glint = clampf(powf(shimmer_strength, 0.95f), 0.0f, 1.0f);

        float r = st->base[0] + (st->fracture_colour[0] - st->base[0]) * frost;
        float g = st->base[1] + (st->fracture_colour[1] - st->base[1]) * frost;
        float b = st->base[2] + (st->fracture_colour[2] - st->base[2]) * frost;

        r += (st->sparkle_colour[0] - r) * glint;
        g += (st->sparkle_colour[1] - g) * glint;
        b += (st->sparkle_colour[2] - b) * glint;

        float pulse = sinf((float)frame_idx * 0.007f + (float)x * 0.021f);
        float brightness = 0.22f + frost * (0.45f + 0.25f * shimmer) +
                           glint * (0.35f + 0.40f * shimmer) + pulse * 0.04f;
        brightness *= 0.85f + 0.15f * shimmer;
        brightness = clampf(brightness, 0.06f, 1.25f);

        frame_rgb[3 * x + 0] = (uint8_t)(clampf(r * brightness, 0.0f, 1.0f) * 255.0f + 0.5f);
        frame_rgb[3 * x + 1] = (uint8_t)(clampf(g * brightness, 0.0f, 1.0f) * 255.0f + 0.5f);
        frame_rgb[3 * x + 2] = (uint8_t)(clampf(b * brightness, 0.0f, 1.0f) * 255.0f + 0.5f);
    }
}

