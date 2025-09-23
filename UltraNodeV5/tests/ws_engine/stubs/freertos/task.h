#pragma once
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void* TaskHandle_t;

TickType_t xTaskGetTickCount(void);
void vTaskDelayUntil(TickType_t* const pxPreviousWakeTime, TickType_t xTimeIncrement);
void vTaskDelay(TickType_t ticks);
void vTaskDelete(TaskHandle_t task);

#ifdef __cplusplus
}
#endif
