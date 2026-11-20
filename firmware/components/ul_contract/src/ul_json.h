/* Private JSON field helpers for the contract codec. */
#pragma once

#include <stdarg.h>
#include <stdio.h>
#include "cJSON.h"
#include "ul_contract.h"

/* Return values of the j_* getters: 1 present and valid, 0 absent, -1 invalid. */
#define J_ABSENT 0
#define J_OK 1
#define J_BAD -1

typedef struct {
    char *err;
    size_t errlen;
} jerr_t;

int j_fail(jerr_t *e, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Every member of o must be in the NULL-terminated list (strict objects). */
int j_only(const cJSON *o, const char *const *allowed, const char *what, jerr_t *e);

int j_str(const cJSON *o, const char *k, char *dst, size_t dstsz, bool required,
          bool (*valid)(const char *), jerr_t *e);
int j_int(const cJSON *o, const char *k, long long min, long long max, long long *dst,
          bool required, jerr_t *e);
int j_num(const cJSON *o, const char *k, double min, double max, double *dst,
          bool required, jerr_t *e);
int j_bool(const cJSON *o, const char *k, bool *dst, bool required, jerr_t *e);

/* Parse text into an object; NULL (and err set) when it isn't a JSON object. */
cJSON *j_parse_object(const char *json, size_t len, jerr_t *e);

/* Print and free; NULL in, NULL out. */
char *j_print(cJSON *root);

bool ul_is_src(const char *s);
bool ul_is_semver(const char *s);
int ul_mac_format(const uint8_t mac[6], char out[18]);
