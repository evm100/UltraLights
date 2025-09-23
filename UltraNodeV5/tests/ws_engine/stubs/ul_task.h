#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

BaseType_t ul_task_create(TaskFunction_t task_func,
                          const char *name,
                          const uint32_t stack_depth,
                          void *params,
                          UBaseType_t priority,
                          TaskHandle_t *task_handle,
                          BaseType_t core_id);
