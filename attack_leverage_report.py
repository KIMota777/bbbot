# -*- coding: utf-8 -*-
"""Разбор результата attack_leverage_order.json: пляшет ли порядок от плеча."""
import json
import sys


def spearman(a, b):
    n = len(a)
    ra = rank(a)
    rb = rank(b)
    ma = sum(ra) / n
    mb = sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


d = json.load(open("attack_leverage_order.json", encoding="utf-8"))
rows = d["rows"]
levs = ["%g" % x for x in d["levs"]]
# конфиг без сделок ни на одной половине при x5 — отсев как в оригинале
def alive(r):
    v = r["levs"]["5"]
    return v["train"]["trades"] + v["hold"]["trades"] > 0


rows = [r for r in rows if alive(r)]
name = ["%s %s/%s" % (r["sym"].replace("USDT", ""), r["src"], r["tag"])
        for r in rows]
print("живых конфигов: %d\n" % len(rows))

wr = {}
for L in levs:
    wr[L] = [min(r["levs"][L]["train"]["rob"], r["levs"][L]["hold"]["rob"])
             for r in rows]

print("=== 1. ПОРЯДОК ПО ХУДШЕЙ ПОЛОВИНЕ УСТОЙЧИВОСТИ ===")
print("ранговая связь порядка с x5:")
for L in levs:
    print("  x%-3s rho(x5) = %+.3f" % (L, spearman(wr["5"], wr[L])))

order = {L: sorted(range(len(rows)), key=lambda i: -wr[L][i]) for L in levs}
print("\nТОП-10 на каждом плече (место: кто):")
hdr = "%-4s" % "мес"
for L in levs:
    hdr += " | x%-26s" % L
print(hdr)
for p in range(10):
    line = "%-4d" % (p + 1)
    for L in levs:
        i = order[L][p]
        line += " | %-27s" % ("%s %+.1f" % (name[i][:20], wr[L][i]))
    print(line)

print("\nсмещение мест относительно x5 (|разница мест|):")
pos5 = {i: p for p, i in enumerate(order["5"])}
for L in levs:
    posl = {i: p for p, i in enumerate(order[L])}
    dif = [abs(pos5[i] - posl[i]) for i in range(len(rows))]
    top5 = set(order["5"][:5]) & set(order[L][:5])
    top10 = set(order["5"][:10]) & set(order[L][:10])
    print("  x%-3s средн %.1f  макс %d  общих в топ-5: %d  в топ-10: %d  "
          "лидер: %s" % (L, sum(dif) / len(dif), max(dif), len(top5),
                         len(top10), name[order[L][0]][:26]))

print("\nсколько конфигов «обе половины устойчивости в плюсе»:")
for L in levs:
    print("  x%-3s : %d из %d" % (L, sum(1 for x in wr[L] if x > 0), len(rows)))

print("\n=== 2. КАК ПЛЕЧО МЕНЯЕТ МЕХАНИКУ (доли причин выхода) ===")
for r in rows:
    if not (r["src"] == "config.py" and r["sym"] == "BTCUSDT"):
        continue
    print("\n%s %s/%s (родное плечо x%s)"
          % (r["sym"], r["src"], r["tag"], r["own_lev"]))
    print("  %-4s %-6s %8s %8s %8s %6s %6s %6s %6s %6s"
          % ("плечо", "полов", "итог%", "уст%", "просад%", "сдел", "tp",
             "stop", "liq", "t/o"))
    for L in levs:
        for half in ("train", "hold"):
            v = r["levs"][L][half]
            rs = v["reasons"]
            print("  x%-4s %-6s %+7.1f%% %+7.1f%% %7.1f%% %6d %6d %6d %6d %6d"
                  % (L, half, v["comp"], v["rob"], v["dd"], v["trades"],
                     rs.get("tp", 0), rs.get("stop", 0), rs.get("liq", 0),
                     rs.get("timeout", 0)))

print("\n=== 3. ДОЛЯ ЛИКВИДАЦИЙ ПО ВСЕМ КОНФИГАМ ===")
for L in levs:
    tot = liq = 0
    nz = 0
    for r in rows:
        for half in ("train", "hold"):
            v = r["levs"][L][half]
            tot += v["trades"]
            liq += v["liqs"]
        if sum(r["levs"][L][h]["liqs"] for h in ("train", "hold")) > 0:
            nz += 1
    print("  x%-3s ликвидаций %5d из %6d сделок (%.2f%%), конфигов с "
          "ликвидациями: %d из %d"
          % (L, liq, tot, 100.0 * liq / tot if tot else 0, nz, len(rows)))
