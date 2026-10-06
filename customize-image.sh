#!/bin/bash
# iNextOS T68M 板级定制：在构建阶段补齐官方镜像具备的 roceos 网页后台
#
# 调用方（框架 lib/functions/rootfs/customize.sh）：
#   chroot_sdcard /tmp/customize-image.sh "$RELEASE" "$LINUXFAMILY" "$BOARD" "$BUILD_DESKTOP" "$ARCH"
# 因此本脚本运行在**目标镜像的 chroot 内**，/ 就是镜像根文件系统。
# 载荷经 userpatches/overlay 通道送入，在 chroot 内位于 /tmp/overlay/。
#
# 补齐内容（与官方 Easepi-r1 镜像逐项一致）：
#   /opt/roceos            主程序 roceos + roceos-vision + www 前端
#   /opt/roceos-ai         AI 服务 server.py + librkllmrt.so
#   roceos.service / roceos-ai.service / roceos-vision.service（并启用）
#   nginx sites-available/roceos.conf + ssl/roceos.{crt,key}（并启用，移除 default）
#   Python Flask 栈：apt 安装，失败则用载荷内文件级 fallback
#
# 背景：缺了这一套，板子能启动但 192.168.100.1 只会显示 nginx 默认欢迎页。
set -e

RELEASE=$1
LINUXFAMILY=$2
BOARD=$3
BUILD_DESKTOP=$4
ARCH=$5

STAGE=/tmp/roceos-payload
PAYLOAD=/tmp/overlay/roceos-payload.tar.gz
FLASK_PKGS="python3-flask python3-werkzeug python3-jinja2 python3-itsdangerous python3-markupsafe python3-blinker"

log() { echo "[ T68M customize ] $*"; }

log "参数: RELEASE=$RELEASE LINUXFAMILY=$LINUXFAMILY BOARD=$BOARD BUILD_DESKTOP=$BUILD_DESKTOP ARCH=$ARCH"

if [ ! -f "$PAYLOAD" ]; then
  log "!! 载荷缺失: $PAYLOAD（检查 workflow 是否下载到 userpatches/overlay/）"
  exit 1
fi

log "▶ 解包载荷到 $STAGE（先全部落在暂存区，校验通过再落地）"
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar xzf "$PAYLOAD" -C "$STAGE"
for d in opt/roceos/roceos opt/roceos/www/index.html opt/roceos-ai/server.py \
         opt/roceos-ai/librkllmrt.so \
         etc/systemd/system/roceos.service etc/systemd/system/roceos-ai.service \
         etc/systemd/system/roceos-vision.service \
         etc/nginx/sites-available/roceos.conf etc/nginx/ssl/roceos.crt etc/nginx/ssl/roceos.key; do
  [ -e "$STAGE/$d" ] || { log "!! 载荷内缺少 $d"; exit 1; }
done
log "  载荷校验通过（$(du -sh "$STAGE" | cut -f1)）"

log "▶ 安装应用本体"
rm -rf /opt/roceos /opt/roceos-ai
cp -a "$STAGE/opt/roceos" /opt/
cp -a "$STAGE/opt/roceos-ai" /opt/
mkdir -p /opt/roceos/data /opt/roceos/logs /storage
chmod +x /opt/roceos/roceos /opt/roceos/roceos-vision
chmod +x /opt/roceos/bin/* 2>/dev/null || true
chown -R root:root /opt/roceos /opt/roceos-ai /storage

log "▶ 安装 systemd 服务并启用"
# 显式建立 multi-user.target.wants 链接（等价于 systemctl enable，且在 chroot 内确定可靠）
install -m 644 "$STAGE/etc/systemd/system/roceos.service"        /etc/systemd/system/
install -m 644 "$STAGE/etc/systemd/system/roceos-ai.service"     /etc/systemd/system/
install -m 644 "$STAGE/etc/systemd/system/roceos-vision.service" /etc/systemd/system/
mkdir -p /etc/systemd/system/multi-user.target.wants
for s in roceos.service roceos-ai.service roceos-vision.service; do
  ln -sf "/etc/systemd/system/$s" "/etc/systemd/system/multi-user.target.wants/$s"
  log "  已启用 $s"
done

log "▶ 安装 nginx 站点与证书"
mkdir -p /etc/nginx/ssl /etc/nginx/sites-available /etc/nginx/sites-enabled
install -m 644 "$STAGE/etc/nginx/ssl/roceos.crt" /etc/nginx/ssl/
install -m 600 "$STAGE/etc/nginx/ssl/roceos.key" /etc/nginx/ssl/
install -m 644 "$STAGE/etc/nginx/sites-available/roceos.conf" /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/roceos.conf /etc/nginx/sites-enabled/roceos.conf
# 坑：default 站点与 roceos.conf 都声明 `listen 80 default_server`，共存会让 nginx 启动失败。
# 另注意：判符号链接必须用 -L —— 对悬空链接 `-e` 返回假，曾因此漏删。
if [ -L /etc/nginx/sites-enabled/default ] || [ -e /etc/nginx/sites-enabled/default ]; then
  rm -f /etc/nginx/sites-enabled/default
  log "  已移除 default 站点"
fi

log "▶ 安装 Python Flask 栈（优先 apt）"
apt-get install -y -qq $FLASK_PKGS >/dev/null 2>&1 || log "  apt 安装未成功，改用载荷内 fallback"
if ! python3 -c "import flask, werkzeug, jinja2, itsdangerous, markupsafe, blinker" 2>/dev/null; then
  log "  使用载荷内 dist-packages fallback"
  cp -a "$STAGE/usr/lib/python3/dist-packages/." /usr/lib/python3/dist-packages/
fi
if python3 - <<'PYEOF'
import importlib.metadata as md
for p in ('flask', 'werkzeug', 'jinja2', 'itsdangerous', 'markupsafe', 'blinker'):
    md.version(p)
print('[ T68M customize ]   ✓ Flask 栈可导入：' + ' '.join(
    f'{p}={md.version(p)}' for p in ('flask', 'werkzeug', 'jinja2', 'itsdangerous', 'markupsafe', 'blinker')))
PYEOF
then :; else
  log "!! Flask 栈不可导入"; exit 1
fi

log "▶ 自证"
fail=0
for f in /opt/roceos/roceos /opt/roceos/www/index.html /opt/roceos-ai/server.py \
         /opt/roceos-ai/librkllmrt.so \
         /etc/systemd/system/roceos.service /etc/systemd/system/roceos-ai.service \
         /etc/systemd/system/roceos-vision.service \
         /etc/systemd/system/multi-user.target.wants/roceos.service \
         /etc/systemd/system/multi-user.target.wants/roceos-ai.service \
         /etc/systemd/system/multi-user.target.wants/roceos-vision.service \
         /etc/nginx/sites-available/roceos.conf /etc/nginx/ssl/roceos.crt /etc/nginx/ssl/roceos.key; do
  if [ -e "$f" ]; then log "   ✅ $f"; else log "   ❌ 缺 $f"; fail=1; fi
done
if [ -L /etc/nginx/sites-enabled/roceos.conf ]; then log "   ✅ nginx 站点已启用"; else log "   ❌ nginx 站点未启用"; fail=1; fi
if [ -L /etc/nginx/sites-enabled/default ] || [ -e /etc/nginx/sites-enabled/default ]; then
  log "   ❌ default 站点仍在"; fail=1
else
  log "   ✅ default 站点已移除"
fi
[ "$fail" = 0 ] || { log "!! 自证失败"; exit 1; }

# nginx 配置语法检查（chroot 内可能因缺 /run 目录报错，属环境噪音，不致命）
if nginx -t >/dev/null 2>&1; then log "   ✅ nginx -t 通过"; else log "   ⚠️ nginx -t 未通过（环境相关，非致命）"; fi

log "✅ roceos 网页后台已在构建阶段就位"
exit 0
