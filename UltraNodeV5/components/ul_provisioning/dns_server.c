#include "dns_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>

typedef struct dns_header {
  uint16_t id;
  uint16_t flags;
  uint16_t qdcount;
  uint16_t ancount;
  uint16_t nscount;
  uint16_t arcount;
} __attribute__((packed)) dns_header_t;

struct dns_server_handle_t {
  int sock;
  TaskHandle_t task;
  uint32_t ip_addr;
  bool running;
};

static const char *TAG = "ul_dns";

static void dns_server_task(void *arg) {
  dns_server_handle_t *handle = (dns_server_handle_t *)arg;
  const uint32_t ip_addr = handle->ip_addr;

  while (handle->running) {
    uint8_t buffer[512];
    struct sockaddr_in source = {0};
    socklen_t socklen = sizeof(source);
    int len = recvfrom(handle->sock, buffer, sizeof(buffer), 0,
                       (struct sockaddr *)&source, &socklen);
    if (len < (int)sizeof(dns_header_t)) {
      if (len < 0) {
        int err = errno;
        if (err == EINTR) {
          continue;
        }
        ESP_LOGW(TAG, "recvfrom error: %d", err);
      }
      continue;
    }

    dns_header_t *header = (dns_header_t *)buffer;
    uint16_t qdcount = ntohs(header->qdcount);
    if (qdcount == 0) {
      continue;
    }

    // Prepare response by copying query
    uint8_t response[512];
    memcpy(response, buffer, len);
    dns_header_t *resp_header = (dns_header_t *)response;
    resp_header->flags = htons(0x8180); // Standard query response, no error
    resp_header->ancount = htons(1);
    resp_header->nscount = 0;
    resp_header->arcount = 0;

    int offset = sizeof(dns_header_t);
    // Skip questions to find answer location
    for (uint16_t i = 0; i < qdcount; ++i) {
      while (offset < len && response[offset] != 0) {
        offset += response[offset] + 1;
      }
      offset += 5; // zero byte + type + class
      if (offset > len) {
        offset = len;
        break;
      }
    }

    if (offset + 16 > (int)sizeof(response)) {
      continue;
    }

    // Append a single A record answer referencing the first question
    response[offset++] = 0xC0;
    response[offset++] = 0x0C; // pointer to first question name (offset 12)
    response[offset++] = 0x00;
    response[offset++] = 0x01; // type A
    response[offset++] = 0x00;
    response[offset++] = 0x01; // class IN
    response[offset++] = 0x00;
    response[offset++] = 0x00;
    response[offset++] = 0x00;
    response[offset++] = 0x3C; // TTL 60s
    response[offset++] = 0x00;
    response[offset++] = 0x04; // data length

    response[offset++] = (ip_addr >> 24) & 0xFF;
    response[offset++] = (ip_addr >> 16) & 0xFF;
    response[offset++] = (ip_addr >> 8) & 0xFF;
    response[offset++] = ip_addr & 0xFF;

    int resp_len = offset;
    sendto(handle->sock, response, resp_len, 0, (struct sockaddr *)&source, socklen);
  }

  vTaskDelete(NULL);
}

esp_err_t ul_dns_server_start(uint32_t ip_addr, dns_server_handle_t **out_handle) {
  if (!out_handle)
    return ESP_ERR_INVALID_ARG;
  *out_handle = NULL;

  int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (sock < 0) {
    ESP_LOGE(TAG, "Failed to create DNS socket: %d", errno);
    return ESP_FAIL;
  }

  struct sockaddr_in addr = {0};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(53);
  addr.sin_addr.s_addr = htonl(INADDR_ANY);

  if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
    ESP_LOGE(TAG, "Failed to bind DNS socket: %d", errno);
    close(sock);
    return ESP_FAIL;
  }

  dns_server_handle_t *handle = calloc(1, sizeof(*handle));
  if (!handle) {
    close(sock);
    return ESP_ERR_NO_MEM;
  }

  handle->sock = sock;
  handle->ip_addr = ip_addr;
  handle->running = true;

  BaseType_t created = xTaskCreate(dns_server_task, "dns", 3072, handle, 3, &handle->task);
  if (created != pdPASS) {
    ESP_LOGE(TAG, "Failed to create DNS task");
    close(sock);
    free(handle);
    return ESP_ERR_NO_MEM;
  }

  *out_handle = handle;
  return ESP_OK;
}

void ul_dns_server_stop(dns_server_handle_t *handle) {
  if (!handle)
    return;
  handle->running = false;
  if (handle->sock >= 0) {
    shutdown(handle->sock, SHUT_RDWR);
    close(handle->sock);
    handle->sock = -1;
  }
  vTaskDelay(pdMS_TO_TICKS(10));
  free(handle);
}
