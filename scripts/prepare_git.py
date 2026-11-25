import subprocess
import os

subprocess.run(["git", "add", "."], check=True)
res = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
staged = [l for l in res.stdout.splitlines() if l.startswith("A ")]
print(f"Total staged files: {len(staged)}")

total_bytes = 0
large_staged = []
for l in staged:
    fp = l[2:].strip().strip('"')
    if os.path.exists(fp):
        sz = os.path.getsize(fp)
        total_bytes += sz
        if sz > 1024 * 1024:
            large_staged.append((fp, sz / (1024 * 1024)))

print(f"Total staged size: {total_bytes / (1024 * 1024):.2f} MB")
print("Staged files > 1MB:")
for name, sz in large_staged:
    print(f"  {name}: {sz:.2f} MB")
