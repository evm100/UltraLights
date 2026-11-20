/* Helpers shared by the host tests. */
#pragma once

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "unity.h"

static inline char *ut_read(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) {
        n = 0;
    }
    buf[n] = '\0';
    fclose(f);
    if (len) {
        *len = (size_t)n;
    }
    return buf;
}

/* Call fn(path, name) for every *.json in dir, sorted. Returns how many. */
static inline int ut_each_json(const char *dir, void (*fn)(const char *path, const char *name))
{
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    int count = 0;
    for (int i = 0; i < n; i++) {
        const char *nm = list[i]->d_name;
        size_t l = strlen(nm);
        if (l > 5 && strcmp(nm + l - 5, ".json") == 0) {
            char path[1024];
            snprintf(path, sizeof(path), "%s/%s", dir, nm);
            fn(path, nm);
            count++;
        }
        free(list[i]);
    }
    if (n >= 0) {
        free(list);
    }
    return count;
}

/* Write an encoded message for the schema check in host_test.sh. */
static inline void ut_emit(const char *schema, const char *name, const char *json)
{
    char dir[1024], path[1200];
    mkdir(UL_HOST_OUT_DIR, 0755);
    snprintf(dir, sizeof(dir), "%s/%s", UL_HOST_OUT_DIR, schema);
    mkdir(dir, 0755);
    snprintf(path, sizeof(path), "%s/%s.json", dir, name);
    FILE *f = fopen(path, "w");
    TEST_ASSERT_NOT_NULL_MESSAGE(f, path);
    fputs(json, f);
    fclose(f);
}

#define EX(rel) UL_CONTRACTS_DIR "/examples/" rel
