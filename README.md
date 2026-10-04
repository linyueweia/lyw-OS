# lyt-t68m-inextos

在 **LYT T68M (Rockchip RK3568)** 上编译 **iNextOS**（iStoreNext，Debian trixie / 6.1.115-vendor-rk35xx）镜像。

基于 **jjm2473/armbian-easepi**（branch `easepi-v26.02`，即 iNextOS 的构建框架），
用 GitHub Actions 云编译产出可直接刷写的 `.img` / `.img.xz`。

## 仓库性质与来源说明（重要，勿混淆）

| 名称 | 性质 |
|---|---|
| **iNextOS / iStoreNext** | iStoreOS 团队出品的「路由+存储」系统；**官方镜像由 KoolCenter 酷友社发布**（https://www.koolcenter.com/t/topic/13495 ）。官方支持机型为 EasePi R1/R2、Hinlink H88K 等，**LYT T68M 不在官方支持列表内** |
| **`jjm2473/armbian-easepi`** | iNextOS 的 **ARM 构建框架**（fork of `armbian/build`，branch `easepi-v26.02`），由 iStoreOS/LinkEase 团队开发者 **jjm2473（Liangbin Lian）** 维护，也是社区编译 EasePi / NanoPi R5S 等 iNextOS 固件所依赖的事实标准。**它不是 iNextOS 官方的发行仓库** |
| **本仓库 `linyueweia/lyt-t68m-inextos`** | **第三方板级适配仓库**：为 T68M 增加 board conf + 板级 DTS，并调用上面的构建框架云编译。**非官方** |

即：本仓库 = 「T68M 板级适配层」，真正的系统构建由 `jjm2473/armbian-easepi` 完成。

## 与 EasePi R1 的差异（不可照搬）

| 项 | LYT T68M | EasePi R1 |
|---|---|---|
| 网口 | 2×GbE (`gmac0`@fe2a0000 → eth0, `gmac1`@fe010000 → eth1) + 2×RTL8125 2.5GbE (`pcie3x1`→eth3 / `pcie3x2`→eth2) | 4 口，编号不同 |
| PCIe | **`pcie2x1` disabled** — 释放 `combphy2` 给 SATA2 | `pcie2x1` okay |
| 存储 | eMMC(`sdhci`) + TF(`sdmmc0`) + **SATA2**@fc800000 + **SDIO WiFi AIC8800**(`sdmmc2`) | 无 SATA2 / SDIO |
| PMIC | RK809 + 独立 TCS4525(vdd_cpu) | 同族 |
| LED | gpio4 PC6(power) / PC5(status) | 不同 |

> 本板真实硬件配置经**实机实测**确认（`lspci` 双 RTL8125、`aic8800_bsp` 已在场、`ata1` 在场、
> live DTB 中 `sata@fc800000=okay` / `pcie@fe260000=disabled`）。

## 板级 DTS 的来源（重要）

`patch/kernel/rk35xx-vendor-6.1/dt/rk3568-lyt-t68m.dts` 以 **T68M 自己的官方设备树**为基准：

```
来源：iStoreOS / ImmortalWrt 官方
  immortalwrt/immortalwrt @594ab12  target/linux/rockchip/files/arch/arm64/boot/dts/rockchip/rk3568-lyt-t68m.dts
```

**这是关键**：板级数据必须用目标板自己的设备树，**不能**拿别的板子（如 rk3568-roc-k40pro）的骨架去套
（那样会导致 PCIe 全挂 / 网口不通 / 显示黑屏）。

移植到 iNextOS 的 6.1 vendor 内核时，只做了 9 处 label 映射：

| 官方 DTS | BSP 6.1 vendor |
|---|---|
| `&combphy0` / `&combphy1` / `&combphy2` | `&combphy0_us` / `&combphy1_usq` / `&combphy2_psq` |
| `&usb_host0_xhci` / `&usb_host1_xhci` | `&usbdrd_dwc3` / `&usbhost_dwc3` |
| `&usb2phy1_host` / `&usb2phy1_otg` | `&u2phy1_host` / `&u2phy1_otg` |
| `&hdmi_in` / `&hdmi_out` | BSP 的 `hdmi_in_vp0` + 新增 `port@1` |

并叠加本板实际配置：`&pcie2x1 { status="disabled"; }`、`&sata2 { status="okay"; }`、
`&sdmmc2`（AIC8800 SDIO）+ `sdio_pwrseq`、`&vcc3v3_minipcie { regulator-always-on; }`。

## 引导链

```
BootROM → idbloader.img @32KiB → u-boot.itb @8MiB
          （由 armbian 以 BOOT_SCENARIO="spl-blobs" + BOOTCONFIG="radxa-e25-rk3568_defconfig"
            编译的通用 U-Boot —— 即 iNextOS 官方方案；不再注入外部 bootloader blobs）
        → bootcmd = bootflow scan -lb              (boot_targets = mmc1(TF) mmc0(eMMC) …)
        → 引导分区(p1, FAT16 "armbi_boot") 上的 boot.scr（armbian 标准）
          以及 extlinux/extlinux.conf（本仓库额外放置，作为 bootstd 原生入口的双保险）
        → Image + rk3568-lyt-t68m.dtb → booti
```

`armbianEnv.txt` 里 **`fdtfile=rockchip/rk3568-lyt-t68m.dtb` 就是唯一的换板开关**。

## 使用

1. **Actions → Build iNextOS for LYT T68M → Run workflow**（或 push 到 `main`）
2. 构建完成后下载 artifact `inextos-lyt-t68m`（含 `.img` 与 `.img.xz`）
3. 刷写（**建议先写 TF 卡验证**，U-Boot 的 `boot_targets` 首位是 `mmc1`，插卡即走卡，eMMC 原系统零改动）：

```sh
xz -dc iNextOS_*.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

4. 首次启动：eth0 默认 DHCP；也可通过 `console=ttyS2,1500000` 串口查看
5. 登录：`root` / `password`

## 目录

```
config/boards/lyt-t68m.csc                     板级配置（BOOT_FDT_FILE / 网口重命名 / 默认网口）
patch/kernel/rk35xx-vendor-6.1/dt/*.dts        板级设备树（框架会自动拷入内核并改 Makefile）
kernel-files/rk3568-lyt-t68m.dtb              板级 DTB（用于 boot 分区）
kernel-files/{idbloader.img,u-boot.itb}      仅作参考备份，**不再注入镜像**
.github/workflows/build.yml                    CI：云编译 + 镜像后处理
```

## 已验证 / 待验证

- ✅ 板级 DTS 在 6.1 vendor 内核树中 `cpp + dtc` 编译 **0 Error**，产物外设状态与本机实机逐项一致
- ✅ 引导件 md5 与 T68M 实测可用件一致（`idbloader 2adb84fb…` / `u-boot.itb 095b6fff…`）
- ⏳ **实机启动验证**（K1 的教训：结构全对也可能实机不通，必须真机验证）
