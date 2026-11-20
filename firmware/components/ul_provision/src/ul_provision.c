/*
 * Setup portal and reset button.
 *
 * A node with no Wi-Fi credentials, or one whose reset button is held for
 * hold_ms (default 5 s), opens an open access point "UltraLights-XXXX" with a
 * captive DNS (every name resolves to 192.168.4.1) and one page asking for the
 * network, password and an optional DNS server. Saving stores them in NVS and
 * restarts. If saved credentials don't connect within 3 minutes the portal also
 * opens, next to the station, and closes again once the station joins.
 */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "driver/gpio.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "ul_board.h"
#include "ul_contract.h"
#include "ul_core.h"
#include "ul_provision.h"
#include "ul_setup_page.h"
#include "ul_wifi.h"

static const char *TAG = "ul_provision";

#define AP_IP            "192.168.4.1"
#define FALLBACK_US      (3LL * 60 * 1000 * 1000)
#define BUTTON_POLL_MS   20
#define SHORT_PRESS_MS   50

static httpd_handle_t s_http;
static TaskHandle_t s_dns_task;
static volatile bool s_portal_up;
static esp_timer_handle_t s_fallback_timer;

/* ---------- helpers ---------- */

static void html_escape(const char *in, char *out, size_t n)
{
    size_t o = 0;
    for (; *in && o + 7 < n; in++) {
        const char *rep = NULL;
        switch (*in) {
        case '<': rep = "&lt;"; break;
        case '>': rep = "&gt;"; break;
        case '&': rep = "&amp;"; break;
        case '"': rep = "&quot;"; break;
        case '\'': rep = "&#39;"; break;
        default: break;
        }
        if (rep) {
            size_t l = strlen(rep);
            memcpy(out + o, rep, l);
            o += l;
        } else {
            out[o++] = *in;
        }
    }
    out[o] = '\0';
}

/* Decode one application/x-www-form-urlencoded field. Returns false if absent or too long. */
static bool form_field(const char *body, const char *key, char *out, size_t n)
{
    size_t kl = strlen(key);
    for (const char *p = body; p && *p; p = strchr(p, '&') ? strchr(p, '&') + 1 : NULL) {
        if (strncmp(p, key, kl) != 0 || p[kl] != '=') {
            continue;
        }
        const char *v = p + kl + 1;
        size_t o = 0;
        for (; *v && *v != '&'; v++) {
            if (o + 1 >= n) {
                return false;
            }
            if (*v == '+') {
                out[o++] = ' ';
            } else if (*v == '%' && isxdigit((unsigned char)v[1]) && isxdigit((unsigned char)v[2])) {
                char hex[3] = {v[1], v[2], 0};
                out[o++] = (char)strtol(hex, NULL, 16);
                v += 2;
            } else {
                out[o++] = *v;
            }
        }
        out[o] = '\0';
        return true;
    }
    return false;
}

static void ap_name(char out[20])
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(out, 20, "UltraLights-%02X%02X", mac[4], mac[5]);
}

/* ---------- HTTP ---------- */

static esp_err_t page_get(httpd_req_t *req)
{
    const ul_provision_t *p = ul_board_provision();
    const ul_board_desc_t *raw = ul_board_raw();
    const char *berr = ul_board_error();
    char board[160], notice[240], dns[64], id[16], mac_s[18], tmp[120];
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(mac_s, sizeof(mac_s), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    snprintf(id, sizeof(id), "%s", p ? p->device_id : "not set");
    snprintf(tmp, sizeof(tmp), "%s%s%s", raw ? raw->board : "none", berr ? ": " : "", berr ? berr : "");
    html_escape(tmp, board, sizeof(board));
    char cur_dns[16] = "";
    if (p && p->has_dns) {
        snprintf(cur_dns, sizeof(cur_dns), "%s", p->dns);
    }
    html_escape(cur_dns, dns, sizeof(dns));
    notice[0] = '\0';
    if (!p) {
        snprintf(notice, sizeof(notice),
                 "<p class=warn>This node has no board descriptor, so it can't enroll. "
                 "Flash one from the node builder.</p>");
    } else if (berr) {
        snprintf(notice, sizeof(notice), "<p class=warn>The board descriptor was rejected, so no outputs are driven. "
                 "Fix it in the node builder.</p>");
    }
    size_t n = sizeof(UL_SETUP_PAGE) + sizeof(notice) + sizeof(board) + 128;
    char *html = malloc(n);
    if (!html) {
        return httpd_resp_send_500(req);
    }
    snprintf(html, n, UL_SETUP_PAGE, notice, dns, id, board, esp_app_get_description()->version, mac_s);
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_sendstr(req, html);
    free(html);
    return err;
}

static esp_err_t scan_get(httpd_req_t *req)
{
    wifi_scan_config_t sc = {.show_hidden = false};
    uint16_t n = 0;
    wifi_ap_record_t *aps = NULL;
    if (esp_wifi_scan_start(&sc, true) == ESP_OK && esp_wifi_scan_get_ap_num(&n) == ESP_OK && n) {
        n = n > 20 ? 20 : n;
        aps = calloc(n, sizeof(*aps));
        if (!aps || esp_wifi_scan_get_ap_records(&n, aps) != ESP_OK) {
            n = 0;
        }
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr_chunk(req, "[");
    int sent = 0;
    for (int i = 0; i < n; i++) {
        const char *ssid = (const char *)aps[i].ssid;
        if (!ssid[0]) {
            continue;
        }
        bool dup = false;
        for (int k = 0; k < i; k++) {
            dup |= strcmp(ssid, (const char *)aps[k].ssid) == 0;
        }
        if (dup) {
            continue; /* records are sorted by signal, keep the strongest */
        }
        char esc[100], item[160];
        size_t o = 0;
        for (const char *c = ssid; *c && o + 7 < sizeof(esc); c++) {
            if (*c == '"' || *c == '\\') {
                esc[o++] = '\\';
                esc[o++] = *c;
            } else if ((unsigned char)*c < 0x20) {
                o += (size_t)snprintf(esc + o, sizeof(esc) - o, "\\u%04x", *c);
            } else {
                esc[o++] = *c;
            }
        }
        esc[o] = '\0';
        snprintf(item, sizeof(item), "%s{\"ssid\":\"%s\",\"rssi\":%d,\"secure\":%s}", sent ? "," : "", esc,
                 aps[i].rssi, aps[i].authmode == WIFI_AUTH_OPEN ? "false" : "true");
        httpd_resp_sendstr_chunk(req, item);
        sent++;
    }
    free(aps);
    httpd_resp_sendstr_chunk(req, "]");
    return httpd_resp_sendstr_chunk(req, NULL);
}

static void restart_later(void *arg)
{
    esp_restart();
}

static esp_err_t save_post(httpd_req_t *req)
{
    char body[400];
    if (req->content_len >= sizeof(body)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "too long");
    }
    int got = 0;
    while (got < (int)req->content_len) {
        int r = httpd_req_recv(req, body + got, req->content_len - (size_t)got);
        if (r <= 0) {
            return ESP_FAIL;
        }
        got += r;
    }
    body[got] = '\0';
    char ssid[33] = "", psk[65] = "", dns[16] = "";
    if (!form_field(body, "ssid", ssid, sizeof(ssid)) || !ssid[0]) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Choose or type a network name.");
    }
    form_field(body, "psk", psk, sizeof(psk));
    form_field(body, "dns", dns, sizeof(dns));
    size_t pl = strlen(psk);
    if (pl && (pl < 8 || pl > 63)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Wi-Fi passwords are 8 to 63 characters.");
    }
    if (dns[0] && !ul_is_ipv4(dns)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "The DNS server must be an address like 192.168.1.99.");
    }
    if (ul_wifi_save(ssid, psk, dns) != ESP_OK) {
        return httpd_resp_send_500(req);
    }
    char esc[200], html[sizeof(UL_SAVED_PAGE) + 200];
    html_escape(ssid, esc, sizeof(esc));
    snprintf(html, sizeof(html), UL_SAVED_PAGE, esc);
    httpd_resp_set_type(req, "text/html");
    httpd_resp_sendstr(req, html);
    esp_timer_handle_t t;
    const esp_timer_create_args_t ta = {.callback = restart_later, .name = "ul_restart"};
    if (esp_timer_create(&ta, &t) == ESP_OK) {
        esp_timer_start_once(t, 1500 * 1000);
    }
    return ESP_OK;
}

/* Anything else (phones probing for captive portals) goes to the page. */
static esp_err_t redirect(httpd_req_t *req, httpd_err_code_t err)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://" AP_IP "/");
    return httpd_resp_send(req, NULL, 0);
}

/* ---------- captive DNS ---------- */

static void dns_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (sock < 0 || bind(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        ESP_LOGE(TAG, "captive DNS: bind failed");
        vTaskDelete(NULL);
    }
    struct timeval tv = {.tv_sec = 1};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)); /* so the task notices the portal closing */
    uint8_t buf[512];
    const uint32_t ip = inet_addr(AP_IP);
    while (s_portal_up) {
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n = recvfrom(sock, buf, sizeof(buf) - 16, 0, (struct sockaddr *)&from, &fl);
        if (n < 12 || (buf[2] & 0x80)) {
            continue; /* too short, or a response */
        }
        /* Find the end of the first question (name, type, class). */
        int q = 12;
        while (q < n && buf[q]) {
            q += buf[q] + 1;
        }
        q += 5;
        if (q > n) {
            continue;
        }
        uint16_t qtype = (uint16_t)(buf[q - 4] << 8 | buf[q - 3]);
        buf[2] = 0x81;                 /* response, recursion desired */
        buf[3] = 0x80;                 /* recursion available, no error */
        buf[6] = 0; buf[7] = qtype == 1 ? 1 : 0;  /* answers */
        buf[8] = buf[9] = buf[10] = buf[11] = 0;
        int len = q;
        if (qtype == 1) {
            const uint8_t ans[] = {0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4};
            memcpy(buf + len, ans, sizeof(ans));
            len += sizeof(ans);
            memcpy(buf + len, &ip, 4);
            len += 4;
        }
        sendto(sock, buf, (size_t)len, 0, (struct sockaddr *)&from, fl);
    }
    close(sock);
    vTaskDelete(NULL);
}

/* ---------- portal ---------- */

esp_err_t ul_provision_open_portal(void)
{
    if (s_portal_up) {
        return ESP_OK;
    }
    char name[20];
    ap_name(name);
    ul_wifi_start_ap(name);
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.lru_purge_enable = true;
    cfg.max_uri_handlers = 6;
    cfg.stack_size = 6144;
    esp_err_t err = httpd_start(&s_http, &cfg);
    if (err != ESP_OK) {
        return err;
    }
    const httpd_uri_t uris[] = {
        {.uri = "/", .method = HTTP_GET, .handler = page_get},
        {.uri = "/scan", .method = HTTP_GET, .handler = scan_get},
        {.uri = "/save", .method = HTTP_POST, .handler = save_post},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(s_http, &uris[i]);
    }
    httpd_register_err_handler(s_http, HTTPD_404_NOT_FOUND, redirect);
    s_portal_up = true;
    xTaskCreate(dns_task, "ul_dns", 3072, NULL, 5, &s_dns_task);
    ESP_LOGI(TAG, "setup page at http://" AP_IP "/ on '%s'", name);
    esp_event_post(UL_EVENT, UL_EV_SETUP_STARTED, NULL, 0, 0);
    return ESP_OK;
}

static void close_portal(void)
{
    if (!s_portal_up) {
        return;
    }
    s_portal_up = false; /* the DNS task exits on its next packet */
    httpd_stop(s_http);
    s_http = NULL;
    ul_wifi_stop_ap();
    ESP_LOGI(TAG, "setup page closed");
}

bool ul_provision_portal_open(void)
{
    return s_portal_up;
}

static void fallback(void *arg)
{
    if (!ul_wifi_connected()) {
        ESP_LOGW(TAG, "no connection after 3 minutes, opening the setup page too");
        ul_provision_open_portal();
    }
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == UL_EV_NET_UP) {
        esp_timer_stop(s_fallback_timer);
        if (ul_wifi_has_credentials()) {
            close_portal();
        }
    }
}

/* ---------- reset button ---------- */

static void button_task(void *arg)
{
    /* The raw descriptor is fine here: reading an input can't harm a board that failed its checks,
     * and the button is how a badly set-up node gets back to the setup page. */
    const ul_board_desc_t *b = ul_board_raw();
    const gpio_num_t pin = (gpio_num_t)b->reset_gpio;
    const bool low = b->reset_active_low;
    const uint32_t hold = b->reset_hold_ms;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << pin, .mode = GPIO_MODE_INPUT,
        .pull_up_en = low ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE,
        .pull_down_en = low ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&io);
    uint32_t held = 0;
    bool fired = false;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(BUTTON_POLL_MS));
        bool down = gpio_get_level(pin) == (low ? 0 : 1);
        if (down) {
            held += BUTTON_POLL_MS;
            if (held >= hold && !fired) {
                fired = true;
                ESP_LOGW(TAG, "reset button held: forgetting Wi-Fi and opening the setup page");
                ul_btn_action_t a = UL_BTN_RESET;
                esp_event_post(UL_EVENT, UL_EV_BUTTON, &a, sizeof(a), 0);
                ul_wifi_forget();
                vTaskDelay(pdMS_TO_TICKS(500)); /* let the evt go out if online */
                esp_restart();
            }
        } else {
            if (held >= SHORT_PRESS_MS && !fired) {
                ul_btn_action_t a = held >= 1000 ? UL_BTN_HOLD : UL_BTN_PRESS;
                esp_event_post(UL_EVENT, UL_EV_BUTTON, &a, sizeof(a), 0);
            }
            held = 0;
            fired = false;
        }
    }
}

static esp_err_t ul_provision_init(void)
{
    const ul_board_desc_t *b = ul_board_raw();
    if (b && b->has_reset && b->reset_gpio <= UL_CHIP_RULES[b->chip].gpio_max &&
        !UL_CHIP_RULES[b->chip].unusable[b->reset_gpio]) {
        xTaskCreate(button_task, "ul_button", 2560, NULL, 4, NULL);
    }
    ESP_ERROR_CHECK(esp_event_handler_register(UL_EVENT, UL_EV_NET_UP, on_event, NULL));
    const esp_timer_create_args_t ta = {.callback = fallback, .name = "ul_setup_fallback"};
    ESP_ERROR_CHECK(esp_timer_create(&ta, &s_fallback_timer));
    if (!ul_wifi_has_credentials()) {
        return ul_provision_open_portal();
    }
    return esp_timer_start_once(s_fallback_timer, FALLBACK_US);
}

UL_MODULE_REGISTER(provision, "ul_provision", UL_STAGE_SERVICE, 20, ul_provision_init)
