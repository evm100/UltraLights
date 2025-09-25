idf_component_register(SRCS "ul_provisioning.c" "dns_server.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_http_server esp_wifi esp_event esp_netif freertos lwip ul_wifi ul_core json
                       EMBED_TXTFILES portal_index.html)
