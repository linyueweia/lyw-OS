#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板级 DTS 语法门（无需内核树、无需 dtc，纯文本，~1 秒）

为什么要有它：2026-10-28 run #62 里我手滑把 `poll-interval = <100>;` 写成
`poll-interval = 100>;`，dtc 要等到内核 make 阶段才报
`rk3568-lyt-t68m.dts:51.19-22 syntax error` —— 等了 3414 秒（57 分钟）才红。
这类错在源码上是**纯文本可判定**的，1 秒就能判死，不该烧掉一小时 runner。

判据（只做"必错"的判断，不猜语义）：
  1. `{` `}` 必须配平
  2. `<` `>` 必须配平（且 `>` 不能先于 `<`）
  3. `"` 必须配平、转义闭合
  4. `name = value;` 的 value 必须是下列之一（可逗号连接）：
       空（布尔属性） / "字符串" / <cells> / &label / [hex字节] / 纯数字
     其余形态（如 `100>`、`<100`、裸 `}` 后跟垃圾）一律判错
  5. `#include` 行不参与检查；`/dts-v1/;` 允许
用法：python3 dts-lint.py <file.dts> [more.dts ...]
"""
import sys, os, re

ALLOWED_VALUE = re.compile(
    r'^(?:'
    r'\s*'
    r'(?:'
    r'"(?:[^"\\]|\\.)*"'              # 字符串
    r'|<[A-Za-z0-9_<>&,\s\-\.\[\]]*>'  # cells（含 &label、宏、数字）
    r'|&[A-Za-z_][A-Za-z0-9_]*'        # 单独的 phandle
    r'|\[[0-9A-Fa-f\s]*\]'             # hex 字节
    r'|-?\d+'                          # 纯数字
    r'|\*'                             # 少数 binding 用 *
    r'|[A-Za-z_][A-Za-z0-9_]*'         # 裸宏（cpp 会展开成字符串/数字，如 LED_FUNCTION_LAN）
    r')'
    r'(?:\s*,\s*(?:"(?:[^"\\]|\\.)*"|<[A-Za-z0-9_<>&,\s\-\.\[\]]*>|&[A-Za-z_][A-Za-z0-9_]*|\[[0-9A-Fa-f\s]*\]|-?\d+|[A-Za-z_][A-Za-z0-9_]*))*'
    r'\s*)$')


def strip_comments(text):
    """去注释但保留换行，行号不变"""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            if j < 0:
                return ''.join(out) + '\n@@UNTERMINATED_COMMENT@@\n'
            out.append('\n' * text.count('\n', i, j + 2))
            i = j + 2
        elif text.startswith('//', i):
            j = text.find('\n', i)
            if j < 0: j = n
            i = j
        else:
            out.append(text[i]); i += 1
    return ''.join(out)


def lint(path):
    raw = open(path, encoding='utf-8', errors='replace').read()
    text = strip_comments(raw)
    errs = []
    line = lambda pos: (text[:pos].count('\n') + 1,
                        pos - (text.rfind('\n', 0, pos) + 1) + 1)

    if '@@UNTERMINATED_COMMENT@@' in text:
        ln, col = line(text.find('@@UNTERMINATED_COMMENT@@'))
        errs.append((ln, col, '注释未闭合（缺 */）'))

    # 逐字符配平
    brace = angle = 0
    instr = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if instr:
            if c == '\\': i += 2; continue
            if c == '"': instr = False
            i += 1; continue
        if c == '"': instr = True
        elif c == '{': brace += 1
        elif c == '}':
            brace -= 1
            if brace < 0:
                ln, col = line(i); errs.append((ln, col, '多余的 }')); brace = 0
        elif c == '<': angle += 1
        elif c == '>':
            angle -= 1
            if angle < 0:
                ln, col = line(i)
                errs.append((ln, col, '出现了没有对应 < 的 >（多半是少了 <）'))
                angle = 0
        i += 1
    if instr:
        errs.append((len(text.split('\n')), 1, '字符串未闭合（缺 "）'))
    if brace > 0:
        errs.append((len(text.split('\n')), 1, f'{{ 未闭合，还差 {brace} 个 }}'))
    if angle > 0:
        errs.append((len(text.split('\n')), 1, f'< 未闭合，还差 {angle} 个 >'))

    # 逐条属性 value 形态
    for m in re.finditer(r'^([ \t]*)([A-Za-z_][A-Za-z0-9_,\-\.]*)[ \t]*=[ \t]*(.*?);[ \t]*$',
                         text, re.M):
        val = m.group(2 + 1)
        if val == '' or ALLOWED_VALUE.match(val):
            continue
        pos = m.start(3)
        ln, col = line(pos)
        errs.append((ln, col, f'属性值形态非法：{val.strip()[:40]!r}（应为 "" / <> / &label / 数字）'))

    return errs


def main():
    bad = 0
    for p in sys.argv[1:]:
        errs = lint(p)
        name = os.path.basename(p)
        if errs:
            bad = 1
            print(f'❌ {name}')
            for ln, col, msg in sorted(set(errs)):
                print(f'    {name}:{ln}:{col}: {msg}')
        else:
            print(f'✅ {name} 语法形态通过')
    if bad:
        print('\n!! 板级 DTS 语法门未通过 —— 这类错 dtc 要等内核 make 才报（实测 3414 秒），'
              '在这里 1 秒判死。')
    return bad


if __name__ == '__main__':
    sys.exit(main())
