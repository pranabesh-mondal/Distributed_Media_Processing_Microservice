"""Load test: concurrent uploads + job submissions against a running API.

Simulates N clients uploading media and processing jobs concurrently,
reporting throughput and latency percentiles.

Usage:
    python scripts/load_test.py --jobs 50 --concurrency 10 \
        --api http://localhost:8000 --media-type image

Requires: a reachable API, S3 credentials in the environment/.env, and a
running worker. Image payloads are generated in-memory with Pillow; video
payloads are skipped (generate real clips if you want to load-test video).
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests
from PIL import Image


@dataclass
class JobResult:
    job_id: str = ""
    ok: bool = False
    error: str = ""
    upload_latency: float = 0.0
    end_to_end: float = 0.0
    status: str = ""
    result_key: str = ""


def make_test_png(width: int = 640, height: int = 480) -> bytes:
    """Generate a deterministic test image in memory."""
    img = Image.new("RGB", (width, height))
    for x in range(0, width, 40):
        for y in range(0, height, 40):
            img.paste(
                Image.new("RGB", (40, 40), ((x * 7) % 256, (y * 13) % 256, 128)),
                (x, y),
            )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def run_single_job(api, media_type, payload, operations, poll_timeout) -> JobResult:
    """Upload, submit and poll one job; return timing metrics."""
    result = JobResult()
    t0 = time.monotonic()
    session = requests.Session()
    body: dict = {}
    try:
        # 1. Pre-signed upload URL.
        resp = session.get(
            f"{api}/api/v1/upload-url",
            params={"media_type": media_type, "filename": "load.png"},
            timeout=15,
        )
        resp.raise_for_status()
        up = resp.json()

        # 2. Direct PUT to S3.
        headers = {"Content-Type": up.get("content_type") or "application/octet-stream"}
        t_up = time.monotonic()
        put = session.put(up["upload_url"], data=payload, headers=headers, timeout=60)
        put.raise_for_status()
        result.upload_latency = time.monotonic() - t_up

        # 3. Submit the job.
        resp = session.post(
            f"{api}/api/v1/jobs",
            json={
                "media_type": media_type,
                "source_key": up["object_key"],
                "operations": operations,
            },
            timeout=15,
        )
        resp.raise_for_status()
        result.job_id = resp.json()["job_id"]

        # 4. Poll until terminal status.
        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            status_resp = session.get(
                f"{api}/api/v1/jobs/{result.job_id}", timeout=15
            )
            status_resp.raise_for_status()
            body = status_resp.json()
            result.status = body["status"]
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.25)

        result.ok = result.status == "completed"
        result.result_key = body.get("result_key") or ""
        result.end_to_end = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001 - report any per-job failure
        result.error = f"{type(exc).__name__}: {exc}"
        result.end_to_end = time.monotonic() - t0
    finally:
        session.close()
    return result


def percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(len(ordered) * pct / 100) - 1))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--jobs", type=int, default=20, help="Total jobs to run.")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel clients.")
    parser.add_argument("--media-type", choices=["image"], default="image")
    parser.add_argument("--image-size", default="640x480")
    parser.add_argument("--poll-timeout", type=float, default=120.0)
    parser.add_argument(
        "--operations",
        default='[{"op": "resize", "params": {"width": 320}}, '
                '{"op": "compress", "params": {"format": "jpeg", "quality": 80}}]',
    )
    args = parser.parse_args()

    width, height = (int(v) for v in args.image_size.split("x"))
    payload = make_test_png(width, height)
    operations = json.loads(args.operations)

    print(f"Load test: {args.jobs} jobs, concurrency={args.concurrency}, "
          f"image={width}x{height} ({len(payload)} bytes)")
    results: list[JobResult] = []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(run_single_job, args.api, args.media_type, payload,
                        operations, args.poll_timeout)
            for _ in range(args.jobs)
        ]
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            state = "OK  " if res.ok else "FAIL"
            print(f"  [{state}] {res.job_id or '-':36s} "
                  f"e2e={res.end_to_end:6.2f}s upload={res.upload_latency:5.2f}s "
                  f"{res.error or res.status}")
    wall = time.monotonic() - t0

    ok = [r for r in results if r.ok]
    e2e = [r.end_to_end for r in ok]
    print("\n===== Summary =====")
    print(f"Total: {len(results)}  OK: {len(ok)}  Failed: {len(results) - len(ok)}")
    print(f"Wall time: {wall:.2f}s  Throughput: {len(results) / wall:.2f} jobs/s")
    if e2e:
        print(f"End-to-end latency (s): min={min(e2e):.2f} "
              f"p50={percentile(e2e, 50):.2f} p95={percentile(e2e, 95):.2f} "
              f"p99={percentile(e2e, 99):.2f} "
              f"mean={statistics.mean(e2e):.2f} max={max(e2e):.2f}")


if __name__ == "__main__":
    main()
