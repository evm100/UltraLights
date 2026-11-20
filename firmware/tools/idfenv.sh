# Source this file: activates ESP-IDF 6.1 and defines `idf` (idf.py is an alias
# that non-interactive shells don't expand).
#   source firmware/tools/idfenv.sh && idf --version
source "${UL_IDF_ACTIVATE:-$HOME/.espressif/tools/activate_idf_v6.1.sh}" >/dev/null 2>&1
idf() { python "$IDF_PATH/tools/idf.py" "$@"; }
