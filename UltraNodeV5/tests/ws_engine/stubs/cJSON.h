#pragma once
#include <stdbool.h>

typedef struct cJSON {
    int valueint;
    char *valuestring;
} cJSON;

static inline cJSON* cJSON_GetObjectItem(const cJSON* object, const char* string) {
    (void)object;
    (void)string;
    return NULL;
}

static inline bool cJSON_IsNumber(const cJSON* item) {
    (void)item;
    return false;
}

static inline bool cJSON_IsString(const cJSON* item) {
    (void)item;
    return false;
}

static inline bool cJSON_IsArray(const cJSON* item) {
    (void)item;
    return false;
}
