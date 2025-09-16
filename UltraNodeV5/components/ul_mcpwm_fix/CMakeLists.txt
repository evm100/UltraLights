idf_component_register(REQUIRES esp_driver_mcpwm)

idf_component_get_property(_mcpwm_lib esp_driver_mcpwm COMPONENT_LIB)
if(_mcpwm_lib)
    target_include_directories(${_mcpwm_lib} BEFORE PUBLIC "${CMAKE_CURRENT_LIST_DIR}/include")
endif()
