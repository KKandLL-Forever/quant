#!/bin/bash
# AWS EC2 上 ClickHouse 一键安装脚本（支持 Ubuntu/Debian 与 Amazon Linux/RHEL）
# 用法：
#   sudo bash install_clickhouse.sh [CLICKHOUSE_PASSWORD]
#
# 关于密码参数：
#   这是 ClickHouse 内置 'default' 用户的初始登录密码。脚本会：
#     1. 用 sha256 哈希后写入 /etc/clickhouse-server/users.d/default-password.xml
#     2. 之后本地 CLI 与远程 HTTP 都用这个密码：
#          clickhouse-client --password '<密码>'
#          http://default:<密码>@<host>:8123/
#     3. 同时给 default 用户开启 access_management 权限，登录后可用 SQL 创建其他用户：
#          CREATE USER analyst IDENTIFIED BY 'xxx';
#          GRANT SELECT ON *.* TO analyst;
#   注意事项：
#     - 不传参数时脚本会自动生成随机密码，并在最后输出，请务必保存。
#     - 后续修改密码：编辑 users.d/default-password.xml 中的 sha256 后
#       `systemctl restart clickhouse-server`，或登录后执行 SQL：
#          ALTER USER default IDENTIFIED BY '<新密码>';
#     - 密码若包含 $ ! ` " 等 shell 特殊字符，命令行务必用单引号包裹（见下方示例）。
#
# 可选环境变量（用于把 EBS 数据盘挂到 /var/lib/clickhouse）：
#   DATA_DEVICE=/dev/nvme1n1     # 待格式化并挂载的块设备（指定该变量才会执行挂盘步骤）
#   DATA_MOUNT=/var/lib/clickhouse   # 挂载点（默认 /var/lib/clickhouse）
#   DATA_FSTYPE=ext4             # 文件系统（ext4 或 xfs，默认 ext4）
#   FORCE_FORMAT=0               # 设为 1 则强制重新格式化（危险，会清空盘上数据）
#
# 一行命令示例：
#   sudo bash install_clickhouse.sh 'YourPassw0rd'
#   sudo DATA_DEVICE=/dev/nvme1n1 DATA_FSTYPE=ext4 bash install_clickhouse.sh 'YourPassw0rd'
#
# 安装完成后：
#   - 服务管理： systemctl status clickhouse-server
#   - 原生端口（TCP）：9000
#   - HTTP 端口：     8123
#   - 配置目录： /etc/clickhouse-server/
#   - 数据目录： /var/lib/clickhouse/
#   - 日志目录： /var/log/clickhouse-server/

set -euo pipefail

CH_PASSWORD="${1:-${CLICKHOUSE_PASSWORD:-}}"
DATA_DEVICE="${DATA_DEVICE:-}"
DATA_MOUNT="${DATA_MOUNT:-/var/lib/clickhouse}"
DATA_FSTYPE="${DATA_FSTYPE:-ext4}"
FORCE_FORMAT="${FORCE_FORMAT:-0}"
if [[ -z "$CH_PASSWORD" ]]; then
    CH_PASSWORD=$(openssl rand -base64 24 2>/dev/null || head -c 24 /dev/urandom | base64)
    echo "[INFO] 未传入密码，已随机生成： $CH_PASSWORD"
fi

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] 请使用 root 权限运行（sudo）。" >&2
    exit 1
fi

# ---- 识别系统发行版 ----
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    DISTRO_ID="${ID,,}"
    DISTRO_LIKE="${ID_LIKE:-}"
else
    echo "[ERROR] 无法识别操作系统。" >&2
    exit 1
fi

echo "[INFO] 检测到系统： $DISTRO_ID"

# ---- 挂载 EBS 数据盘（可选，需在装包之前完成，使 /var/lib/clickhouse 落在数据盘上）----
mount_data_disk() {
    local dev="$1" mnt="$2" fs="$3"

    if [[ ! -b "$dev" ]]; then
        echo "[ERROR] 设备 $dev 不存在。当前可用块设备：" >&2
        lsblk -o NAME,SIZE,TYPE,MOUNTPOINT >&2
        exit 1
    fi

    # 拒绝对根盘或已挂载分区动手
    if lsblk -no MOUNTPOINT "$dev" | grep -qE '^/$'; then
        echo "[ERROR] $dev 看起来是根盘，已中止。" >&2
        exit 1
    fi

    local existing_fs
    existing_fs=$(blkid -o value -s TYPE "$dev" 2>/dev/null || true)

    if [[ -n "$existing_fs" && "$FORCE_FORMAT" != "1" ]]; then
        echo "[INFO] $dev 已有文件系统 '$existing_fs'，跳过格式化（如需强制重做请设置 FORCE_FORMAT=1）。"
    else
        echo "[INFO] 将 $dev 格式化为 $fs ..."
        case "$fs" in
            ext4) mkfs.ext4 -F -L clickhouse "$dev" ;;
            xfs)  mkfs.xfs  -f -L clickhouse "$dev" ;;
            *) echo "[ERROR] 不支持的 DATA_FSTYPE： $fs" >&2; exit 1 ;;
        esac
    fi

    mkdir -p "$mnt"

    local uuid
    uuid=$(blkid -o value -s UUID "$dev")
    if [[ -z "$uuid" ]]; then
        echo "[ERROR] 无法读取 $dev 的 UUID" >&2
        exit 1
    fi

    # 不存在则写入 fstab
    if ! grep -q "UUID=$uuid" /etc/fstab; then
        echo "UUID=$uuid  $mnt  $fs  defaults,nofail,noatime  0  2" >> /etc/fstab
        echo "[INFO] 已将 $dev 写入 /etc/fstab"
    fi

    if ! mountpoint -q "$mnt"; then
        mount "$mnt"
        echo "[INFO] 已挂载 $dev 到 $mnt"
    else
        echo "[INFO] $mnt 已经处于挂载状态。"
    fi

    df -hT "$mnt"
}

if [[ -n "$DATA_DEVICE" ]]; then
    echo "[INFO] 开始挂载数据盘 $DATA_DEVICE -> $DATA_MOUNT （$DATA_FSTYPE）..."
    mount_data_disk "$DATA_DEVICE" "$DATA_MOUNT" "$DATA_FSTYPE"
else
    echo "[INFO] 未指定 DATA_DEVICE，跳过挂盘步骤。"
fi

install_debian() {
    echo "[INFO] 在 Debian/Ubuntu 上安装..."
    apt-get update -y
    apt-get install -y apt-transport-https ca-certificates curl gnupg

    # 用 --dearmor 转成传统 GPG 二进制格式，新版 apt 才认；直接 --import 会生成
    # keybox 格式被 apt 拒绝（报 "unsupported filetype" / NO_PUBKEY 警告）
    rm -f /usr/share/keyrings/clickhouse-keyring.gpg
    curl -fsSL 'https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key' \
        | gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg
    chmod 644 /usr/share/keyrings/clickhouse-keyring.gpg

    echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg] https://packages.clickhouse.com/deb stable main" \
        > /etc/apt/sources.list.d/clickhouse.list

    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        -o Dpkg::Options::="--force-confdef" \
        -o Dpkg::Options::="--force-confold" \
        clickhouse-server clickhouse-client
}

install_rhel() {
    echo "[INFO] 在 RHEL/Amazon Linux/CentOS 上安装..."
    if command -v dnf >/dev/null 2>&1; then
        PKG=dnf
    else
        PKG=yum
    fi
    $PKG install -y yum-utils curl
    $PKG-config-manager --add-repo https://packages.clickhouse.com/rpm/clickhouse.repo 2>/dev/null \
        || yum-config-manager --add-repo https://packages.clickhouse.com/rpm/clickhouse.repo
    $PKG install -y clickhouse-server clickhouse-client
}

case "$DISTRO_ID" in
    ubuntu|debian)
        install_debian
        ;;
    amzn|amazon|rhel|centos|rocky|almalinux|fedora)
        install_rhel
        ;;
    *)
        if [[ "$DISTRO_LIKE" == *debian* ]]; then
            install_debian
        elif [[ "$DISTRO_LIKE" == *rhel* || "$DISTRO_LIKE" == *fedora* ]]; then
            install_rhel
        else
            echo "[ERROR] 不支持的发行版： $DISTRO_ID" >&2
            exit 1
        fi
        ;;
esac

# ---- 修正数据目录权限（防止挂盘后 owner 不正确）----
if [[ -n "$DATA_DEVICE" ]]; then
    chown -R clickhouse:clickhouse "$DATA_MOUNT"
fi

# ---- 开启远程访问 ----
echo "[INFO] 开启远程访问（监听 0.0.0.0）..."
mkdir -p /etc/clickhouse-server/config.d
cat > /etc/clickhouse-server/config.d/listen.xml <<EOF
<clickhouse>
    <listen_host>0.0.0.0</listen_host>
</clickhouse>
EOF

# ---- 设置 default 用户密码 ----
echo "[INFO] 设置 default 用户密码..."
PASSWORD_SHA256=$(printf '%s' "$CH_PASSWORD" | sha256sum | awk '{print $1}')
mkdir -p /etc/clickhouse-server/users.d
cat > /etc/clickhouse-server/users.d/default-password.xml <<EOF
<clickhouse>
    <users>
        <!-- replace="replace": 整段替换原 users.xml 中的 default 用户，
             避免新版 ClickHouse(26+) 因 <password> 与 <password_sha256_hex> 并存而报错 -->
        <default replace="replace">
            <password_sha256_hex>${PASSWORD_SHA256}</password_sha256_hex>
            <networks><ip>::/0</ip></networks>
            <profile>default</profile>
            <quota>default</quota>
            <access_management>1</access_management>
            <named_collection_control>1</named_collection_control>
            <show_named_collections>1</show_named_collections>
            <show_named_collections_secrets>1</show_named_collections_secrets>
        </default>
    </users>
</clickhouse>
EOF
chown -R clickhouse:clickhouse /etc/clickhouse-server/users.d /etc/clickhouse-server/config.d
chmod 640 /etc/clickhouse-server/users.d/default-password.xml

# ---- 启动服务 ----
echo "[INFO] 启动 clickhouse-server..."
systemctl daemon-reload
systemctl enable clickhouse-server
systemctl restart clickhouse-server

sleep 3
systemctl --no-pager status clickhouse-server | head -n 15 || true

# ---- 验证 ----
echo "[INFO] 验证连接..."
if clickhouse-client --password "$CH_PASSWORD" --query "SELECT version()"; then
    echo "[OK] ClickHouse 已运行。"
else
    echo "[WARN] 验证查询失败，请检查日志： journalctl -u clickhouse-server -n 100"
fi

cat <<EOF

==================== ClickHouse 安装完成 ====================
  原生端口（TCP） : 9000
  HTTP 端口       : 8123
  默认用户        : default
  密码            : $CH_PASSWORD

本地客户端：
  clickhouse-client --password '$CH_PASSWORD'

远程测试（在本机执行）：
  curl 'http://default:$CH_PASSWORD@<EC2_PUBLIC_IP>:8123/?query=SELECT+version()'

EC2 安全组：仅向你自己的 IP 开放 8123 / 9000 入站，切勿对 0.0.0.0/0 开放。
=============================================================
EOF
