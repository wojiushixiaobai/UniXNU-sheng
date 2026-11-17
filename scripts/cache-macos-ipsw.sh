#!/usr/bin/env bash
# Keep the large macOS IPSW disk images (the OS and SystemOS AEA archives, about 13 GiB) in a
# GitHub release so a repeated userdata build reuses them instead of downloading them from Apple
# again. actions/cache cannot hold them: the repository cache budget is 10 GB and the other build
# caches already fill a large part of it. Release assets are capped at 2 GB, so each member is
# stored as numbered parts beside a manifest with its name, size and sha256.
#
# The member files live in UniXNU/out/ipsw/<build>. The extraction step of UniXNU accepts a member
# whose size matches, so restoring the files there skips the HTTPS range downloads.
#
# usage: scripts/cache-macos-ipsw.sh restore|save <build> <cache-dir> [tag]
set -euo pipefail

action=${1:?usage: cache-macos-ipsw.sh restore|save <build> <cache-dir> [tag]}
build=${2:?build}
cache=${3:?cache directory}
tag=${4:-ipsw-media-$build}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

export GH_TOKEN=${GH_TOKEN:-${GITHUB_TOKEN:-}}
if [ -z "${GH_TOKEN:-}" ]; then
  echo 'cache-macos-ipsw: GH_TOKEN is not set; skipping' >&2
  exit 0
fi

case "$action" in
restore)
  if ! gh release view "$tag" >/dev/null 2>&1; then
    echo "cache-macos-ipsw: no release $tag yet; the build downloads its own media"
    exit 0
  fi
  echo "cache-macos-ipsw: restoring $tag"
  gh release download "$tag" --dir "$stage"
  python3 - "$stage" "$cache" <<'PY'
import hashlib, json, pathlib, sys
stage, cache = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
manifest = json.loads((stage / 'manifest.json').read_text())
cache.mkdir(parents=True, exist_ok=True)
restored = 0
for entry in manifest['files']:
    target = cache / entry['name']
    if target.is_file() and target.stat().st_size == entry['size']:
        continue
    data = b''.join((stage / part).read_bytes() for part in entry['parts'])
    if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise SystemExit(f"cache-macos-ipsw: {entry['name']} does not match its manifest")
    target.write_bytes(data)
    restored += 1
print(f'cache-macos-ipsw: restored {restored} member file(s)')
PY
  ;;
save)
  if gh release view "$tag" >/dev/null 2>&1; then
    echo "cache-macos-ipsw: $tag already holds the media"
    exit 0
  fi
  python3 - "$cache" "$stage" "$build" <<'PY'
import hashlib, json, pathlib, sys
cache, stage, build = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
part = 1_900_000_000
files = [p for p in sorted(cache.glob('*')) if p.is_file() and (p.name.endswith('.aea') or p.name.endswith('.aea.json'))]
if not files:
    raise SystemExit(0)
manifest = {'build': build, 'files': []}
saved = 0
for path in files:
    data = path.read_bytes()
    parts = []
    for start in range(0, len(data), part):
        name = f"part-{len(manifest['files']):03d}-{start // part:03d}"
        (stage / name).write_bytes(data[start:start + part])
        parts.append(name)
    manifest['files'].append({'name': path.name, 'size': len(data),
                              'sha256': hashlib.sha256(data).hexdigest(), 'parts': parts})
    saved += len(data)
(stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f"cache-macos-ipsw: {len(manifest['files'])} media file(s), {saved / 2**30:.2f} GiB")
PY
  if [ ! -f "$stage/manifest.json" ]; then
    echo 'cache-macos-ipsw: no media members to save'
    exit 0
  fi
  gh release create "$tag" --title "IPSW media cache $build" --prerelease \
    --notes "Cached macOS $build IPSW disk images for repeated userdata builds. Delete this release to refresh them."
  gh release upload "$tag" "$stage"/*
  echo "cache-macos-ipsw: saved $tag"
  ;;
*)
  echo "cache-macos-ipsw: unknown action $action" >&2
  exit 2
  ;;
esac