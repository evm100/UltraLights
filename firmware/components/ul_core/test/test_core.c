/* Host tests: default mapping, mapping checks, seq ordering, rendering. */
#include "cJSON.h"
#include "test_util.h"
#include "ul_contract.h"
#include "ul_core_logic.h"

static char errbuf[160];
static ul_board_desc_t board;
static ul_config_t cfg;

static void load_board(void)
{
    char *j = ut_read(EX("board/c3-bench.json"), NULL);
    cJSON *root = cJSON_Parse(j);
    TEST_ASSERT_EQUAL_INT(0, ul_board_from_cjson(root, &board, errbuf, sizeof(errbuf)));
    cJSON_Delete(root);
    free(j);
}

static int load_config(const char *json)
{
    if (ul_config_decode(json, strlen(json), &cfg, errbuf, sizeof(errbuf)) != 0) {
        return -2;
    }
    return ul_mapping_validate(&board, &cfg, errbuf, sizeof(errbuf));
}

static void test_default_mapping(void)
{
    load_board();
    ul_mapping_default(&board, &cfg);
    TEST_ASSERT_EQUAL_INT(0, cfg.rev);
    TEST_ASSERT_EQUAL_INT(4, cfg.n);
    TEST_ASSERT_EQUAL(UL_KIND_WHITE, cfg.out[0].kind);
    TEST_ASSERT_EQUAL_STRING("warm", cfg.out[0].ch[UL_ROLE_W]);
    TEST_ASSERT_EQUAL(UL_KIND_ARGB, cfg.out[2].kind);
    TEST_ASSERT_EQUAL_UINT16(60, cfg.out[2].pixels);
    TEST_ASSERT_EQUAL(UL_KIND_RELAY, cfg.out[3].kind);
    TEST_ASSERT_EQUAL_HEX32(UL_CAP_ONOFF, cfg.out[3].caps);
    TEST_ASSERT_EQUAL_HEX32(UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_COLOR, cfg.out[2].caps);
}

static void test_mapping_checks(void)
{
    load_board();
    char *j = ut_read(EX("config/c3-bench-cct.json"), NULL);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, load_config(j), errbuf);
    free(j);

    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"cct\",\"channels\":{\"warm\":\"warm\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "needs role cool"));
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"white\",\"channels\":{\"w\":\"nope\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "not on board"));
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"white\",\"channels\":{\"w\":\"strip\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "is argb"));
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"white\",\"channels\":{\"w\":\"warm\"}},"
        "{\"key\":\"b\",\"kind\":\"white\",\"channels\":{\"w\":\"warm\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "already used"));
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"s\",\"kind\":\"argb\",\"channels\":{\"data\":\"strip\"},\"pixels\":61}]}"));
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"white\",\"channels\":{\"w\":\"warm\"}},"
        "{\"key\":\"a\",\"kind\":\"white\",\"channels\":{\"w\":\"cool\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "duplicate key"));
}

static ul_state_msg_t msg(bool on, float level, uint64_t seq)
{
    ul_state_msg_t m = {0};
    m.fields = UL_F_ON | UL_F_LEVEL | UL_F_SEQ;
    m.on = on;
    m.level = level;
    m.seq = seq;
    return m;
}

static void test_seq_ordering(void)
{
    load_board();
    ul_mapping_default(&board, &cfg);
    ul_out_state_t s;
    ul_state_init(&s, &cfg.out[0]);
    ul_state_msg_t a = msg(true, 0.5f, 100);
    TEST_ASSERT_EQUAL_INT(1, ul_state_apply(&s, &cfg.out[0], &a, false));
    ul_state_msg_t stale = msg(true, 0.9f, 100);
    TEST_ASSERT_EQUAL_INT(0, ul_state_apply(&s, &cfg.out[0], &stale, true));
    stale.seq = 99;
    TEST_ASSERT_EQUAL_INT(0, ul_state_apply(&s, &cfg.out[0], &stale, false));
    TEST_ASSERT_EQUAL_FLOAT(0.5f, s.cur.level);
    ul_state_msg_t live = {0};
    live.fields = UL_F_LEVEL | UL_F_SEQ;
    live.level = 0.7f;
    live.seq = 101;
    TEST_ASSERT_EQUAL_INT(1, ul_state_apply(&s, &cfg.out[0], &live, true));
    TEST_ASSERT_TRUE(s.cur.on);
    TEST_ASSERT_EQUAL_FLOAT(0.7f, s.cur.level);
    TEST_ASSERT_TRUE(s.cur.seq == 101);

    /* After a reboot (fresh RAM state) the first message applies whatever its seq. */
    ul_out_state_t fresh;
    ul_state_init(&fresh, &cfg.out[0]);
    ul_state_msg_t low = msg(true, 0.2f, 1);
    TEST_ASSERT_EQUAL_INT(1, ul_state_apply(&fresh, &cfg.out[0], &low, false));
}

static void test_fields_follow_kind(void)
{
    load_board();
    ul_mapping_default(&board, &cfg);
    ul_out_state_t s;
    ul_state_init(&s, &cfg.out[3]); /* relay */
    ul_state_msg_t m = msg(true, 0.3f, 1);
    ul_state_apply(&s, &cfg.out[3], &m, false);
    TEST_ASSERT_EQUAL_FLOAT(1.0f, s.cur.level); /* level ignored on a relay */
    ul_render_t r;
    ul_render(&cfg.out[3], &s.cur, &r);
    TEST_ASSERT_EQUAL_FLOAT(1.0f, r.v[UL_ROLE_COIL]);
}

static void test_curve(void)
{
    TEST_ASSERT_EQUAL_FLOAT(0.0f, ul_curve_cie(0));
    TEST_ASSERT_EQUAL_FLOAT(1.0f, ul_curve_cie(1));
    TEST_ASSERT_FLOAT_WITHIN(0.0005f, 0.1842f, ul_curve_cie(0.5f));
    TEST_ASSERT_FLOAT_WITHIN(0.00001f, 5.0f / 903.3f, ul_curve_cie(0.05f));
    float prev = 0;
    for (int i = 1; i <= 1000; i++) {
        float y = ul_curve_cie(i / 1000.0f);
        TEST_ASSERT_TRUE(y > prev);
        prev = y;
    }
    TEST_ASSERT_EQUAL_FLOAT(1.0f, ul_srgb_to_linear(255));
    TEST_ASSERT_EQUAL_FLOAT(0.0f, ul_srgb_to_linear(0));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.2159f, ul_srgb_to_linear(128));
}

static void test_render_cct_and_limits(void)
{
    load_board();
    char *j = ut_read(EX("config/c3-bench-cct.json"), NULL);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, load_config(j), errbuf);
    free(j);
    const ul_output_def_t *d = &cfg.out[0]; /* cct 2700-6500, min_duty 0.004 */
    ul_state_msg_t st = {0};
    st.on = true;
    st.level = 1.0f;
    st.cct = 2700;
    ul_render_t r;
    ul_render(d, &st, &r);
    TEST_ASSERT_EQUAL_FLOAT(1.0f, r.v[UL_ROLE_WARM]);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.v[UL_ROLE_COOL]);
    st.cct = 6500;
    ul_render(d, &st, &r);
    TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, r.v[UL_ROLE_COOL]);
    st.cct = 3820; /* about halfway in mireds */
    ul_render(d, &st, &r);
    TEST_ASSERT_FLOAT_WITHIN(1e-5f, 1.0f, r.v[UL_ROLE_WARM] + r.v[UL_ROLE_COOL]);
    TEST_ASSERT_FLOAT_WITHIN(0.02f, 0.5f, r.v[UL_ROLE_COOL]);
    /* min_duty lifts the lowest level; zero stays zero. */
    st.cct = 2700;
    st.level = 0.001f;
    ul_render(d, &st, &r);
    TEST_ASSERT_TRUE(r.v[UL_ROLE_WARM] >= 0.004f);
    st.level = 0;
    ul_render(d, &st, &r);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.v[UL_ROLE_WARM]);
    st.on = false;
    st.level = 1;
    ul_render(d, &st, &r);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.v[UL_ROLE_WARM]);
    /* max_level caps the strip. */
    const ul_output_def_t *strip = &cfg.out[1];
    ul_state_msg_t s2 = {0};
    s2.on = true;
    s2.level = 1;
    s2.color[0] = 255;
    ul_render(strip, &s2, &r);
    TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.8f, r.rgbw[0]);
    TEST_ASSERT_EQUAL_FLOAT(0.0f, r.rgbw[1]);
}

static void test_lerp(void)
{
    ul_state_msg_t a = {0}, b = {0}, o;
    a.on = false;
    a.level = 1;
    b.on = true;
    b.level = 0.8f;
    b.cct = 4000;
    a.cct = 3000;
    ul_state_lerp(&a, &b, 0.5f, &o);
    TEST_ASSERT_TRUE(o.on);
    TEST_ASSERT_EQUAL_FLOAT(0.4f, o.level);
    TEST_ASSERT_EQUAL_UINT16(3500, o.cct);
    ul_state_lerp(&b, &a, 1.0f, &o);
    TEST_ASSERT_FALSE(o.on);
}

/* ---------- cycle: a servo pressing an appliance's mode button ---------- */

static void load_servo_board(void)
{
    char *j = ut_read(EX("board/c3-xmas-servo.json"), NULL);
    cJSON *root = cJSON_Parse(j);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, ul_board_from_cjson(root, &board, errbuf, sizeof(errbuf)), errbuf);
    cJSON_Delete(root);
    free(j);
}

static ul_state_msg_t cycle_set(bool on, const char *mode, uint64_t seq)
{
    ul_state_msg_t m = {0};
    m.fields = UL_F_ON | UL_F_SEQ;
    m.on = on;
    m.seq = seq;
    if (mode) {
        m.fields |= UL_F_MODE;
        snprintf(m.mode, sizeof(m.mode), "%s", mode);
    }
    return m;
}

static void test_cycle_mapping(void)
{
    load_servo_board();
    ul_mapping_default(&board, &cfg);
    TEST_ASSERT_EQUAL_INT(1, cfg.n);
    TEST_ASSERT_EQUAL(UL_KIND_CYCLE, cfg.out[0].kind);
    TEST_ASSERT_EQUAL_STRING("xmas", cfg.out[0].ch[UL_ROLE_PRESS]);
    TEST_ASSERT_EQUAL_HEX32(UL_CAP_ONOFF | UL_CAP_MODE, cfg.out[0].caps);
    char *j = ut_read(EX("config/cycle.json"), NULL);
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, load_config(j), errbuf);
    free(j);
    /* The press role needs a servo channel. */
    load_board();
    TEST_ASSERT_EQUAL_INT(-1, load_config(
        "{\"v\":1,\"rev\":1,\"outputs\":[{\"key\":\"a\",\"kind\":\"cycle\",\"channels\":{\"press\":\"relay\"}}]}"));
    TEST_ASSERT_NOT_NULL(strstr(errbuf, "needs servo"));
}

static void test_cycle_planning(void)
{
    load_servo_board();
    ul_mapping_default(&board, &cfg);
    const ul_channel_desc_t *ch = &board.ch[0];
    const ul_output_def_t *d = &cfg.out[0];
    ul_out_state_t s;
    ul_cycle_init(&s, d, ch);
    TEST_ASSERT_FALSE(s.cur.on);
    TEST_ASSERT_EQUAL_STRING("white", s.cur.mode);
    TEST_ASSERT_EQUAL_STRING("off", s.cur.at);
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_target(ch, &s.cur));

    /* Off -> white: one press. White -> off: three (white, color, alternating, off). */
    ul_state_msg_t m = cycle_set(true, "white", 1);
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_apply(&s, d, ch, &m, false));
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_target(ch, &s.cur));
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_presses(ch, 0, ul_cycle_target(ch, &s.cur)));
    m = cycle_set(false, NULL, 2);
    ul_cycle_apply(&s, d, ch, &m, false);
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_target(ch, &s.cur));
    TEST_ASSERT_EQUAL_INT(3, ul_cycle_presses(ch, 1, 0));
    /* Off keeps the selected mode; on alone goes back to it. */
    TEST_ASSERT_EQUAL_STRING("white", s.cur.mode);
    m = cycle_set(true, NULL, 3);
    ul_cycle_apply(&s, d, ch, &m, false);
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_target(ch, &s.cur));
    /* Color -> alternating: one. Alternating -> color: three (off, white, color). */
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_presses(ch, 2, 3));
    TEST_ASSERT_EQUAL_INT(3, ul_cycle_presses(ch, 3, 2));
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_presses(ch, 2, 2));
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_presses(ch, 9, 1)); /* out of range: never press */

    /* A mode the appliance doesn't have is ignored; on still applies. */
    m = cycle_set(true, "color", 4);
    ul_cycle_apply(&s, d, ch, &m, false);
    m = cycle_set(false, "disco", 5);
    TEST_ASSERT_EQUAL_INT(1, ul_cycle_apply(&s, d, ch, &m, false));
    TEST_ASSERT_FALSE(s.cur.on);
    TEST_ASSERT_EQUAL_STRING("color", s.cur.mode);
    /* Stale seq: dropped. */
    m = cycle_set(true, "alternating", 5);
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_apply(&s, d, ch, &m, false));

    /* Sync: the appliance is really in alternating, so that's the target too. */
    ul_cycle_sync(&s, ch, 3);
    TEST_ASSERT_TRUE(s.cur.on);
    TEST_ASSERT_EQUAL_STRING("alternating", s.cur.mode);
    TEST_ASSERT_EQUAL_STRING("alternating", s.cur.at);
    TEST_ASSERT_EQUAL_INT(0, ul_cycle_presses(ch, 3, ul_cycle_target(ch, &s.cur)));
    ul_cycle_sync(&s, ch, 0);
    TEST_ASSERT_FALSE(s.cur.on);
    TEST_ASSERT_EQUAL_STRING("alternating", s.cur.mode); /* remembered for the next on */
    TEST_ASSERT_EQUAL_STRING("off", s.cur.at);

    /* The state a device publishes carries mode and at. */
    char *enc = ul_state_encode(&s.cur);
    TEST_ASSERT_NOT_NULL(strstr(enc, "\"mode\":\"alternating\""));
    TEST_ASSERT_NOT_NULL(strstr(enc, "\"at\":\"off\""));
    TEST_ASSERT_NULL(strstr(enc, "\"level\""));
    ut_emit("state", "cycle-synced", enc);
    free(enc);
}

void run_core_tests(void)
{
    RUN_TEST(test_cycle_mapping);
    RUN_TEST(test_cycle_planning);
    RUN_TEST(test_default_mapping);
    RUN_TEST(test_mapping_checks);
    RUN_TEST(test_seq_ordering);
    RUN_TEST(test_fields_follow_kind);
    RUN_TEST(test_curve);
    RUN_TEST(test_render_cct_and_limits);
    RUN_TEST(test_lerp);
}
