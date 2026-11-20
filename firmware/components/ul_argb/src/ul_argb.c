/*
 * Addressable strips on RMT. Phase 1 renders a solid color with fades; effects
 * come later (ul_fx). One render task serves every strip.
 *
 * RMT memory: both chips have 2 TX channels and 4 blocks of 48 symbols, and a
 * TX channel can borrow the blocks after it. One strip gets all 4 blocks; with
 * two, the first gets 1 block (channel 0) and the second 3 (channel 1). The
 * driver allocates the lowest free channel, so creation order sets this.
 * See docs/hardware-notes.md "Addressable strips".
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "driver/rmt_tx.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "soc/soc_caps.h"
#include "ul_argb.h"
#include "ul_argb_chip.h"
#include "ul_board.h"
#include "ul_core.h"

static const char *TAG = "ul_argb";

#define RES_HZ       10000000   /* 0.1 us ticks */
#define MAX_STRIPS   2
#define MAX_POINTS   8
#define FRAME_US     16000      /* ~60 fps cap while fading */
#define RESET_US     300        /* newer WS2812B need >280 us */

typedef struct {
    rmt_channel_handle_t chan;
    rmt_encoder_handle_t enc;
    rmt_symbol_word_t bit0, bit1, reset;
    uint8_t *buf;
    uint16_t pixels;         /* fitted */
    uint8_t bpp;             /* 3 or 4 */
    uint8_t order[4];        /* byte position of r, g, b, w */
    /* animation, guarded by s_lock */
    float from[4], pts[MAX_POINTS][4];
    int n_pts;
    int64_t t0, dur;
    uint16_t lit;            /* pixels in use */
    bool dirty;
} strip_t;

static strip_t s_strip[MAX_STRIPS];
static int s_n;
static SemaphoreHandle_t s_lock;
static TaskHandle_t s_task;

/* ---------- encoder (runs in the RMT ISR, so it must be in IRAM) ---------- */

static size_t IRAM_ATTR encode(const void *data, size_t size, size_t written, size_t free_syms,
                               rmt_symbol_word_t *out, bool *done, void *arg)
{
    const strip_t *s = arg;
    if (free_syms < 8) {
        return 0;
    }
    size_t pos = written / 8;
    if (pos < size) {
        uint8_t byte = ((const uint8_t *)data)[pos];
        for (int i = 0; i < 8; i++) {
            out[i] = (byte & (0x80 >> i)) ? s->bit1 : s->bit0;
        }
        return 8;
    }
    out[0] = s->reset;
    *done = true;
    return 1;
}

static void set_timing(strip_t *s, ul_chipset_t chipset)
{
    /* high/low times in 0.1 us */
    uint16_t t0h = 3, t0l = 9, t1h = 9, t1l = 3;
    if (chipset == UL_CHIPSET_SK6812) {
        t1h = 6;
        t1l = 6;
    } else if (chipset == UL_CHIPSET_WS2811) {
        t0h = 3; t0l = 10; t1h = 6; t1l = 6;
    }
    s->bit0 = (rmt_symbol_word_t){.level0 = 1, .duration0 = t0h, .level1 = 0, .duration1 = t0l};
    s->bit1 = (rmt_symbol_word_t){.level0 = 1, .duration0 = t1h, .level1 = 0, .duration1 = t1l};
    uint16_t half = RESET_US * (RES_HZ / 1000000) / 2;
    s->reset = (rmt_symbol_word_t){.level0 = 0, .duration0 = half, .level1 = 0, .duration1 = half};
}

static void set_order(strip_t *s, ul_pixel_order_t o)
{
    /* order[k] = byte index of component k (r=0, g=1, b=2, w=3) */
    static const uint8_t map[][4] = {
        [UL_ORDER_GRB] = {1, 0, 2, 3}, [UL_ORDER_RGB] = {0, 1, 2, 3}, [UL_ORDER_BRG] = {1, 2, 0, 3},
        [UL_ORDER_RBG] = {0, 2, 1, 3}, [UL_ORDER_GBR] = {2, 0, 1, 3}, [UL_ORDER_BGR] = {2, 1, 0, 3},
        [UL_ORDER_GRBW] = {1, 0, 2, 3}, [UL_ORDER_RGBW] = {0, 1, 2, 3},
    };
    memcpy(s->order, map[o], 4);
    s->bpp = (o == UL_ORDER_GRBW || o == UL_ORDER_RGBW) ? 4 : 3;
}

/* ---------- rendering ---------- */

static uint8_t q8(float v)
{
    v = v < 0 ? 0 : v > 1 ? 1 : v;
    return (uint8_t)lroundf(v * 255.0f);
}

/* Current color of a strip's animation; true when it has reached the end. */
static bool sample(const strip_t *s, int64_t now, float out[4])
{
    if (s->dur <= 0 || now >= s->t0 + s->dur) {
        memcpy(out, s->pts[s->n_pts - 1], sizeof(float) * 4);
        return true;
    }
    float t = (float)(now - s->t0) / (float)s->dur * (float)s->n_pts;
    int seg = (int)t;
    float f = t - (float)seg;
    const float *a = seg == 0 ? s->from : s->pts[seg - 1];
    const float *b = s->pts[seg];
    for (int k = 0; k < 4; k++) {
        out[k] = a[k] + (b[k] - a[k]) * f;
    }
    return false;
}

static void fill(strip_t *s, const float c[4])
{
    uint8_t px[4] = {q8(c[0]), q8(c[1]), q8(c[2]), q8(c[3])};
    if (s->bpp == 4 && px[3] == 0) {
        /* RGBW strip without a white value: move the common part to the W die. */
        uint8_t w = px[0] < px[1] ? px[0] : px[1];
        w = w < px[2] ? w : px[2];
        px[0] -= w;
        px[1] -= w;
        px[2] -= w;
        px[3] = w;
    }
    uint8_t bytes[4] = {0};
    for (int k = 0; k < s->bpp; k++) {
        bytes[s->order[k]] = px[k];
    }
    size_t lit = (size_t)s->lit * s->bpp, all = (size_t)s->pixels * s->bpp;
    for (size_t i = 0; i < lit; i += s->bpp) {
        memcpy(s->buf + i, bytes, s->bpp);
    }
    memset(s->buf + lit, 0, all - lit);
}

static void render_task(void *arg)
{
    rmt_transmit_config_t tx = {.loop_count = 0};
    for (;;) {
        bool animating = false;
        int64_t now = esp_timer_get_time();
        for (int i = 0; i < s_n; i++) {
            strip_t *s = &s_strip[i];
            float c[4];
            xSemaphoreTake(s_lock, portMAX_DELAY);
            bool send = s->dirty;
            bool end = sample(s, now, c);
            if (send) {
                fill(s, c);
                s->dirty = !end;
            }
            xSemaphoreGive(s_lock);
            animating |= send && !end;
            if (send) {
                rmt_transmit(s->chan, s->enc, s->buf, (size_t)s->pixels * s->bpp, &tx);
            }
        }
        for (int i = 0; i < s_n; i++) {
            rmt_tx_wait_all_done(s_strip[i].chan, 100);
        }
        int64_t spent = esp_timer_get_time() - now;
        if (animating) {
            int64_t wait = FRAME_US - spent;
            vTaskDelay(wait > 1000 ? pdMS_TO_TICKS(wait / 1000) : 1);
        } else {
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        }
    }
}

static esp_err_t set_rgbw(void *ctx, const float (*points)[4], int n, uint16_t pixels, uint32_t fade_ms)
{
    strip_t *s = ctx;
    n = n < 1 ? 1 : n > MAX_POINTS ? MAX_POINTS : n;
    int64_t now = esp_timer_get_time();
    xSemaphoreTake(s_lock, portMAX_DELAY);
    sample(s, now, s->from);  /* start from wherever the last fade has got to */
    memcpy(s->pts, points, sizeof(float) * 4 * (size_t)n);
    s->n_pts = n;
    s->t0 = now;
    s->dur = (int64_t)fade_ms * 1000;
    if (pixels) {
        s->lit = pixels > s->pixels ? s->pixels : pixels;
    }
    s->dirty = true;
    xSemaphoreGive(s_lock);
    xTaskNotifyGive(s_task);
    return ESP_OK;
}

static const ul_channel_ops_t OPS = {.set_rgbw = set_rgbw};

static esp_err_t ul_argb_init(void)
{
    const ul_board_desc_t *b = ul_board_get();
    int total = 0;
    for (int i = 0; i < b->n_channels; i++) {
        total += b->ch[i].type == UL_CH_ARGB;
    }
    if (!total) {
        return ESP_OK;
    }
    s_lock = xSemaphoreCreateMutex();
    for (int i = 0; i < b->n_channels; i++) {
        const ul_channel_desc_t *d = &b->ch[i];
        if (d->type != UL_CH_ARGB) {
            continue;
        }
        if (s_n >= MAX_STRIPS || s_n >= UL_ARGB_CHIP.tx_channels) {
            ul_core_report_error("too_many_argb", d->id, "no RMT channel left");
            continue;
        }
        strip_t *s = &s_strip[s_n];
        /* One strip: every block. Two: 1 block on channel 0, the rest on channel 1. */
        int blocks = total == 1 ? UL_ARGB_CHIP.mem_blocks : (s_n == 0 ? 1 : UL_ARGB_CHIP.mem_blocks - 1);
        rmt_tx_channel_config_t cc = {
            .gpio_num = d->gpio, .clk_src = RMT_CLK_SRC_DEFAULT, .resolution_hz = RES_HZ,
            .mem_block_symbols = (size_t)blocks * SOC_RMT_MEM_WORDS_PER_CHANNEL, .trans_queue_depth = 2,
        };
        esp_err_t err = rmt_new_tx_channel(&cc, &s->chan);
        if (err != ESP_OK) {
            ul_core_report_error("argb_channel", d->id, "RMT setup failed: %s", esp_err_to_name(err));
            continue;
        }
        set_timing(s, d->chipset);
        set_order(s, d->order);
        s->pixels = d->pixels;
        s->lit = d->pixels;
        s->n_pts = 1;
        s->buf = calloc(d->pixels, s->bpp);
        rmt_simple_encoder_config_t ec = {.callback = encode, .arg = s, .min_chunk_size = 8};
        if (!s->buf || rmt_new_simple_encoder(&ec, &s->enc) != ESP_OK || rmt_enable(s->chan) != ESP_OK) {
            ul_core_report_error("argb_channel", d->id, "out of memory");
            continue;
        }
        s->dirty = true; /* clear the strip at boot */
        s_n++;
        ESP_ERROR_CHECK(ul_core_register_channel(d->id, &OPS, s));
        ESP_LOGI(TAG, "%s: GPIO%d, %u px, %d RMT blocks", d->id, d->gpio, d->pixels, blocks);
    }
    if (s_n && xTaskCreate(render_task, "ul_argb", 3072, NULL, 10, &s_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

UL_MODULE_REGISTER(argb, "ul_argb", UL_STAGE_DRIVER, 20, ul_argb_init)
