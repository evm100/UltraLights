cmake_minimum_required(VERSION 3.22)

file(STRINGS ${CMAKE_CURRENT_LIST_DIR}/version.txt UL_VERSION LIMIT_COUNT 1)
set(PROJECT_VER ${UL_VERSION})

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# One lock file per chip, committed, so managed components are reproducible
# and building one target doesn't rewrite the other's lock.
idf_build_set_property(DEPENDENCIES_LOCK ${CMAKE_CURRENT_LIST_DIR}/dependencies.${IDF_TARGET}.lock)

project(ultralights)
