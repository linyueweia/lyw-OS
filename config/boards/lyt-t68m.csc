# Rockchip RK3568 quad core, LYT T68M NAS board
#   compatible: lyt,t68m / rockchip,rk3568
#   NET : 2x GbE (gmac0@fe2a0000 label lan1 / gmac1@fe010000 label lan2, RTL8211F)
#         + 2x RTL8125 2.5GbE (pcie3x1@fe270000 10,0 → lan4 / pcie3x2@fe280000 20,0 → lan3)
#   STO : eMMC (sdhci) + TF (sdmmc0) + SATA2 @fc800000 + SDIO WiFi AIC8800 (sdmmc2)
#   PMIC: RK809 + TCS4525(vdd_cpu);  LED: gpio4 PC6(power) / PC5(status), default-state=on
#   BTN : gpio-keys 复位键 (gpio3 RK_PB1 低有效) + adc-keys(saradc0 1750000mV) —— 两者并存
# NB: on this board pcie2x1 is DISABLED, combphy2 is given to SATA2
#     （miniPCIe 与 SATA 二选一：combphy2_psq 一个 PHY 两种模式，物理互斥）。
#
# 板级 DTS 权威件：xck-nas/dts/rk3568-lyt-t68m.dts（飞牛金标准，含完整 GPU/VPU/NPU 节点）；
# 旁证：iStoreOS 25.12 的 rk3568-t68m.dts、immortalwrt @594ab12 的 rk3568-lyt-t68m.dts。
# 写法按 iNextOS 的 6.1 vendor BSP 内核适配，逐项差异见 README「板级 DTS 的对齐口径」。

BOARD_NAME="LYT T68M"
BOARD_VENDOR="lyt"
BOARDFAMILY="rk35xx"
BOARD_MAINTAINER="linyueweia"
BOOTCONFIG="radxa-e25-rk3568_defconfig"
KERNEL_TARGET="vendor"

# 根文件系统发行版，与 CI 的 RELEASE=trixie、README 的「Debian trixie」钉死成同一份事实。
# 框架没有板级/家族默认 RELEASE（rockchip64_common.inc 与 rk35xx.conf 都不设），不传就会
# 停在交互选择 —— 漏传一次，本地产物就和 CI 产物不是同一个发行版。
declare -g RELEASE="trixie"

# 桌面开关：与官方 easepi-r1.csc 同值（FULL_DESKTOP 是框架废弃变量，lib/ 全仓无读取者；
# BOOT_LOGO 只在 BUILD_DESKTOP=yes 时生效，见 distro-agnostic.sh:591）。
# 官方发布件同样是「csc 写 yes + CI 传 BUILD_DESKTOP=no」⇒ 产物是 CLI；本仓照抄，
# 不在 csc 里改这两个值（改了=自造与官方的差异）。
FULL_DESKTOP="yes"
BOOT_LOGO="desktop"
BOOT_FDT_FILE="rockchip/rk3568-lyt-t68m.dtb"
BOOT_SCENARIO="spl-blobs"
# 分区表：**msdos（MBR）**，不是 gpt —— 2026-10-28 定案，为了从结构上根除"写卡就起不来"的坑。
#   原因：GPT 的备份头 / last_usable_lba / 保护 MBR 的 count 全部按**镜像文件大小**算，
#         镜像（≈3.4 GiB）dd 进更大的 TF 卡后三者都指向镜像末尾而不是卡末尾：
#            · U-Boot 宽容（`Using Backup GPT` 照样引导）—— 所以"能引导"不能当判据；
#            · Linux 内核严格时直接拒表 → 卡上只出 `mmcblkX`、不出 `p1 p2` → `Waiting for root device`。
#         MBR 没有备份表、没有 last_usable、没有保护 MBR —— 这三个字段根本不存在，
#         任何容量的卡写进去都不需要再跑任何修补脚本（飞牛线 2026-10-27 踩的就是这个坑，
#         那边靠 `tools/gpt-fix.py --apply --renew-partuuid=2` 兜底，本板直接换表根除）。
#   与官方 R1 的差异：官方发布件用 gpt。但引导链不依赖表类型 —— U-Boot 用 `part uuid` 现场取值，
#         内核按 PARTUUID/UUID 找 root，两种表等价；本仓仍保留 `tools/gpt-fix.py`（含 --selftest）
#         作兜底与回归判据，CI 发现是 MBR 表时会直接跳过。
IMAGE_PARTITION_TABLE="msdos"
BOOTFS_TYPE="fat"

# 扩展：本板依赖 istorenext + t68m-roceos 两个扩展。CI 通过命令行
#   ENABLE_EXTENSIONS="istorenext,t68m-roceos" 传入，本文件不再重复声明（避免与 CI 双写）。
# 本地构建必须自己带上，否则出来的镜像没有 roceos 网页后台、也没有 istorenext，且过程不报错：
#   ./compile.sh build BOARD=lyt-t68m BRANCH=vendor RELEASE=trixie \
#     ENABLE_EXTENSIONS="istorenext,t68m-roceos" BUILD_DESKTOP=no BUILD_MINIMAL=yes EXPERT=yes
#
# BUILD_MINIMAL=yes 是**必须**的（2026-10-28 定案，与官方发布件同口径）：
#   官方 R1 发布件文件名带 `_minimal`（框架 rootfs-to-image.sh:21 只有 yes 才加这个后缀），
#   实测 718 个包。传 no 时框架会装 config/cli/<rel>/main/packages.additional 整份，
#   我们因此装到 958 个包，多出 avahi-autoipd / unattended-upgrades / vnstat / iperf3 /
#   plymouth / dkms / rng-tools / build-essential 等。其中
#     · avahi-autoipd 在 DHCP 失败时把网卡配成 169.254 链路本地地址（插件钩子在 if-up.d）
#     · unattended-upgrades 后台自动升级并重启 dnsmasq/网络服务
#   两者都会表现成“网口灯亮、下级设备拿不到 IP / 用几小时就掉”，且官方 minimal 完全没有。
#   CI 已加硬断言：镜像文件名必须含 _minimal，且上述包不得出现在镜像的 dpkg 状态里。

# 网络栈：对齐官方标准镜像（官方 EasePi R1 实测 dpkg 里根本没有 network-manager）。
# 框架默认 BUILD_MINIMAL=no → NETWORKING_STACK=network-manager，会装并启用
# network-manager/network-manager-openvpn/netplan.io/chrony，与本板的
# ifupdown-ng(networking.service) + roceos(建 br-lan1/挂口/写 dnsmasq 配置) + dnsmasq
# 抢管网口 —— 自动生成的 Wired connection 档案会把口从网桥摘走，LAN 时好时坏的根因。
# 显式置 none（框架 main-config 的正式取值，走 "Not adding networking extensions" 分支）。
# 时间同步不受影响：ntpsec-ntpdate 由 istorenext 扩展带入（与官方 R1 同源）。
declare -g NETWORKING_STACK="none"

function post_family_tweaks__lyt_t68m_hold_dtb() {
	display_alert "$BOARD" "Prevent armbian-upgrade from removing our dtb" "info"
	chroot_sdcard apt-mark hold linux-dtb-vendor-rk35xx || true
	return 0
}

function post_family_tweaks__lyt_t68m_network_interfaces() {
	display_alert "$BOARD" "Renaming LYT T68M network interfaces to eth0-3" "info"

	# 与官方 easepi-r1.csc 保持一致：udev 规则与 /etc/eth_order 并用
	# （udev 在设备出现时改名；fix-ifaces-name.service 再按 eth_order 校正一次）。
	# 顺序表必须与 DTS 里的 label（lan1~lan4）及 PCI 拓扑一致：
	#   eth0 = fe2a0000 (gmac0) / eth1 = fe010000 (gmac1)
	#   eth2 = 0002:21:00.0 (pcie3x2@fe280000) / eth3 = 0001:11:00.0 (pcie3x1@fe270000)
	mkdir -p "${SDCARD}/etc/udev/rules.d/"
	cat <<- EOF > "${SDCARD}/etc/udev/rules.d/70-persistent-net.rules"
		SUBSYSTEM=="net", ACTION=="add", KERNELS=="fe2a0000.ethernet", NAME:="eth0"
		SUBSYSTEM=="net", ACTION=="add", KERNELS=="fe010000.ethernet", NAME:="eth1"
		SUBSYSTEM=="net", ACTION=="add", KERNELS=="0002:21:00.0", NAME:="eth2"
		SUBSYSTEM=="net", ACTION=="add", KERNELS=="0001:11:00.0", NAME:="eth3"
	EOF

	echo "DEFAULT_INTERFACE=eth0" > "${SDCARD}/root/.default-network"
	echo "fe2a0000.ethernet,fe010000.ethernet,0002:21:00.0,0001:11:00.0" > "${SDCARD}/etc/eth_order"
	return 0
}

# ── 对齐官方 R1：smartmontools「包在、但不启用」（2026-10-29 实机取证定案）──
# 官方 R1 发布件实测：/usr/lib/systemd/system/smartmontools.service **存在**，
# 但 multi-user.target.wants 里**没有**它的启用链接（官方 wants 共 30 个）。
# 我们的镜像因为在构建期 deb 解包时被 Debian trixie 的 preset（smartmontools:
# preset enabled）自动生成了链接，结果每次开机必 failed —— eMMC 不支持 SMART，
# smartd 退出码 17「No devices to monitor」，systemctl --failed 长期挂一个红。
# 实机取证（判据＝enable 链 mtime 比对开机时刻）：
#   enable 链 mtime = Oct 28 16:21:24（构建期）  <  开机 = Oct 28 16:22:56
#   ⇒ 构建产物偏差；而 openvpn/strongswan/roceos-guard 的链 mtime 都在开机之后，
#     属 roceos 首启自装/在线更新，不算偏差，本钩子不去动它们。
# 修法＝**只删启用链接、保留包**（官方 R1 也发行这个包），不是卸载。
# 为什么直接 rm 而不是 systemctl disable：框架已知坑 —— chroot_sdcard 里跑
# systemctl 在构建容器里未必改得动镜像内的链接（enable 侧同坑，见技能 §5），
# 所以直接删文件并在函数内自证；镜像侧再由 build.yml 的反向断言把关。
function post_family_tweaks__lyt_t68m_disable_smartmontools() {
	display_alert "$BOARD" "Align smartmontools with official R1 (package present, NOT enabled)" "info"
	rm -f "${SDCARD}/etc/systemd/system/multi-user.target.wants/smartmontools.service"
	rm -f "${SDCARD}/etc/systemd/system/smartd.service"
	# 自证 1：链接必须已消失（删不掉就构建失败，别让偏差溜进镜像）
	if [ -e "${SDCARD}/etc/systemd/system/multi-user.target.wants/smartmontools.service" ] ||
	   [ -L "${SDCARD}/etc/systemd/system/multi-user.target.wants/smartmontools.service" ]; then
		exit_with_error "smartmontools enable link still present in image (official R1: package ships, NOT enabled)"
	fi
	# 自证 2：包本身必须还在 —— 官方 R1 同样发行它，卸载是自造差异而非对齐官方
	if ! grep -q "^Package: smartmontools$" "${SDCARD}/var/lib/dpkg/status" 2>/dev/null; then
		exit_with_error "smartmontools package missing; official R1 ships it — disable only, do NOT uninstall"
	fi
	return 0
}

