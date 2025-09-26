#include "ul_relay.h"
#include "sdkconfig.h"

#if !(CONFIG_UL_RELAY0_ENABLED || CONFIG_UL_RELAY1_ENABLED ||                    \
      CONFIG_UL_RELAY2_ENABLED || CONFIG_UL_RELAY3_ENABLED)

#include <string.h>

bool ul_relay_start(void) { return true; }
void ul_relay_stop(void) {}

bool ul_relay_apply_json(cJSON *root, int *out_channel, bool *out_desired) {
  (void)root;
  if (out_channel)
    *out_channel = 0;
  if (out_desired)
    *out_desired = false;
  return false;
}

bool ul_relay_set_state(int channel, bool on) {
  (void)channel;
  (void)on;
  return false;
}

int ul_relay_get_channel_count(void) { return 0; }

bool ul_relay_get_status(int channel, ul_relay_status_t *out) {
  (void)channel;
  if (out)
    memset(out, 0, sizeof(*out));
  return false;
}

#else

#include "cJSON.h"
#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "string.h"
#include "ul_health.h"

#include <ctype.h>

static const char *TAG = "ul_relay";

typedef struct {
  bool enabled;
  int gpio;
  bool active_high;
  bool state;
  uint32_t min_interval_ms;
  uint64_t last_change_us;
} relay_channel_t;

static relay_channel_t s_channels[4];
static int s_channel_count;
static bool s_started;

static void reset_channels(void) {
  memset(s_channels, 0, sizeof(s_channels));
  s_channel_count = 0;
  s_started = false;
}

static bool apply_gpio_level(relay_channel_t *ch, bool on) {
  if (!ch || !ch->enabled)
    return false;
  int level = ch->active_high ? (on ? 1 : 0) : (on ? 0 : 1);
  esp_err_t err = gpio_set_level((gpio_num_t)ch->gpio, level);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set GPIO%d level: %s", ch->gpio, esp_err_to_name(err));
    return false;
  }
  return true;
}

static bool configure_channel(int index, int gpio, bool active_high,
                              uint32_t min_interval_ms) {
  relay_channel_t *ch = &s_channels[index];
  ch->enabled = false;
  ch->gpio = gpio;
  ch->active_high = active_high;
  ch->state = false;
  ch->min_interval_ms = min_interval_ms;
  ch->last_change_us = 0;

  if (gpio < 0)
    return false;

  gpio_config_t cfg = {
      .pin_bit_mask = 1ULL << gpio,
      .mode = GPIO_MODE_OUTPUT,
      .pull_up_en = GPIO_PULLUP_DISABLE,
      .pull_down_en = GPIO_PULLDOWN_DISABLE,
      .intr_type = GPIO_INTR_DISABLE,
  };

  esp_err_t err = gpio_config(&cfg);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to configure GPIO%d for relay %d: %s", gpio, index,
             esp_err_to_name(err));
    return false;
  }

  ch->enabled = true;
  if (!apply_gpio_level(ch, false)) {
    ch->enabled = false;
    return false;
  }

  ch->state = false;
  ch->last_change_us = esp_timer_get_time();
  s_channel_count++;
  return true;
}

static void init_enabled_channels(void) {
  reset_channels();

#if CONFIG_UL_RELAY0_ENABLED
  configure_channel(0, CONFIG_UL_RELAY0_GPIO, CONFIG_UL_RELAY0_ACTIVE_HIGH,
                    CONFIG_UL_RELAY0_MIN_INTERVAL_MS);
#endif
#if CONFIG_UL_RELAY1_ENABLED
  configure_channel(1, CONFIG_UL_RELAY1_GPIO, CONFIG_UL_RELAY1_ACTIVE_HIGH,
                    CONFIG_UL_RELAY1_MIN_INTERVAL_MS);
#endif
#if CONFIG_UL_RELAY2_ENABLED
  configure_channel(2, CONFIG_UL_RELAY2_GPIO, CONFIG_UL_RELAY2_ACTIVE_HIGH,
                    CONFIG_UL_RELAY2_MIN_INTERVAL_MS);
#endif
#if CONFIG_UL_RELAY3_ENABLED
  configure_channel(3, CONFIG_UL_RELAY3_GPIO, CONFIG_UL_RELAY3_ACTIVE_HIGH,
                    CONFIG_UL_RELAY3_MIN_INTERVAL_MS);
#endif
}

bool ul_relay_start(void) {
  if (s_started) {
    ESP_LOGW(TAG, "Relay engine already started");
    return true;
  }

  init_enabled_channels();

  if (s_channel_count == 0) {
    ESP_LOGI(TAG, "Relay engine started with no configured channels");
    ul_health_notify_relay_engine_ok();
    s_started = true;
    return true;
  }

  bool any_enabled = false;
  for (size_t i = 0; i < sizeof(s_channels) / sizeof(s_channels[0]); ++i) {
    if (s_channels[i].enabled) {
      any_enabled = true;
    }
  }

  if (!any_enabled) {
    ESP_LOGE(TAG, "Relay engine failed to configure any channels");
    ul_health_notify_relay_engine_failure();
    reset_channels();
    return false;
  }

  ul_health_notify_relay_engine_ok();
  s_started = true;
  ESP_LOGI(TAG, "Relay engine initialized (%d channel%s)", s_channel_count,
           s_channel_count == 1 ? "" : "s");
  return true;
}

void ul_relay_stop(void) {
  if (!s_started)
    return;

  for (size_t i = 0; i < sizeof(s_channels) / sizeof(s_channels[0]); ++i) {
    if (s_channels[i].enabled) {
      apply_gpio_level(&s_channels[i], false);
    }
  }
  reset_channels();
}

bool ul_relay_set_state(int channel, bool on) {
  if (channel < 0 || channel >= (int)(sizeof(s_channels) / sizeof(s_channels[0])))
    return false;
  relay_channel_t *ch = &s_channels[channel];
  if (!ch->enabled)
    return false;

  if (ch->state == on)
    return true;

  uint64_t now_us = esp_timer_get_time();
  uint64_t min_interval_us = (uint64_t)ch->min_interval_ms * 1000ULL;
  if (min_interval_us > 0 && ch->last_change_us != 0 &&
      now_us - ch->last_change_us < min_interval_us) {
    ESP_LOGW(TAG,
             "Relay %d command ignored (rate limited: %llu ms since last change)",
             channel, (unsigned long long)((now_us - ch->last_change_us) / 1000ULL));
    return false;
  }

  if (!apply_gpio_level(ch, on))
    return false;

  ch->state = on;
  ch->last_change_us = now_us;
  ESP_LOGI(TAG, "Relay %d set %s", channel, on ? "on" : "off");
  return true;
}

int ul_relay_get_channel_count(void) { return s_channel_count; }

bool ul_relay_get_status(int channel, ul_relay_status_t *out) {
  if (out)
    memset(out, 0, sizeof(*out));

  if (channel < 0 || channel >= (int)(sizeof(s_channels) / sizeof(s_channels[0])))
    return false;

  relay_channel_t *ch = &s_channels[channel];
  if (!ch->enabled)
    return false;

  if (out) {
    out->enabled = ch->enabled;
    out->state = ch->state;
    out->active_high = ch->active_high;
    out->gpio = ch->gpio;
    out->min_interval_ms = ch->min_interval_ms;
    out->last_change_us = ch->last_change_us;
  }
  return true;
}

static bool parse_state_string(const char *str, bool *out_state) {
  if (!str || !out_state)
    return false;
  char buf[8];
  size_t len = strlen(str);
  if (len >= sizeof(buf))
    len = sizeof(buf) - 1;
  for (size_t i = 0; i < len; ++i) {
    buf[i] = (char)tolower((unsigned char)str[i]);
  }
  buf[len] = '\0';
  if (strcmp(buf, "on") == 0)
    *out_state = true;
  else if (strcmp(buf, "off") == 0)
    *out_state = false;
  else
    return false;
  return true;
}

bool ul_relay_apply_json(cJSON *root, int *out_channel, bool *out_desired) {
  if (!root)
    return false;

  int channel = 0;
  cJSON *jch = cJSON_GetObjectItem(root, "channel");
  if (jch && cJSON_IsNumber(jch))
    channel = jch->valueint;

  bool desired = false;
  bool have_state = false;

  cJSON *jstate = cJSON_GetObjectItem(root, "state");
  if (jstate) {
    if (cJSON_IsBool(jstate)) {
      desired = cJSON_IsTrue(jstate);
      have_state = true;
    } else if (cJSON_IsString(jstate)) {
      have_state = parse_state_string(jstate->valuestring, &desired);
    }
  }

  if (!have_state) {
    cJSON *jon = cJSON_GetObjectItem(root, "on");
    if (jon && cJSON_IsBool(jon)) {
      desired = cJSON_IsTrue(jon);
      have_state = true;
    }
  }

  if (out_channel)
    *out_channel = channel;
  if (out_desired)
    *out_desired = desired;

  if (!have_state)
    return false;

  return ul_relay_set_state(channel, desired);
}

#endif

