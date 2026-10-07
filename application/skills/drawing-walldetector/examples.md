# drawing-walldetector 사용 예

## 0) 경로

`floor_wall_original.*` 가 이미 있어도 묻지 않고 덮어쓴다.

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
FLOOR=12F

ls "$ART/floors/$FLOOR/floor_original.dxf" "$ART/floors/$FLOOR/floor_original.png"
```

로컬 개발(비 Runtime):

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/path/to/user/artifacts/sk_yongin_jiwon
```

## 1) 샘플 2장으로 도면별 조건 (wall_samples/wall_conditions.json 이 없을 때만)

```bash
if command -v python3.13 >/dev/null 2>&1; then PY=python3.13; else PY=python3; fi
"$PY" "$SCRIPTS/sample_wall_conditions.py" \
  --artifacts "$ART" \
  --floor 12F \
  --vision
```

이미 `wall_samples/wall_conditions.json` 이 있으면 이 단계를 건너뛴다.

## 2) 한 층

```bash
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F
```

미리보기: `$ART/floors/12F/floor_wall_original.png` (이미 있으면 덮어씀)

## 3) 여러 층은 한 번

```bash
python3 "$SCRIPTS/detect_walls_all.py" --artifacts "$ART"
```

## 4) (선택) 레거시 타일 — parts 가 있고 사용자가 명시한 경우만

```bash
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F \
  --with-tiles

python3 "$SCRIPTS/detect_walls_tile.py" \
  --dxf "$ART/floors/12F/parts/R0C0.dxf" \
  --out-dir "$ART/floors/12F/walls" \
  --tile-id R0C0 \
  --floor 12F
```

## 5) 일부 층 제외

```bash
python3 "$SCRIPTS/detect_walls_all.py" --artifacts "$ART" --skip 12F
# → $ART/walls_all_index.json
```
