"""并发压测脚本（asyncio + httpx），打 ai-metrics 后端 API。

只依赖 httpx (已在 [dev] extra)。运行：
    python -m tests.loadtest --base http://192.168.0.132:8001 -c 50 -n 500
    python -m tests.loadtest --base http://127.0.0.1:8001 -c 100 -n 1000

按场景统计 p50/p95/p99/max/RPS/错误数；同时给出每端点的吞吐。
"""
from __future__ import annotations
import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Scenario:
    name: str
    method: str
    path: str
    weight: float = 1.0
    body: dict | None = None
    body_fn: callable | None = None     # 动态生成 body


@dataclass
class Stats:
    durations_ms: list[float] = field(default_factory=list)
    statuses: list[int] = field(default_factory=list)
    errors: int = 0


SCENARIOS = [
    Scenario("healthz",   "GET",  "/api/healthz",                                weight=2.0),
    Scenario("config",    "GET",  "/api/reports/ai_metrics/config",              weight=1.0),
    Scenario("projects",  "GET",  "/api/dropdowns/projects",                     weight=1.0),
    Scenario("summary_all","POST","/api/reports/ai_metrics/summary",             weight=4.0,
             body={"paging": "none", "project_codes": None}),
    Scenario("summary_filtered","POST","/api/reports/ai_metrics/summary",        weight=3.0,
             body_fn=lambda: {"paging": "none", "project_codes": [random.choice(["proj_alpha","proj_beta","proj_gamma","proj_delta"])]}),
    Scenario("distinct",  "POST", "/api/reports/ai_metrics/distinct",            weight=1.0,
             body={"column": "domain_code"}),
    Scenario("drilldown", "POST", "/api/reports/ai_metrics/drilldown",           weight=1.5,
             body_fn=lambda: {
                 "ref": "metric_drill",
                 "row": {"domain_code": random.choice(["core","business","platform","data","infra","frontend"])},
                 "filter": {"business_date": {"from": None, "to": None}, "projects": []},
                 "cell": {"column": random.choice(["ai_case_count","ai_code_lines","new_case_count"])},
                 "paging": {"page": 1, "page_size": 20}
             }),
]


def pick_scenario() -> Scenario:
    total = sum(s.weight for s in SCENARIOS)
    r = random.random() * total
    acc = 0.0
    for s in SCENARIOS:
        acc += s.weight
        if r <= acc: return s
    return SCENARIOS[-1]


async def one_request(client: httpx.AsyncClient, s: Scenario) -> tuple[str, int, float, str | None]:
    t0 = time.perf_counter()
    try:
        kwargs = {}
        if s.body or s.body_fn:
            kwargs["json"] = s.body_fn() if s.body_fn else s.body
        r = await client.request(s.method, s.path, **kwargs)
        return s.name, r.status_code, (time.perf_counter() - t0) * 1000, None
    except Exception as e:
        return s.name, 0, (time.perf_counter() - t0) * 1000, str(e)[:80]


async def worker(client: httpx.AsyncClient, n: int, stats: dict[str, Stats]):
    for _ in range(n):
        s = pick_scenario()
        name, code, dur, err = await one_request(client, s)
        st = stats.setdefault(name, Stats())
        st.durations_ms.append(dur)
        st.statuses.append(code)
        if err: st.errors += 1


def percentile(xs: list[float], p: float) -> float:
    if not xs: return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[idx]


def fmt_table(rows: list[list[str]]) -> str:
    widths = [max(len(str(r[c])) for r in rows) for c in range(len(rows[0]))]
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(str(c).ljust(widths[j]) for j, c in enumerate(r)))
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="例：http://192.168.0.132:8001")
    ap.add_argument("-c", "--concurrency", type=int, default=50)
    ap.add_argument("-n", "--per-worker", type=int, default=20, help="每个并发 worker 跑多少次请求")
    ap.add_argument("--warmup", type=int, default=5, help="预热轮数（不计入统计）")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--report-json", default=None, help="把结果写入 JSON 文件")
    args = ap.parse_args()

    total_planned = args.concurrency * args.per_worker
    print(f"\n== 压测目标 {args.base}")
    print(f"   并发 {args.concurrency} × 每 worker {args.per_worker} = {total_planned} 请求")
    print(f"   场景权重: " + ", ".join(f"{s.name}={s.weight}" for s in SCENARIOS))

    limits = httpx.Limits(max_keepalive_connections=args.concurrency,
                          max_connections=args.concurrency * 2)
    timeout = httpx.Timeout(args.timeout)

    async with httpx.AsyncClient(base_url=args.base, limits=limits, timeout=timeout) as client:
        # 预热
        if args.warmup > 0:
            print(f"== 预热 {args.warmup} 轮 …")
            for _ in range(args.warmup):
                await one_request(client, SCENARIOS[0])

        stats: dict[str, Stats] = {}
        t_start = time.perf_counter()
        await asyncio.gather(*[
            worker(client, args.per_worker, stats) for _ in range(args.concurrency)
        ])
        t_total = time.perf_counter() - t_start

    # 汇总
    total_n = sum(len(st.durations_ms) for st in stats.values())
    total_errs = sum(st.errors for st in stats.values())
    total_2xx = sum(1 for st in stats.values() for s in st.statuses if 200 <= s < 300)

    print(f"\n== 总览")
    print(f"   总请求数  : {total_n}")
    print(f"   2xx       : {total_2xx} ({total_2xx*100/max(1,total_n):.1f}%)")
    print(f"   错误      : {total_errs}")
    print(f"   总耗时    : {t_total:.2f}s")
    print(f"   总 RPS    : {total_n / t_total:.1f}")

    rows = [["scenario", "n", "ok", "err", "p50ms", "p95ms", "p99ms", "max", "avg", "rps"]]
    summary_dict = {}
    for name in sorted(stats.keys()):
        st = stats[name]
        durs = st.durations_ms
        ok = sum(1 for s in st.statuses if 200 <= s < 300)
        p50 = percentile(durs, 50)
        p95 = percentile(durs, 95)
        p99 = percentile(durs, 99)
        rps = len(durs) / t_total
        rows.append([
            name, str(len(durs)), str(ok), str(st.errors),
            f"{p50:.1f}", f"{p95:.1f}", f"{p99:.1f}",
            f"{max(durs):.1f}", f"{statistics.mean(durs):.1f}",
            f"{rps:.1f}",
        ])
        summary_dict[name] = {
            "n": len(durs), "ok": ok, "err": st.errors,
            "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
            "max_ms": max(durs), "avg_ms": statistics.mean(durs),
            "rps": rps,
        }

    print("\n== 分场景")
    print(fmt_table(rows))

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump({
                "target": args.base,
                "concurrency": args.concurrency,
                "per_worker": args.per_worker,
                "total_requests": total_n,
                "total_2xx": total_2xx,
                "total_errors": total_errs,
                "duration_s": t_total,
                "rps": total_n / t_total,
                "scenarios": summary_dict,
            }, f, indent=2, ensure_ascii=False)
        print(f"\n报告写入 {args.report_json}")


if __name__ == "__main__":
    asyncio.run(main())
