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

# NAS CLI 镜像（CI 固定 BUILD_DESKTOP=no）：不要桌面包、不要桌面 logo。
FULL_DESKTOP="no"
BOOT_FDT_FILE="rockchip/rk3568-lyt-t68m.dtb"
BOOT_SCENARIO="spl-blobs"
IMAGE_PARTITION_TABLE="gpt"
BOOTFS_TYPE="fat"

# 扩展：本板依赖 istorenext + t68m-roceos 两个扩展。CI 通过命令行
#   ENABLE_EXTENSIONS="istorenext,t68m-roceos" 传入，本文件不再重复声明（避免与 CI 双写）。
# 本地构建必须自己带上，否则出来的镜像没有 roceos 网页后台、也没有 istorenext，且过程不报错：
#   ./compile.sh build BOARD=lyt-t68m BRANCH=vendor RELEASE=trixie \
#     ENABLE_EXTENSIONS="istorenext,t68m-roceos" BUILD_DESKTOP=no BUILD_MINIMAL=no EXPERT=yes

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
