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

function post_family_tweaks__lyt_t68m_hold_dtb() {
	display_alert "$BOARD" "Prevent armbian-upgrade from removing our dtb" "info"
	chroot_sdcard apt-mark hold linux-dtb-vendor-rk35xx || true
	return 0
}

function post_family_tweaks__lyt_t68m_network_interfaces() {
	display_alert "$BOARD" "Setting T68M eth_order and default network interface" "info"

	# iNextOS 官方机制：/etc/eth_order + fix-ifaces-name.service 负责把网口按顺序
	# 重命名为 eth0..N，因此这里不再写 udev 规则（会与 fix-ifaces-name 冲突）。
	echo "fe2a0000.ethernet,fe010000.ethernet,0002:21:00.0,0001:11:00.0" > "${SDCARD}/etc/eth_order"

	# 首启静态管理口：istorenext-init-network.service 会读它并生成 /etc/network/interfaces
	echo "DEFAULT_INTERFACE=eth0" > "${SDCARD}/root/.default-network"
	return 0
}

