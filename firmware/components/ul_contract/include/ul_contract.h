/*
 * Encode and decode contract messages (contracts/schemas). Pure C over cJSON,
 * built for the chip and for host tests.
 *
 * Decoders return 0 on success and -1 on a message that breaks the schema,
 * with a short reason in err. Board and partition decoding is strict (unknown
 * fields are rejected, as the schema says). Server-to-device messages (config,
 * set, live, cmd, enrollment response) ignore unknown fields so the server can
 * add optional fields before every node is updated; every known field is still
 * checked.
 *
 * Encoders return a malloc'd, NUL-terminated compact JSON string (free() it),
 * or NULL when out of memory.
 */
#pragma once

#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct cJSON cJSON;

/* ---- names ---- */
const char *ul_chip_name(ul_chip_t chip);
int ul_chip_from_name(const char *s, ul_chip_t *out);
const char *ul_kind_name(ul_kind_t kind);
const char *ul_role_name(ul_role_t role);
const char *ul_ch_type_name(ul_ch_type_t t);
const char *ul_order_name(ul_pixel_order_t o);
const char *ul_op_name(ul_op_t op);
uint32_t ul_kind_caps(ul_kind_t kind);   /* caps a kind can have; drivers may remove some */

/* ---- field checks shared with ul_board / ul_core ---- */
bool ul_is_key(const char *s);
bool ul_is_device_id(const char *s);
bool ul_is_ipv4(const char *s);

/* ---- board descriptor and partition payload (strict) ---- */
int ul_board_from_cjson(const cJSON *j, ul_board_desc_t *out, char *err, size_t errlen);
cJSON *ul_board_to_cjson(const ul_board_desc_t *b);
int ul_partition_decode(const char *json, size_t len, ul_board_desc_t *board,
                        ul_provision_t *prov, char *err, size_t errlen);

/* ---- server to device ---- */
int ul_config_decode(const char *json, size_t len, ul_config_t *out, char *err, size_t errlen);
char *ul_config_encode(const ul_config_t *c);   /* for NVS and tests */
int ul_set_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen);
int ul_live_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen);
int ul_cmd_decode(const char *json, size_t len, ul_cmd_t *out, char *err, size_t errlen);
int ul_enroll_resp_decode(const char *json, size_t len, ul_enroll_resp_t *out, char *err, size_t errlen);
void ul_enroll_resp_free(ul_enroll_resp_t *r);

/* ---- device to server ---- */
char *ul_state_encode(const ul_state_msg_t *m);
int ul_state_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen); /* tests, NVS */
char *ul_status_encode(bool online);
char *ul_info_encode(const ul_info_t *info);
char *ul_evt_encode(const ul_evt_t *e);
char *ul_health_encode(const ul_health_t *h);
char *ul_enroll_req_encode(const ul_enroll_req_t *r);

#ifdef __cplusplus
}
#endif
