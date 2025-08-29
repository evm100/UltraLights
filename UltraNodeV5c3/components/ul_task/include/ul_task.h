#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

extern uint8_t ul_core_count;

void ul_set_core_count(uint8_t count);

BaseType_t ul_task_create(TaskFunction_t task_func,
                          const char *name,
                          const uint32_t stack_depth,
                          void *params,
                          UBaseType_t priority,
                          TaskHandle_t *task_handle,
                          BaseType_t core_id);

#ifdef __cplusplus
}
#endif
