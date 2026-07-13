#!/usr/bin/env bash

set -euo pipefail

readonly WATERMARK="EndofTimeWorks"
readonly MAX_SIZE="2400x2400>"
readonly WATERMARK_TILE_SIZE="600x300"
readonly WATERMARK_CANVAS_SIZE="2400x2400"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly PROJECT_DIR

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 INPUT_IMAGE [OUTPUT_WEBP]" >&2
  exit 2
fi

readonly input=$1
fallback_name="$(basename -- "${input%.*}").webp"
readonly fallback_name
readonly output=${2:-"${PROJECT_DIR}/_draft/photos/${fallback_name}"}

if [[ ! -f ${input} ]]; then
  echo "Input image not found: ${input}" >&2
  exit 1
fi

if [[ -e ${output} ]]; then
  echo "Refusing to overwrite existing file: ${output}" >&2
  exit 1
fi

mkdir -p -- "$(dirname -- "${output}")"

magick "${input}" \
  -auto-orient \
  -strip \
  -resize "${MAX_SIZE}" \
  \( \
    -size "${WATERMARK_TILE_SIZE}" \
    xc:none \
    -gravity center \
    -font DejaVu-Sans-Bold \
    -pointsize 34 \
    -fill "rgba(255,255,255,0.24)" \
    -stroke "rgba(0,0,0,0.18)" \
    -strokewidth 1 \
    -annotate 25x25 "${WATERMARK}" \
    -write mpr:watermark \
    +delete \
  \) \
  \( -size "${WATERMARK_CANVAS_SIZE}" tile:mpr:watermark \) \
  -compose over \
  -composite \
  -quality 86 \
  "${output}"

echo "Created watermarked web copy: ${output}"
