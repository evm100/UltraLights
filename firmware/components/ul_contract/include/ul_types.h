/*
 * Plain C types for every contract message (contracts/schemas). No ESP-IDF
 * headers here: this file is shared by firmware and host tests.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UL_MAX_CHANNELS   16
#define UL_MAX_OUTPUTS    16
#define UL_KEY_SZ         17   /* key: [a-z][a-z0-9_]{0,15} + NUL */
#define UL_NAME_SZ        49
#define UL_SRC_SZ         65
#define UL_DEVICE_ID_SZ   10   /* ul-xxxxxx + NUL */
#define UL_TOKEN_SZ       65
#define UL_HOST_SZ        254
#define UL_SERVER_CA_SZ   4096
#define UL_MAX_PIXELS     1500
#define UL_MAX_PULSE_MS   7200000  /* solenoid pulse and max_on_ms ceiling: 2 h (contracts output/cmd) */
#define UL_MAX_MODES      7        /* servo channel: modes after off (contracts board $defs/modes) */

typedef enum { UL_CHIP_ESP32C3 = 0, UL_CHIP_ESP32C6 = 1, UL_CHIP_COUNT } ul_chip_t;

/* ---------- board descriptor ---------- */

typedef enum { UL_CH_PWM = 0, UL_CH_ARGB, UL_CH_SWITCH, UL_CH_MOTION, UL_CH_SERVO } ul_ch_type_t;

typedef enum {
    UL_ORDER_GRB = 0, UL_ORDER_RGB, UL_ORDER_BRG, UL_ORDER_RBG, UL_ORDER_GBR, UL_ORDER_BGR,
    UL_ORDER_GRBW, UL_ORDER_RGBW,
} ul_pixel_order_t;

typedef enum { UL_CHIPSET_WS2812 = 0, UL_CHIPSET_SK6812, UL_CHIPSET_WS2811 } ul_chipset_t;

typedef struct {
    char id[UL_KEY_SZ];
    char name[UL_NAME_SZ];
    ul_ch_type_t type;
    int gpio;
    /* pwm */
    uint32_t hz;            /* default 19000 */
    bool invert;
    /* argb */
    uint16_t pixels;
    ul_pixel_order_t order; /* default GRB */
    ul_chipset_t chipset;   /* default ws2812 */
    /* switch, motion */
    bool active_low;
    /* servo: presses an appliance's mode button */
    uint8_t press_deg, rest_deg;
    uint16_t hold_ms;
    uint16_t release_ms;    /* default 400 */
    uint8_t n_modes;        /* default 1: {"on"} */
    char modes[UL_MAX_MODES][UL_KEY_SZ];
} ul_channel_desc_t;

typedef struct {
    ul_chip_t chip;
    char board[UL_NAME_SZ];
    char hw_rev[UL_KEY_SZ];
    int n_channels;
    ul_channel_desc_t ch[UL_MAX_CHANNELS];
    bool has_reset;
    int reset_gpio;
    bool reset_active_low;  /* default true */
    uint32_t reset_hold_ms; /* default 5000 */
    bool has_status;
    int status_gpio;
    bool status_active_low; /* default false */
} ul_board_desc_t;

/* ---------- ul_board partition payload: provisioning ---------- */

typedef struct {
    char device_id[UL_DEVICE_ID_SZ];
    char token[UL_TOKEN_SZ];          /* "" = no token */
    char server[UL_HOST_SZ];
    uint16_t mqtt_port;               /* default 8883 */
    char server_ca[UL_SERVER_CA_SZ];  /* "" = use the public CA bundle */
    bool has_wifi;
    char ssid[33];
    char psk[65];
    bool has_dns;
    char dns[16];
} ul_provision_t;

/* ---------- outputs ---------- */

typedef enum {
    UL_KIND_WHITE = 0, UL_KIND_CCT, UL_KIND_RGB, UL_KIND_RGBW, UL_KIND_RGBCCT,
    UL_KIND_ARGB, UL_KIND_RELAY, UL_KIND_SOLENOID, UL_KIND_MOTION, UL_KIND_CYCLE, UL_KIND_COUNT
} ul_kind_t;

typedef enum {
    UL_ROLE_W = 0, UL_ROLE_WARM, UL_ROLE_COOL, UL_ROLE_R, UL_ROLE_G, UL_ROLE_B,
    UL_ROLE_DATA, UL_ROLE_COIL, UL_ROLE_IN, UL_ROLE_PRESS, UL_ROLE_COUNT
} ul_role_t;

typedef enum {
    UL_CAP_ONOFF  = 1 << 0, UL_CAP_LEVEL = 1 << 1, UL_CAP_CCT    = 1 << 2,
    UL_CAP_COLOR  = 1 << 3, UL_CAP_WHITE = 1 << 4, UL_CAP_EFFECT = 1 << 5,
    UL_CAP_PULSE  = 1 << 6, UL_CAP_MOTION = 1 << 7, UL_CAP_POWER = 1 << 8,
    UL_CAP_MODE   = 1 << 9,
} ul_cap_t;

typedef enum { UL_CURVE_CIE = 0, UL_CURVE_LINEAR } ul_curve_t;
typedef enum { UL_POWER_ON_LAST = 0, UL_POWER_ON_OFF, UL_POWER_ON_ON } ul_power_on_t;

typedef struct {
    char key[UL_KEY_SZ];
    ul_kind_t kind;
    char name[UL_NAME_SZ];
    char ch[UL_ROLE_COUNT][UL_KEY_SZ];  /* channel id per role, "" = unused */
    uint32_t caps;                      /* ul_cap_t bits; set by the device */
    uint16_t cct_warm, cct_cool;        /* default 2700, 6500 */
    ul_curve_t curve;
    float min_duty;                     /* default 0 */
    float max_level;                    /* default 1 */
    uint16_t pixels;                    /* argb: 0 = all of the channel */
    ul_power_on_t power_on;
    uint32_t max_on_ms;                 /* solenoid */
} ul_output_def_t;

typedef struct {
    int rev;
    int n;
    ul_output_def_t out[UL_MAX_OUTPUTS];
} ul_config_t;

/* ---------- set / live / state ---------- */

enum {
    UL_F_ON = 1 << 0, UL_F_LEVEL = 1 << 1, UL_F_CCT = 1 << 2, UL_F_COLOR = 1 << 3,
    UL_F_WHITE = 1 << 4, UL_F_EFFECT = 1 << 5, UL_F_FADE = 1 << 6, UL_F_SRC = 1 << 7,
    UL_F_SEQ = 1 << 8, UL_F_PULSE_LEFT = 1 << 9, UL_F_MOTION = 1 << 10,
    UL_F_MODE = 1 << 11, UL_F_AT = 1 << 12,
};

typedef struct {
    uint32_t fields;    /* UL_F_* present */
    bool on;
    float level;
    uint16_t cct;
    uint8_t color[3];
    float white;
    char effect[25];    /* "" = solid (null or absent) */
    uint32_t fade_ms;
    char src[UL_SRC_SZ];
    uint64_t seq;
    uint32_t pulse_ms_left;
    bool motion;
    char mode[UL_KEY_SZ];   /* cycle: selected mode (kept while off) */
    char at[UL_KEY_SZ];     /* cycle (state only): where the appliance is, "off" or a mode */
} ul_state_msg_t;

/* ---------- cmd ---------- */

typedef enum {
    UL_OP_IDENTIFY = 0, UL_OP_REBOOT, UL_OP_WIFI_RESET, UL_OP_FACTORY_RESET,
    UL_OP_PULSE, UL_OP_OTA, UL_OP_SELFTEST, UL_OP_CLOSE, UL_OP_SYNC,
    UL_OP_COUNT
} ul_op_t;

typedef struct {
    char id[41];
    ul_op_t op;
    int64_t ts;         /* 0 = absent */
    char src[UL_SRC_SZ];
    uint32_t ms;        /* identify (default 5000), pulse */
    char out[UL_KEY_SZ]; /* identify (optional), pulse, close, sync */
    char at[UL_KEY_SZ];  /* sync: "off" or a mode */
    char url[384];
    char sha256[65];
    char version[32];
} ul_cmd_t;

/* ---------- evt ---------- */

typedef enum { UL_EVT_CMD_RESULT = 0, UL_EVT_BUTTON, UL_EVT_ERROR, UL_EVT_MOTION } ul_evt_type_t;
typedef enum { UL_BTN_PRESS = 0, UL_BTN_HOLD, UL_BTN_RESET } ul_btn_action_t;

typedef struct {
    ul_evt_type_t type;
    char cmd_id[41];
    bool ok;
    const char *error;    /* NULL = absent */
    const char *detail;   /* NULL = absent */
    int progress;         /* -1 = absent */
    ul_btn_action_t action;
    const char *code;
    const char *out;      /* NULL = absent */
    bool active;
} ul_evt_t;

/* ---------- health ---------- */

typedef struct {
    uint32_t uptime_s;
    uint32_t heap_free, heap_min;
    int rssi;
    char ip[16];
    int channel;            /* 0 = absent */
    const char *reset_reason;
    uint32_t wifi_reconnects, mqtt_reconnects;
    bool has_temp;
    float temp_c;
    const char *ota;        /* NULL = absent */
    char dns[16];           /* "" = absent */
} ul_health_t;

/* ---------- info ---------- */

typedef struct {
    const char *id;
    ul_chip_t chip;
    const char *fw, *profile, *idf;
    const ul_board_desc_t *board;
    uint32_t board_crc;
    uint8_t mac[6];
    const ul_config_t *outputs;   /* caps filled in */
    int config_rev;
    const char *config_error;     /* NULL = absent */
} ul_info_t;

/* ---------- enrollment ---------- */

typedef struct {
    const char *device_id, *token, *csr, *fw, *profile;
    ul_chip_t chip;
    uint8_t mac[6];
    uint32_t board_crc;
} ul_enroll_req_t;

typedef struct {
    bool ok;
    char error[24];       /* when !ok */
    char device_id[UL_DEVICE_ID_SZ];
    char *cert;           /* malloc'd PEM, caller frees */
    char *ca_chain;       /* malloc'd PEM, caller frees */
    char mqtt_host[UL_HOST_SZ];
    uint16_t mqtt_port;
} ul_enroll_resp_t;

#ifdef __cplusplus
}
#endif
