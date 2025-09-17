#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include <math.h>
#include <string.h>
#include <stdbool.h>

#define FIRE_MAX_STRIPS 2
#define FIRE_LAYERS 64
#define FIRE_DEFAULT_INTENSITY 1.2f

// Two-colour fire simulation backed by a large 2D heat field stored in PSRAM.
// Each strip keeps a FIRE_LAYERS x pixels grid of floating-point heat values
// which are advected upwards every frame.  The dense grid smooths the
// animation and creates the appearance of embers drifting through the flame.
// The ESP32's external PSRAM allows us to keep this state for up to two strips
// without exhausting internal memory.

typedef struct {
    float intensity;           // overall flame energy multiplier
    float primary[3];          // hot colour (1.0 = full channel)
    float secondary[3];        // cool colour
    float* grid;               // current heat field (FIRE_LAYERS * capacity)
    float* scratch;            // next heat field (same size)
    int capacity;              // number of columns allocated in the field
    bool params_set;           // whether custom parameters have been applied
    uint32_t rng;              // per-strip random generator state
} fire_state_t;

static fire_state_t s_fire[FIRE_MAX_STRIPS];

static inline float clampf(float v, float lo, float hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static uint32_t xorshift32(uint32_t* state) {
    uint32_t x = *state;
    if (x == 0) x = 0x12345678u;  // avoid the zero lock-up state
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

static float frand(uint32_t* state) {
    return (xorshift32(state) >> 8) * (1.0f / 16777216.0f);
}

static float* fire_alloc_cells(size_t cells) {
    float* ptr = heap_caps_calloc(cells, sizeof(float), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!ptr) {
        ptr = heap_caps_calloc(cells, sizeof(float), MALLOC_CAP_8BIT);
    }
    return ptr;
}

static bool ensure_capacity(fire_state_t* st, int width) {
    if (width <= 0) {
        return false;
    }
    if (width <= st->capacity && st->grid && st->scratch) {
        return true;
    }
    size_t cells = (size_t)width * FIRE_LAYERS;
    float* new_grid = fire_alloc_cells(cells);
    float* new_scratch = fire_alloc_cells(cells);
    if (!new_grid || !new_scratch) {
        if (new_grid) heap_caps_free(new_grid);
        if (new_scratch) heap_caps_free(new_scratch);
        return false;
    }
    if (st->grid) heap_caps_free(st->grid);
    if (st->scratch) heap_caps_free(st->scratch);
    st->grid = new_grid;
    st->scratch = new_scratch;
    st->capacity = width;
    return true;
}

static void set_default_palette(fire_state_t* st) {
    // Warm default reminiscent of a camp fire – deep red core fading to amber.
    st->intensity = FIRE_DEFAULT_INTENSITY;
    st->primary[0] = 1.0f;   st->primary[1] = 0.25f; st->primary[2] = 0.0f;   // #ff4000
    st->secondary[0] = 1.0f; st->secondary[1] = 0.85f; st->secondary[2] = 0.4f; // #ffd966
    st->params_set = false;
}

void fire_init(void) {
    for (int i = 0; i < FIRE_MAX_STRIPS; ++i) {
        fire_state_t* st = &s_fire[i];
        if (!st->params_set) {
            set_default_palette(st);
        }
        if (st->rng == 0) {
            st->rng = 0x9E3779B9u * (uint32_t)(i + 1);
        }
        if (st->grid && st->capacity > 0) {
            memset(st->grid, 0, (size_t)st->capacity * FIRE_LAYERS * sizeof(float));
        }
        if (st->scratch && st->capacity > 0) {
            memset(st->scratch, 0, (size_t)st->capacity * FIRE_LAYERS * sizeof(float));
        }
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

void fire_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip >= FIRE_MAX_STRIPS) return;
    if (!params || !cJSON_IsArray(params)) return;
    if (cJSON_GetArraySize(params) < 7) return;  // intensity + two colours

    fire_state_t* st = &s_fire[strip];

    const cJSON* intensity_item = cJSON_GetArrayItem(params, 0);
    if (intensity_item && cJSON_IsNumber(intensity_item)) {
        float intensity = (float)intensity_item->valuedouble;
        if (intensity > 10.0f) {
            // The UI slider publishes 0-200 so treat large values as a percent.
            intensity *= 0.01f;
        }
        st->intensity = clampf(intensity, 0.0f, 5.0f);
    }

    apply_colour_params(st->primary, params, 1);
    apply_colour_params(st->secondary, params, 4);
    st->params_set = true;
}

void fire_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    int strip = ul_ws_effect_current_strip();
    if (strip < 0 || strip >= FIRE_MAX_STRIPS) return;

    fire_state_t* st = &s_fire[strip];
    if (!ensure_capacity(st, pixels)) return;

    float* current = st->grid;
    float* next = st->scratch;
    int stride = st->capacity;

    float intensity = st->intensity;
    if (intensity < 0.0f) intensity = 0.0f;
    float intensity_norm = clampf(intensity, 0.0f, 4.0f);

    // Cool existing heat slightly each frame with a random perturbation.
    float cooling = 0.010f + 0.035f / (1.0f + intensity_norm * 1.6f);
    float jitter = 0.018f + 0.010f / (1.0f + intensity_norm);
    size_t active_cells = (size_t)pixels * FIRE_LAYERS;
    for (size_t i = 0; i < active_cells; ++i) {
        float offset = (frand(&st->rng) - 0.5f) * jitter;
        float cooled = current[i] - (cooling + offset);
        current[i] = cooled > 0.0f ? cooled : 0.0f;
    }

    // Seed new heat at the base with flickering bursts.
    for (int x = 0; x < pixels; ++x) {
        float spark = frand(&st->rng);
        float spark_energy = intensity * (0.55f + 0.45f * powf(spark, 3.0f));
        float base = current[x] * 0.25f + spark_energy;
        next[x] = clampf(base, 0.0f, 1.0f);
    }

    // Advect heat upwards with mild horizontal diffusion and turbulence.
    for (int y = 1; y < FIRE_LAYERS; ++y) {
        int row = y * stride;
        int below = (y - 1) * stride;
        int below2 = (y >= 2 ? (y - 2) * stride : below);
        for (int x = 0; x < pixels; ++x) {
            int left = (x == 0) ? pixels - 1 : x - 1;
            int right = (x == pixels - 1) ? 0 : x + 1;
            float advect = current[below + x] * 0.54f;
            advect += (current[below + left] + current[below + right]) * 0.22f;
            advect += current[below2 + x] * 0.08f;
            advect += (frand(&st->rng) - 0.5f) * 0.06f;
            next[row + x] = clampf(advect, 0.0f, 1.0f);
        }
        if (pixels < stride) {
            memset(&next[row + pixels], 0, (size_t)(stride - pixels) * sizeof(float));
        }
    }

    // Zero any unused columns in the bottom row as well.
    if (pixels < stride) {
        memset(&next[pixels], 0, (size_t)(stride - pixels) * sizeof(float));
    }

    // Swap buffers – next becomes current for the next frame.
    st->scratch = current;
    st->grid = next;
    current = st->grid;

    // Convert heat map into colours for each LED.
    const float weight_norm = 2.0f / (float)(FIRE_LAYERS * (FIRE_LAYERS + 1));
    const int top_row = (FIRE_LAYERS - 1) * stride;
    for (int x = 0; x < pixels; ++x) {
        float weighted = 0.0f;
        for (int y = 0; y < FIRE_LAYERS; ++y) {
            weighted += current[y * stride + x] * (float)(y + 1);
        }
        float heat = clampf(weighted * weight_norm, 0.0f, 1.0f);
        float tip = current[top_row + x];
        float brightness = clampf(heat * (0.65f + 0.25f * intensity_norm) + tip * 0.30f, 0.0f, 1.0f);
        float mix = clampf(powf(heat, 0.85f), 0.0f, 1.0f);

        float r = st->secondary[0] + (st->primary[0] - st->secondary[0]) * mix;
        float g = st->secondary[1] + (st->primary[1] - st->secondary[1]) * mix;
        float b = st->secondary[2] + (st->primary[2] - st->secondary[2]) * mix;

        r = clampf(r * brightness, 0.0f, 1.0f);
        g = clampf(g * brightness, 0.0f, 1.0f);
        b = clampf(b * brightness, 0.0f, 1.0f);

        frame_rgb[3 * x + 0] = (uint8_t)(r * 255.0f + 0.5f);
        frame_rgb[3 * x + 1] = (uint8_t)(g * 255.0f + 0.5f);
        frame_rgb[3 * x + 2] = (uint8_t)(b * 255.0f + 0.5f);
    }
}

#endif
