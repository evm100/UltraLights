#include "unity.h"

void setUp(void) {}
void tearDown(void) {}

void run_contract_tests(void);
void run_board_tests(void);
void run_core_tests(void);

int main(void)
{
    UNITY_BEGIN();
    run_contract_tests();
    run_board_tests();
    run_core_tests();
    return UNITY_END();
}
