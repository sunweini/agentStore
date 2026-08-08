#!/bin/sh
# compile_service/docker-entrypoint.sh
# 容器入口:COMPILE_SERVICE_REQUIRES_DLLS=1 时校验金蝶 BOS DLL 是否到位,
# 缺失则显式报错退出(标记"DLL 未到位",不静默启动服务)。
# 注:当前基础镜像为 Windows Server Core(msbuild),默认不跑本脚本;
#     换 mono/Linux 基础镜像或团队自备镜像时,以 ENTRYPOINT 接入。
set -e

REFS_DIR="${REFS_DIR:-/app/references}"

if [ "$COMPILE_SERVICE_REQUIRES_DLLS" = "1" ]; then
    if [ ! -d "$REFS_DIR" ] || [ -z "$(ls -A "$REFS_DIR" 2>/dev/null)" ]; then
        echo "ERROR: 金蝶 BOS DLL 未提供($REFS_DIR 为空),真实编译不可用。请将 DLL 放入 build/references/ 后重新构建。" >&2
        exit 1
    fi
    echo "金蝶 BOS DLL 就绪:$(ls -A "$REFS_DIR" | wc -l) 个引用文件"
fi

exec "$@"
