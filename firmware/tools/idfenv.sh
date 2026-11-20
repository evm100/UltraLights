# Source this file to activate ESP-IDF 6.1 and get an `idf` function.
#   source firmware/tools/idfenv.sh && idf --version
#
# The EIM activation script refuses to run when sourced from another script
# (it checks $0) and defines idf.py as an alias that scripts can't expand. Its
# -e mode prints the environment instead, which works everywhere.
_ul_act="${UL_IDF_ACTIVATE:-$HOME/.espressif/tools/activate_idf_v6.1.sh}"
if [ -f "$_ul_act" ]; then
    while IFS='=' read -r _ul_k _ul_v; do
        case "$_ul_k" in
            ''|SYSTEM_PATH) ;;
            PATH) export PATH="$_ul_v:$PATH" ;;
            *) export "$_ul_k=$_ul_v" ;;
        esac
    done <<EOF
$(sh "$_ul_act" -e)
EOF
    export VIRTUAL_ENV="${IDF_PYTHON_ENV_PATH:-}"
fi
unset _ul_act _ul_k _ul_v
idf() { python "$IDF_PATH/tools/idf.py" "$@"; }
