"""批量爬取调度器：保持固定并发，逐校跑完并把结果记账。

用法：
    python scripts/crawl_batch.py --workers 2                # 跑全部内置学校
    python scripts/crawl_batch.py --workers 2 --only A,B,C   # 只跑指定学校
    python scripts/crawl_batch.py --workers 2 --skip A,B     # 跳过指定学校

行为：
- 每所学校一个独立子进程（`crawl_university.py --university X --graduate --details`），
  互不干扰；某校失败只记录，不中断整批。
- **自动跳过正在被其它进程爬取的学校**：若 data/crawl_<校名>.log 在
  `--live-window` 秒内被写过，说明该校已有进程在跑，本次不重复启动。
- 逐校结果写入 data/batch_logs/<校名>.json，全部结束后打印汇总表。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

# 控制台默认可能是 GBK，中文日志会乱码或直接报 UnicodeEncodeError；
# 统一改成 UTF-8 输出，日志文件才能直接读。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CRAWLER = ROOT / "scripts" / "crawl_university.py"
BATCH_LOGS = ROOT / "data" / "batch_logs"
LIVE_WINDOW = 900.0


def load_universities() -> list[str]:
    """取爬取脚本内置的学校清单（含各校官网首页）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import crawl_university  # noqa: PLC0415 - 需要在设置 sys.path 之后再导入

    return list(crawl_university.UNIVERSITIES)


def is_live(university: str, now: float, window: float) -> bool:
    """该校是否已有爬取进程在跑（用日志文件的最后写入时间判断）。"""
    log = ROOT / "data" / f"crawl_{university}.log"
    if not log.exists():
        return False
    return (now - log.stat().st_mtime) < window


def summarize(university: str) -> dict:
    """读取该校爬取结果，统计导师数与字段完整度。"""
    path = ROOT / "data" / "official_crawl" / f"{university}.json"
    info: dict = {"university": university, "ok": False}
    if not path.exists():
        info["error"] = "没有产出 JSON"
        return info
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 汇总不该因单个坏文件中断
        info["error"] = f"JSON 读取失败：{exc}"
        return info

    total = with_title = with_email = with_dir = 0
    zero_colleges: list[str] = []
    for college in payload:
        entries = college.get("entries") or []
        if not entries:
            zero_colleges.append(college.get("college", "?"))
        for entry in entries:
            total += 1
            with_title += bool(entry.get("title"))
            with_email += bool(entry.get("email"))
            with_dir += bool(entry.get("directions"))

    info.update(
        ok=total > 0,
        colleges=len(payload),
        advisors=total,
        title=with_title,
        email=with_email,
        directions=with_dir,
        zero_colleges=zero_colleges,
    )
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2, help="同时爬几所学校")
    parser.add_argument("--only", default="", help="只跑这些学校，逗号分隔")
    parser.add_argument("--skip", default="", help="跳过这些学校，逗号分隔")
    parser.add_argument("--delay", type=float, default=0.25, help="每次请求间隔秒数")
    parser.add_argument("--live-window", type=float, default=LIVE_WINDOW,
                        help="日志多少秒内更新过就认为该校正在被爬")
    parser.add_argument("--js", action="store_true", help="强制浏览器渲染取页")
    parser.add_argument("--no-live-check", action="store_true", help="不检查是否已有进程在爬")
    args = parser.parse_args()

    all_schools = load_universities()
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        missing = [s for s in wanted if s not in all_schools]
        if missing:
            print(f"内置清单里没有这些学校：{'、'.join(missing)}")
        queue = [s for s in wanted if s in all_schools]
    else:
        queue = list(all_schools)

    skipped = {s.strip() for s in args.skip.split(",") if s.strip()}
    queue = [s for s in queue if s not in skipped]

    if not args.no_live_check:
        now = time.time()
        live = [s for s in queue if is_live(s, now, args.live_window)]
        if live:
            print(f"已有进程在爬，本次跳过：{'、'.join(live)}")
        queue = [s for s in queue if s not in live]

    print(f"待爬 {len(queue)} 所学校，并发 {args.workers}：{'、'.join(queue)}", flush=True)
    if not queue:
        print("没有需要爬的学校。")
        return 0

    extra = ["--js"] if args.js else []
    running: dict[str, subprocess.Popen] = {}
    results: list[dict] = []
    started_at = time.time()

    pending = list(queue)
    while pending or running:
        while pending and len(running) < args.workers:
            university = pending.pop(0)
            BATCH_LOGS.mkdir(parents=True, exist_ok=True)
            log = (BATCH_LOGS / f"{university}.log").open("w", encoding="utf-8")
            cmd = [
                str(PYTHON), str(CRAWLER),
                "--university", university,
                "--graduate", "--details",
                "--delay", str(args.delay), *extra,
            ]
            proc = subprocess.Popen(
                cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT)
            )
            proc._log_handle = log  # type: ignore[attr-defined]  # 保持句柄直到进程结束
            proc._started = time.time()  # type: ignore[attr-defined]
            running[university] = proc
            print(f"[启动] {university}（当前并行 {len(running)}）", flush=True)

        time.sleep(5)
        for university in list(running):
            proc = running[university]
            if proc.poll() is None:
                continue
            spent = time.time() - proc._started  # type: ignore[attr-defined]
            proc._log_handle.close()  # type: ignore[attr-defined]
            del running[university]
            info = summarize(university)
            info["exit_code"] = proc.returncode
            info["seconds"] = round(spent)
            results.append(info)
            status = "完成" if info.get("ok") else "无结果"
            print(
                f"[{status}] {university} 用时 {spent / 60:.1f} 分钟"
                f" | 导师 {info.get('advisors', 0)} 人"
                f" | 0人学院 {len(info.get('zero_colleges', []))} 个",
                flush=True,
            )

    total_seconds = time.time() - started_at
    report = ROOT / "data" / "batch_report.json"
    report.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_minutes": round(total_seconds / 60, 1),
                "schools": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== 汇总 ===")
    grand = sum(r.get("advisors", 0) for r in results)
    for info in sorted(results, key=lambda r: -r.get("advisors", 0)):
        zero = info.get("zero_colleges", [])
        line = (
            f"{info['university']:<12} 导师 {info.get('advisors', 0):>5} 人"
            f" | 学院 {info.get('colleges', 0):>3} 个"
            f" | 职称 {info.get('title', 0):>5}"
            f" | 邮箱 {info.get('email', 0):>5}"
            f" | 方向 {info.get('directions', 0):>5}"
        )
        if zero:
            line += f" | 0人学院 {len(zero)} 个"
        if info.get("error"):
            line += f" | {info['error']}"
        print(line)
    print(f"\n合计 {grand} 位导师，总耗时 {total_seconds / 60:.1f} 分钟")
    print(f"明细已存 {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
