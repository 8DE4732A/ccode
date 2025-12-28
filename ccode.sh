#!/usr/bin/env bash

# 处理参数
OPTION="$1"
shift 2>/dev/null || true
CLAUDE_ARGS=("$@")

# 获取配置文件路径
get_config_path() {
    local script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    local current_dir_config="${script_dir}/ccode_config.yaml"

    if [[ -f "$current_dir_config" ]]; then
        echo "$current_dir_config"
        return
    fi

    local user_home="${HOME}"
    echo "${user_home}/.ccode/ccode_config.yaml"
}

# 读取并解析 YAML 配置
# 输出格式:
#   common|KEY=VALUE
#   option|NAME|KEY=VALUE
read_yaml_config() {
    local config_path="$1"

    if [[ ! -f "$config_path" ]]; then
        echo "错误: 配置文件未找到: $config_path" >&2
        exit 1
    fi

    local current_section=""
    local current_option=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

        if [[ -z "$line" ]] || [[ "$line" =~ ^# ]]; then
            continue
        fi

        if [[ "$line" =~ ^[^:]+:$ ]] && ! [[ "$line" =~ [[:space:]] ]]; then
            section_name="${line%:}"
            if [[ "$section_name" == "options" ]] || [[ "$section_name" == "common" ]]; then
                current_section="$section_name"
                current_option=""
            elif [[ "$current_section" == "options" ]]; then
                current_option="$section_name"
            fi
        elif [[ "$line" =~ : ]]; then
            key=$(echo "$line" | cut -d':' -f1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            value=$(echo "$line" | cut -d':' -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//")

            if [[ "$current_section" == "common" ]]; then
                echo "common|${key}=${value}"
            elif [[ "$current_section" == "options" ]] && [[ -n "$current_option" ]]; then
                echo "option|${current_option}|${key}=${value}"
            fi
        fi
    done < "$config_path"
}

# 生成环境变量设置脚本
generate_env_script() {
    local config_path="$1"
    local selected_option="$2"

    local config_data
    config_data=$(read_yaml_config "$config_path")

    # 输出环境变量设置命令
    echo "#!/usr/bin/env bash"
    echo "# 自动生成的环境变量设置脚本"

    # 先输出 common
    echo "$config_data" | grep "^common|" | cut -d'|' -f2 | while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        echo "export ${key}=\"${value}\""
    done

    # 再输出选中选项的配置（覆盖 common）
    echo "$config_data" | grep "^option|${selected_option}|" | cut -d'|' -f3 | while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        echo "export ${key}=\"${value}\""
    done
}

# 显示配置信息
show_configuration() {
    local selected_option="$1"
    local config_path="$2"
    shift 2
    local available_options=("$@")

    local config_data
    config_data=$(read_yaml_config "$config_path")

    echo "==========================================="
    echo -e "\033[32mClaude Code 启动工具\033[0m"
    echo "==========================================="
    echo ""
    echo -e "\033[33m当前配置:\033[0m \033[32m${selected_option}\033[0m"
    echo ""
    echo -e "\033[33m可用选项:\033[0m"

    for opt in "${available_options[@]}"; do
        if [[ "$opt" == "$selected_option" ]]; then
            echo -e "* \033[32m${opt}\033[0m"
        else
            echo "  ${opt}"
        fi
    done

    echo ""
    echo -e "\033[33m最终环境变量:\033[0m"

    # 收集所有变量到数组
    declare -a env_vars
    declare -a processed_keys

    # common
    while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        if [[ ! " ${processed_keys[@]} " =~ " ${key} " ]]; then
            env_vars+=("${key}=${value}")
            processed_keys+=("$key")
        fi
    done < <(echo "$config_data" | grep "^common|" | cut -d'|' -f2)

    # option (覆盖 common)
    while IFS='=' read -r key value; do
        [[ -z "$key" ]] && continue
        # 查找并替换
        for i in "${!env_vars[@]}"; do
            if [[ "${env_vars[$i]}" == ${key}=* ]]; then
                unset "env_vars[$i]"
                break
            fi
        done
        env_vars+=("${key}=${value}")
    done < <(echo "$config_data" | grep "^option|${selected_option}|" | cut -d'|' -f3)

    # 排序显示
    for item in $(printf '%s\n' "${env_vars[@]}" | sort); do
        key=$(echo "$item" | cut -d'=' -f1)
        value=$(echo "$item" | cut -d'=' -f2-)

        if [[ "$key" == "ANTHROPIC_AUTH_TOKEN" ]] && [[ -n "$value" ]]; then
            if [[ ${#value} -gt 6 ]]; then
                prefix="${value:0:3}"
                suffix="${value: -3}"
                masked_length=$((${#value} - 6))
                masked_part=$(printf '%*s' "$masked_length" | tr ' ' '*')
                echo " $key = ${prefix}${masked_part}${suffix}"
            else
                masked_part=$(printf '%*s' "${#value}" | tr ' ' '*')
                echo " $key = ${masked_part}"
            fi
        else
            echo " $key = $value"
        fi
    done

    echo ""
    echo "==========================================="
    echo ""
}

# 主逻辑
main() {
    local config_path
    config_path=$(get_config_path)

    # 解析配置获取可用选项
    local config_data
    config_data=$(read_yaml_config "$config_path")

    local available_options
    available_options=$(echo "$config_data" | grep "^option|" | cut -d'|' -f2 | sort -u)

    if [[ -z "$available_options" ]]; then
        echo "错误: 配置文件中未找到任何选项" >&2
        exit 1
    fi

    # 将选项转为数组
    local opt_array=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && opt_array+=("$line")
    done < <(echo "$available_options")

    # 确定要使用的选项
    if [[ -z "$OPTION" ]]; then
        selected_option="${opt_array[0]}"
    else
        local found=0
        for opt in "${opt_array[@]}"; do
            if [[ "$opt" == "$OPTION" ]]; then
                found=1
                break
            fi
        done

        if [[ $found -eq 1 ]]; then
            selected_option="$OPTION"
        else
            echo "错误: 选项 '$OPTION' 不存在。可用选项: ${opt_array[*]}" >&2
            exit 1
        fi
    fi

    # 显示配置信息
    show_configuration "$selected_option" "$config_path" "${opt_array[@]}"

    # 设置环境变量
    echo -e "\033[32m正在设置环境变量...\033[0m"

    # 生成临时脚本并 source 它
    local temp_script=$(mktemp)
    generate_env_script "$config_path" "$selected_option" > "$temp_script"
    chmod +x "$temp_script"

    # source 该脚本以在当前 shell 设置环境变量
    source "$temp_script"
    rm -f "$temp_script"

    echo -e "\033[32m环境变量设置完成\033[0m"
    echo ""
    echo -e "\033[32m正在启动 claude 命令...\033[0m"
    if [[ ${#CLAUDE_ARGS[@]} -gt 0 ]]; then
        echo -e "\033[33m附加参数:\033[0m ${CLAUDE_ARGS[*]}"
    fi
    echo ""

    # 验证环境变量（可选调试）
    # echo "验证 ANTHROPIC_BASE_URL: $ANTHROPIC_BASE_URL"

    claude "${CLAUDE_ARGS[@]}"
}

# 运行主函数
main "$@"
