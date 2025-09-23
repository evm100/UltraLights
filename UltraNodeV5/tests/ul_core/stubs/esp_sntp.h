#pragma once
#include <sys/time.h>

#ifdef __cplusplus
extern "C" {
#endif

void esp_sntp_set_time_sync_notification_cb(void (*cb)(struct timeval *tv));
void esp_sntp_stop(void);

#ifdef __cplusplus
}
#endif
