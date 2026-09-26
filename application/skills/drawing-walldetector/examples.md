# drawing-walldetector 사용 예

## 0) 경로·기존 walls 확인 (항상 먼저)

```bash
SCRIPTS="$WORKING_DIR/skills/drawing-walldetector/scripts"
ART="$ARTIFACTS_DIR/sk_yongin_jiwon"
FLOOR=12F

# devider 산출 확인
ls "$ART/floors/$FLOOR/floor_original.png" "$ART/floors/$FLOOR/floor_parts_index.json"

if [ -d "$ART/floors/$FLOOR/walls" ]; then
  echo "EXISTING: $ART/floors/$FLOOR/walls — 사용자에게 계속/중단 확인 후 진행"
  ls "$ART/floors/$FLOOR/walls" | head -20
  # STOP: 허락 전 detect 금지
fi
```

로컬 개발(비 Runtime):

```bash
SCRIPTS=/Users/ksdyb/Documents/src/agent-skills/application/skills/drawing-walldetector/scripts
ART=/path/to/user/artifacts/sk_yongin_jiwon
```

## 1) 파일럿 1층

```bash
# floor_original로 구조 확인 후
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F

# 샘플 타일만
python3 "$SCRIPTS/detect_walls_floor.py" \
  --artifacts "$ART" \
  --floor 12F \
  --only R0C0,R1C0
```

미리보기: `$ART/floors/12F/floor_wall_original.png`, `$ART/floors/12F/walls/R0C0_walls.png`

## 2) 컨펌 후 다음 층 (층당 bash 1회)

```bash
# ❌ 금지: for FLOOR in 5F 6F …; do detect_walls_floor …; done
# ❌ 기본 금지: detect_walls_all (사용자가 일괄을 명시한 경우만)

python3 "$SCRIPTS/detect_walls_floor.py" --artifacts "$ART" --floor 6F
# 보고 후 7F는 별도 bash
```

## 3) 단일 타일

```bash
python3 "$SCRIPTS/detect_walls_tile.py" \
  --dxf "$ART/floors/12F/parts/R0C0.dxf" \
  --out-dir "$ART/floors/12F/walls" \
  --tile-id R0C0 \
  --floor 12F
```

## 4) (선택) 사용자가 일괄을 명시한 경우만

```bash
python3 "$SCRIPTS/detect_walls_all.py" --artifacts "$ART" --skip 12F
# → $ART/walls_all_index.json
```
