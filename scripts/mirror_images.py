#!/usr/bin/env python3
"""
Mirror the collection's IPFS images into the repo as optimized WebP, then rewrite
the data CSV(s) so images are served from jsDelivr (a fast global CDN) instead of
the slow ipfs.io gateway.

Runs in GitHub Actions (see .github/workflows/mirror-images.yml). You can also run
it locally:
    pip install Pillow requests
    REPO=hemibroscommunity-del/Hemi-Bros-catalogue python scripts/mirror_images.py

Resumable: images already present in images/ are skipped, so if the job times out
you can just run it again and it continues. Only images that mirror successfully
get their CSV URL rewritten — anything that fails stays pointing at IPFS.
"""
import os, re, io, glob, time
import concurrent.futures as cf
import requests
from PIL import Image

REPO      = os.environ.get("REPO", "hemibroscommunity-del/Hemi-Bros-catalogue")
BRANCH    = os.environ.get("BRANCH", "main")
MAX_WIDTH = int(os.environ.get("MAX_WIDTH", "640"))   # downscale only; keeps files small
QUALITY   = int(os.environ.get("QUALITY", "80"))
WORKERS   = int(os.environ.get("WORKERS", "16"))
OUT_DIR   = "images"
JSDELIVR  = f"https://cdn.jsdelivr.net/gh/{REPO}@{BRANCH}/{OUT_DIR}"

# Gateways tried in order for each CID. GitHub runners can reach all of these.
GATEWAYS = [
    "https://{cid}.ipfs.w3s.link",
    "https://{cid}.ipfs.dweb.link",
    "https://ipfs.io/ipfs/{cid}",
    "https://{cid}.ipfs.4everland.io",
    "https://gateway.pinata.cloud/ipfs/{cid}",
]

PAT_PATH = re.compile(r"/ipfs/([A-Za-z0-9]{40,})")
PAT_SUB  = re.compile(r"//([A-Za-z0-9]{40,})\.ipfs\.")

def find_cids():
    cids = set()
    for f in glob.glob("**/*.csv", recursive=True):
        try:
            txt = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        cids.update(PAT_PATH.findall(txt))
        cids.update(PAT_SUB.findall(txt))
    return sorted(cids)

def mirror_one(cid):
    out = os.path.join(OUT_DIR, f"{cid}.webp")
    if os.path.exists(out):
        return (cid, True)
    for gw in GATEWAYS:
        url = gw.format(cid=cid)
        for attempt in range(2):
            try:
                r = requests.get(url, timeout=45)
                if r.status_code == 200 and r.content:
                    img = Image.open(io.BytesIO(r.content)); img.load()
                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        img = img.convert("RGBA")
                    else:
                        img = img.convert("RGB")
                    if img.width > MAX_WIDTH:
                        h = round(img.height * MAX_WIDTH / img.width)
                        img = img.resize((MAX_WIDTH, h), Image.LANCZOS)
                    img.save(out, "WEBP", quality=QUALITY, method=6)
                    return (cid, True)
            except Exception:
                time.sleep(1.0 * (attempt + 1))
    return (cid, False)

def rewrite_csvs(have_cids):
    have = set(have_cids)
    changed = 0
    for f in glob.glob("**/*.csv", recursive=True):
        txt = open(f, encoding="utf-8", errors="ignore").read()
        new = txt
        for cid in have:
            # path form: any-gateway/ipfs/<cid>
            new = re.sub(r"https?://[^\s,\"']*?/ipfs/" + re.escape(cid) + r"\b",
                         f"{JSDELIVR}/{cid}.webp", new)
            # subdomain form: //<cid>.ipfs.<gateway>...
            new = re.sub(r"https?://" + re.escape(cid) + r"\.ipfs\.[^\s,\"']*",
                         f"{JSDELIVR}/{cid}.webp", new)
        if new != txt:
            open(f, "w", encoding="utf-8").write(new)
            changed += 1
    return changed

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cids = find_cids()
    print(f"Found {len(cids)} unique IPFS CIDs in CSV files.")
    if not cids:
        print("Nothing to mirror."); return
    ok = fail = done = 0
    failed_examples = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for cid, success in ex.map(mirror_one, cids):
            done += 1
            if success:
                ok += 1
            else:
                fail += 1
                if len(failed_examples) < 20:
                    failed_examples.append(cid)
            if done % 100 == 0 or done == len(cids):
                print(f"  {done}/{len(cids)} processed | ok={ok} fail={fail}", flush=True)
    print(f"Mirrored {ok} images; {fail} failed.")
    if failed_examples:
        print("Failed CIDs (left pointing at IPFS):", failed_examples)
    have = [c for c in cids if os.path.exists(os.path.join(OUT_DIR, f"{c}.webp"))]
    n = rewrite_csvs(have)
    print(f"Rewrote image URLs in {n} CSV file(s) to jsDelivr ({JSDELIVR}/<cid>.webp).")

if __name__ == "__main__":
    main()
