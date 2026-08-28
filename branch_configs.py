# -*- coding: utf-8 -*-
"""Геномы из ДРУГИХ веток репозитория — чтобы проверка не пропускала их.

ЗАЧЕМ. Проверка обходила config.py текущей ветки и файлы волн, лежащие в ней
же. Но конфиг с тем же именем в другой ветке может быть другим геномом: у
LTC/final в ветке main тридцать четыре параметра отличаются от одноимённого
конфига в ветке аудита — это разные стратегии под одним названием. Такой
геном не проверялся вовсе, и на витрине его не было ни в каком виде.

ЧТО ДЕЛАЕТ. Достаёт config.py из каждой ветки (git show), импортирует его как
отдельный модуль и складывает в JSON те геномы, которых нет в текущей ветке.
Дальше их подхватывает all_configs_honest.collect() наравне с волнами, то есть
они проходят ровно ту же переоценку: обе половины, плечо x5, копеечные выходы
обнулены, устойчивость по соседям.

ПОЧЕМУ НЕ ДОБАВЛЯТЬ ИХ В config.py. Там лежат ЗАПУСКАЕМЫЕ конфиги: всё, что
попало в SYMBOL_PARAMS, можно запустить в торговлю одной командой. Геном из
чужой ветки проверить надо, а давать ему такую кнопку — нет.

Запуск: python branch_configs.py
"""
import importlib.util
import json
import os
import subprocess
import sys

import all_configs_honest as ach
import config
import evolution7 as e7

OUT = "branch_genomes.json"
TMP = os.path.join(os.environ.get("TEMP", "."), "_branch_cfg")


def branches():
    """Локальные ветки, кроме текущей."""
    cur = subprocess.run(["git", "branch", "--show-current"],
                         capture_output=True, text=True).stdout.strip()
    raw = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)",
                          "refs/heads"], capture_output=True, text=True).stdout
    return [b.strip() for b in raw.splitlines() if b.strip() and b.strip() != cur]


def load_branch_config(branch):
    """config.py указанной ветки, импортированный как отдельный модуль."""
    os.makedirs(TMP, exist_ok=True)
    src = subprocess.run(["git", "show", "%s:config.py" % branch],
                         capture_output=True, text=True)
    if src.returncode != 0 or not src.stdout:
        return None
    path = os.path.join(TMP, "cfg_%s.py" % branch.replace("/", "_"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src.stdout)
    name = "branchcfg_" + branch.replace("/", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                       # noqa: BLE001
        print("  %s: config.py не импортируется (%s)" % (branch, exc))
        return None
    return mod


def genome_key(g):
    return json.dumps(ach.with_defaults(g), sort_keys=True, default=str)


def main():
    # что уже есть в текущей ветке и в волнах — повторно не берём
    seen = set()
    for rec in ach.collect():
        seen.add(genome_key(rec["g"]))
    print("уже проверяется геномов: %d" % len(seen))

    out, skipped = {}, 0
    for br in branches():
        mod = load_branch_config(br)
        if mod is None:
            continue
        added = []
        for sym, modes in getattr(mod, "SYMBOL_PARAMS", {}).items():
            for mode, p in modes.items():
                if not isinstance(p, dict):
                    continue
                try:
                    g = ach.with_defaults(e7.cfg_to_genome(p, mode))
                except Exception:                  # noqa: BLE001
                    continue
                if p.get("vol_gate"):
                    g["vol_gate"] = float(p["vol_gate"])
                k = genome_key(g)
                if k in seen:
                    skipped += 1
                    continue
                seen.add(k)
                key = "%s|%s|%s" % (br, sym, mode)
                out[key] = dict(symbol=sym, genome=g, lev=p.get("lev", 5),
                                branch=br, mode=mode)
                added.append("%s/%s" % (sym.replace("USDT", ""), mode))
        print("  %-32s новых геномов: %d %s"
              % (br, len(added), ("(%s)" % ", ".join(added)) if added else ""))

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\nсовпало с уже проверенными и пропущено: %d" % skipped)
    print("записано новых: %d -> %s" % (len(out), OUT))
    if not out:
        print("Ветки не содержат ничего, чего нет в текущей. Это нормальный")
        print("исход, а не ошибка: значит проверка уже покрывала все геномы.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
