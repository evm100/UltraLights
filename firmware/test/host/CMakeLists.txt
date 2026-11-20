# Host tests for the pure-C parts of the firmware (contract codec, board
# descriptor, device model). Plain CMake + Unity; no chip needed.
#   firmware/tools/host_test.sh
cmake_minimum_required(VERSION 3.16)
project(ul_host_tests C)

set(CMAKE_C_STANDARD 11)
set(FW ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(CONTRACTS ${FW}/../contracts)

include(FetchContent)
if(POLICY CMP0169)
    cmake_policy(SET CMP0169 OLD)  # FetchContent_Populate: we only want the sources
endif()

# cJSON: same version as the espressif/cjson component used on the chip.
if(DEFINED ENV{UL_CJSON_DIR})
    set(CJSON_DIR $ENV{UL_CJSON_DIR})
else()
    FetchContent_Declare(cjson
        GIT_REPOSITORY https://github.com/DaveGamble/cJSON.git
        GIT_TAG v1.7.19
        GIT_SHALLOW TRUE)
    FetchContent_GetProperties(cjson)
    if(NOT cjson_POPULATED)
        FetchContent_Populate(cjson)
    endif()
    set(CJSON_DIR ${cjson_SOURCE_DIR})
endif()

# Unity: from ESP-IDF when available, otherwise upstream.
if(DEFINED ENV{IDF_PATH} AND EXISTS $ENV{IDF_PATH}/components/unity/unity/src/unity.c)
    set(UNITY_DIR $ENV{IDF_PATH}/components/unity/unity/src)
else()
    FetchContent_Declare(unity
        GIT_REPOSITORY https://github.com/ThrowTheSwitch/Unity.git
        GIT_TAG v2.6.0
        GIT_SHALLOW TRUE)
    FetchContent_GetProperties(unity)
    if(NOT unity_POPULATED)
        FetchContent_Populate(unity)
    endif()
    set(UNITY_DIR ${unity_SOURCE_DIR}/src)
endif()

add_executable(ul_host_tests
    main.c
    ${CJSON_DIR}/cJSON.c
    ${UNITY_DIR}/unity.c
    ${FW}/components/ul_contract/src/ul_json.c
    ${FW}/components/ul_contract/src/ul_contract_board.c
    ${FW}/components/ul_contract/src/ul_contract_msgs.c
    ${FW}/components/ul_board/src/ul_board_rules.c
    ${FW}/components/ul_board/src/ul_chip_rules.c
    ${FW}/components/ul_core/src/ul_core_logic.c
    ${FW}/components/ul_contract/test/test_contract.c
    ${FW}/components/ul_board/test/test_board.c
    ${FW}/components/ul_core/test/test_core.c
)
target_include_directories(ul_host_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CJSON_DIR}
    ${UNITY_DIR}
    ${FW}/components/ul_contract/include
    ${FW}/components/ul_contract/src
    ${FW}/components/ul_board/include
    ${FW}/components/ul_core/include
)
target_compile_definitions(ul_host_tests PRIVATE
    UL_CONTRACTS_DIR="${CONTRACTS}"
    UL_HOST_OUT_DIR="${CMAKE_CURRENT_BINARY_DIR}/encoded"
    UNITY_INCLUDE_DOUBLE
)
target_compile_options(ul_host_tests PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter
    $<$<COMPILE_LANGUAGE:C>:-Wno-missing-field-initializers>)
set_source_files_properties(${CJSON_DIR}/cJSON.c ${UNITY_DIR}/unity.c PROPERTIES COMPILE_OPTIONS "-w")
target_link_libraries(ul_host_tests PRIVATE m)

enable_testing()
add_test(NAME ul_host_tests COMMAND ul_host_tests)
