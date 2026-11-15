
#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/tse/Downloads/pianokt_300_auto") 
# ACCEPTED = ROOT / "output" / "accepted"
ACCEPTED = ROOT / "output" / "intermediate_advanced" / "accepted" 

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually upload to Supabase",
    )
    args = parser.parse_args()

    if not ACCEPTED.is_dir():
        sys.exit(f"Directory not found: {ACCEPTED}")

    # Collect and validate all accepted segments first.
    jobs = []
    errors = []

    for song_dir in sorted(ACCEPTED.iterdir()):
        if not song_dir.is_dir():
            continue

        metadata_path = song_dir / "metadata.json"
        if not metadata_path.exists():
            errors.append(f"{song_dir.name}: missing metadata.json")
            continue

        try:
            meta = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )

            if meta.get("status") != "accepted":
                continue

            required = [
                "title",
                "composer",
                "license",
                "source_url",
            ]
            for field in required:
                if not meta.get(field):
                    raise ValueError(f"missing {field}")

            segments = meta.get("segments") or []
            if not segments:
                raise ValueError("no segments in metadata")

            actual_files = sorted(
                song_dir.glob("segment_*.mid")
            )

            if len(actual_files) != len(segments):
                raise ValueError(
                    "segment file count differs from metadata"
                )

            for segment in segments:
                # 使用 metadata 中的真实 segment ID 编号
                index = int(segment["segment_id"].rsplit("_", 1)[-1])

                midi_path = (
                    song_dir / f"segment_{index:03d}.mid"
                )

                if not midi_path.is_file():
                    raise ValueError(
                        f"missing {midi_path.name}"
                    )

                expected_path = (
                    f"accepted/{song_dir.name}/"
                    f"segment_{index:03d}.mid"
                )

                if segment.get("midi_path") != expected_path:
                    raise ValueError(
                        f"metadata mismatch: {midi_path.name}"
                    )

                command = [
                    "pianokt-data",
                    "import-song",
                    str(midi_path),
                    "--title", meta["title"],
                    "--composer", meta["composer"],
                    "--license", meta["license"],
                    "--source-url", meta["source_url"],
                    "--slug", f"pianokt-{song_dir.name}",
                    "--segment-index", str(index),
                    "--segment-title",
                    f"{meta['title']} - Part {index}",
                ]

                if meta.get("genre"):
                    command += ["--genre", meta["genre"]]

                if not args.apply:
                    command.append("--dry-run")

                jobs.append({
                    "song": meta["title"],
                    "segment": index,
                    "command": command,
                })

        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{song_dir.name}: {exc}")

    if errors:
        print("\nPRECHECK FAILED")
        for error in errors:
            print("ERROR:", error)
        sys.exit(1)

    if not jobs:
        sys.exit("No eligible segments found.")

    print(f"\nSongs: {len(set(j['command'][j['command'].index('--slug')+1] for j in jobs))}")
    print(f"Segments: {len(jobs)}")
    print("Mode:", "UPLOAD" if args.apply else "DRY RUN")

    results = []
    failed = 0

    for number, job in enumerate(jobs, 1):
        print(
            f"\n[{number}/{len(jobs)}] "
            f"{job['song']} - Part {job['segment']}",
            flush=True,
        )

        command = job["command"]
        print(" ".join(__import__("shlex").quote(x) for x in command))

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )

            success = completed.returncode == 0
            output = completed.stdout
            error = completed.stderr

        except OSError as exc:
            success = False
            output = ""
            error = str(exc)

        if success:
            print("SUCCESS")
            if output.strip():
                print(output.strip())
        else:
            failed += 1
            print("FAILED:", error, file=sys.stderr)

        results.append({
            "song": job["song"],
            "segment": job["segment"],
            "success": success,
            "stdout": output,
            "stderr": error,
        })

        # Stop before publishing more segments on error.
        if not success and args.apply:
            print("Upload stopped after failure.")
            break

    report_path = ROOT / (
        "batch_import_report.json"
        if args.apply else "batch_import_dry_run.json"
    )

    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n========== SUMMARY ==========")
    print("Processed:", len(results))
    print("Succeeded:", len(results) - failed)
    print("Failed:", failed)
    print("Report:", report_path)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
