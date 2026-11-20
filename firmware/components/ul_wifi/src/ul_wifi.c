#include <string.h>
#include <time.h>
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "lwip/ip4_addr.h"
#include "nvs.h"
#include "ul_board.h"
#include "ul_contract.h"
#include "ul_core.h"
#include "ul_wifi.h"

static const char *TAG = "ul_wifi";

#define NVS_NS          "ul_wifi"
#define BACKOFF_MIN_MS  1000
#define BACKOFF_MAX_MS  60000
#define DNS_CHECK_US    (30 * 1000 * 1000)

static esp_netif_t *s_sta, *s_ap;
static char s_ssid[33], s_psk[65], s_dns[16];
static bool s_have_creds, s_connected, s_sntp_started;
static uint32_t s_reconnects, s_backoff_ms = BACKOFF_MIN_MS;
static esp_timer_handle_t s_retry_timer, s_dns_timer;

/* ---------- NVS ---------- */

static void load(void)
{
    nvs_handle_t h;
    s_have_creds = false;
    s_dns[0] = '\0';
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    size_t n = sizeof(s_ssid);
    if (nvs_get_str(h, "ssid", s_ssid, &n) == ESP_OK && s_ssid[0]) {
        s_have_creds = true;
        n = sizeof(s_psk);
        if (nvs_get_str(h, "psk", s_psk, &n) != ESP_OK) {
            s_psk[0] = '\0';
        }
    }
    n = sizeof(s_dns);
    if (nvs_get_str(h, "dns", s_dns, &n) != ESP_OK || !ul_is_ipv4(s_dns)) {
        s_dns[0] = '\0';
    }
    nvs_close(h);
}

esp_err_t ul_wifi_save(const char *ssid, const char *psk, const char *dns)
{
    if (!ssid || !ssid[0] || strlen(ssid) > 32 || (psk && strlen(psk) > 64) ||
        (dns && dns[0] && !ul_is_ipv4(dns))) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "psk", psk ? psk : "");
    if (dns && dns[0]) {
        nvs_set_str(h, "dns", dns);
    } else {
        nvs_erase_key(h, "dns");
    }
    err = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "saved network '%s'%s%s", ssid, dns && dns[0] ? ", DNS " : "", dns && dns[0] ? dns : "");
    return err;
}

esp_err_t ul_wifi_forget(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        nvs_erase_key(h, "ssid");
        nvs_erase_key(h, "psk");
        nvs_erase_key(h, "dns");
        err = nvs_commit(h);
        nvs_close(h);
    }
    return err;
}

/*
 * A descriptor flashed with Wi-Fi (bench nodes) seeds NVS once per descriptor:
 * the CRC of the copied descriptor is remembered, so a later Wi-Fi reset from
 * the button isn't undone by copying the same credentials again.
 */
static void seed_from_board(void)
{
    const ul_provision_t *p = ul_board_provision();
    if (!p || !p->has_wifi) {
        return;
    }
    uint32_t crc = ul_board_crc(), seeded = 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    if (nvs_get_u32(h, "seeded_crc", &seeded) != ESP_OK || seeded != crc) {
        nvs_set_u32(h, "seeded_crc", crc);
        nvs_commit(h);
        nvs_close(h);
        ul_wifi_save(p->ssid, p->psk, p->has_dns ? p->dns : NULL);
        ESP_LOGI(TAG, "Wi-Fi seeded from the board descriptor");
        return;
    }
    nvs_close(h);
}

/* ---------- DNS override ---------- */

static void apply_dns(void)
{
    if (!s_dns[0] || !s_sta) {
        return;
    }
    esp_netif_dns_info_t cur = {0};
    esp_netif_get_dns_info(s_sta, ESP_NETIF_DNS_MAIN, &cur);
    esp_netif_dns_info_t want = {.ip.type = ESP_IPADDR_TYPE_V4};
    want.ip.u_addr.ip4.addr = ipaddr_addr(s_dns);
    if (cur.ip.type == ESP_IPADDR_TYPE_V4 && cur.ip.u_addr.ip4.addr == want.ip.u_addr.ip4.addr) {
        return;
    }
    esp_netif_set_dns_info(s_sta, ESP_NETIF_DNS_MAIN, &want);
    /* No router fallback: a different answer for lights.evm100.org would defeat the override. */
    esp_netif_dns_info_t none = {.ip.type = ESP_IPADDR_TYPE_V4};
    esp_netif_set_dns_info(s_sta, ESP_NETIF_DNS_BACKUP, &none);
    ESP_LOGI(TAG, "DNS set to %s", s_dns);
}

static void dns_check(void *arg)
{
    if (s_connected) {
        apply_dns(); /* DHCP renewals may have replaced it */
    }
}

/* ---------- connection ---------- */

static void retry(void *arg)
{
    esp_wifi_connect();
}

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (!s_have_creds) {
        return; /* station is only up for scanning on the setup page */
    }
    if (id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        bool was = s_connected;
        s_connected = false;
        s_reconnects++;
        ESP_LOGW(TAG, "disconnected (reason %d), retry in %lu ms", d->reason, (unsigned long)s_backoff_ms);
        esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_ms * 1000);
        s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
        if (was) {
            esp_event_post(UL_EVENT, UL_EV_NET_DOWN, NULL, 0, 0);
        }
    }
}

static void on_ip(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id != IP_EVENT_STA_GOT_IP) {
        return;
    }
    const ip_event_got_ip_t *e = data;
    apply_dns();
    s_backoff_ms = BACKOFF_MIN_MS;
    ESP_LOGI(TAG, "got " IPSTR, IP2STR(&e->ip_info.ip));
    if (!s_sntp_started) {
        esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
        esp_netif_sntp_init(&cfg);
        s_sntp_started = true;
    }
    if (!s_connected) {
        s_connected = true;
        esp_event_post(UL_EVENT, UL_EV_NET_UP, NULL, 0, 0);
    }
}

esp_netif_t *ul_wifi_start_ap(const char *ssid)
{
    if (!s_ap) {
        s_ap = esp_netif_create_default_wifi_ap();
    }
    wifi_config_t ap = {.ap = {.channel = 1, .max_connection = 4, .authmode = WIFI_AUTH_OPEN}};
    size_t n = strlen(ssid);
    memcpy(ap.ap.ssid, ssid, n > 32 ? 32 : n);
    ap.ap.ssid_len = (uint8_t)(n > 32 ? 32 : n);
    esp_wifi_set_mode(WIFI_MODE_APSTA);
    esp_wifi_set_config(WIFI_IF_AP, &ap);
    ESP_LOGI(TAG, "setup network '%s' up", ssid);
    return s_ap;
}

void ul_wifi_stop_ap(void)
{
    esp_wifi_set_mode(WIFI_MODE_STA);
}

static esp_err_t ul_wifi_init(void)
{
    seed_from_board();
    load();
    s_sta = esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM)); /* our NVS keys are the only copy */
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_ip, NULL));
    const esp_timer_create_args_t rt = {.callback = retry, .name = "ul_wifi_retry"};
    const esp_timer_create_args_t dt = {.callback = dns_check, .name = "ul_wifi_dns"};
    ESP_ERROR_CHECK(esp_timer_create(&rt, &s_retry_timer));
    ESP_ERROR_CHECK(esp_timer_create(&dt, &s_dns_timer));
    if (s_dns[0]) {
        esp_timer_start_periodic(s_dns_timer, DNS_CHECK_US);
    }

    wifi_config_t sta = {0};
    if (s_have_creds) {
        memcpy(sta.sta.ssid, s_ssid, strlen(s_ssid));
        memcpy(sta.sta.password, s_psk, strlen(s_psk));
        sta.sta.threshold.authmode = s_psk[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
        sta.sta.pmf_cfg.capable = true;
        sta.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
        sta.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    }
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta));
    ESP_ERROR_CHECK(esp_wifi_start());
    if (!s_have_creds) {
        ESP_LOGW(TAG, "no Wi-Fi credentials");
    } else {
        ESP_LOGI(TAG, "joining '%s'%s%s", s_ssid, s_dns[0] ? " with DNS " : "", s_dns);
    }
    return ESP_OK;
}

bool ul_wifi_has_credentials(void) { return s_have_creds; }
bool ul_wifi_connected(void) { return s_connected; }
uint32_t ul_wifi_reconnects(void) { return s_reconnects; }
bool ul_wifi_time_synced(void) { return time(NULL) > 1700000000; }

int ul_wifi_rssi(void)
{
    int rssi = 0;
    return s_connected && esp_wifi_sta_get_rssi(&rssi) == ESP_OK ? rssi : 0;
}

int ul_wifi_channel(void)
{
    wifi_ap_record_t ap;
    return s_connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.primary : 0;
}

void ul_wifi_ip(char out[16])
{
    esp_netif_ip_info_t ip;
    out[0] = '\0';
    if (s_connected && esp_netif_get_ip_info(s_sta, &ip) == ESP_OK) {
        snprintf(out, 16, IPSTR, IP2STR(&ip.ip));
    }
}

void ul_wifi_dns(char out[16])
{
    esp_netif_dns_info_t d;
    out[0] = '\0';
    if (s_sta && esp_netif_get_dns_info(s_sta, ESP_NETIF_DNS_MAIN, &d) == ESP_OK &&
        d.ip.type == ESP_IPADDR_TYPE_V4 && d.ip.u_addr.ip4.addr) {
        snprintf(out, 16, IPSTR, IP2STR(&d.ip.u_addr.ip4));
    }
}

UL_MODULE_REGISTER(wifi, "ul_wifi", UL_STAGE_SERVICE, 10, ul_wifi_init)
