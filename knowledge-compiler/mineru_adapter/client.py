import os
import io
import time
import zipfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


TOKEN = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiI3MzAwMDEzMCIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3ODY1OTE5NywiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiIiwib3BlbklkIjpudWxsLCJ1dWlkIjoiYmIxMWNlYzktNTJkOC00OGJhLWEyODMtM2UxZDYwMDgwN2ExIiwiZW1haWwiOiIiLCJleHAiOjE3ODY0MzUxOTd9.2fwiDtQMPqr3Mx4TgojTjchdHTmA6k3r5EGD49QZXUbqRfytN6Oxv0Bj0nqmdnnyNhjBBs0G4RVvfmRA1gWE1Q"
BASE_URL = "https://mineru.net"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}"
}


def upload_and_process_all(pdf_paths: list, is_ocr: bool = True) -> list:
    """Batch upload PDFs and process in parallel."""
    # Step 1: Get presigned upload URLs
    files_meta = [
        {"name": os.path.basename(p), "data_id": f"part_{i:03d}", "is_ocr": is_ocr}
        for i, p in enumerate(pdf_paths)
    ]

    res = requests.post(
        f"{BASE_URL}/api/v4/file-urls/batch",
        headers=HEADERS,
        json={"files": files_meta, "model_version": "vlm"}
    )
    result = res.json()
    assert result["code"] == 0, f"Failed to get upload URLs: {result['msg']}"

    batch_id = result["data"]["batch_id"]
    upload_urls = result["data"]["file_urls"]
    print(f"batch_id: {batch_id}, {len(upload_urls)} files")

    # Step 2: Parallel upload (no Content-Type header!)
    def upload_one(args):
        i, url, path = args
        with open(path, "rb") as f:
            r = requests.put(url, data=f)
        status = "OK" if r.status_code == 200 else "FAIL"
        print(f"  [{status}] {os.path.basename(path)}")
        return r.status_code == 200

    with ThreadPoolExecutor(max_workers=5) as executor:
        tasks = [(i, url, path) for i, (url, path) in enumerate(zip(upload_urls, pdf_paths))]
        results = list(executor.map(upload_one, tasks))

    if not all(results):
        raise Exception("Some files failed to upload")

    # Step 3: Poll batch result (auto-submitted after upload)
    print("Waiting for MinerU to process...")
    return poll_batch(batch_id)


def poll_batch(batch_id: str, interval: int = 5, timeout: int = 900) -> list:
    """Poll batch until all done or failed."""
    url = f"{BASE_URL}/api/v4/extract-results/batch/{batch_id}"
    start = time.time()

    while time.time() - start < timeout:
        res = requests.get(url, headers=HEADERS)
        data = res.json()["data"]
        files = data["extract_result"]

        done = [f for f in files if f["state"] == "done"]
        running = [f for f in files if f["state"] in ("running", "pending", "waiting-file", "converting")]
        failed = [f for f in files if f["state"] == "failed"]

        print(f"  Progress: {len(done)}/{len(files)} done, {len(running)} running, {len(failed)} failed")

        if len(done) + len(failed) == len(files):
            if failed:
                for f in failed:
                    print(f"  FAILED: {f['file_name']} - {f.get('err_msg', 'unknown')}")
            return files

        time.sleep(interval)

    raise TimeoutError(f"Timeout after {timeout}s")


def download_markdowns(extract_result: list, output_dir: str) -> list:
    """Download zip files and extract markdown."""
    os.makedirs(output_dir, exist_ok=True)
    md_files = []

    for item in extract_result:
        if item["state"] != "done":
            continue

        zip_url = item.get("full_zip_url")
        if not zip_url:
            continue

        res = requests.get(zip_url)
        if res.status_code != 200:
            print(f"  WARNING: download failed for {item.get('file_name')}")
            continue

        z = zipfile.ZipFile(io.BytesIO(res.content))
        for name in z.namelist():
            if name.endswith(".md"):
                md_content = z.read(name).decode("utf-8")
                md_name = os.path.splitext(item["file_name"])[0] + ".md"
                md_path = os.path.join(output_dir, md_name)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                md_files.append(md_path)
                print(f"  Saved: {md_name}")

    return md_files
