#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/gpt-fix.py —— UEFI GPT 校验 / 修复 / 换 PARTUUID（iNextOS 板级仓专用）

为什么必须有这一步（2026-10-27 T68M 实锤、2026-10-28 在 lyw-OS 复现同一坑）：
  镜像本身是 GPT（IMAGE_PARTITION_TABLE=gpt，官方 R1 发布件同样是 gpt、offset 16MiB）：
    p1 = 256MiB FAT bootfs，p2 = ext4 rootfs。
  GPT 的「备份头/末可用扇区/保护 MBR 的 count」都是按**镜像文件大小**算的。把镜像 dd 进
  一张比它大的 TF 卡（如 32G）后，这三个字段全部指向镜像末尾而不是卡的末尾：
    · U-Boot 宽容 —— 会打 `Using Backup GPT` 照样引导，**看起来没问题**；
    · Linux 内核严格 —— 校验失败 ⇒ 卡上只认到 `mmcblkX`、**不出 `p1 p2`** ⇒ 找不到 root
      ⇒ `Waiting for root device ...` 卡死。表现就是「U-Boot 正常、系统起不来」。
  串口判据（一眼定性）：`mmcblk1: mmc1: ... 31.9 GiB` 之后**没有** `mmcblk1: p1 p2`
  （而 eMMC 那边有），立刻查 GPT，别去查 DTB/驱动。

用法：
  python3 tools/gpt-fix.py <镜像|块设备>                 # 纯诊断，有问题 exit 1
  python3 tools/gpt-fix.py <块设备> --apply              # 按规范重算 MBR/主头/备份项/备份头
  python3 tools/gpt-fix.py <块设备> --apply --renew-partuuid=2
        # 同时给第 N 个分区换新的 PARTUUID（16 字节 GUID）。写入 TF 卡时必带：
        # 板上 eMMC 里若跑着同源系统，两个 PARTUUID 相同会导致内核挂上 eMMC 那个、起的是旧系统。
        # 引导件不用改：armbian 的 boot.cmd 用 `part uuid ${devtype} ${devnum}:${partition}` 现场取值。
  python3 tools/gpt-fix.py --selftest                    # 判据自测（正例 1 + 负例 2），CI 用

只动 GPT 元数据（保护 MBR、主头、备份项、备份头），**分区内容零修改**。
"""
import struct, zlib, sys, os, uuid, random

SEC = 512


def crc32(b):
    return zlib.crc32(b) & 0xffffffff


def hdr_crc(h, hsz=92):
    h2 = bytearray(h[:hsz])
    h2[16:20] = b"\0\0\0\0"
    return crc32(bytes(h2))


def dev_size(path):
    base = os.path.basename(path)
    sysf = "/sys/class/block/%s/size" % base
    if os.path.exists(sysf):
        try:
            return int(open(sysf).read().strip()) * SEC
        except Exception:
            pass
    return os.path.getsize(path)


def scan(f, last, off_mbr=0):
    """诊断：返回 (问题列表, 主头 bytes, 分区项 bytes)"""
    probs = []
    f.seek(off_mbr)
    mbr = bytearray(f.read(SEC))
    if mbr[510:512] != b"\x55\xaa":
        probs.append("保护 MBR 缺 55AA 签名")
    p = mbr[446:462]
    if p[4] != 0xEE:
        probs.append("MBR 非保护项(type=0x%02x)" % p[4])
    cnt = struct.unpack_from("<I", p, 12)[0]
    if cnt != last:
        probs.append("MBR count=%d ≠ 末扇区 %d" % (cnt, last))
    f.seek(off_mbr + SEC)
    prim = bytearray(f.read(SEC))
    if prim[:8] != b"EFI PART":
        return (["主头无 EFI PART 签名"], prim, b"")
    hsz = struct.unpack_from("<I", prim, 12)[0]
    hc = struct.unpack_from("<I", prim, 16)[0]
    if hsz != 92:
        probs.append("主头 hsz=%d ≠ 92" % hsz)
    if hdr_crc(prim, hsz) != hc:
        probs.append("主头 CRC 坏(声明 0x%08x 实算 0x%08x)" % (hc, hdr_crc(prim, hsz)))
    alt = struct.unpack_from("<Q", prim, 32)[0]
    lus = struct.unpack_from("<Q", prim, 48)[0]
    if alt != last:
        probs.append("主头 alt=%d ≠ 末扇区 %d" % (alt, last))
    if lus != last - 33:
        probs.append("last_usable=%d ≠ %d" % (lus, last - 33))
    ent_lba, num, esz = struct.unpack_from("<QII", prim, 72)
    ecrc = struct.unpack_from("<I", prim, 88)[0]
    f.seek(off_mbr + ent_lba * SEC)
    entries = f.read(num * esz)
    real = crc32(entries)
    if real != ecrc:
        probs.append("分区项 CRC 坏(声明 0x%08x 实算 0x%08x)" % (ecrc, real))
    f.seek(off_mbr + last * SEC)
    bak = f.read(SEC)
    if bak[:8] != b"EFI PART":
        probs.append("备份头@末扇区 无签名")
    else:
        bsz = struct.unpack_from("<I", bak, 12)[0]
        bhc = struct.unpack_from("<I", bak, 16)[0]
        if bsz != 92 or hdr_crc(bak, bsz) != bhc:
            probs.append("备份头 CRC 坏")
        bent, bnum, besz = struct.unpack_from("<QII", bak, 72)
        f.seek(off_mbr + bent * SEC)
        bents = f.read(bnum * besz)
        if crc32(bents) != struct.unpack_from("<I", bak, 88)[0]:
            probs.append("备份项 CRC 坏")
        elif bents != entries:
            probs.append("备份项与主项不一致")
    return (probs, prim, entries)


def repair(f, last, mbr, prim, entries, off_mbr=0):
    b_lba = last - 32
    # 保护 MBR
    struct.pack_into("<I", mbr, 446 + 12, last)
    mbr[446 + 4] = 0xEE
    mbr[510:512] = b"\x55\xaa"
    f.seek(off_mbr)
    f.write(mbr)
    # 主头
    struct.pack_into("<Q", prim, 32, last)          # alt = 末扇区
    struct.pack_into("<Q", prim, 48, last - 33)     # last_usable
    struct.pack_into("<Q", prim, 72, 2)             # 项数组 @LBA2
    struct.pack_into("<I", prim, 12, 92)
    struct.pack_into("<I", prim, 88, crc32(entries))  # 项 CRC 以内容为准
    struct.pack_into("<I", prim, 16, hdr_crc(prim))   # 头 CRC 最后算
    # 备份项 + 备份头
    f.seek(off_mbr + b_lba * SEC)
    f.write(entries)
    # 主项数组（换过 PARTUUID 时主项也必须重写，否则主/备不一致、CRC 与新内容对不上）
    f.seek(off_mbr + 2 * SEC)
    f.write(entries)
    nb = bytearray(prim)
    struct.pack_into("<Q", nb, 24, last)            # 备份头所在 = 末扇区
    struct.pack_into("<Q", nb, 32, 1)               # 备份的 alt = 主头
    struct.pack_into("<Q", nb, 72, b_lba)
    struct.pack_into("<I", nb, 16, hdr_crc(nb))
    f.seek(off_mbr + last * SEC)
    f.write(nb)
    f.seek(off_mbr + SEC)
    f.write(prim)
    f.flush()
    os.fsync(f.fileno())


def renew_partuuid(f, entries, index, off_mbr=0):
    """给第 index 个分区（1 基）换新的 PARTUUID（分区项里的 16 字节 unique GUID）"""
    esz = 128
    off = (index - 1) * esz
    if off + esz > len(entries):
        raise SystemExit("分区号 %d 越界（只有 %d 项）" % (index, len(entries) // esz))
    if entries[off:off + 16] == b"\0" * 16:
        raise SystemExit("第 %d 项为空项，拒绝改 PARTUUID" % index)
    newguid = uuid.UUID(bytes_le=bytes(random.SystemRandom().getrandbits(8) for _ in range(16)))
    ent = bytearray(entries)
    ent[off:off + 16] = newguid.bytes_le
    return bytes(ent), str(newguid)


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    do_apply = "--apply" in argv
    renew = None
    for a in argv:
        if a.startswith("--renew-partuuid="):
            renew = int(a.split("=", 1)[1])
    last = dev_size(path) // SEC - 1
    print("[gpt-fix] %s 末扇区=%d 模式=%s%s" % (path, last, "APPLY" if do_apply else "诊断",
                                              "" if renew is None else " 换 PARTUUID:p%d" % renew))
    # MBR(msdos) 表直接跳过：没有备份头 / last_usable / 保护 MBR，不存在"写进更大的盘就失配"这回事。
    # 本仓 lyt-t68m 自 2026-10-28 起就是 msdos —— 从结构上根除该坑，不再依赖写卡后修补。
    with open(path, "rb") as f:
        f.seek(SEC)
        sig = f.read(8)
        f.seek(446)
        mtype = f.read(5)[4]
    if sig != b"EFI PART" and mtype != 0xEE:
        print("  ✓ 非 GPT 分区表（msdos/MBR）—— 无备份头可失配，跳过")
        return 0
    rc = 0
    with open(path, "r+b" if do_apply else "rb") as f:
        probs, prim, entries = scan(f, last)
        if probs:
            for p in probs:
                print("  ✗", p)
            if not do_apply:
                return 1
            if not entries:
                print("  ❌ 分区项读取失败，放弃修复")
                return 1
            f.seek(0)
            mbr = bytearray(f.read(SEC))
            if renew is not None:
                entries, g = renew_partuuid(f, entries, renew)
                print("  新 PARTUUID(p%d) = %s" % (renew, g))
            repair(f, last, mbr, prim, entries)
        else:
            print("  ✓ GPT 全字段自洽（保护 MBR / 主头 / 分区项 / 备份头 / 备份项）")
            if do_apply and renew is not None:
                entries, g = renew_partuuid(f, entries, renew)
                print("  新 PARTUUID(p%d) = %s" % (renew, g))
                f.seek(0)
                mbr = bytearray(f.read(SEC))
                repair(f, last, mbr, prim, entries)
    with open(path, "rb") as f:
        probs2, _, _ = scan(f, last)
    if probs2:
        for p in probs2:
            print("  ✗ 复检:", p)
        return 1
    if probs:
        print("  ✓ 修复后复检通过")
    return rc


# ───────────────────────── 判据自测（CI 用） ─────────────────────────
def _mk_image(path, size_mb=64, parts=((1, 16, 48), (2, 64, None))):
    """造一个最小 GPT 镜像：保护 MBR + 主头 + 项数组 + 备份项 + 备份头"""
    total = size_mb * 1024 * 1024
    last = total // SEC - 1
    img = bytearray(total)
    # 保护 MBR
    img[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, last)
    img[510:512] = b"\x55\xaa"
    # 分区项
    entries = bytearray(128 * 128)
    for i, (num, start_mb, size_mb_) in enumerate(parts):
        off = i * 128
        start = start_mb * 1024 * 1024 // SEC
        end = last - 33 if size_mb_ is None else start + size_mb_ * 1024 * 1024 // SEC - 1
        entries[off:off + 16] = uuid.uuid4().bytes_le
        entries[off + 16:off + 32] = uuid.uuid4().bytes_le
        struct.pack_into("<QQ", entries, off + 32, start, end)
    ecrc = crc32(bytes(entries))

    def header(my_lba, alt_lba, ent_lba):
        h = bytearray(92)
        h[0:8] = b"EFI PART"
        struct.pack_into("<I", h, 8, 0x00010000)
        struct.pack_into("<I", h, 12, 92)
        struct.pack_into("<Q", h, 24, my_lba)
        struct.pack_into("<Q", h, 32, alt_lba)
        struct.pack_into("<Q", h, 40, 34)
        struct.pack_into("<Q", h, 48, last - 33)
        h[56:72] = uuid.uuid4().bytes_le
        struct.pack_into("<Q", h, 72, ent_lba)
        struct.pack_into("<I", h, 80, 128)
        struct.pack_into("<I", h, 84, 128)
        struct.pack_into("<I", h, 88, ecrc)
        struct.pack_into("<I", h, 16, hdr_crc(h))
        return h

    img[SEC:SEC + 92] = header(1, last, 2)
    img[2 * SEC:2 * SEC + len(entries)] = entries
    img[(last - 32) * SEC:(last - 32) * SEC + len(entries)] = entries
    img[last * SEC:last * SEC + 92] = header(last, 1, last - 32)
    open(path, "wb").write(img)


def _grow(path, mb):
    with open(path, "r+b") as f:
        f.truncate(mb * 1024 * 1024)


def selftest():
    import tempfile
    bad = 0

    def check(name, cond, extra=None):
        nonlocal bad
        ok = bool(cond)
        bad |= (0 if ok else 1)
        print("%s selftest %s%s" % ("✅" if ok else "❌", name, "" if ok else "  ← " + str(extra)))

    d = tempfile.mkdtemp(prefix="gptfix-")
    p = os.path.join(d, "t.img")

    # 1) 正例：镜像自身自洽
    _mk_image(p)
    check("镜像自身自洽", not scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[0])

    # 2) 负例：写进更大的卡（文件被拉大）→ 必须报错，且 --apply 后自洽
    _grow(p, 200)
    probs = scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[0]
    check("烧进大盘后必须检出问题", len(probs) >= 3, probs)
    main_apply(p)
    check("--apply 后复检通过", not scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[0])

    # 3) --renew-partuuid 真的换了 p2 的 GUID
    before = scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[2][128:144]
    main_apply(p, renew=2)
    after = scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[2][128:144]
    check("PARTUUID 已更换(p2)", before != after, (before, after))
    check("换 GUID 后仍自洽", not scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[0])

    # 4) 负例：故意破坏主头 CRC，诊断必须报
    with open(p, "r+b") as f:
        f.seek(SEC + 16)
        f.write(b"\xde\xad\xbe\xef")
    check("主头 CRC 损坏必须检出",
          any("CRC" in x for x in scan(open(p, "rb"), os.path.getsize(p) // SEC - 1)[0]))

    if bad:
        print("\n!! gpt-fix 自测失败 —— 判据本身有问题，先修判据。")
    else:
        print("\n全部自测通过（正例 1 / 负例 2 / APPLY 2 / PARTUUID 1）")
    return bad


def main_apply(path, renew=None):
    last = dev_size(path) // SEC - 1
    with open(path, "r+b") as f:
        probs, prim, entries = scan(f, last)
        f.seek(0)
        mbr = bytearray(f.read(SEC))
        if renew is not None:
            entries, _ = renew_partuuid(f, entries, renew)
        repair(f, last, mbr, prim, entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
