idf_component_register(
    SRCS "test_main.c"
    PRIV_INCLUDE_DIRS "../../../components/ul_identity/src"
    PRIV_REQUIRES ul_identity ul_contract mbedtls unity
)
