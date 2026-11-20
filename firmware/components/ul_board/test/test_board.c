/* Host tests: ul_board blob vector and pin rules against contracts/examples. */
#include "cJSON.h"
#include "test_util.h"
#include "ul_board_rules.h"
#include "ul_contract.h"

static char errbuf[160];

static void test_crc32_known_value(void)
{
    TEST_ASSERT_EQUAL_HEX32(0xCBF43926, ul_crc32("123456789", 9));
    TEST_ASSERT_EQUAL_HEX32(0, ul_crc32("", 0));
}

static void test_vector_decodes(void)
{
    size_t n;
    uint8_t *blob = (uint8_t *)ut_read(EX("vectors/ul_board.c3-bench.bin"), &n);
    char *crc_txt = ut_read(EX("vectors/ul_board.c3-bench.crc"), NULL);
    TEST_ASSERT_NOT_NULL(blob);
    TEST_ASSERT_EQUAL_size_t(4096, n);
    const char *payload;
    size_t len;
    uint32_t crc;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_blob_parse(blob, n, &payload, &len, &crc, errbuf, sizeof(errbuf)), errbuf);
    char hex[9];
    snprintf(hex, sizeof(hex), "%08x", (unsigned)crc);
    TEST_ASSERT_EQUAL_STRING_LEN(crc_txt, hex, 8);

    static ul_board_desc_t b, b2;
    static ul_provision_t p, p2;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_partition_decode(payload, len, &b, &p, errbuf, sizeof(errbuf)), errbuf);
    TEST_ASSERT_EQUAL(UL_CHIP_ESP32C3, b.chip);
    TEST_ASSERT_EQUAL_INT(4, b.n_channels);
    TEST_ASSERT_EQUAL_STRING("strip", b.ch[2].id);
    TEST_ASSERT_EQUAL(UL_CH_ARGB, b.ch[2].type);
    TEST_ASSERT_EQUAL_UINT16(60, b.ch[2].pixels);
    TEST_ASSERT_TRUE(b.has_reset);
    TEST_ASSERT_EQUAL_INT(9, b.reset_gpio);
    TEST_ASSERT_TRUE(b.reset_active_low);
    TEST_ASSERT_EQUAL_UINT32(5000, b.reset_hold_ms);
    TEST_ASSERT_EQUAL_STRING("ul-0a1b2c", p.device_id);
    TEST_ASSERT_EQUAL_STRING("lights.evm100.org", p.server);
    TEST_ASSERT_EQUAL_UINT16(8883, p.mqtt_port);
    TEST_ASSERT_TRUE(p.has_wifi);
    TEST_ASSERT_EQUAL_STRING("ExampleNet", p.ssid);
    TEST_ASSERT_TRUE(p.has_dns);
    TEST_ASSERT_EQUAL_STRING("192.168.1.99", p.dns);

    /* Same content as the source JSON. */
    size_t jn;
    char *j = ut_read(EX("partition/c3-bench.json"), &jn);
    TEST_ASSERT_EQUAL_INT(0, ul_partition_decode(j, jn, &b2, &p2, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_MEMORY(&b, &b2, sizeof(b));
    TEST_ASSERT_EQUAL_MEMORY(&p, &p2, sizeof(p));
    free(j);
    free(crc_txt);
    free(blob);
}

static void test_blob_rejects_damage(void)
{
    size_t n;
    uint8_t *blob = (uint8_t *)ut_read(EX("vectors/ul_board.c3-bench.bin"), &n);
    const char *payload;
    size_t len;
    uint32_t crc;
    blob[40] ^= 1;
    TEST_ASSERT_EQUAL_INT(-1, ul_board_blob_parse(blob, n, &payload, &len, &crc, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_STRING("crc mismatch", errbuf);
    memset(blob, 0xFF, n);
    TEST_ASSERT_EQUAL_INT(-1, ul_board_blob_parse(blob, n, &payload, &len, &crc, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_STRING("erased", errbuf);
    memcpy(blob, "XXXX", 4);
    TEST_ASSERT_EQUAL_INT(-1, ul_board_blob_parse(blob, n, &payload, &len, &crc, errbuf, sizeof(errbuf)));
    free(blob);
}

/* Each board-rules example lists the exact result contracts/tools/pins.py gives. */
static void check_rules(const char *path, const char *name)
{
    char *j = ut_read(path, NULL);
    cJSON *root = cJSON_Parse(j);
    TEST_ASSERT_NOT_NULL(root);
    cJSON *expect = cJSON_DetachItemFromObjectCaseSensitive(root, "_expect");
    TEST_ASSERT_NOT_NULL(expect);
    static ul_board_desc_t b;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_from_cjson(root, &b, errbuf, sizeof(errbuf)), errbuf);
    static ul_pin_report_t r;
    ul_board_check_pins(&b, &r);

    const char *lists[2] = {"errors", "warnings"};
    for (int l = 0; l < 2; l++) {
        cJSON *want = cJSON_GetObjectItemCaseSensitive(expect, lists[l]);
        int got_n = l == 0 ? r.n_err : r.n_warn;
        char (*got)[UL_PIN_CODE_SZ] = l == 0 ? r.err : r.warn;
        char msg[256];
        snprintf(msg, sizeof(msg), "%s %s count", name, lists[l]);
        TEST_ASSERT_EQUAL_INT_MESSAGE(cJSON_GetArraySize(want), got_n, msg);
        for (int i = 0; i < got_n; i++) {
            snprintf(msg, sizeof(msg), "%s %s[%d]", name, lists[l], i);
            TEST_ASSERT_EQUAL_STRING_MESSAGE(cJSON_GetArrayItem(want, i)->valuestring, got[i], msg);
        }
    }
    cJSON_Delete(expect);
    cJSON_Delete(root);
    free(j);
}

static void test_pin_rules_match_reference(void)
{
    TEST_ASSERT_GREATER_THAN_INT(0, ut_each_json(EX("board-rules"), check_rules));
}

static void clean_board(const char *path, const char *name)
{
    char *j = ut_read(path, NULL);
    cJSON *root = cJSON_Parse(j);
    static ul_board_desc_t b;
    TEST_ASSERT_EQUAL_INT(0, ul_board_from_cjson(root, &b, errbuf, sizeof(errbuf)));
    static ul_pin_report_t r;
    ul_board_check_pins(&b, &r);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, r.n_err, name);
    cJSON_Delete(root);
    free(j);
}

static void test_example_boards_have_no_pin_errors(void)
{
    TEST_ASSERT_GREATER_THAN_INT(0, ut_each_json(EX("board"), clean_board));
}

void run_board_tests(void)
{
    RUN_TEST(test_crc32_known_value);
    RUN_TEST(test_vector_decodes);
    RUN_TEST(test_blob_rejects_damage);
    RUN_TEST(test_pin_rules_match_reference);
    RUN_TEST(test_example_boards_have_no_pin_errors);
}
