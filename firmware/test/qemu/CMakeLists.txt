# On-chip tests that run in QEMU (ESP32-C3 only; QEMU has no C6 model and no radio).
#   firmware/tools/qemu_test.sh
cmake_minimum_required(VERSION 3.22)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../../components)
set(COMPONENTS main)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(DEPENDENCIES_LOCK ${CMAKE_CURRENT_LIST_DIR}/dependencies.lock)
project(ul_qemu_tests)
