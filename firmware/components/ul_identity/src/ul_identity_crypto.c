#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_log.h"
#include "mbedtls/pk.h"
#include "mbedtls/x509_csr.h"
#include "psa/crypto.h"
#include "ul_identity_crypto.h"

static const char *TAG = "ul_identity";

#define KEY_PEM_MAX 400
#define CSR_PEM_MAX 1024

esp_err_t ul_id_generate_key(char **pem_out)
{
    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;
    psa_set_key_type(&attr, PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));
    psa_set_key_bits(&attr, 256);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_SIGN_HASH | PSA_KEY_USAGE_EXPORT);
    psa_set_key_algorithm(&attr, PSA_ALG_ECDSA(PSA_ALG_SHA_256));
    mbedtls_svc_key_id_t id;
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
    *pem_out = pem;
    return ESP_OK;
}

char *ul_id_make_csr(const char *key_pem, const char *device_id)
{
    mbedtls_pk_context pk;
    mbedtls_x509write_csr csr;
    mbedtls_pk_init(&pk);
    mbedtls_x509write_csr_init(&csr);
    char *out = calloc(1, CSR_PEM_MAX);
    char subject[32];
    snprintf(subject, sizeof(subject), "CN=%s", device_id);
    int rc = out ? mbedtls_pk_parse_key(&pk, (const unsigned char *)key_pem, strlen(key_pem) + 1, NULL, 0) : -1;
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
