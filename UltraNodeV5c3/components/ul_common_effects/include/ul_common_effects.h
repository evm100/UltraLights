#pragma once
#include <stdint.h>

float ul_ease_in_out(float t);
uint8_t ul_gamma8(uint8_t x);
void ul_apply_transition(uint8_t* dst, const uint8_t* src_from, const uint8_t* src_to, int count, float alpha);
