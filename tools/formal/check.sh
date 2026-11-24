#!/usr/bin/env bash
set -euo pipefail
jar="${TLA2TOOLS_JAR:-${XDG_CACHE_HOME:-$HOME/.cache}/stock-analyzer/tla2tools-1.8.0.jar}"
if [ ! -f "$jar" ]; then
  echo "tla2tools.jar v1.8.0 not found at $jar" >&2
  echo "fetch: curl -fL -o '$jar' https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar" >&2
  exit 2
fi
command -v java >/dev/null || { echo "java not found (Java 11+ needed)" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
model="${1:-tdnet_pdf_retire}"
cfg="${2:-MC.cfg}"
dir="$here/$model"
spec="$(ls "$dir"/*.tla | head -n1)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$dir"
java -XX:+UseParallelGC -cp "$jar" tlc2.TLC -noGenerateSpecTE -workers 1 -metadir "$work" -config "$cfg" "$spec"
