/*
 * Key, CSR and enrollment.
 *
 * Boot: load key/cert from NVS. If the descriptor's device_id differs from the
 * one the certificate was issued to, the old identity is dropped (the node was
 * rebuilt as another device). A missing key is generated with PSA. With a
 * certificate the identity is ready; otherwise, with a token, the node enrolls
 * once it has an IP. Network and 5xx failures retry with backoff; 4xx answers
 * (bad or spent token) stop until the next boot, because retrying can't help.
 */
#include <stdlib.h>
#include <string.h>
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/pk.h"
#include "mbedtls/x509_csr.h"
#include "nvs.h"
#include "psa/crypto.h"
#include "sdkconfig.h"
#include "ul_board.h"
#include "ul_contract.h"
#include "ul_core.h"
#include "ul_identity.h"
#include "ul_wifi.h"

static const char *TAG = "ul_identity";

#define NVS_NS        "ul_id"
#define KEY_PEM_MAX   400
#define CSR_PEM_MAX   1024
#define RESP_MAX      8192
#define RETRY_MIN_S   10
#define RETRY_MAX_S   300

static char *s_key, *s_chain;
static char s_dev[UL_DEVICE_ID_SZ];
static char s_mqtt_host[UL_HOST_SZ];
static uint16_t s_mqtt_port;
static bool s_ready, s_enrolling, s_gave_up;
static uint32_t s_retry_s = RETRY_MIN_S;
static esp_timer_handle_t s_retry_timer;

/* ---------- NVS ---------- */

static char *nvs_get_alloc(nvs_handle_t h, const char *k)
{
    size_t n = 0;
    if (nvs_get_blob(h, k, NULL, &n) != ESP_OK || n == 0) {
        return NULL;
    }
    char *v = malloc(n + 1);
    if (v && nvs_get_blob(h, k, v, &n) == ESP_OK) {
        v[n] = '\0';
        return v;
    }
    free(v);
    return NULL;
}

static void load(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    s_key = nvs_get_alloc(h, "key");
    s_chain = nvs_get_alloc(h, "chain");
    size_t n = sizeof(s_dev);
    if (nvs_get_str(h, "dev", s_dev, &n) != ESP_OK) {
        s_dev[0] = '\0';
    }
    n = sizeof(s_mqtt_host);
    if (nvs_get_str(h, "mqtt_host", s_mqtt_host, &n) != ESP_OK) {
        s_mqtt_host[0] = '\0';
    }
    if (nvs_get_u16(h, "mqtt_port", &s_mqtt_port) != ESP_OK) {
        s_mqtt_port = 0;
    }
    nvs_close(h);
}

esp_err_t ul_identity_forget(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        nvs_erase_all(h);
        err = nvs_commit(h);
        nvs_close(h);
    }
    return err;
}

static void forget_cert(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_key(h, "chain");
        nvs_erase_key(h, "dev");
        nvs_erase_key(h, "mqtt_host");
        nvs_erase_key(h, "mqtt_port");
        nvs_commit(h);
        nvs_close(h);
    }
    free(s_chain);
    s_chain = NULL;
    s_dev[0] = '\0';
    s_mqtt_host[0] = '\0';
    s_mqtt_port = 0;
}

/* ---------- key and CSR ---------- */

static esp_err_t generate_key(void)
{
    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_set_key_type(&attr, PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));
    psa_set_key_bits(&attr, 256);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_SIGN_HASH | PSA_KEY_USAGE_EXPORT);
    psa_set_key_algorithm(&attr, PSA_ALG_ECDSA(PSA_ALG_SHA_256));
    mbedtls_svc_key_id_t id;
    int64_t t0 = esp_timer_get_time();
    psa_status_t st = psa_generate_key(&attr, &id);
    if (st != PSA_SUCCESS) {
        ESP_LOGE(TAG, "key generation failed: %d", (int)st);
        return ESP_FAIL;
    }
    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);
    char *pem = calloc(1, KEY_PEM_MAX);
    int rc = pem ? mbedtls_pk_copy_from_psa(id, &pk) : -1;
    if (rc == 0) {
        rc = mbedtls_pk_write_key_pem(&pk, (unsigned char *)pem, KEY_PEM_MAX);
    }
    mbedtls_pk_free(&pk);
    psa_destroy_key(id);
    if (rc != 0) {
        free(pem);
        ESP_LOGE(TAG, "key export failed: -0x%04x", (unsigned)-rc);
        return ESP_FAIL;
    }
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_blob(h, "key", pem, strlen(pem));
        nvs_commit(h);
        nvs_close(h);
    }
    if (err != ESP_OK) {
        free(pem);
        return err;
    }
    s_key = pem;
    ESP_LOGI(TAG, "generated P-256 key in %lld ms", (esp_timer_get_time() - t0) / 1000);
    return ESP_OK;
}

static char *make_csr(const char *device_id)
{
    mbedtls_pk_context pk;
    mbedtls_x509write_csr csr;
    mbedtls_pk_init(&pk);
    mbedtls_x509write_csr_init(&csr);
    char *out = calloc(1, CSR_PEM_MAX);
    char subject[32];
    snprintf(subject, sizeof(subject), "CN=%s", device_id);
    int rc = out ? mbedtls_pk_parse_key(&pk, (const unsigned char *)s_key, strlen(s_key) + 1, NULL, 0) : -1;
    if (rc == 0) {
        mbedtls_x509write_csr_set_key(&csr, &pk);
        mbedtls_x509write_csr_set_md_alg(&csr, MBEDTLS_MD_SHA256);
        rc = mbedtls_x509write_csr_set_subject_name(&csr, subject);
    }
    if (rc == 0) {
        rc = mbedtls_x509write_csr_pem(&csr, (unsigned char *)out, CSR_PEM_MAX);
    }
    mbedtls_x509write_csr_free(&csr);
    mbedtls_pk_free(&pk);
    if (rc != 0) {
        ESP_LOGE(TAG, "CSR failed: -0x%04x", (unsigned)-rc);
        free(out);
        return NULL;
    }
    return out;
}

/* ---------- enrollment ---------- */

typedef enum { ENROLL_OK, ENROLL_RETRY, ENROLL_STOP } enroll_rc_t;

static enroll_rc_t enroll_once(const ul_provision_t *p)
{
    char *csr = make_csr(p->device_id);
    if (!csr) {
        return ENROLL_STOP;
    }
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    ul_enroll_req_t req = {
        .device_id = p->device_id, .token = p->token, .csr = csr,
        .fw = esp_app_get_description()->version, .profile = CONFIG_UL_PROFILE,
        .chip = ul_board_running_chip(), .board_crc = ul_board_crc(),
    };
    memcpy(req.mac, mac, 6);
    char *body = ul_enroll_req_encode(&req);
    free(csr);
    if (!body) {
        return ENROLL_RETRY;
    }

    char url[UL_HOST_SZ + 32];
    snprintf(url, sizeof(url), "https://%s/api/v1/enroll", p->server);
    esp_http_client_config_t cfg = {
        .url = url, .method = HTTP_METHOD_POST, .timeout_ms = 15000,
        .crt_bundle_attach = p->server_ca[0] ? NULL : esp_crt_bundle_attach,
        .cert_pem = p->server_ca[0] ? p->server_ca : NULL,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    enroll_rc_t rc = ENROLL_RETRY;
    char *resp = NULL;
    if (!c) {
        free(body);
        return ENROLL_RETRY;
    }
    esp_http_client_set_header(c, "Content-Type", "application/json");
    size_t blen = strlen(body);
    esp_err_t err = esp_http_client_open(c, (int)blen);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "enroll: can't reach %s: %s", p->server, esp_err_to_name(err));
        goto out;
    }
    if (esp_http_client_write(c, body, (int)blen) != (int)blen) {
        goto out;
    }
    esp_http_client_fetch_headers(c);
    int status = esp_http_client_get_status_code(c);
    resp = calloc(1, RESP_MAX + 1);
    int got = 0, r;
    while (resp && got < RESP_MAX && (r = esp_http_client_read(c, resp + got, RESP_MAX - got)) > 0) {
        got += r;
    }
    ul_enroll_resp_t er;
    char derr[96];
    if (status == 200 && resp && ul_enroll_resp_decode(resp, (size_t)got, &er, derr, sizeof(derr)) == 0 && er.ok) {
        if (strcmp(er.device_id, p->device_id) != 0) {
            ESP_LOGE(TAG, "enroll: certificate is for %s, not %s", er.device_id, p->device_id);
            ul_enroll_resp_free(&er);
            rc = ENROLL_STOP;
            goto out;
        }
        size_t cl = strlen(er.cert), al = strlen(er.ca_chain);
        char *chain = malloc(cl + al + 2);
        if (chain) {
            memcpy(chain, er.cert, cl);
            size_t o = cl;
            if (o && chain[o - 1] != '\n') {
                chain[o++] = '\n';
            }
            memcpy(chain + o, er.ca_chain, al + 1);
        }
        nvs_handle_t h;
        if (chain && nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
            nvs_set_blob(h, "chain", chain, strlen(chain));
            nvs_set_str(h, "dev", p->device_id);
            nvs_set_str(h, "mqtt_host", er.mqtt_host);
            nvs_set_u16(h, "mqtt_port", er.mqtt_port);
            nvs_commit(h);
            nvs_close(h);
            free(s_chain);
            s_chain = chain;
            snprintf(s_dev, sizeof(s_dev), "%s", p->device_id);
            snprintf(s_mqtt_host, sizeof(s_mqtt_host), "%s", er.mqtt_host);
            s_mqtt_port = er.mqtt_port;
            rc = ENROLL_OK;
        } else {
            free(chain);
        }
        ul_enroll_resp_free(&er);
    } else if (status >= 400 && status < 500 && status != 429) {
        ul_enroll_resp_t e = {0};
        ul_enroll_resp_decode(resp ? resp : "", (size_t)got, &e, derr, sizeof(derr));
        ESP_LOGE(TAG, "enroll refused (HTTP %d %s); flash a new token from the node builder", status,
                 e.error[0] ? e.error : "");
        ul_error_t ev = {0};
        snprintf(ev.code, sizeof(ev.code), "enroll_%s", e.error[0] ? e.error : "refused");
        snprintf(ev.detail, sizeof(ev.detail), "HTTP %d", status);
        esp_event_post(UL_EVENT, UL_EV_IDENTITY_FAILED, &ev, sizeof(ev), 0);
        ul_enroll_resp_free(&e);
        rc = ENROLL_STOP;
    } else {
        ESP_LOGW(TAG, "enroll: HTTP %d, will retry", status);
    }
out:
    free(resp);
    free(body);
    esp_http_client_cleanup(c);
    return rc;
}

static void enroll_task(void *arg)
{
    const ul_provision_t *p = ul_board_provision();
    ESP_LOGI(TAG, "enrolling %s with %s", p->device_id, p->server);
    enroll_rc_t rc = enroll_once(p);
    s_enrolling = false;
    if (rc == ENROLL_OK) {
        s_ready = true;
        ESP_LOGI(TAG, "enrolled; certificate saved");
        esp_event_post(UL_EVENT, UL_EV_IDENTITY_READY, NULL, 0, 0);
    } else if (rc == ENROLL_STOP) {
        s_gave_up = true;
    } else {
        esp_timer_start_once(s_retry_timer, (uint64_t)s_retry_s * 1000000);
        s_retry_s = s_retry_s * 2 > RETRY_MAX_S ? RETRY_MAX_S : s_retry_s * 2;
    }
    vTaskDelete(NULL);
}

static void start_enroll(void)
{
    if (s_ready || s_enrolling || s_gave_up) {
        return;
    }
    s_enrolling = true;
    if (xTaskCreate(enroll_task, "ul_enroll", 8192, NULL, 5, NULL) != pdPASS) {
        s_enrolling = false;
    }
}

static void retry(void *arg)
{
    start_enroll();
}

static void on_net_up(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    start_enroll();
}

static esp_err_t ul_identity_init(void)
{
    const ul_provision_t *p = ul_board_provision();
    load();
    if (!p) {
        ESP_LOGW(TAG, "no provisioning data: can't enroll");
        return ESP_OK;
    }
    if (s_dev[0] && strcmp(s_dev, p->device_id) != 0) {
        ESP_LOGW(TAG, "descriptor is now %s (was %s): dropping the old identity", p->device_id, s_dev);
        forget_cert();
        free(s_key);
        s_key = NULL;
        ul_identity_forget();
    }
    if (!s_key && generate_key() != ESP_OK) {
        return ESP_FAIL;
    }
    if (s_chain && s_dev[0]) {
        s_ready = true;
        ESP_LOGI(TAG, "identity %s ready", s_dev);
        return esp_event_post(UL_EVENT, UL_EV_IDENTITY_READY, NULL, 0, 0);
    }
    if (!p->token[0]) {
        ul_error_t ev = {.code = "no_token"};
        snprintf(ev.detail, sizeof(ev.detail), "no certificate and no enrollment token");
        ESP_LOGE(TAG, "%s", ev.detail);
        return esp_event_post(UL_EVENT, UL_EV_IDENTITY_FAILED, &ev, sizeof(ev), 0);
    }
    const esp_timer_create_args_t ta = {.callback = retry, .name = "ul_enroll_retry"};
    ESP_ERROR_CHECK(esp_timer_create(&ta, &s_retry_timer));
    esp_err_t err = esp_event_handler_register(UL_EVENT, UL_EV_NET_UP, on_net_up, NULL);
    if (ul_wifi_connected()) {
        start_enroll();
    }
    return err;
}

bool ul_identity_ready(void) { return s_ready; }
const char *ul_identity_cert_chain(void) { return s_chain; }
const char *ul_identity_key(void) { return s_key; }

const char *ul_identity_device_id(void)
{
    const ul_provision_t *p = ul_board_provision();
    return p ? p->device_id : NULL;
}

const char *ul_identity_mqtt_host(void)
{
    const ul_provision_t *p = ul_board_provision();
    return s_mqtt_host[0] ? s_mqtt_host : (p ? p->server : NULL);
}

uint16_t ul_identity_mqtt_port(void)
{
    const ul_provision_t *p = ul_board_provision();
    return s_mqtt_port ? s_mqtt_port : (p ? p->mqtt_port : 8883);
}

const char *ul_identity_server_ca(void)
{
    const ul_provision_t *p = ul_board_provision();
    return p && p->server_ca[0] ? p->server_ca : NULL;
}

UL_MODULE_REGISTER(identity, "ul_identity", UL_STAGE_SERVICE, 30, ul_identity_init)
