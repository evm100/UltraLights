#include "sdkconfig.h"
#include "ul_task.h"

uint8_t ul_core_count = CONFIG_UL_CORE_COUNT;

void ul_set_core_count(uint8_t count) {
    ul_core_count = count;
}

BaseType_t ul_task_create(TaskFunction_t task_func,
                          const char *name,
                          const uint32_t stack_depth,
                          void *params,
                          UBaseType_t priority,
                          TaskHandle_t *task_handle,
                          BaseType_t core_id) {
    if (ul_core_count > 1) {
        return xTaskCreatePinnedToCore(task_func, name, stack_depth, params,
                                       priority, task_handle, core_id);
    }
    return xTaskCreate(task_func, name, stack_depth, params,
                       priority, task_handle);
}
