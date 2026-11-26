/* Host tests: every contract example decodes (or is rejected), and every
 * encoder reproduces the matching example. Built by firmware/test/host. */
#include "cJSON.h"
#include "test_util.h"
#include "ul_board_rules.h"
#include "ul_contract.h"
#include "ul_core_logic.h"

static char errbuf[160];

/* ---------- decoders accept every valid example ---------- */

typedef int (*decode_fn)(const char *, size_t, void *, char *, size_t);

static int dec_set(const char *j, size_t n, void *o, char *e, size_t el) { return ul_set_decode(j, n, o, e, el); }
static int dec_live(const char *j, size_t n, void *o, char *e, size_t el) { return ul_live_decode(j, n, o, e, el); }
static int dec_state(const char *j, size_t n, void *o, char *e, size_t el) { return ul_state_decode(j, n, o, e, el); }
static int dec_cmd(const char *j, size_t n, void *o, char *e, size_t el) { return ul_cmd_decode(j, n, o, e, el); }
static int dec_config(const char *j, size_t n, void *o, char *e, size_t el) { return ul_config_decode(j, n, o, e, el); }
static int dec_board(const char *j, size_t n, void *o, char *e, size_t el)
{
    cJSON *root = cJSON_ParseWithLength(j, n);
    int rc = root ? ul_board_from_cjson(root, o, e, el) : -1;
    cJSON_Delete(root);
    return rc;
}
static int dec_enroll(const char *j, size_t n, void *o, char *e, size_t el)
{
    int rc = ul_enroll_resp_decode(j, n, o, e, el);
    ul_enroll_resp_free(o);
    return rc;
}
static int dec_partition(const char *j, size_t n, void *o, char *e, size_t el)
{
    static ul_provision_t p;
    return ul_partition_decode(j, n, o, &p, e, el);
}

static decode_fn cur_fn;
static bool cur_expect_ok;
static union {
    ul_state_msg_t s;
    ul_cmd_t c;
    ul_config_t cfg;
    ul_board_desc_t b;
    ul_enroll_resp_t r;
} scratch;

static void check_one(const char *path, const char *name)
{
    size_t n;
    char *j = ut_read(path, &n);
    TEST_ASSERT_NOT_NULL(j);
    errbuf[0] = 0;
    int rc = cur_fn(j, n, &scratch, errbuf, sizeof(errbuf));
    char msg[1400];
    snprintf(msg, sizeof(msg), "%s: %s", path, cur_expect_ok ? errbuf : "should be rejected");
    if (cur_expect_ok) {
        TEST_ASSERT_EQUAL_INT_MESSAGE(0, rc, msg);
    } else {
        TEST_ASSERT_EQUAL_INT_MESSAGE(-1, rc, msg);
    }
    free(j);
}

static void each(const char *dir, decode_fn fn, bool ok)
{
    cur_fn = fn;
    cur_expect_ok = ok;
    int n = ut_each_json(dir, check_one);
    TEST_ASSERT_GREATER_THAN_INT_MESSAGE(0, n, dir);
}

static void test_valid_examples_decode(void)
{
    each(EX("set"), dec_set, true);
    each(EX("live"), dec_live, true);
    each(EX("state"), dec_state, true);
    each(EX("cmd"), dec_cmd, true);
    each(EX("config"), dec_config, true);
    each(EX("board"), dec_board, true);
    each(EX("partition"), dec_partition, true);
    each(EX("enroll-response"), dec_enroll, true);
}

static void test_invalid_examples_rejected(void)
{
    each(EX("invalid/set"), dec_set, false);
    each(EX("invalid/live"), dec_live, false);
    each(EX("invalid/cmd"), dec_cmd, false);
    each(EX("invalid/config"), dec_config, false);
    each(EX("invalid/board"), dec_board, false);
}

/* ---------- round trips ---------- */

static void roundtrip_state(const char *path, const char *name)
{
    size_t n;
    char *j = ut_read(path, &n);
    ul_state_msg_t a, b;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, cur_fn(j, n, &a, errbuf, sizeof(errbuf)), errbuf);
    a.fields &= ~(uint32_t)UL_F_FADE; /* state has no fade_ms */
    char *enc = ul_state_encode(&a);
    TEST_ASSERT_NOT_NULL(enc);
    char tag[256];
    snprintf(tag, sizeof(tag), "from-%s", name);
    tag[strlen(tag) - 5] = 0;
    ut_emit("state", tag, enc);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_state_decode(enc, strlen(enc), &b, errbuf, sizeof(errbuf)), errbuf);
    if (!(a.fields & UL_F_ON)) {
        b.fields &= ~(uint32_t)UL_F_ON; /* live without "on" gains on:false */
        b.on = a.on;
    }
    TEST_ASSERT_EQUAL_HEX32(a.fields, b.fields);
    TEST_ASSERT_EQUAL(a.on, b.on);
    TEST_ASSERT_EQUAL_FLOAT(a.level, b.level);
    TEST_ASSERT_EQUAL_UINT16(a.cct, b.cct);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(a.color, b.color, 3);
    TEST_ASSERT_EQUAL_STRING(a.effect, b.effect);
    TEST_ASSERT_EQUAL_STRING(a.mode, b.mode);
    TEST_ASSERT_EQUAL_STRING(a.at, b.at);
    TEST_ASSERT_EQUAL_STRING(a.src, b.src);
    TEST_ASSERT_TRUE(a.seq == b.seq);
    free(enc);
    free(j);
}

static void test_state_roundtrip(void)
{
    cur_fn = dec_set;
    ut_each_json(EX("set"), roundtrip_state);
    cur_fn = dec_state;
    ut_each_json(EX("state"), roundtrip_state);
    cur_fn = dec_live;
    ut_each_json(EX("live"), roundtrip_state);
}

static void test_big_seq_exact(void)
{
    const char *j = "{\"on\":true,\"seq\":9007199254740991}";
    ul_state_msg_t m;
    TEST_ASSERT_EQUAL_INT(0, ul_set_decode(j, strlen(j), &m, errbuf, sizeof(errbuf)));
    TEST_ASSERT_TRUE(m.seq == 9007199254740991ULL);
    char *enc = ul_state_encode(&m);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(enc, "\"seq\":9007199254740991}"), enc);
    free(enc);
    m.seq = 1763600000000010ULL; /* %1.15g would print this in exponent form */
    enc = ul_state_encode(&m);
    TEST_ASSERT_NOT_NULL_MESSAGE(strstr(enc, "\"seq\":1763600000000010}"), enc);
    free(enc);
}

static void test_config_roundtrip(void)
{
    size_t n;
    char *j = ut_read(EX("config/c3-bench-cct.json"), &n);
    ul_config_t a, b;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_config_decode(j, n, &a, errbuf, sizeof(errbuf)), errbuf);
    TEST_ASSERT_EQUAL_INT(3, a.rev);
    TEST_ASSERT_EQUAL_INT(3, a.n);
    TEST_ASSERT_EQUAL(UL_KIND_CCT, a.out[0].kind);
    TEST_ASSERT_EQUAL_STRING("warm", a.out[0].ch[UL_ROLE_WARM]);
    TEST_ASSERT_EQUAL_FLOAT(0.004f, a.out[0].min_duty);
    TEST_ASSERT_EQUAL(UL_POWER_ON_OFF, a.out[2].power_on);
    char *enc = ul_config_encode(&a);
    ut_emit("config", "c3-bench-cct", enc);
    TEST_ASSERT_EQUAL_INT(0, ul_config_decode(enc, strlen(enc), &b, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_MEMORY(&a, &b, sizeof(a));
    free(enc);
    free(j);
}

static void test_unknown_fields_ignored_from_server(void)
{
    const char *set = "{\"on\":true,\"seq\":5,\"future_field\":1}";
    const char *cmd = "{\"id\":\"abcdefgh\",\"op\":\"reboot\",\"later\":true}";
    ul_state_msg_t m;
    ul_cmd_t c;
    TEST_ASSERT_EQUAL_INT(0, ul_set_decode(set, strlen(set), &m, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_INT(0, ul_cmd_decode(cmd, strlen(cmd), &c, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL(UL_OP_REBOOT, c.op);
}

static void test_cmd_fields(void)
{
    size_t n;
    char *j = ut_read(EX("cmd/pulse.json"), &n);
    ul_cmd_t c;
    TEST_ASSERT_EQUAL_INT(0, ul_cmd_decode(j, n, &c, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL(UL_OP_PULSE, c.op);
    TEST_ASSERT_EQUAL_STRING("valve", c.out);
    TEST_ASSERT_EQUAL_UINT32(3000, c.ms);
    free(j);
    j = ut_read(EX("cmd/identify.json"), &n);
    TEST_ASSERT_EQUAL_INT(0, ul_cmd_decode(j, n, &c, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL(UL_OP_IDENTIFY, c.op);
    TEST_ASSERT_TRUE(c.ts == 1763600000);
    free(j);
    /* The server's OTA URL (content-addressed by the image's sha256) fits and survives decoding. */
    j = ut_read(EX("cmd/ota-server-url.json"), &n);
    TEST_ASSERT_EQUAL_INT(0, ul_cmd_decode(j, n, &c, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL(UL_OP_OTA, c.op);
    TEST_ASSERT_EQUAL_STRING("https://lights.evm100.org/api/v1/fw/"
                             "d6a06165d26385fdd0a039479b1f4c08e3a0d6664c59edfd060b938971d95465.bin", c.url);
    TEST_ASSERT_EQUAL_STRING("d6a06165d26385fdd0a039479b1f4c08e3a0d6664c59edfd060b938971d95465", c.sha256);
    TEST_ASSERT_EQUAL_STRING("0.1.0", c.version);
    free(j);
    j = ut_read(EX("cmd/sync.json"), &n);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_cmd_decode(j, n, &c, errbuf, sizeof(errbuf)), errbuf);
    TEST_ASSERT_EQUAL(UL_OP_SYNC, c.op);
    TEST_ASSERT_EQUAL_STRING("xmas", c.out);
    TEST_ASSERT_EQUAL_STRING("color", c.at);
    free(j);
    const char *off = "{\"id\":\"abcdefgh\",\"op\":\"sync\",\"out\":\"xmas\",\"at\":\"off\"}";
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_cmd_decode(off, strlen(off), &c, errbuf, sizeof(errbuf)), errbuf);
    TEST_ASSERT_EQUAL_STRING("off", c.at);
    /* A set can't select "off" as a mode: off is on: false. */
    const char *bad = "{\"on\":true,\"mode\":\"off\",\"seq\":1}";
    ul_state_msg_t m;
    TEST_ASSERT_EQUAL_INT(-1, ul_set_decode(bad, strlen(bad), &m, errbuf, sizeof(errbuf)));
}

/* ---------- encoders reproduce the examples ---------- */

static void assert_json_equal_file(const char *path, const char *json)
{
    size_t n;
    char *want = ut_read(path, &n);
    TEST_ASSERT_NOT_NULL_MESSAGE(want, path);
    cJSON *a = cJSON_Parse(want), *b = cJSON_Parse(json);
    TEST_ASSERT_NOT_NULL(a);
    TEST_ASSERT_NOT_NULL(b);
    char msg[4096];
    snprintf(msg, sizeof(msg), "%s\n  got: %s", path, json);
    TEST_ASSERT_TRUE_MESSAGE(cJSON_Compare(a, b, true), msg);
    cJSON_Delete(a);
    cJSON_Delete(b);
    free(want);
}

static void load_bench_board(ul_board_desc_t *b)
{
    size_t n;
    char *j = ut_read(EX("board/c3-bench.json"), &n);
    TEST_ASSERT_EQUAL_INT(0, dec_board(j, n, b, errbuf, sizeof(errbuf)));
    free(j);
}

static void test_info_encode(void)
{
    static ul_board_desc_t b;
    static ul_config_t c;
    load_bench_board(&b);
    ul_mapping_default(&b, &c);
    ul_info_t in = {
        .id = "ul-0a1b2c", .chip = UL_CHIP_ESP32C3, .fw = "0.1.0", .profile = "c3-lights",
        .idf = "v6.1", .board = &b, .board_crc = 0, .mac = {0x34, 0x85, 0x18, 0, 0, 1},
        .outputs = &c, .config_rev = 0,
    };
    char *enc = ul_info_encode(&in);
    ut_emit("info", "c3-bench-default", enc);
    assert_json_equal_file(EX("info/c3-bench-default.json"), enc);
    free(enc);

    /* With a real config and an error string, still schema-valid. */
    size_t n;
    char *j = ut_read(EX("config/c3-bench-cct.json"), &n);
    TEST_ASSERT_EQUAL_INT(0, ul_config_decode(j, n, &c, errbuf, sizeof(errbuf)));
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_mapping_validate(&b, &c, errbuf, sizeof(errbuf)), errbuf);
    in.config_rev = c.rev;
    in.config_error = "output x: channel y not on board";
    enc = ul_info_encode(&in);
    ut_emit("info", "c3-bench-cct", enc);
    free(enc);
    free(j);

    /* A servo board: its cycle output carries the channel's modes. */
    j = ut_read(EX("board/c3-xmas-servo.json"), &n);
    cJSON *root = cJSON_Parse(j);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_from_cjson(root, &b, errbuf, sizeof(errbuf)), errbuf);
    cJSON_Delete(root);
    free(j);
    TEST_ASSERT_EQUAL(UL_CH_SERVO, b.ch[0].type);
    TEST_ASSERT_EQUAL_UINT8(62, b.ch[0].press_deg);
    TEST_ASSERT_EQUAL_UINT8(95, b.ch[0].rest_deg);
    TEST_ASSERT_EQUAL_UINT16(250, b.ch[0].hold_ms);
    TEST_ASSERT_EQUAL_UINT16(400, b.ch[0].release_ms);
    TEST_ASSERT_EQUAL_UINT8(3, b.ch[0].n_modes);
    TEST_ASSERT_EQUAL_STRING("alternating", b.ch[0].modes[2]);
    ul_mapping_default(&b, &c);
    in.config_rev = 0;
    in.config_error = NULL;
    enc = ul_info_encode(&in);
    ut_emit("info", "c3-xmas-servo", enc);
    TEST_ASSERT_NOT_NULL(strstr(enc, "\"kind\":\"cycle\""));
    TEST_ASSERT_NOT_NULL(strstr(enc, "\"caps\":[\"onoff\",\"mode\"]"));
    TEST_ASSERT_NOT_NULL(strstr(enc, "\"modes\":[\"white\",\"color\",\"alternating\"]"));
    free(enc);

    /* Defaults: release_ms 400, one mode "on". */
    const char *min = "{\"v\":1,\"chip\":\"esp32c3\",\"board\":\"x\",\"channels\":[{\"id\":\"s\",\"type\":\"servo\","
                      "\"gpio\":4,\"press_deg\":10,\"rest_deg\":40,\"hold_ms\":100}]}";
    root = cJSON_Parse(min);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_from_cjson(root, &b, errbuf, sizeof(errbuf)), errbuf);
    cJSON_Delete(root);
    TEST_ASSERT_EQUAL_UINT16(400, b.ch[0].release_ms);
    TEST_ASSERT_EQUAL_UINT8(1, b.ch[0].n_modes);
    TEST_ASSERT_EQUAL_STRING("on", b.ch[0].modes[0]);
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_index(&b.ch[0], "off"));
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_index(&b.ch[0], "on"));
    TEST_ASSERT_EQUAL_INT(-1, ul_cycle_index(&b.ch[0], "white"));
    TEST_ASSERT_EQUAL_STRING("off", ul_cycle_name(&b.ch[0], 2));
    /* A board round-trips through its encoder. */
    cJSON *bj = ul_board_to_cjson(&b);
    static ul_board_desc_t b2;
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_from_cjson(bj, &b2, errbuf, sizeof(errbuf)), errbuf);
    cJSON_Delete(bj);
    TEST_ASSERT_EQUAL_MEMORY(&b.ch[0], &b2.ch[0], sizeof(b.ch[0]));
}

static void test_status_encode(void)
{
    char *on = ul_status_encode(true), *off = ul_status_encode(false);
    assert_json_equal_file(EX("status/online.json"), on);
    assert_json_equal_file(EX("status/offline-will.json"), off);
    free(on);
    free(off);
}

static void test_evt_encode(void)
{
    ul_evt_t e = {.type = UL_EVT_CMD_RESULT, .cmd_id = "3f1c9a20-0001", .ok = true, .progress = -1};
    char *s = ul_evt_encode(&e);
    assert_json_equal_file(EX("evt/cmd-ok.json"), s);
    free(s);
    ul_evt_t f = {.type = UL_EVT_CMD_RESULT, .cmd_id = "3f1c9a20-0002", .ok = false,
                  .error = "no_such_output", .progress = -1};
    s = ul_evt_encode(&f);
    assert_json_equal_file(EX("evt/cmd-fail.json"), s);
    free(s);
    ul_evt_t g = {.type = UL_EVT_BUTTON, .action = UL_BTN_HOLD};
    s = ul_evt_encode(&g);
    assert_json_equal_file(EX("evt/button.json"), s);
    free(s);
    ul_evt_t h = {.type = UL_EVT_ERROR, .code = "config_rejected",
                  .detail = "output main: channel warm2 not on board"};
    s = ul_evt_encode(&h);
    assert_json_equal_file(EX("evt/error.json"), s);
    free(s);
    ul_evt_t p = {.type = UL_EVT_CMD_RESULT, .cmd_id = "3f1c9a20-0003", .ok = true, .progress = 40};
    s = ul_evt_encode(&p);
    ut_emit("evt", "ota-progress", s);
    free(s);
}

static void test_health_encode(void)
{
    ul_health_t h = {
        .uptime_s = 3600, .heap_free = 182340, .heap_min = 150112, .rssi = -61, .ip = "192.168.1.57",
        .channel = 6, .reset_reason = "poweron", .wifi_reconnects = 0, .mqtt_reconnects = 1,
        .has_temp = true, .temp_c = 41.5f, .ota = "valid", .dns = "192.168.1.99",
    };
    char *s = ul_health_encode(&h);
    assert_json_equal_file(EX("health/typical.json"), s);
    free(s);
}

static void test_enroll_encode(void)
{
    ul_enroll_req_t r = {
        .device_id = "ul-0a1b2c", .token = "tok_EXAMPLE_ONLY_000000000000000000000",
        .csr = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB...\n-----END CERTIFICATE REQUEST-----\n",
        .fw = "0.1.0", .profile = "c3-lights", .chip = UL_CHIP_ESP32C3,
        .mac = {0x34, 0x85, 0x18, 0, 0, 1}, .board_crc = 0,
    };
    char *s = ul_enroll_req_encode(&r);
    assert_json_equal_file(EX("enroll-request/c3.json"), s);
    free(s);
}

static void test_enroll_response(void)
{
    size_t n;
    char *j = ut_read(EX("enroll-response/ok.json"), &n);
    ul_enroll_resp_t r;
    TEST_ASSERT_EQUAL_INT(0, ul_enroll_resp_decode(j, n, &r, errbuf, sizeof(errbuf)));
    TEST_ASSERT_TRUE(r.ok);
    TEST_ASSERT_EQUAL_STRING("lights.evm100.org", r.mqtt_host);
    TEST_ASSERT_EQUAL_UINT16(8883, r.mqtt_port);
    TEST_ASSERT_NOT_NULL(strstr(r.cert, "BEGIN CERTIFICATE"));
    ul_enroll_resp_free(&r);
    free(j);
    j = ut_read(EX("enroll-response/token-used.json"), &n);
    TEST_ASSERT_EQUAL_INT(0, ul_enroll_resp_decode(j, n, &r, errbuf, sizeof(errbuf)));
    TEST_ASSERT_FALSE(r.ok);
    TEST_ASSERT_EQUAL_STRING("token_used", r.error);
    free(j);
}

static void test_value_checks(void)
{
    TEST_ASSERT_TRUE(ul_is_ipv4("192.168.1.99"));
    TEST_ASSERT_FALSE(ul_is_ipv4("192.168.1"));
    TEST_ASSERT_FALSE(ul_is_ipv4("192.168.1.256"));
    TEST_ASSERT_FALSE(ul_is_ipv4("01.2.3.4"));
    TEST_ASSERT_TRUE(ul_is_key("pwm1"));
    TEST_ASSERT_FALSE(ul_is_key("1pwm"));
    TEST_ASSERT_FALSE(ul_is_key("abcdefghijklmnopq"));
    TEST_ASSERT_TRUE(ul_is_device_id("ul-0a1b2c"));
    TEST_ASSERT_FALSE(ul_is_device_id("ul-0A1B2C"));
}

void run_contract_tests(void)
{
    RUN_TEST(test_valid_examples_decode);
    RUN_TEST(test_invalid_examples_rejected);
    RUN_TEST(test_state_roundtrip);
    RUN_TEST(test_big_seq_exact);
    RUN_TEST(test_config_roundtrip);
    RUN_TEST(test_unknown_fields_ignored_from_server);
    RUN_TEST(test_cmd_fields);
    RUN_TEST(test_info_encode);
    RUN_TEST(test_status_encode);
    RUN_TEST(test_evt_encode);
    RUN_TEST(test_health_encode);
    RUN_TEST(test_enroll_encode);
    RUN_TEST(test_enroll_response);
    RUN_TEST(test_value_checks);
}
