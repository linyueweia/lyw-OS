# 判据夹具：run #73 的真实产物 DTB

`rk3568-lyt-t68m-run73.dtb`（170516 B，md5 `147678fe39d40bf712f16e2f1cfe633b`）

## 来源（可复核）

- GitHub Actions run：`linyueweia/lyw-OS` #73（run id `40696181567`，commit `2d785be`）
- 取自该 run 上传的 artifact `inextos-lyt-t68m` 里的
  `images/iNextOSA_26.2.1_Lyt-t68m_trixie_vendor_6.1.115_minimal.img.xz`
  → 解压后引导分区 p1 里的 `/dtb/rockchip/rk3568-lyt-t68m.dtb`
- 提取方式：对大 artifact 做 HTTP Range 片段下载 + 流式解 zip/解 xz，只取到该 DTB
  （不整包下载 1.1 GB）；提取后 md5 与 run #73 构建日志里 `md5sum` 打出的值逐字节一致
- 该 run 的失败点在 `(2d-2)` 段：4 条红全是**判据写法**问题（fdtget 输出不带 `0x` 前缀、
  gpios 第一格是 phandle），这颗 DTB 本身除 hdmi-con/port@1 外各项取值都正确

## 用途

`tools/board-parity-check.py --selftest` 用它做两件事：

1. **原样跑**：必须精确报红 2 条（`/hdmi-con 不存在`、`hdmi port@1 不存在`），
   其余 50 条全绿 —— 证明判据既会抓真偏差，也不会把正确项判死
2. **改后视图**：在解析结果里摘掉那两棵子树（= 本次"删除 hdmi-con/port@1"之后应有的树），
   必须 52 条全绿 —— 证明修复方向本身是绿的
3. **字节级负例**：按属性值偏移篡改真实 DTB（引脚、led-data、tx_delay、LDO、mpp status、
   pi6c 引脚），每次必须精确新增 1 条红

整机镜像构建与 `build.yml` 的 `dtb` job 跑的是同一个判据工具，
所以这里的正/负例结果直接决定那两处门禁是否可信。
