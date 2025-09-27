#include "ul_wifi_credentials.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <string.h>

#define UL_WIFI_NAMESPACE "ulwifi"

static const char *TAG = "ul_wifi_credentials";

static esp_err_t ul_wifi_open_namespace(nvs_open_mode_t mode, nvs_handle_t *out_handle) {
  if (!out_handle)
    return ESP_ERR_INVALID_ARG;
  return nvs_open(UL_WIFI_NAMESPACE, mode, out_handle);
}

bool ul_wifi_credentials_load(ul_wifi_credentials_t *out) {
  if (!out)
    return false;
  memset(out, 0, sizeof(*out));
  nvs_handle_t handle;
  esp_err_t err = ul_wifi_open_namespace(NVS_READONLY, &handle);
  if (err != ESP_OK) {
    if (err != ESP_ERR_NVS_NOT_FOUND) {
      ESP_LOGW(TAG, "Failed to open NVS namespace: %s", esp_err_to_name(err));
    }
    return false;
  }

  size_t ssid_len = sizeof(out->ssid);
  err = nvs_get_str(handle, "ssid", out->ssid, &ssid_len);
  if (err != ESP_OK) {
    if (err != ESP_ERR_NVS_NOT_FOUND) {
      ESP_LOGW(TAG, "Failed to read stored SSID: %s", esp_err_to_name(err));
    }
    nvs_close(handle);
    return false;
  }

  size_t pass_len = sizeof(out->password);
  err = nvs_get_str(handle, "password", out->password, &pass_len);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    out->password[0] = '\0';
    err = ESP_OK;
  }
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to read stored password: %s", esp_err_to_name(err));
    nvs_close(handle);
    return false;
  }

  size_t user_len = sizeof(out->user);
  err = nvs_get_str(handle, "user", out->user, &user_len);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    out->user[0] = '\0';
    err = ESP_OK;
  }
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to read stored user: %s", esp_err_to_name(err));
    nvs_close(handle);
    return false;
  }

  size_t user_pass_len = sizeof(out->user_password);
  err = nvs_get_str(handle, "user_password", out->user_password, &user_pass_len);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    // Fall back to legacy key name "secret" for compatibility.
    user_pass_len = sizeof(out->user_password);
    err = nvs_get_str(handle, "secret", out->user_password, &user_pass_len);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
      out->user_password[0] = '\0';
      err = ESP_OK;
    }
  }
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to read stored account password: %s",
             esp_err_to_name(err));
    nvs_close(handle);
    return false;
  }

  nvs_close(handle);
  return out->ssid[0] != '\0';
}

esp_err_t ul_wifi_credentials_save(const ul_wifi_credentials_t *creds) {
  if (!creds)
    return ESP_ERR_INVALID_ARG;
  nvs_handle_t handle;
  esp_err_t err = ul_wifi_open_namespace(NVS_READWRITE, &handle);
  if (err == ESP_ERR_NVS_NOT_INITIALIZED) {
    err = nvs_flash_init();
    if (err == ESP_OK) {
      err = ul_wifi_open_namespace(NVS_READWRITE, &handle);
    }
  }
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to open NVS namespace: %s", esp_err_to_name(err));
    return err;
  }

  err = nvs_set_str(handle, "ssid", creds->ssid);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to save SSID: %s", esp_err_to_name(err));
    nvs_close(handle);
    return err;
  }

  err = nvs_set_str(handle, "password", creds->password);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to save password: %s", esp_err_to_name(err));
    nvs_close(handle);
    return err;
  }

  err = nvs_set_str(handle, "user", creds->user);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to save user: %s", esp_err_to_name(err));
    nvs_close(handle);
    return err;
  }

  err = nvs_set_str(handle, "user_password", creds->user_password);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to save account password: %s", esp_err_to_name(err));
    nvs_close(handle);
    return err;
  }

  // Remove legacy key if it exists so future reads use the new name.
  esp_err_t erase_legacy = nvs_erase_key(handle, "secret");
  if (erase_legacy != ESP_OK && erase_legacy != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase legacy account secret key: %s",
             esp_err_to_name(erase_legacy));
  }

  err = nvs_commit(handle);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to commit credentials: %s", esp_err_to_name(err));
  }
  nvs_close(handle);
  return err;
}

esp_err_t ul_wifi_credentials_clear(void) {
  nvs_handle_t handle;
  esp_err_t err = ul_wifi_open_namespace(NVS_READWRITE, &handle);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    return ESP_OK;
  }
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to open NVS namespace for erase: %s", esp_err_to_name(err));
    return err;
  }
  esp_err_t erase_err = nvs_erase_key(handle, "ssid");
  if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase SSID key: %s", esp_err_to_name(erase_err));
  }
  erase_err = nvs_erase_key(handle, "password");
  if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase password key: %s", esp_err_to_name(erase_err));
  }
  erase_err = nvs_erase_key(handle, "user");
  if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase user key: %s", esp_err_to_name(erase_err));
  }
  erase_err = nvs_erase_key(handle, "user_password");
  if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase account password key: %s",
             esp_err_to_name(erase_err));
  }
  erase_err = nvs_erase_key(handle, "secret");
  if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(TAG, "Failed to erase legacy secret key: %s",
             esp_err_to_name(erase_err));
  }
  err = nvs_commit(handle);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to commit credential erase: %s", esp_err_to_name(err));
  }
  nvs_close(handle);
  return err;
}
