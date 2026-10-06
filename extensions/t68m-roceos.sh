#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# T68M 板级扩展：装入 roceos 网页后台 + AIC8800 WiFi 固件
#
# 为什么用扩展 + post_post_debootstrap_tweaks 这个早期钩子：
#   官方 istorenext 扩展就是在这一阶段用
#       chroot_sdcard systemctl --no-reload enable <unit>
#   启用它自己的服务的。实测（构建 #37404316629）在 customize-image.sh 阶段
#   用 `ln -sf` 建 multi-user.target.wants 链接，钩子内自检通过、但最终镜像里
#   链接消失（被后续流程清掉），导致构建自证失败。改用官方同款阶段后链接可留存。
#
# 装的东西（与官方 Easepi-r1 镜像逐项一致）：
#   /opt/roceos、/opt/roceos-ai、roceos{,-ai,-vision}.service（并启用）、
#   nginx 站点+证书（移除 default 站点）、Flask 栈、AIC8800 SDIO 固件
# ══════════════════════════════════════════════════════════════════════════

# 载荷 sha256（与 release roceos-payload-v1 一致；内容一变就报错）
ROCEOS_PAYLOAD_SHA256="6b819584a1b1701163f8983bd59a31821643f64dd65c76ba8f500ef54c5e8bb5"

function post_post_debootstrap_tweaks__t68m_roceos() {
	display_alert "Installing roceos web UI + AIC8800 firmware (T68M)" "$EXTENSION" "info"

	local payload="${USERPATCHES_PATH}/overlay/roceos-payload.tar.gz"
	[[ -f "$payload" ]] || exit_with_error "roceos payload not found: ${payload}"
	# 指纹固定断言：不留"以为改了其实没改"的余地
	echo "${ROCEOS_PAYLOAD_SHA256}  ${payload}" | sha256sum -c - || exit_with_error "roceos payload sha256 mismatch"

	local stage
	stage="$(mktemp -d)"
	tar xzf "$payload" -C "$stage"
	for f in opt/roceos/roceos opt/roceos/www/index.html opt/roceos-ai/server.py \
		etc/systemd/system/roceos.service etc/nginx/sites-available/roceos.conf \
		etc/nginx/ssl/roceos.key; do
		[[ -e "${stage}/${f}" ]] || exit_with_error "payload missing ${f}"
	done

	# ── 应用本体 ────────────────────────────────────────────────
	mkdir -p "${SDCARD}/opt" "${SDCARD}/storage"
	rm -rf "${SDCARD}/opt/roceos" "${SDCARD}/opt/roceos-ai"
	cp -a "${stage}/opt/roceos" "${stage}/opt/roceos-ai" "${SDCARD}/opt/"
	mkdir -p "${SDCARD}/opt/roceos/data" "${SDCARD}/opt/roceos/logs"
	chmod +x "${SDCARD}/opt/roceos/roceos" "${SDCARD}/opt/roceos/roceos-vision" 2>/dev/null || true
	for b in "${SDCARD}"/opt/roceos/bin/*; do [[ -e "$b" ]] && chmod +x "$b"; done
	chown -R root:root "${SDCARD}/opt/roceos" "${SDCARD}/opt/roceos-ai" "${SDCARD}/storage"

	# ── systemd 服务 ───────────────────────────────────────────
	for s in roceos.service roceos-ai.service roceos-vision.service; do
		cp -a "${stage}/etc/systemd/system/${s}" "${SDCARD}/etc/systemd/system/"
	done

	# ── nginx 站点与证书（default 站点必须移除：它与 roceos.conf 抢 default_server）──
	mkdir -p "${SDCARD}/etc/nginx/ssl" "${SDCARD}/etc/nginx/sites-available" "${SDCARD}/etc/nginx/sites-enabled"
	cp -a "${stage}/etc/nginx/ssl/roceos.crt" "${stage}/etc/nginx/ssl/roceos.key" "${SDCARD}/etc/nginx/ssl/"
	cp -a "${stage}/etc/nginx/sites-available/roceos.conf" "${SDCARD}/etc/nginx/sites-available/"
	ln -sf /etc/nginx/sites-available/roceos.conf "${SDCARD}/etc/nginx/sites-enabled/roceos.conf"
	rm -f "${SDCARD}/etc/nginx/sites-enabled/default"

	# ── AIC8800 SDIO WiFi 固件 ─────────────────────────────────
	# 驱动已内建（CONFIG_AIC8800_WLAN_SUPPORT=m），缺的只是固件。
	# 驱动编译进的路径是全大写 aic8800/SDIO/aic8800D80/，厂商用的是小写 aic8800/sdio/，
	# 两份都放，避免路径大小写差异导致加载失败。
	if [[ -d "${stage}/lib/firmware/aic8800" ]]; then
		mkdir -p "${SDCARD}/lib/firmware"
		cp -a "${stage}/lib/firmware/aic8800" "${SDCARD}/lib/firmware/"
	fi

	# ── Flask 栈（roceos-ai 用）：apt 优先，失败回退载荷内文件 ────
	chroot_sdcard apt-get install -y -qq python3-flask python3-werkzeug python3-jinja2 \
		python3-itsdangerous python3-markupsafe python3-blinker || true
	if ! chroot_sdcard python3 -c "import flask, werkzeug, jinja2, itsdangerous, markupsafe, blinker" 2>/dev/null; then
		display_alert "Flask stack import failed, using payload fallback" "$EXTENSION" "warn"
		mkdir -p "${SDCARD}/usr/lib/python3/dist-packages"
		cp -a "${stage}/usr/lib/python3/dist-packages/." "${SDCARD}/usr/lib/python3/dist-packages/"
	fi

	rm -rf "$stage"

	# ── 启用服务（官方同款写法）───────────────────────────────
	chroot_sdcard systemctl --no-reload enable roceos.service
	chroot_sdcard systemctl --no-reload enable roceos-ai.service
	chroot_sdcard systemctl --no-reload enable roceos-vision.service

	# ── 再补一份 systemd preset ────────────────────────────────
	# 万一后续流程跑了 systemctl preset-all（会按 preset 重置启用状态），
	# 有这份 preset 才能保证这三个服务保持 enabled。
	mkdir -p "${SDCARD}/etc/systemd/system-preset"
	cat >"${SDCARD}/etc/systemd/system-preset/90-t68m-roceos.preset" <<-'EOF'
	enable roceos.service
	enable roceos-ai.service
	enable roceos-vision.service
	EOF

	# ── 阶段内自证 ────────────────────────────────────────────
	local fail=0
	for f in opt/roceos/roceos opt/roceos/www/index.html opt/roceos-ai/server.py \
		etc/systemd/system/roceos.service \
		etc/systemd/system/multi-user.target.wants/roceos.service \
		etc/systemd/system/multi-user.target.wants/roceos-ai.service \
		etc/systemd/system/multi-user.target.wants/roceos-vision.service \
		etc/nginx/sites-available/roceos.conf etc/nginx/ssl/roceos.key \
		etc/systemd/system-preset/90-t68m-roceos.preset; do
		[[ -e "${SDCARD}/${f}" ]] || { display_alert "missing ${f}" "$EXTENSION" "err"; fail=1; }
	done
	[[ -L "${SDCARD}/etc/nginx/sites-enabled/roceos.conf" ]] || { display_alert "nginx site not enabled" "$EXTENSION" "err"; fail=1; }
	[[ -e "${SDCARD}/etc/nginx/sites-enabled/default" ]] && { display_alert "default site still present" "$EXTENSION" "err"; fail=1; }
	[[ $fail == 0 ]] || exit_with_error "t68m roceos customization self-check failed"

	display_alert "roceos web UI + WiFi firmware installed and enabled (T68M)" "$EXTENSION" "info"
	return 0
}
