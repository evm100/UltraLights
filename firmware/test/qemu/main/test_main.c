/*
 * QEMU tests for code that needs the real ESP-IDF runtime (mbedTLS 4 / PSA).
 * Prints the CSR between markers so tools/qemu_test.sh can verify it with openssl.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mbedtls/pk.h"
#include "mbedtls/x509_csr.h"
#include "unity.h"
#include "ul_identity_crypto.h"

static char *s_key;

static void test_key_generation(void)
{
    TEST_ASSERT_EQUAL(ESP_OK, ul_id_generate_key(&s_key));
    TEST_ASSERT_NOT_NULL(strstr(s_key, "-----BEGIN EC PRIVATE KEY-----"));
    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);
    TEST_ASSERT_EQUAL_INT(0, mbedtls_pk_parse_key(&pk, (const unsigned char *)s_key, strlen(s_key) + 1, NULL, 0));
    TEST_ASSERT_EQUAL_size_t(256, mbedtls_pk_get_bitlen(&pk));
    mbedtls_pk_free(&pk);
}

static void test_csr(void)
{
    TEST_ASSERT_NOT_NULL(s_key);
    char *csr = ul_id_make_csr(s_key, "ul-0a1b2c");
    TEST_ASSERT_NOT_NULL(csr);
    mbedtls_x509_csr parsed;
    mbedtls_x509_csr_init(&parsed);
    TEST_ASSERT_EQUAL_INT(0, mbedtls_x509_csr_parse(&parsed, (const unsigned char *)csr, strlen(csr) + 1));
    char dn[64];
    TEST_ASSERT_GREATER_THAN_INT(0, mbedtls_x509_dn_gets(dn, sizeof(dn), &parsed.subject));
    TEST_ASSERT_EQUAL_STRING("CN=ul-0a1b2c", dn);
    mbedtls_x509_csr_free(&parsed);
    printf("UL_CSR_BEGIN\n%sUL_CSR_END\n", csr);
    free(csr);
}

void app_main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_key_generation);
    RUN_TEST(test_csr);
    UNITY_END();
    printf("UL_QEMU_DONE\n");
}
