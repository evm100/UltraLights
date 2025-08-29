# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION 3.5)

file(MAKE_DIRECTORY
  "/home/evm100/esp-idf/components/bootloader/subproject"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/tmp"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/src/bootloader-stamp"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/src"
  "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/src/bootloader-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/src/bootloader-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/evm100/Documents/UltraLights/UltraLightsV5/LightsTest/build/bootloader-prefix/src/bootloader-stamp${cfgdir}") # cfgdir has leading slash
endif()
