#!/usr/bin/env bash
# Regenerate the four built-in overlay loops (light-leak, glitter, bokeh-dream,
# neon-pulse) as procedural substitutes.
#
# These are blended over the video with `screen`/`lighten`/`overlay`, so black
# is effectively transparent and only the bright parts show through.
#
# Usage: scripts/make_overlays.sh [output_dir] [ffmpeg_bin]
set -euo pipefail

OUT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets/overlays}"
FF="${2:-${FFMPEG_BIN:-ffmpeg}}"
command -v "$FF" >/dev/null 2>&1 || { echo "ffmpeg not found ($FF)"; exit 1; }

W=720; H=1280; FPS=24; D=4
mkdir -p "$OUT"
enc=(-an -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p
     -movflags +faststart -t "$D" -y)

echo "→ light-leak.mp4   (warm radial hotspot, drifts + flickers)"
"$FF" -hide_banner -loglevel error -f lavfi \
  -i "color=c=black:s=480x854:r=${FPS}:d=${D}" \
  -vf "format=rgb24,geq=r='clip(255*pow(max(0\,1-hypot(X-(150+45*sin(T*0.7))\,Y-(300+110*sin(T*0.45)))/360)\,2.4)\,0\,255)':g='clip(200*pow(max(0\,1-hypot(X-(150+45*sin(T*0.7))\,Y-(300+110*sin(T*0.45)))/430)\,2.8)\,0\,255)':b='clip(130*pow(max(0\,1-hypot(X-(150+45*sin(T*0.7))\,Y-(300+110*sin(T*0.45)))/480)\,3.2)\,0\,255)',scale=${W}:${H}:flags=bilinear,gblur=sigma=5,eq=brightness='0.05+0.13*abs(sin(2*PI*t/1.8))':eval=frame" \
  "${enc[@]}" "$OUT/light-leak.mp4"

echo "→ glitter.mp4      (sparse twinkling sparkles)"
"$FF" -hide_banner -loglevel error -f lavfi \
  -i "color=c=black:s=${W}x${H}:r=${FPS}:d=${D}" \
  -vf "format=gray,scale=180:320:flags=neighbor,geq=lum='if(gt(random(1),0.985),255,0)',scale=${W}:${H}:flags=bilinear,colorchannelmixer=rr=1.0:gg=0.97:bb=0.86" \
  "${enc[@]}" "$OUT/glitter.mp4"

echo "→ bokeh-dream.mp4  (soft teal→violet bokeh wash, slow drift)"
"$FF" -hide_banner -loglevel error -f lavfi \
  -i "gradients=s=${W}x${H}:r=${FPS}:d=${D}:c0=0x04030a:c1=0x2fd6c8:c2=0x7c5cff:c3=0xff5cae:n=4:speed=0.06:type=radial" \
  -vf "eq=brightness=-0.17:saturation=1.0,gblur=sigma=40,vignette=PI/3.4" \
  "${enc[@]}" "$OUT/bokeh-dream.mp4"

echo "→ neon-pulse.mp4   (cyan/violet neon band, 1.6 s pulse)"
"$FF" -hide_banner -loglevel error -f lavfi \
  -i "gradients=s=${W}x${W}:r=${FPS}:d=${D}:c0=0x05030f:c1=0x00f0ff:c2=0xff2bd6:c3=0x0a1030:n=4:speed=0.05:type=linear" \
  -vf "crop=${W}:300:0:210,gblur=sigma=48,pad=${W}:${H}:0:470:black,eq=brightness='0.10+0.06*sin(2*PI*t/1.6)':saturation=1.4:eval=frame" \
  "${enc[@]}" "$OUT/neon-pulse.mp4"

ls -la "$OUT"
