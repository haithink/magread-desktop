"""从固定版本 ECDICT CSV 导出基础至四级已知词；不联网、不修改用户词表。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

COMMIT = "39cfb9672c0906b63569e0453c2e6abfb9ab182a"
SOURCE = f"https://raw.githubusercontent.com/skywind3000/ECDICT/{COMMIT}/ecdict.csv"
TAGS = {"zk", "gk", "cet4"}
SOURCE_SHA256 = "d1ecbdf9ee2b920afb6f430ca6b6bb9f559588cf0dff86e04bd255f9390843cc"


def build(source: Path, output: Path):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("源文件校验值不符，请使用指定提交的完整 ECDICT CSV")
    known = set()
    rows = 0
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            rows += 1
            if set(row.get("tag", "").split()) & TAGS:
                word = row["word"].strip().lower()
                if word:
                    known.add(word)
    if rows < 700000 or len(known) < 4000:
        raise ValueError("输入不是完整的 ECDICT CSV，不能用截断文件生成词表")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(sorted(known)) + "\n", encoding="utf-8")
    metadata = {"project": "skywind3000/ECDICT", "commit": COMMIT, "source": SOURCE,
                "sourceSha256": digest,
                "sourceRows": rows, "tags": sorted(TAGS), "wordCount": len(known),
                "license": "MIT", "licenseFile": "LICENSES/ECDICT-MIT.txt"}
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(known)} words from {rows} source entries to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="已下载的固定版本 ecdict.csv")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "kandu/assets/cet4_known_supplement.txt")
    args = parser.parse_args()
    build(args.source, args.output)
