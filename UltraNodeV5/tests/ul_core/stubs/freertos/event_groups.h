#pragma once
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct EventGroupStub *EventGroupHandle_t;

EventGroupHandle_t xEventGroupCreate(void);
EventBits_t xEventGroupWaitBits(EventGroupHandle_t event_group,
                                EventBits_t bits_to_wait_for,
                                BaseType_t clear_on_exit,
                                BaseType_t wait_for_all_bits,
                                TickType_t ticks_to_wait);
EventBits_t xEventGroupSetBits(EventGroupHandle_t event_group,
                               EventBits_t bits_to_set);
EventBits_t xEventGroupClearBits(EventGroupHandle_t event_group,
                                 EventBits_t bits_to_clear);
EventBits_t xEventGroupGetBits(EventGroupHandle_t event_group);
void vEventGroupDelete(EventGroupHandle_t event_group);

#ifdef __cplusplus
}
#endif
