# drawing-walldetector — 참고

## 산출물 위치

bash/`execute_code` cwd는 사용자 `artifacts/`이며, 환경변수 `$ARTIFACTS_DIR`가 동일 경로를 가리킨다.

```text
$ARTIFACTS_DIR/<drawing_id>/
  floors/<F>/
    floor_original.dxf / .png                         # drawing-devider
    floor_wall_original.dxf / .png / _meta.json       # 층 전체 wall (기본)
    floor_wall_index.json                             # 층 요약
  walls_all_index.json                                # 선택(다층 요약)
```

- `floor_walls_overview.*` / `walls/floor_wall_original.*` 는 **생성·사용하지 않음**.
- `parts/` · `walls/R*C*_walls.*` 는 **기본 미생성** (`--with-tiles` 레거시만).

- 스크립트 호출: `$WORKING_DIR/skills/drawing-walldetector/scripts/...`
- 상대 `skills/`·`scripts/`·구경로 `cde-pilot/...` **금지**
- 로컬 개발 시에도 **사용자 artifacts**를 쓴다.

## 기존 산출 게이트

`floors/<F>/floor_wall_original.*`가 이미 있으면:

1. 경로·기존 파일 요약을 사용자에게 보여 준다
2. **계속 / 중단**을 묻는다
3. 허락 전에는 `detect_walls_floor` / `detect_walls_all`을 실행하지 않는다
4. 중단 시 폴더를 임의 삭제하지 않는다

## 층 게이트 · 층별 1개씩

1. 파일럿 1층만 `detect_walls_floor.py`
2. 사용자 허락 후 나머지 층도 **층당 bash 1회**
3. **금지(기본):** `for FLOOR in …` 일괄, `detect_walls_all` 한 방에 전층
4. `detect_walls_all`은 사용자가 일괄을 **명시한 경우만**

## 왜 기하 휴리스틱인가

지원동 클린 DXF는 벽/가구가 거의 전부 `0arch`에 있다. 레이어 분리가 불가하므로:

1. LINE / LWPOLYLINE 세그먼트 추출
2. 축정렬(H/V) + 최소 길이
3. 직교 방향으로 **벽 두께 대역(기본 50–420 mm)** 안의 평행 쌍 + 길이 방향 overlap
4. 평행선이 너무 많으면(≥5) 계단 해칭으로 보고 제외
5. 닫힌 사각 ≤ 3.5 m(기둥·가구) · 짧은 다변 폴리(조경·해칭) 제외
6. ARC/CIRCLE은 벽이 아님
7. 폴리라인은 벽 비율 ≥ 75%일 때만 통째 WALL

## 파라미터

| 이름 | 기본 | 의미 |
|------|------|------|
| `min_len_mm` | 500 | 벽 후보 세그먼트 최소 길이 |
| `thick_min_mm` | 50 | 이중선 최소 간격 |
| `thick_max_mm` | 420 | 이중선 최대 간격 |
| `entity_wall_ratio` | 0.75 | 폴리라인 통째 벽 승격 |
| `furniture_box_max_mm` | 3500 | 이하 닫힌 박스 = 비가중 |
| `short_pair_max_mm` | 2800 | 양쪽 모두 짧으면 가구 변으로 제외 |

화장실 칸막이·코어 벽은 잡혀야 하고, 문 스윙·기둥·가구·계단 트레드 해칭은 빠져야 한다.  
파일럿 PNG에서 과검출/미검출이 보이면 위 값을 조정한다.

## devider와의 관계

| 단계 | 스킬 | 산출 |
|------|------|------|
| 층 추출 | `drawing-devider` | `$ARTIFACTS_DIR/<id>/floors/<F>/floor_original.dxf` |
| 벽 | `drawing-walldetector` | `$ARTIFACTS_DIR/<id>/floors/<F>/floor_wall_original.dxf` |

타일 `parts/` 분할은 기본 파이프라인에서 쓰지 않는다.
