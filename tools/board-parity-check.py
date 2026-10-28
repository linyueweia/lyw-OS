#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T68M DTB 全量自证（iNextOS / RK3568 / 6.1 vendor 线）。

用法：
  python3 tools/board-parity-check.py <dtb>        # 对产物 DTB 跑全部断言
  python3 tools/board-parity-check.py --selftest   # 判据自身正/负例自测（1 秒，用真实 DTB 夹具）

覆盖：SoC IP 块 / USB3 glue 父节点 / 千兆口 RGMII 时序 / EEE / 卡检测脚 /
      板级对齐（金标准件）/ PMIC 关机句柄 / LED 与 HDMI 路由 / PCIe3 分叉。

为什么用 python 而不是 fdtget 字符串比较（run #73 的 4 条红全是判据写法，真 DTB 值全对）：
  ① `fdtget -t x` 输出**不带 0x 前缀**、多 cell 空格分隔，且 gpios 第一格是随内核树
     变化的 phandle（本树 0x8b / 0x3f）⇒ 写死 `0x030x090x01` 会把**完全正确**的镜像判死；
  ② `fdtget -t s` 读 phandle 属性（非字符串）直接失败，`|| echo MISSING` 把"存在"读成"缺失"。
  ⇒ 判据一律绑语义：phandle 解析成节点名再比、数值按 cell 比，不写字面量。

判据边界：只断言**本仓改过/覆写过的项**，官方默认值不断言。
"""

import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


# ---------------------------------------------------------------- FDT 解析
def parse_fdt(path):
    """返回 (nodes, phandles, offsets)。

    nodes[路径][属性名] = 原始字节；phandles[编号] = 路径；
    offsets[(路径, 属性名)] = 该属性**值**在文件里的偏移（自测中做字节级篡改用）。
    """
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 40:
        raise ValueError("文件太短，不是 DTB")
    (magic, totalsize, off_struct, off_strings, off_rsv,
     version, last_comp, boot_cpu, size_strings, size_struct) = struct.unpack(">10I", d[:40])
    if magic != FDT_MAGIC:
        raise ValueError("魔数不是 DTB（0x%08x）" % magic)
    if off_struct + size_struct > len(d) or off_strings + size_strings > len(d):
        raise ValueError("DTB 头里的结构块/字符串块越界（文件被截断？）")

    nodes, stack, offsets, p = {}, [], {}, off_struct
    while True:
        if p + 4 > len(d):
            raise ValueError("结构块意外结束（缺 FDT_END）")
        tok = struct.unpack(">I", d[p:p + 4])[0]
        p += 4
        if tok == FDT_BEGIN_NODE:
            end = d.index(b"\x00", p)
            name = d[p:end].decode("utf-8", "replace")
            p = (end + 4) & ~3
            cur = "/" if not stack else stack[-1].rstrip("/") + "/" + name
            stack.append(cur)
            nodes[cur] = {}
        elif tok == FDT_END_NODE:
            if not stack:
                raise ValueError("END_NODE 多于 BEGIN_NODE")
            stack.pop()
        elif tok == FDT_PROP:
            ln, nameoff = struct.unpack(">II", d[p:p + 8])
            p += 8
            val = d[p:p + ln]
            nend = d.index(b"\x00", off_strings + nameoff)
            nm = d[off_strings + nameoff:nend].decode()
            nodes[stack[-1]][nm] = val
            offsets[(stack[-1], nm)] = p
            p = (p + ln + 3) & ~3
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            break
        else:
            raise ValueError("未知 token %d @ %d" % (tok, p - 4))

    phandles = {}
    for pth, props in nodes.items():
        for key in ("phandle", "linux,phandle"):
            if key in props:
                phandles[struct.unpack(">I", props[key][:4])[0]] = pth
    return nodes, phandles, offsets


def cells(val):
    return [struct.unpack(">I", val[i:i + 4])[0] for i in range(0, len(val) // 4 * 4, 4)]


def as_str(val):
    return val.split(b"\x00")[0].decode("utf-8", "replace")


def hx(n):
    return "0x%x" % n


# ---------------------------------------------------------------- 判据工厂
def r_status(path, want="okay"):
    def rule(n, ph):
        if path not in n:
            return "节点 %s 不存在" % path
        v = n[path].get("status")
        if v is None:
            return "缺 status 属性（应为 %r）" % want
        got = as_str(v)
        return None if got == want else "status=%r，期望 %r" % (got, want)
    return rule


def r_cells(path, prop, want, why=""):
    def rule(n, ph):
        v = n.get(path, {}).get(prop)
        if v is None:
            return "%s 缺属性 %s" % (path, prop)
        c = cells(v)
        if not c:
            return "%s %s 为空" % (path, prop)
        return None if c[0] == want else "%s %s=%s，期望 %s%s" % (
            path, prop, hx(c[0]), hx(want), ("（%s）" % why) if why else "")
    return rule


def r_str(path, prop, want):
    def rule(n, ph):
        if path not in n:
            return "节点 %s 不存在" % path
        v = n[path].get(prop)
        if v is None:
            return "%s 缺属性 %s（该节点有: %s）" % (
                path, prop, ", ".join(sorted(n[path])[:8]))
        got = as_str(v)
        return None if got == want else "%s %s=%r，期望 %r" % (path, prop, got, want)
    return rule


def r_gpio(path, prop, bank, pin, flags):
    def rule(n, ph):
        v = n.get(path, {}).get(prop)
        if v is None:
            return "%s 缺属性 %s" % (path, prop)
        c = cells(v)
        if len(c) < 3:
            return "%s %s 只有 %d 个 cell（应为 <phandle pin flags>）" % (path, prop, len(c))
        target = ph.get(c[0])
        if target != bank:
            return "%s %s 第1格 phandle %s 指向 %r，期望 %s" % (
                path, prop, hx(c[0]), target, bank)
        if c[1] != pin:
            return "%s %s 引脚 cell=%s，期望 %s" % (path, prop, hx(c[1]), hx(pin))
        if c[2] != flags:
            return "%s %s flags cell=%s，期望 %s" % (path, prop, hx(c[2]), hx(flags))
        return None
    return rule


def r_has(path, prop, why=""):
    def rule(n, ph):
        if path not in n:
            return "节点 %s 不存在" % path
        return None if prop in n[path] else "%s 缺 %s%s" % (
            path, prop, ("（%s）" % why) if why else "")
    return rule


def r_has_bool(path, prop, why=""):
    """布尔属性：必须存在且长度为 0。"""
    def rule(n, ph):
        if path not in n:
            return "节点 %s 不存在" % path
        if prop not in n[path]:
            return "%s 缺 %s%s" % (path, prop, ("（%s）" % why) if why else "")
        ln = len(n[path][prop])
        return None if ln == 0 else "%s 的 %s 不是布尔属性（长度 %d）" % (path, prop, ln)
    return rule


def r_not_has(path, prop, why=""):
    def rule(n, ph):
        return None if prop not in n.get(path, {}) else "%s 仍带 %s%s" % (
            path, prop, ("（%s）" % why) if why else "")
    return rule


def r_node_absent(path, why=""):
    def rule(n, ph):
        return None if path not in n else "%s 仍存在%s" % (
            path, ("（%s）" % why) if why else "")
    return rule


def r_no_child(path, why=""):
    def rule(n, ph):
        kids = [k for k in n if k.startswith(path.rstrip("/") + "/")]
        return None if not kids else "%s 下多出子节点 %s%s" % (
            path, kids[:4], ("（%s）" % why) if why else "")
    return rule


def r_phandle_eq(path_a, prop_a, path_b, prop_b):
    """两处 phandle 必须指向同一目标节点（route-hdmi 路由校验）。"""
    def rule(n, ph):
        a, b = n.get(path_a, {}).get(prop_a), n.get(path_b, {}).get(prop_b)
        if a is None or b is None:
            return "缺 %s %s 或 %s %s" % (path_a, prop_a, path_b, prop_b)
        ca, cb = cells(a), cells(b)
        if not ca or not cb:
            return "phandle 属性为空"
        if ca[0] != cb[0]:
            return "phandle %s(%s) != %s(%s)" % (hx(ca[0]), ph.get(ca[0]), hx(cb[0]), ph.get(cb[0]))
        return None
    return rule


# ---------------------------------------------------------------- 判据表
GPIO3 = "/pinctrl/gpio@fe760000"      # RK3568 gpio3：T68M 复位键
GPIO0 = "/pinctrl/gpio@fdd60000"      # RK3568 gpio0：pi6c 使能脚
G0, G1 = "/ethernet@fe2a0000", "/ethernet@fe010000"
PHY0, PHY1 = G0 + "/mdio/ethernet-phy@0", G1 + "/mdio/ethernet-phy@0"
PMIC = "/i2c@fdd40000/pmic@20"

GROUPS = [
    ("(2b) SoC IP 块 / USB3 glue 父节点必须使能", [
        ("vdpu okay", r_status("/vdpu@fdea0400")),
        ("vepu okay", r_status("/vepu@fdee0000")),
        ("rkvdec okay", r_status("/rkvdec@fdf80200")),
        ("rkvenc okay", r_status("/rkvenc@fdf40000")),
        ("rk_rga okay", r_status("/rk_rga@fdeb0000")),
        ("iep okay", r_status("/iep@fdef0000")),
        ("jpegd okay", r_status("/jpegd@fded0000")),
        ("rng okay", r_status("/rng@fe388000")),
        ("iommu@fdea0800 okay", r_status("/iommu@fdea0800")),
        ("iommu@fdf40f00 okay", r_status("/iommu@fdf40f00")),
        ("iommu@fdf80800 okay", r_status("/iommu@fdf80800")),
        ("mpp-srv okay（缺则 /dev/mpp_service 不存在，硬转码全不可用）", r_status("/mpp-srv")),
        ("/usbdrd okay（USB3 glue 父节点，缺则 4G 通道不实例化）", r_status("/usbdrd")),
        ("/usbhost okay（USB3 glue 父节点）", r_status("/usbhost")),
    ]),
    ("(2d) 千兆口时序 = rgmii + MAC 侧延迟；EEE 只关 1000BASE-T", [
        ("gmac0 phy-mode=rgmii", r_str(G0, "phy-mode", "rgmii")),
        ("gmac0 tx_delay=0x3c", r_cells(G0, "tx_delay", 0x3C)),
        ("gmac0 rx_delay=0x2f", r_cells(G0, "rx_delay", 0x2F)),
        ("gmac1 phy-mode=rgmii", r_str(G1, "phy-mode", "rgmii")),
        ("gmac1 tx_delay=0x42", r_cells(G1, "tx_delay", 0x42)),
        ("gmac1 rx_delay=0x28", r_cells(G1, "rx_delay", 0x28)),
        ("gmac0 PHY eee-broken-1000t", r_has(PHY0, "eee-broken-1000t", "否则进 LPI 偶发整口不收包")),
        ("gmac1 PHY eee-broken-1000t", r_has(PHY1, "eee-broken-1000t", "否则进 LPI 偶发整口不收包")),
        ("gmac0 PHY 无 eee-broken-100tx", r_not_has(PHY0, "eee-broken-100tx", "过度关闭")),
        ("gmac1 PHY 无 eee-broken-100tx", r_not_has(PHY1, "eee-broken-100tx", "过度关闭")),
        ("gmac0 PHY 无 eee-broken-10gt", r_not_has(PHY0, "eee-broken-10gt", "过度关闭")),
        ("gmac1 PHY 无 eee-broken-10gt", r_not_has(PHY1, "eee-broken-10gt", "过度关闭")),
        ("卡检测脚 /pinctrl/sdmmc0-cd-gpio 在位",
         r_has("/pinctrl/sdmmc0-cd-gpio", "rockchip,pins", "否则与 sdmmc0_det 抢同一 pin")),
    ]),
    ("(2d-2) 板级对齐（权威件 = xck-nas 飞牛金标准 DTS；写法适配 6.1 vendor）", [
        ("复位键 label=reset", r_str("/gpio-keys/button-reset", "label", "reset")),
        ("复位键 gpios = <&gpio3 PB1 ACTIVE_LOW>",
         r_gpio("/gpio-keys/button-reset", "gpios", GPIO3, 9, 1)),
        ("ADC 键 adc-keys 在位", r_str("/adc-keys", "compatible", "adc-keys")),
        ("gmac0 PHY realtek,led-data=0x6151", r_cells(PHY0, "realtek,led-data", 0x6151)),
        ("gmac1 PHY realtek,led-data=0x6151", r_cells(PHY1, "realtek,led-data", 0x6151)),
        ("gmac0 PHY 无 leds 子节点（6.12 写法）",
         r_no_child(PHY0, "6.1 vendor 用 realtek,led-data")),
        ("gmac1 PHY 无 leds 子节点（6.12 写法）",
         r_no_child(PHY1, "6.1 vendor 用 realtek,led-data")),
        ("gmac0 label=lan1", r_str(G0, "label", "lan1")),
        ("gmac1 label=lan2", r_str(G1, "label", "lan2")),
        ("r8125@10,0 label=lan4",
         r_str("/pcie@fe270000/pcie@0,0/pcie-eth@10,0", "label", "lan4")),
        ("r8125@20,0 label=lan3",
         r_str("/pcie@fe280000/pcie@0,0/pcie-eth@20,0", "label", "lan3")),
        ("LDO_REG1(vdda0v9_image)=950000（曾错成 900000）",
         r_cells(PMIC + "/regulators/LDO_REG1", "regulator-max-microvolt", 950000)),
        ("pi6c gpios = <&gpio0 PD4 ACTIVE_HIGH>",
         r_gpio("/vcc3v3-pi6c-regulator", "gpios", GPIO0, 0x1C, 0)),
        ("vcc3v3-pcie 无 gpios（两级结构）",
         r_not_has("/vcc3v3-pcie-regulator", "gpios", "使能脚在 pi6c 上")),
        ("minipcie 3.3V regulator-always-on",
         r_has("/vcc3v3-minipcie-regulator", "regulator-always-on", "miniPCIe 3.3V 必须常供")),
        ("/hdmi-con 不存在",
         r_node_absent("/hdmi-con", "BSP dw-hdmi 自建 connector，共存 → drm_bridge_attach -22")),
        ("hdmi port@1 不存在",
         r_node_absent("/hdmi@fe0a0000/ports/port@1", "输出端口由 BSP 自建")),
    ]),
    ("(2e) PMIC 关机句柄必须用 vendor 属性名（主线名 → poweroff 退化成 halt）", [
        ("rk8xx rockchip,system-power-controller（布尔）",
         r_has_bool(PMIC, "rockchip,system-power-controller", "vendor 6.1 只认带前缀的")),
        ("rk8xx 无主线裸名 system-power-controller",
         r_not_has(PMIC, "system-power-controller", "vendor 6.1 不认，等于没注册")),
    ]),
    ("(2c) LED / HDMI 路由 / PCIe3 分叉", [
        ("led-power default-state=on", r_str("/gpio-leds/led-power", "default-state", "on")),
        ("led-status default-state=on", r_str("/gpio-leds/led-status", "default-state", "on")),
        ("route-hdmi okay", r_status("/display-subsystem/route/route-hdmi")),
        ("route-hdmi connect == hdmi_in_vp0",
         r_phandle_eq("/display-subsystem/route/route-hdmi", "connect",
                      "/hdmi@fe0a0000/ports/port@0/endpoint@0", "remote-endpoint")),
        ("pcie@fe270000 rockchip,bifurcation",
         r_has("/pcie@fe270000", "rockchip,bifurcation", "缺则第二路 2.5G 训练失败")),
        ("pcie@fe280000 rockchip,bifurcation",
         r_has("/pcie@fe280000", "rockchip,bifurcation", "缺则第二路 2.5G 训练失败")),
    ]),
]

RULES = [(name, rule) for _, items in GROUPS for name, rule in items]


def run_rules(nodes, phandles, verbose=True, groups=None):
    fails = []
    for group, items in (groups or GROUPS):
        if verbose:
            print("--- %s" % group)
        for name, rule in items:
            try:
                err = rule(nodes, phandles)
            except Exception as exc:                 # 判据自身异常必须算失败
                err = "判据执行异常: %r" % (exc,)
            if err:
                fails.append((name, err))
                if verbose:
                    print("  \u274c %s —— %s" % (name, err))
            elif verbose:
                print("  \u2705 %s" % name)
    return fails


# ---------------------------------------------------------------- 判据自测
FIXTURE = "tests/fixtures/rk3568-lyt-t68m-run73.dtb"
HDMI_NODES = ("/hdmi-con", "/hdmi@fe0a0000/ports/port@1")


def _drop(nodes, prefix):
    """从解析结果里摘掉一棵子树（模拟"DTS 已按本次决定改好"的 DTB 视图）。"""
    gone = [k for k in nodes if k == prefix or k.startswith(prefix.rstrip("/") + "/")]
    for k in gone:
        del nodes[k]
    return gone


def _patch(fixture, out, patches):
    d = bytearray(open(fixture, "rb").read())
    for off, new in patches:
        d[off:off + len(new)] = new
    open(out, "wb").write(bytes(d))


def selftest():
    import os
    import tempfile
    base = os.path.dirname(os.path.abspath(__file__))
    fx = os.path.normpath(os.path.join(base, FIXTURE))
    if not os.path.isfile(fx):
        print("  \u274c 缺夹具 %s" % fx)
        return 1
    bad = 0

    # ① 夹具原样（= run #73 真产物）：必须精确报出 hdmi 两条，其余全绿
    nodes, phandles, offs = parse_fdt(fx)
    fails = run_rules(nodes, phandles, verbose=False)
    names = [f[0] for f in fails]
    if len(fails) == 2 and all(("hdmi" in n) for n in names):
        print("  \u2705 原样夹具（run #73 真产物）：精确报红 2 条（%s），其余 %d 条全绿"
              % (" / ".join(names), len(RULES) - 2))
    else:
        print("  \u274c 原样夹具：期望恰好 2 条 hdmi 红，实得 %d 条 %s" % (len(fails), names))
        bad += 1

    # ② 按本次决定摘掉 hdmi-con / port@1（= 改后 DTS 的 DTB 视图）：必须 0 条失败
    n2 = {k: dict(v) for k, v in nodes.items()}
    for p in HDMI_NODES:
        _drop(n2, p)
    fails = run_rules(n2, phandles, verbose=False)
    if not fails:
        print("  \u2705 改后视图：%d 条断言全绿" % len(RULES))
    else:
        print("  \u274c 改后视图仍有 %d 条红：%s" % (len(fails), [f[0] for f in fails]))
        bad += 1

    # ③ 字节级负例：每条都必须在**真实 DTB** 上精确报红（含既有的 2 条 hdmi 红）
    def pic(path, prop):
        return offs[(path, prop)]

    cases = [
        ("复位键引脚 PB1→PB0", [(pic("/gpio-keys/button-reset", "gpios") + 4, b"\x00\x00\x00\x08")]),
        ("gmac0 led-data →0xac10", [(pic(PHY0, "realtek,led-data"), b"\x00\x00\xac\x10")]),
        ("gmac0 tx_delay →0", [(pic(G0, "tx_delay"), b"\x00\x00\x00\x00")]),
        ("LDO_REG1 950000→900000",
         [(pic(PMIC + "/regulators/LDO_REG1", "regulator-max-microvolt"), b"\x00\x0d\xbb\xa0")]),
        ("mpp-srv okay→disab", [(pic("/mpp-srv", "status"), b"disab")]),
        ("pi6c gpios 引脚 PD4→PD5", [(pic("/vcc3v3-pi6c-regulator", "gpios") + 4,
                                     b"\x00\x00\x00\x1d")]),
    ]
    tmp = tempfile.mkdtemp(prefix="bpc-")
    checked = 0
    for i, (name, patches) in enumerate(cases):
        dst = os.path.join(tmp, "neg%d.dtb" % i)
        _patch(fx, dst, patches)
        try:
            nn, pp, _ = parse_fdt(dst)
            got = [f[0] for f in run_rules(nn, pp, verbose=False)]
        except Exception as exc:
            print("  \u274c 负例[%s] 解析失败: %r" % (name, exc))
            bad += 1
            continue
        os.unlink(dst)
        # 期望：既有 2 条 hdmi 红 + 本次篡改精确引入 1 条 = 3
        if len(got) == 3:
            print("  \u2705 负例[%s] 精确报红 3 条（新增 1 条 + 既有 hdmi 2 条）" % name)
            checked += 1
        else:
            print("  \u274c 负例[%s] 期望 3 条红，实得 %d 条 %s" % (name, len(got), got))
            bad += 1

    # ④ 判据自身异常必须算失败（不能静默吞掉）
    boom = [("异常自测", [("故意抛异常",
                          lambda n, ph: (_ for _ in ()).throw(RuntimeError("boom")))])]
    if len(run_rules({}, {}, verbose=False, groups=boom)) != 1:
        print("  \u274c 判据抛异常未被计为失败")
        bad += 1
    else:
        print("  \u2705 判据抛异常被计为失败")

    total = 3 + len(cases)
    print("[selftest] %d/%d 例通过" % (total - bad, total))
    return 0 if bad == 0 else 1


def main(argv):
    if "--selftest" in argv[1:]:
        print("--- board-parity-check 判据自测（真实 DTB 夹具 + 字节级负例）---")
        return selftest()
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print("用法: python3 tools/board-parity-check.py <dtb> | --selftest", file=sys.stderr)
        return 2
    dtb = args[0]
    try:
        nodes, phandles, _ = parse_fdt(dtb)
    except Exception as exc:
        print("!! 无法解析 DTB %s: %s" % (dtb, exc), file=sys.stderr)
        return 2
    print("[dtb] %s —— %d 节点 / %d phandle" % (dtb, len(nodes), len(phandles)))
    fails = run_rules(nodes, phandles)
    if fails:
        print("!! DTB 自证失败 %d/%d 条" % (len(fails), len(RULES)), file=sys.stderr)
        return 1
    print("    \u2705 DTB 自证通过（%d/%d）：SoC IP 块 / USB3 / RGMII 时序 / EEE / 卡检测脚 / "
          "板级对齐 / PMIC 关机 / LED·HDMI·mPCIe / PCIe3 分叉" % (len(RULES), len(RULES)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
