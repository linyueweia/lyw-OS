# Rockchip RK3568 quad core, LYT T68M NAS board
#   compatible: lyt,t68m / rockchip,rk3568
#   NET : 2x GbE (gmac0@fe2a0000, gmac1@fe010000) + 2x RTL8125 2.5GbE (PCIe3 x1/x2)
#   STO : eMMC (sdhci) + TF (sdmmc0) + SATA2 @fc800000 + SDIO WiFi AIC8800 (sdmmc2)
#   PMIC: RK809 + TCS4525(vdd_cpu);  LED: gpio4 PC6(power)/PC5(status)
# NB: on this board pcie2x1 is DISABLED, combphy2 is given to SATA2.
BOARD_NAME="LYT T68M"
BOARD_VENDOR="lyt"
BOARDFAMILY="rk35xx"
BOARD_MAINTAINER="linyueweia"
BOOTCONFIG="radxa-e25-rk3568_defconfig"
KERNEL_TARGET="vendor"
FULL_DESKTOP="yes"
BOOT_LOGO="desktop"
BOOT_FDT_FILE="rockchip/rk3568-lyt-t68m.dtb"
BOOT_SCENARIO="spl-blobs"
IMAGE_PARTITION_TABLE="gpt"
BOOTFS_TYPE="fat"

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
	# （udev 在设备出现时改名；fix-ifaces-name.service 再按 eth_order 校正一次）
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

