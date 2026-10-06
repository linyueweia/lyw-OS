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
          ⚠️ idbloader 内嵌的 DDR 固件由 CI 在**编译前**替换为 T68M 实测可用版本（见下）
        → bootcmd = bootflow scan -lb              (boot_targets = mmc1(TF) mmc0(eMMC) …)
        → 引导分区(p1, FAT16 "armbi_boot") 上的 boot.scr（armbian 标准）
        → Image + rk3568-lyt-t68m.dtb → booti
```

`armbianEnv.txt` 里 **`fdtfile=rockchip/rk3568-lyt-t68m.dtb` 就是唯一的换板开关**。

### 为什么必须升级 DDR 固件（2026-10 定位）

armbian 框架 `config/sources/families/include/rockchip64_common.inc` 里，rk3568 的
DDR 初始化固件是写死的默认值：

    DDR_BLOB="${DDR_BLOB:-"rk35/rk3568_ddr_1560MHz_v1.21.bin"}"

**该文件已在本仓库构建产物中逐字节命中确认**（位于镜像偏移 `0x8800`）。v1.21 在 E25 /
EasePi R1 上可用，但在 **T68M 上 DDR 训练失败** —— 而训练发生在串口初始化之前，
所以现象是「上电后串口与 HDMI 完全没有任何输出」，且 TF 卡 / eMMC 表现完全一致。

证据链（三方独立来源，互相印证）：

| 比对对象 | 结果 |
|---|---|
| 本仓库产物 vs 官方 iNextOS R1 镜像 | 引导区前 16MiB 仅差 196 字节（全部是构建日期字符串及其校验摘要），`boot.cmd` 一字不差 → **构建内容本身没有问题** |
| 产物内嵌的 DDR 固件 | 与 `armbian/rkbin` 的 `rk3568_ddr_1560MHz_v1.21.bin` **逐字节一致**（@0x8800） |
| 官方 iStoreOS 的 T68M 镜像 | 用 **v1.23**（内含 `ddr-v1.23-03ea844c5d`） |
| **真机 T68M eMMC 前 16MB 实测提取** | DDR 固件 = `ddr-v1.23-03ea844c5d 24/09/03-10:42:57`，与 `armbian/rkbin` 的 **`rk3568_ddr_1056MHz_v1.23.bin` 59392 字节逐字节 100% 相同**（sha256 `20e4bb07…`） |

最后一条是决定性的：**本工作流改用的这份 v1.23，就是这块板子 eMMC 里此刻正在运行的固件本身**（连频率档位 1056MHz 都一致），而不是"另一个更新的版本"。

因此本工作流在**编译前**把框架默认值替换为 `rk35/rk3568_ddr_1056MHz_v1.23.bin`，
并在构建后**回读镜像自证**：内嵌固件必须命中该文件且 sha256 完全一致，否则该步骤失败。

> 早期版本曾尝试"构建后用厂商引导件 dd 进镜像"，已废弃 —— 正确做法是让 armbian 自己
> 用新固件编出 `idbloader.img` / `u-boot.itb`，而不是事后替换二进制。
> 另外，旧版放入的 `kernel-files/idbloader.img` 与 `u-boot.itb` 经查是 **NanoPi R5S**
> 的（内嵌 `rk3568-nanopi-r5s.dtb`），也正是早先"注入引导件无效"的原因，已删除。

### 板级设备树补齐 SoC IP 块（2026-10）

板级 DTS 源自 immortalwrt，只 `#include "rk3568.dtsi"`；而 armbian 官方板级
（如 EasePi R1）是继承完整基线 `rk3568-roc-k40pro.dtsi` —— 那些 IP 块是在基线里
逐个开启的。只在 rk3568.dtsi 之上的结果是**视频编解码/RGA/IEP/JPEG/硬件 RNG/IOMMU
全部停留在默认关闭状态**，表现为：

- jellyfin-ffmpeg（rkmpp）硬件转码不可用
- RGA 缩放、IEP 去隔行、JPEG 硬件解码不可用
- 硬件随机数不可用（影响加解密性能）

修复：在本板 DTS 末尾**照抄官方基线原文**补上 `&vdpu / &vepu / &rkvdec / &rkvenc /
&rk_rga / &iep / &jpegd / &rng / &dfi` 及对应 `*_mmu` 的 `status = "okay"`。
刻意与两个权威来源保持一致：

- `&dmc` 保持 disabled（官方基线里本就是 disabled）
- NPU 保持 disabled（厂商出厂 T68M 设备树里亦为 disabled）

CI 构建后会回读引导分区里的 DTB，用 `fdtget` **断言上述节点 status=okay**，
不通过则整步失败 —— 避免"以为改了其实没生效"。

### 补齐 roceos 网页后台（2026-10）

官方 iNextOS 镜像自带一整套网页后台（`/opt/roceos` 主程序 + `/opt/roceos-ai` AI 服务 +
`roceos{,-ai,-vision}.service` + nginx 站点与证书 + Flask 栈），**本适配仓库原先缺失** ——
现象是板子能正常启动，但 `192.168.100.1` 只显示 nginx 默认欢迎页，管理界面完全是空的。

与官方 Easepi-r1 镜像做穷尽式审计（安装包 / systemd 单元文件 / 启用状态 / 关键目录 /
内核模块 / Python 依赖）后的结论：缺失项**恰好只有这一整套**，其它（内核模块、网口、
内核配置等）零差异。

修复同样做在**构建阶段**，走框架原生的扩展钩子而不是事后往镜像里塞文件：

```
build/userpatches/extensions/t68m-roceos.sh   本仓库 extensions/ 下的扩展
build/userpatches/overlay/roceos-payload.tar.gz   载荷（框架自动送进 chroot）
```

**为什么是扩展 + `post_post_debootstrap_tweaks` 这个早期阶段**：启用服务必须用
`chroot_sdcard systemctl --no-reload enable`，这正是官方 `istorenext` 扩展启用它自己
服务的方式。实测（构建 37404316629）在 `customize-image.sh` 阶段用 `ln -sf` 建
`multi-user.target.wants` 链接，钩子内自检是 ✅、但**最终镜像里链接消失**（被后续流程
清掉），导致构建自证失败。改用官方同款阶段后链接得以留存。

扩展做的事：安装应用本体 → 装 3 个 systemd 服务 → 装 nginx 站点与证书（**移除 `default`
站点**：它与 `roceos.conf` 都声明 `listen 80 default_server`，共存会让 nginx 启动失败）
→ 用框架 helper 启用服务并补一份 systemd preset（防后续 `preset-all` 重置）→
Flask 栈优先 `apt`、失败回退载荷内文件 → 全部逐项自证，任一失败即整步失败。

载荷来源：从**官方 Easepi-r1 镜像**中提取（就是官方自己用的那一份），发布为
[release `roceos-payload-v1`](https://github.com/linyueweia/lyw-OS/releases/tag/roceos-payload-v1)，
工作流以固定 sha256 断言校验（内容一变立即失败）。构建后另有回读自证：镜像内必须齐备
上述组件、`default` 站点必须已移除，并 `chroot` 实测导入 Flask 栈。

### 显示 / LED / 硬件编解码（2026-10，实机定位）

这三项都是**实机 dmesg + 与同内核树官方参考件逐项对照**定位的，全部落在设备树：

| 现象 | 根因 | 修复 |
|---|---|---|
| **HDMI 无画面**（`/sys/class/drm` 下连 HDMI 连接器都没有） | ① 板级 DTS 从 OpenWrt 侧移植时自造了 `hdmi-con` 连接器 + `port@1`；本内核 BSP 的 dw-hdmi 自己会创建 connector，两者共存使 `drm_bridge_attach` 返回 `-22`，HDMI 整体 probe 失败（实测 `dwhdmi-rockchip: probe of fe0a0000.hdmi failed with error -22`）<br>② BSP dtsi 里 `route-hdmi` 默认是 `disabled` 且 `connect = <&vp1_out_hdmi>`，而板级只开了 `hdmi_in_vp0`，路由对不上 | 删除 `hdmi-con` 与 `port@1`；新增 `&route_hdmi { status = "okay"; connect = <&vp0_out_hdmi>; }`；补 `rockchip,phy-table`（照抄 EVB） |
| **电源灯/系统灯不亮** | `leds-gpio` 驱动探测时会把**没有 `default-state` 的 LED 置灭**（内核默认 off）。iStoreOS 那边靠 OpenWrt 用户态按 `led-boot`/`led-running` 属性点亮，iNextOS 没有这套用户态 | 两个 LED 加 `default-state = "on"`，不依赖任何用户态脚本 |
| **硬件转码不可用**（`/dev/mpp_service` 不存在，rkmpp 报 `open vcodec_service failed`） | 早前按基线清单补 IP 块时**漏了 `&mpp_srv`**（官方 EVB 明确开启它；只把 vdpu/vepu/rkvdec/rkvenc 置 okay 是不够的） | 新增 `&mpp_srv { status = "okay"; }` |

> 参考基准：**同内核树**（`armbian/linux-rockchip` @ `rk-6.1-rkr5.1`）自带的官方 EVB
> `rk3568-evb.dtsi`。所有写法以它为准，不照搬别的内核树、也不自造节点。

CI 构建后会回读 DTB 逐项断言：两个 LED 的 `default-state`、`/mpp-srv` 与
`route-hdmi` 的 `status`、`route-hdmi` 的 `connect` 必须等于 HDMI `port@0/endpoint@0`
所连的 VP，且 `/hdmi-con` 必须不存在。

### SDIO WiFi（AIC8800）——只缺固件，不缺驱动（2026-10）

体检发现 `/lib/modules/.../aic8800_sdio/{aic8800_bsp,aic8800_btlpm}.ko` **本来就在镜像里**，
内核配置也已开启（`CONFIG_AIC8800_WLAN_SUPPORT=m`），缺的只是 **固件文件**
（`CONFIG_AIC_FW_PATH="/lib/firmware/aic8800/SDIO/aic8800D80/"`）。

修复：从**厂商 iStoreOS 镜像**提取 `lib/firmware/aic8800/sdio/*`（30 个文件、4.6M），
随载荷一并装入，并且**两份路径都放**（驱动编译进的是全大写 `aic8800/SDIO/aic8800D80/`，
厂商用的是小写 `aic8800/sdio/`），避免大小写差异导致加载失败。

### 第二个 2.5G 口（pcie3x1）链路训练失败 —— PHY 缺分叉配置（2026-10）

现象：`lspci` 里只有一个 RTL8125、少一个 `eth3`；dmesg 里
`rk-pcie 3c0400000.pcie: PCIe Linking... LTSSM is 0x1` 反复刷屏后
`PCIe Link Fail / failed to initialize host`。

定位过程（逐层排除，全部有证据）：
1. 与厂商 T68M DTB **逐属性对比** pcie3x1/x2/phy 三个节点：只差 phandle 编号与
   内核树命名（`pcie-dbi` vs `dbi`），**实质等价** → 不是设备树差异；
2. PCIe3 PHY 初始化正常（无 `lock failed`）；两个控制器都被驱动成功 probe；
3. 连"通用 `dw-pcie` 驱动抢先匹配失败"这一怀疑也被排除 —— **两路都被它抢过、行为一致**；
4. 对照厂商 iStoreOS（内核 6.12）的 dmesg，差别只在三行：

```
phy phy-fe8c0000.phy.7: lane number 0, val 1
phy phy-fe8c0000.phy.7: lane number 1, val 2
phy phy-fe8c0000.phy.7: bifurcation enabled      ← 我们这边没有这一行
```

**根因**：PCIe3 PHY 需要工作在**分叉（bifurcation）**模式才能同时支撑两条独立 x1 链路。
我们内核的 BSP dtsi 在这个节点上就注释着：

```
/* rockchip,bifurcation; lane1 when using 1+1 */
```

即 BSP 要求板级 DTS 在"1+1"（两个独立 x1 口）配置下显式开启该属性；我们（以及厂商
的 DTS）都没有写——厂商之所以不需要，是因为其 6.12 内核的 PHY 驱动会按 `data-lanes`
**自动**判定分叉，而我们 6.1 BSP 的驱动不会。

**修复**：在 `&pcie3x1` 与 `&pcie3x2` 两个节点上各加一行 `rockchip,bifurcation;`。
同内核树的其他 rk3568 NAS 板（`hinlink-h6xk`、`radxa-e25`、`easepi-a2`）都是这么写的。

## 自动跟踪上游（每日检查 + 自动编译）

`.github/workflows/build.yml` 有两个作业：

1. `upstream` —— 读取 `jjm2473/armbian-easepi` 的 `easepi-v26.02` 分支 HEAD，
   与仓库根目录的 `last-upstream-sha` 比对；
2. `build` —— 上游有变化时（或手动触发 / push 到 main）编译镜像。

| 事件 | 行为 |
|---|---|
| `workflow_dispatch`（手动） | 总是构建 |
| `push` 到 `main` | 总是构建（仅改 `last-upstream-sha` 的提交被 `paths-ignore` 跳过） |
| `schedule` 每日 03:00 CST | 上游 HEAD 变过 → 记录新指纹并自动构建；没变 → 数秒内跳过 |

构建始终 checkout 上游分支的**最新提交**，所以"跟踪上游"就等于"上游一动就出一版新固件"。
上游更新频率约每 2–3 周一次。

注意：

- 上游改了框架而本仓库补丁没跟上时，构建的**回读自证会直接失败**（如 DDR 固件的
  sed 断言、设备树断言），不会产出"以为改了其实没改"的镜像；
- GitHub 会在仓库 60 天无提交时暂停定时任务，本仓库的指纹提交会自动维持它；
- 想立刻出一版：Actions → *Build iNextOS for LYT T68M* → Run workflow。

### 系统日志审计与"更新防护"（2026-10）

对整机日志（`journalctl -p err -b`、`dmesg -l err,warn`、apt/dpkg 状态）做了穷尽审计，
每一项都与**官方 Easepi-r1 镜像**（同内核 6.1.115-vendor-rk35xx）或**板载 eMMC 里厂商
iStoreOS 的 DTB** 做了 A/B 对照。结论分两类：

**① 可修、已修并已进镜像**

- **roceos 更新流程会删掉固件包**（一次真实事故的根因）：该流程会装发行版固件包
  （firmware-atheros / firmware-brcm80211 …），而 `armbian-firmware` 的 control 里
  `Provides` 且 `Conflicts` 了其中一部分 —— apt 为满足冲突会把 armbian-firmware 整个
  卸载，连带删除它管理的 `/lib/firmware/aic8800`（WiFi 固件，82 个文件）。而该包不在任何
  apt 源里（随镜像首启安装后即删），apt 搜不到，只能从镜像按 dpkg 清单重建 deb 才装得回。
  **两道防护（已进镜像）**：
  ① `/etc/apt/preferences.d/10-inextos-firmware-conflict` 以 `Pin-Priority: -1`
  禁止安装与 armbian-firmware 互斥的包；
  ② 对更新流程还会请求的固件包装"同名空壳包"（不含文件，内容本由 armbian-firmware 提供），
  使依赖检查成立、从而不再触发危险安装。
- **`serial-getty@ttyFIQ0` 报错**：框架启用了该 getty，而本板无此串口（rk3568 FIQ debugger
  未接出），启动时会 `Timed out waiting for device dev-ttyFIQ0.device`。已建 mask 链接屏蔽。

**② 经 A/B 确认属上游固有、非本板缺陷（不改）**

| 日志项 | 依据 |
|---|---|
| `roceos-ai` failed（模型文件不存在） | 官方 R1 镜像同样无 `model.conf`/`*.rkllm`，模型按需下载 |
| `mali fde60000.gpu: Failed to map registers`、IRQ 未找到 | gpu 节点与官方 R1 逐项一致（reg/interrupts/opp/supply），残余在内核驱动层 |
| `rkvdec/rkvenc: Failed to get leakage`、`shared_niu_* not found` | 与官方 R1 逐项一致，仅 phandle 编号不同 |
| `rockchip-vop2: no regulator (vop) found` | R1 同样没有 `vop-supply`（可选属性，指错会调错稳压器，故不加） |
| `rk_gmac: Can not read property tx_delay/rx_delay` | 上游 OpenWrt 本板 DTS 用 `phy-mode = "rgmii-id"`，延迟由 PHY 内部完成，DT 本就不该有这两个属性；R1 的值是其 PCB 参数，不可照抄 |
| `dw-pcie ... invalid resource / Failed to initialize host` | 通用驱动先试探失败、`rk-pcie` 随即接手并成功（两路均 link up） |
| `rk817-battery/charger/codec: matching dt id`、`arm-scmi protocol 17/22 not active`、`blkmapd`、BPF LSM、alsa-restore GOTO 标签 | 共享 dtsi / 发行版自带噪音，官方镜像同样存在 |

**SDIO WiFi 排查结论**：空总线不是故障，需先确认网卡是否已插入；DT 侧已核对就绪 ——
`&sdmmc2` 对应 `mmc@fe000000`（不是 fe2c0000，那是 sdmmc1，两边都 disabled、与 WiFi 无关），
属性为 `status=okay` + `mmc-pwrseq` + `cap-sdio-irq` + `non-removable` + `supports-sdio` +
`sd-uhs-sdr104`，pwrseq 用 RK809 的 CLK1 作 `ext_clock`、复位脚 `gpio3 PD5` 低有效，
与厂商 DTB 逐项一致；驱动与 82 个固件文件均在镜像内。

### 4G 模块识别不到 —— USB3/DWC3 父节点未使能（2026-10 实机定位）

现象：插入 mini-PCIe 4G 模块后 `lsusb` 无设备、ModemManager 报 `No modems were found`、
roceos 蜂窝页面拿不到模块；但**同一块板**用 lyw 仓库（iStoreOS/OpenWrt）固件能识别该模块。

定位：内核日志里只有 4 个 USB2.0 平台控制器（fd800000/fd840000/fd880000/fd8c0000），
**两个 DWC3（USB3）控制器完全不存在**。查 `aliases` 可知 DWC3 的结构是

```
usbdrd_dwc3 = "/usbdrd/usb@fcc00000";
usbhost_dwc3 = "/usbhost/usb@fd000000";
```

即 `usb@fcc00000` / `usb@fd000000` 是 **`usbdrd30` / `usbhost30` 两个 glue 父节点的子节点**。
本板 DTS 只使能了子节点，而父节点在内核 dtsi 里默认 `disabled` —— **父节点关闭时整棵子树都不会
实例化**，于是 `fd000000.usb` / `fcc00000.usb` 根本不存在（`/sys/bus/platform/devices` 可证），
走该 USB 通道的 4G 模块自然永远枚举不到。

修复：与官方 Easepi-r1（同一颗 6.1.115-vendor-rk35xx 内核，其 `usbdrd`/`usbhost` 均为 okay）一致，
在板级 DTS 里补：

```dts
&usbdrd30 { status = "okay"; };
&usbhost30 { status = "okay"; };
```

CI 自证：构建后回读 DTB，断言 `/usbdrd`、`/usbhost` 均为 `okay`。

**实机验证（2026-10-06 冷启动后）**：两个 DWC3 控制器随之出现并绑定驱动 ——

```
平台设备 : fcc00000.usb、fd000000.usb（此前完全不存在）
驱动绑定 : dwc3 → fcc00000.usb/fd000000.usb；dwc3-of-simple → usbdrd/usbhost
           xhci-hcd → xhci-hcd.0.auto（USB3 root hub 出现）
lsusb    : 2c7c:0125 Quectel ... EC25 LTE modem（板载模组型号 EM05-CN）
串口     : /dev/ttyUSB0-3     网口: cdc_ether 注册 eth4
ModemManager: /org/freedesktop/ModemManager1/Modem/0 [Quectel] EM05-CN
```

即：本板 4G 模块走的是 DWC3（USB3）通道，父节点未使能时整条通道不存在，这正是
「iStoreOS 能认、iNextOS 认不到」的原因。

### 扩展为何一直没生效 —— ENABLE_EXTENSIONS 白名单（2026-10 定位）

构建失败自证（`❌ 缺少 apt 禁止清单 / 空壳固件包 / ttyFIQ0 未屏蔽`）的根因不在扩展代码，
而在 workflow 的构建参数：

```yaml
ENABLE_EXTENSIONS="istorenext"        # 只有白名单里的扩展会被 enable
```

框架 `lib/functions/general/extensions.sh:142` 的逻辑是：把 `ENABLE_EXTENSIONS`（或 `EXT`）
按逗号切分后逐个 `enable_extension`。因此 `userpatches/extensions/t68m-roceos.sh` **从未被加载**，
其安装与自证代码从未执行；此前"roceos 装上了"实为 `userpatches/overlay` 里的载荷被框架
自动拷进根分区所致（扩展自身自证也从未跑过，故报错总来自 workflow 的检查）。

修复：`ENABLE_EXTENSIONS="istorenext,t68m-roceos"`。

排查中另有一处误判需记录：曾把该文件不是可执行位（0644）当作根因 —— **不对**，
加载器只用 `find -name "*.sh"` 加 `grep -l "function <hook>__"` 筛选，不检查可执行位。
权限改为 0755 无害且是好习惯，但真正原因是上面的白名单。

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
extensions/t68m-roceos.sh                     框架扩展：构建阶段补齐 roceos 网页后台 + AIC8800 WiFi 固件
                                              （载荷走 userpatches/overlay，服务用 chroot_sdcard systemctl enable 启用）
.github/workflows/build.yml                    CI：云编译 + 镜像后处理 + DDR / 设备树 / roceos 三重回读自证
```

## 已验证 / 待验证

- ✅ 板级 DTS 在 6.1 vendor 内核树中 `cpp + dtc` 编译 **0 Error**，产物外设状态与本机实机逐项一致
- ✅ 引导件 md5 与 T68M 实测可用件一致（`idbloader 2adb84fb…` / `u-boot.itb 095b6fff…`）
- ✅ **实机启动验证通过**：DDR 固件升级后 T68M 已能正常启动（此前为「上电后串口/HDMI 完全无输出」）
- ✅ **roceos 网页后台**：构建阶段注入方案已在本地镜像上逐项验证（组件齐备 / `default` 站点已移除 /
  `nginx -t` 通过 / Flask 栈可导入）
- ⏳ **CI 端到端验证**：新工作流一次完整构建的产物仍需实机复验
