#!/bin/bash
# binaries.txt の arch に合う行を落として sha256 を照合し、DEST に取り出す。
# 使い方: fetch.sh DEST [LIST]。一つでも照合に失敗したら DEST には何も入れずに exit 1。
set -euo pipefail

dest="${1:?usage: fetch.sh DEST [LIST]}"
list="${2:-$(dirname "${BASH_SOURCE[0]}")/binaries.txt}"

arch="${MON_ARCH:-}"
if [[ -z "$arch" ]]; then
  case "$(uname -m)" in
    x86_64 | amd64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *)
      echo "monitoring: unsupported arch $(uname -m)" >&2
      exit 1
      ;;
  esac
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/out"

found=0
while read -r name row_arch sha url members; do
  [[ -z "${name:-}" || "$name" == \#* ]] && continue
  [[ "$row_arch" == "$arch" ]] || continue
  found=1
  archive="$work/$name.tar.gz"
  if ! curl -fsSL --retry 3 --max-time 300 -o "$archive" "$url"; then
    echo "monitoring: download failed $name" >&2
    exit 1
  fi
  got="$(sha256sum "$archive" | awk '{print $1}')"
  if [[ "$got" != "$sha" ]]; then
    echo "monitoring: sha256 mismatch $name" >&2
    exit 1
  fi
  mkdir -p "$work/x/$name"
  tar -xzf "$archive" -C "$work/x/$name"
  IFS=',' read -r -a pairs <<< "$members"
  for pair in "${pairs[@]}"; do
    member="${pair%%:*}"
    target="${pair##*:}"
    src="$(find "$work/x/$name" -type f -name "$member" | head -n 1)"
    if [[ -z "$src" ]]; then
      echo "monitoring: $member missing in $name" >&2
      exit 1
    fi
    install -m 755 "$src" "$work/out/$target"
  done
done < "$list"

if [[ "$found" != 1 ]]; then
  echo "monitoring: no binaries for $arch" >&2
  exit 1
fi

mkdir -p "$dest"
for f in "$work"/out/*; do
  install -m 755 "$f" "$dest/$(basename "$f")"
done
