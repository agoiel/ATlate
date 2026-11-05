#!/usr/bin/env bash
# Скачать свободный шрифт для тайпа (с кириллицей) в папку fonts.
# Pangolin — SIL Open Font License, его можно свободно распространять вместе с программой.
set -u
dir="${1:-fonts}"
mkdir -p "$dir"
base="https://github.com/google/fonts/raw/main/ofl/pangolin"
curl -sSfL -o "$dir/Pangolin-Regular.ttf" "$base/Pangolin-Regular.ttf" \
  && curl -sSfL -o "$dir/Pangolin-OFL.txt" "$base/OFL.txt" \
  && echo "font ok" || echo "font download failed — will use system fonts"
exit 0
