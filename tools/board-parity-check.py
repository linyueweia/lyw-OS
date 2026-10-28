#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T68M 板级对齐自证（iNextOS / RK3568 / 6.1 vendor 线）。

用法：python3 tools/board-parity-check.py <dtb> [--selftest]

为什么用 python 而不是 fdtget 字符串比较（run #73 的教训）：
  `fdtget -t x` 输出的十六进制**不带 0x 前缀**，多 cell 用空格分隔；而 gpios 的
  第一格是 **phandle 编号**，随内核树/节点顺序变化（本树是 0x8b / 0x3f）。
  于是 `[ "$x" = "0x030x090x01" ]` 这种字面量断言会把**完全正确的 DTB** 判死
  （实测：真值 `8b 9 1` → 剥空格 `8b91`，断言写的是 `0x030x090x01`）。
  另外 `fdtget -t s` 读 phandle 属性（非字符串）会直接失败，`|| echo MISSING`
  就把"存在"读成"缺失"，hdmi port@1 就是这么被误判的。
  ⇒ 判据一律绑**语义**：phandle 解析成节点名、数值按 cell 比较。

判据范围：只断言**本仓改过/覆写过的板级项**（官方默认值不断言），
权威件 = xck-nas 飞牛金标准 DTS（硬件口径），属性写法按 6.1 vendor 内核适配。
"""

import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


# ---------------------------------------------------------------- FDT 解析
def parse_fdt(path):
    """返回 (nodes, phandles)：nodes[路径][属性名] = 原始字节；phandles[编号] = 路径。"""
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 40:
        raise ValueError("文件太短，不是 DTB")
    (magic, totalsize, off_struct, off_strings, off_rsv,
     version, last_comp, boot_cpu, size_strings, size_struct) = struct.unpack(">10I", d[:40])
    if magic != FDT_MAGIC:
        raise ValueError(f"魔数不是 DTB（0x{magic:08x}）")
    if off_struct + size_struct > len(d) or off_strings + size_strings > len(d):
        raise ValueError("DTB 头里的结构块/字符串块越界（文件被截断？）")

    nodes, stack, p = {}, [], off_struct
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
            p = (p + ln + 3) & ~3
            nend = d.index(b"\x00", off_strings + nameoff)
            nodes[stack[-1]][d[off_strings + nameoff:nend].decode()] = val
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            break
        else:
            raise ValueError(f"未知 token {tok} @ {p - 4}")

    phandles = {}
    for pth, props in nodes.items():
        for key in ("phandle", "linux,phandle"):
            if key in props:
                phandles[struct.unpack(">I", props[key][:4])[0]] = pth
    return nodes, phandles


def cells(val):
    return [struct.unpack(">I", val[i:i + 4])[0] for i in range(0, len(val) // 4 * 4, 4)]


def as_str(val):
    return val.split(b"\x00")[0].decode("utf-8", "replace")


def hx(n):
    return "0x%x" % n


# ---------------------------------------------------------------- 判据
# 每条： (说明, 判定函数) ；函数返回 None 表示通过，返回字符串表示失败原因。
def _prop(nodes, path, key):
    return nodes.get(path, {}).get(key)


def _status_prop(path, key, want):
    def rule(n, ph):
        if path not in n:
            return f"节点 {path} 不存在"
        v = _prop(n, path, key)
        if v is None:
            return f"缺属性 {key}（该节点有: {', '.join(sorted(n[path])[:8])}）"
        got = as_str(v)
        return None if got == want else f"{key}={got!r}，期望 {want!r}"
    return rule


def _gpio_rule(path, key, bank, pin, flags):
    def rule(n, ph):
        if path not in n:
            return f"节点 {path} 不存在"
        v = _prop(n, path, key)
        if v is None:
            return f"缺属性 {key}"
        c = cells(v)
        if len(c) < 3:
            return f"{key} 只有 {len(c)} 个 cell（应为 <phandle pin flags>）"
        target = ph.get(c[0])
        if target != bank:
            return f"{key} 第1格 phandle {hx(c[0])} 指向 {target!r}，期望 {bank}"
        if c[1] != pin:
            return f"{key} 引脚 cell = {hx(c[1])}，期望 {hx(pin)}"
        if c[2] != flags:
            return f"{key} flags cell = {hx(c[2])}，期望 {hx(flags)}"
        return None
    return rule


def _cell_rule(path, key, want):
    def rule(n, ph):
        v = _prop(n, path, key)
        if v is None:
            return f"{path} 缺属性 {key}"
        c = cells(v)
        if not c:
            return f"{path} {key} 为空"
        return None if c[0] == want else f"{path} {key} = {hx(c[0])}，期望 {hx(want)}"
    return rule


def _absent_node_rule(path, why):
    def rule(n, ph):
        return None if path not in n else f"{path} 仍存在（{why}）"
    return rule


def _absent_prop_rule(path, key, why):
    def rule(n, ph):
        v = _prop(n, path, key)
        return None if v is None else f"{path} 仍带 {key}（{why}）"
    return rule


def _present_prop_rule(path, key, why):
    def rule(n, ph):
        if path not in n:
            return f"节点 {path} 不存在"
        return None if key in n[path] else f"{path} 缺 {key}（{why}）"
    return rule


def _no_child_rule(path, why):
    def rule(n, ph):
        kids = [k for k in n if k.startswith(path.rstrip("/") + "/")]
        return None if not kids else f"{path} 下多出子节点 {kids[:4]}（{why}）"
    return rule


GPIO3 = "/pinctrl/gpio@fe760000"      # RK3568 gpio3，T68M 复位键挂这
GPIO0 = "/pinctrl/gpio@fdd60000"      # RK3568 gpio0，pi6c 使能脚挂这
GMAC0, GMAC1 = "/ethernet@fe2a0000", "/ethernet@fe010000"

RULES = [
    # ① 两把按键都在：gpio-keys 复位键(gpio3 PB1 低有效) + adc-keys(saradc0)
    ("复位键 label=reset", _status_prop(
        "/gpio-keys/button-reset", "label", "reset")),
    ("复位键 gpios = <&gpio3 PB1 ACTIVE_LOW>", _gpio_rule(
        "/gpio-keys/button-reset", "gpios", GPIO3, 9, 1)),
    ("ADC 键 adc-keys 在位", _status_prop(
        "/adc-keys", "compatible", "adc-keys")),
    # ② 千兆口 PHY：6.1 vendor 线用 realtek,led-data（leds 子节点是 6.12 写法）
    ("gmac0 PHY realtek,led-data=0x6151", _cell_rule(
        GMAC0 + "/mdio/ethernet-phy@0", "realtek,led-data", 0x6151)),
    ("gmac1 PHY realtek,led-data=0x6151", _cell_rule(
        GMAC1 + "/mdio/ethernet-phy@0", "realtek,led-data", 0x6151)),
    ("gmac0 PHY 无 leds 子节点（6.12 写法）", _no_child_rule(
        GMAC0 + "/mdio/ethernet-phy@0", "6.1 vendor 用 realtek,led-data")),
    ("gmac1 PHY 无 leds 子节点（6.12 写法）", _no_child_rule(
        GMAC1 + "/mdio/ethernet-phy@0", "6.1 vendor 用 realtek,led-data")),
    # ③ 四个口 label（金标准 lan1~lan4）
    ("gmac0 label=lan1", _status_prop(GMAC0, "label", "lan1")),
    ("gmac1 label=lan2", _status_prop(GMAC1, "label", "lan2")),
    ("r8125@10,0 label=lan4", _status_prop(
        "/pcie@fe270000/pcie@0,0/pcie-eth@10,0", "label", "lan4")),
    ("r8125@20,0 label=lan3", _status_prop(
        "/pcie@fe280000/pcie@0,0/pcie-eth@20,0", "label", "lan3")),
    # ④ vdda0v9_image(LDO_REG1) = 950000（金标准值，曾错成 900000）
    ("LDO_REG1(vdda0v9_image)=950000", _cell_rule(
        "/i2c@fdd40000/pmic@20/regulators/LDO_REG1", "regulator-max-microvolt", 950000)),
    # ⑤ PCIe 3.3V 两级结构（pi6c 使能脚 + pcie 延时），不得并成一级
    ("pi6c gpios = <&gpio0 PD4 ACTIVE_HIGH>", _gpio_rule(
        "/vcc3v3-pi6c-regulator", "gpios", GPIO0, 0x1c, 0)),
    ("vcc3v3-pcie 无 gpios（两级结构）", _absent_prop_rule(
        "/vcc3v3-pcie-regulator", "gpios", "使能脚在 pi6c 上")),
    # ⑥ miniPCIe 3.3V 必须常供
    ("minipcie 3.3V regulator-always-on", _present_prop_rule(
        "/vcc3v3-minipcie-regulator", "regulator-always-on", "miniPCIe 3.3V 必须常供")),
    # ⑦ HDMI：连接器与输出端口**必须不存在**（BSP dw-hdmi 自建 connector，
    #    共存会让 drm_bridge_attach 返回 -22、整个 HDMI probe 失败；实机已定位）
    ("/hdmi-con 不存在（BSP 自建 connector）", _absent_node_rule(
        "/hdmi-con", "与 BSP dw-hdmi 自建 connector 共存会 -22")),
    ("hdmi port@1 不存在", _absent_node_rule(
        "/hdmi@fe0a0000/ports/port@1", "输出端口由 BSP 自建")),
]


def run_rules(nodes, phandles, rules=RULES, verbose=True):
    fails = []
    for name, rule in rules:
        try:
            err = rule(nodes, phandles)
        except Exception as exc:                     # 判据自身异常 ≠ 通过
            err = f"判据执行异常: {exc!r}"
        if err:
            fails.append((name, err))
            if verbose:
                print(f"  \u274c {name} —— {err}")
        elif verbose:
            print(f"  \u2705 {name}")
    return fails


# ---------------------------------------------------------------- 负例自测
def selftest():
    """判据逻辑的正/负例自测：负例不响 = 判据没在干活。"""
    def tree(**kv):
        return kv

    def prop(*c):
        return b"".join(struct.pack(">I", x) for x in c)

    ok_tree = {
        "/gpio-keys/button-reset": {"label": b"reset\x00", "gpios": prop(0x8b, 9, 1)},
        "/adc-keys": {"compatible": b"adc-keys\x00"},
        GMAC0: {"label": b"lan1\x00"},
        GMAC1: {"label": b"lan2\x00"},
        GMAC0 + "/mdio/ethernet-phy@0": {"realtek,led-data": prop(0x6151)},
        GMAC1 + "/mdio/ethernet-phy@0": {"realtek,led-data": prop(0x6151)},
        "/pcie@fe270000/pcie@0,0/pcie-eth@10,0": {"label": b"lan4\x00"},
        "/pcie@fe280000/pcie@0,0/pcie-eth@20,0": {"label": b"lan3\x00"},
        "/i2c@fdd40000/pmic@20/regulators/LDO_REG1": {"regulator-max-microvolt": prop(950000)},
        "/vcc3v3-pi6c-regulator": {"gpios": prop(0x3f, 0x1c, 0)},
        "/vcc3v3-pcie-regulator": {"startup-delay-us": prop(50000)},
        "/vcc3v3-minipcie-regulator": {"regulator-always-on": b""},
    }
    ok_ph = {0x8b: GPIO3, 0x3f: GPIO0}

    cases = []
    # 正例：全套正确 → 0 条失败
    cases.append(("全绿树", dict(ok_tree), dict(ok_ph), 0))

    # 负例 1：复位键引脚写错（PB1→PB0）→ 必须精确报这一条
    t = {k: dict(v) for k, v in ok_tree.items()}
    t["/gpio-keys/button-reset"]["gpios"] = prop(0x8b, 8, 1)
    cases.append(("复位键引脚错", t, dict(ok_ph), 1))

    # 负例 2：gpios 的 phandle 指向别的 gpio 银行 → 必须报
    t = {k: dict(v) for k, v in ok_tree.items()}
    t["/gpio-keys/button-reset"]["gpios"] = prop(0x3f, 9, 1)
    cases.append(("复位键 phandle 指错银行", t, dict(ok_ph), 1))

    # 负例 3：led-data 退回旧值 0xac10 → 必须报 2 条（两口各一）
    t = {k: dict(v) for k, v in ok_tree.items()}
    t[GMAC0 + "/mdio/ethernet-phy@0"]["realtek,led-data"] = prop(0xAC10)
    t[GMAC1 + "/mdio/ethernet-phy@0"]["realtek,led-data"] = prop(0xAC10)
    cases.append(("led-data 旧值", t, dict(ok_ph), 2))

    # 负例 4：PHY 多了 leds 子节点（6.12 写法混入）→ 必须报
    t = {k: dict(v) for k, v in ok_tree.items()}
    t[GMAC0 + "/mdio/ethernet-phy@0/leds/led@1"] = {"default-state": b"keep\x00"}
    cases.append(("PHY 混入 leds 子节点", t, dict(ok_ph), 1))

    # 负例 5：hdmi-con 被加回来（run #73 后的真实风险）→ 必须报
    t = {k: dict(v) for k, v in ok_tree.items()}
    t["/hdmi-con"] = {"compatible": b"hdmi-connector\x00"}
    cases.append(("hdmi-con 被加回", t, dict(ok_ph), 1))

    # 负例 6：pi6c 并成一级（gpios 挂到 pcie 上、pi6c 丢失）→ 必须报 2 条
    t = {k: dict(v) for k, v in ok_tree.items()}
    t["/vcc3v3-pi6c-regulator"].pop("gpios")
    t["/vcc3v3-pcie-regulator"]["gpios"] = prop(0x3f, 0x1c, 0)
    cases.append(("3.3V 并成一级", t, dict(ok_ph), 2))

    # 负例 7：LDO 退回 900000 → 必须报
    t = {k: dict(v) for k, v in ok_tree.items()}
    t["/i2c@fdd40000/pmic@20/regulators/LDO_REG1"]["regulator-max-microvolt"] = prop(900000)
    cases.append(("LDO 900000", t, dict(ok_ph), 1))

    bad = 0
    for name, nodes, ph, want in cases:
        fails = run_rules(nodes, ph, verbose=False)
        if len(fails) != want:
            bad += 1
            print(f"  \u274c 自测[{name}] 期望 {want} 条失败，实得 {len(fails)}: {fails}")
        else:
            print(f"  \u2705 自测[{name}] 失败数={want} 符合预期")
    # 判据自身异常必须算失败（不能静默吞掉）
    boom = [("故意抛异常", lambda n, p: (_ for _ in ()).throw(RuntimeError("boom")))]
    if len(run_rules({}, {}, rules=boom, verbose=False)) != 1:
        bad += 1
        print("  \u274c 自测[异常] 判据抛异常未被计为失败")
    else:
        print("  \u2705 自测[异常] 判据异常被计为失败")

    print(f"[selftest] {len(cases) + 1 - bad}/{len(cases) + 1} 例通过")
    return 0 if bad == 0 else 1


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    if "--selftest" in argv[1:] and not args:
        print("--- board-parity-check 判据负例自测 ---")
        return selftest()
    if len(args) != 1:
        print("用法: python3 tools/board-parity-check.py <dtb> | --selftest", file=sys.stderr)
        return 2
    dtb = args[0]
    try:
        nodes, phandles = parse_fdt(dtb)
    except Exception as exc:
        print(f"!! 无法解析 DTB {dtb}: {exc}", file=sys.stderr)
        return 2
    print(f"[dtb] {dtb} — {len(nodes)} 节点 / {len(phandles)} phandle")
    fails = run_rules(nodes, phandles)
    if fails:
        print(f"!! 板级对齐自证失败 {len(fails)}/{len(RULES)} 条", file=sys.stderr)
        return 1
    print(f"    \u2705 板级对齐自证通过（{len(RULES)}/{len(RULES)}）："
          "gpio-keys+adc-keys / PHY led-data / label lan1~4 / LDO 950mV / "
          "pi6c 两级 / minipcie 常供 / 无 hdmi-con")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest() if len(sys.argv) == 2 else main(sys.argv))
    sys.exit(main(sys.argv))
