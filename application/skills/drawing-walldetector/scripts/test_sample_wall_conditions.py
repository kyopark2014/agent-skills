#!/usr/bin/env python3
"""샘플 벽 조건: 정규좌표, 군집, common 에 없는 짧은 벽만 조건이 되는지."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import ezdxf

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib_wall_samples import (  # noqa: E402
    collect_conditions,
    measure_pairs_in_box,
    norm_bbox_to_mm,
    parse_observations,
    pick_sample_windows,
    render_sample_png,
    segments_of,
)
from lib_walls import (  # noqa: E402
    Seg,
    append_condition_overrides,
    detect_wall_keys,
    load_wall_conditions,
)


def _seg(x0, y0, x1, y1, entity_idx: int) -> Seg:
    return Seg(x0, y0, x1, y1, entity_idx, 0)


def test_norm_bbox_y_down() -> None:
    window = (0.0, 0.0, 1000.0, 500.0)
    full = norm_bbox_to_mm([0.0, 0.0, 1.0, 1.0], window)
    assert full == (0.0, 0.0, 1000.0, 500.0)
    top = norm_bbox_to_mm([0.0, 0.0, 0.5, 0.5], window)
    assert top == (0.0, 250.0, 500.0, 500.0)


def test_short_wall_becomes_narrow_condition() -> None:
    segs = [
        _seg(0, 0, 800, 0, 0),
        _seg(0, 50, 800, 50, 1),
        _seg(0, 5000, 4000, 5000, 2),
        _seg(0, 5200, 4000, 5200, 3),
    ]
    keys = detect_wall_keys(segs, conditions=load_wall_conditions(None))
    assert (2, 0) in keys and (3, 0) in keys
    assert (0, 0) not in keys and (1, 0) not in keys

    short = measure_pairs_in_box(segs, (-50, -50, 900, 120), wall_keys=keys)
    assert len(short) == 1
    assert short[0]["covered_by_common"] is False
    assert abs(short[0]["gap_mm"] - 50) < 0.1
    assert abs(short[0]["shorter_length_mm"] - 800) < 0.1

    long = measure_pairs_in_box(segs, (-50, 4900, 4100, 5300), wall_keys=keys)
    assert long and long[0]["covered_by_common"] is True

    samples = [
        {
            "id": "sample_01",
            "bbox_mm": {"xmin": -100, "ymin": -100, "xmax": 4500, "ymax": 5500},
            "png_size": {"width": 1000, "height": 1000},
        }
    ]
    # 짧은 벽만 가리킨다. 긴 벽(y=5000)은 박스 밖이다.
    boxes = {"sample_01": [[0.0, 0.95, 0.25, 1.0]]}
    # window y -100..5500, image y down. y=0..50 is near the bottom.
    # ny = (ymax - y) / span. y=0 -> (5500-0)/5600 ≈ 0.98. y=50 -> 0.97.
    collected = collect_conditions(segs, samples, boxes, wall_keys=keys)
    assert len(collected["conditions"]) == 1
    cond = collected["conditions"][0]
    gap = cond["candidate"]["gap_mm"]
    assert gap["min"] <= 50 <= gap["max"]
    assert cond["candidate"]["min_length_mm"] <= 800 <= cond["candidate"]["max_length_mm"]
    assert cond["candidate"]["max_length_mm"] < 2000

    profiles = append_condition_overrides(load_wall_conditions(None), collected["conditions"])
    marked = detect_wall_keys(segs, conditions=profiles)
    assert (0, 0) in marked and (1, 0) in marked
    assert (2, 0) in marked and (3, 0) in marked


def test_parse_observations_pixels() -> None:
    samples = [{"id": "sample_01", "png_size": {"width": 1000, "height": 500}}]
    parsed = parse_observations(
        {"samples": [{"id": "sample_01", "walls": [{"bbox": [100, 50, 200, 400]}]}]},
        samples,
    )
    assert parsed["sample_01"] == [[0.1, 0.1, 0.2, 0.8]]


def test_two_middle_samples_and_png_axis() -> None:
    bounds = (0.0, 0.0, 20000.0, 16000.0)
    segs = [
        _seg(8000, 8000, 8800, 8000, 0),
        _seg(8000, 8050, 8800, 8050, 1),
        _seg(2000, 9000, 6000, 9000, 2),
        _seg(2000, 9200, 6000, 9200, 3),
    ]
    windows = pick_sample_windows(segs, bounds, count=2)
    assert len(windows) == 2
    assert windows[0]["id"] == "sample_01"
    centers = []
    for window in windows:
        box = window["bbox_mm"]
        centers.append(((box["xmin"] + box["xmax"]) / 2, (box["ymin"] + box["ymax"]) / 2))
    assert abs(centers[0][0] - centers[1][0]) > 2000 or abs(centers[0][1] - centers[1][1]) > 2000

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_line((100, 900), (900, 900))
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "sample.png"
        render_sample_png(list(msp), (0.0, 0.0, 1000.0, 1000.0), png, px_width=200)
        from PIL import Image

        image = Image.open(png).convert("L")
        width, height = image.size
        assert abs(width - 200) <= 2
        dark_rows = [y for y in range(height) if any(image.getpixel((x, y)) < 128 for x in range(width))]
        assert dark_rows, "선이 그려지지 않았습니다"
        mean_y = sum(dark_rows) / len(dark_rows)
        # y=900 은 위쪽에 가깝다. 이미지 y 는 아래 방향이라 평균은 높이의 20% 안쪽.
        assert mean_y < height * 0.25, f"y축이 뒤집혔거나 여백이 있습니다: mean_y={mean_y} h={height}"


def test_cli_collects_short_wall() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        art = Path(tmp) / "demo"
        floor = art / "floors" / "1F"
        floor.mkdir(parents=True)
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((8000, 8000), (8800, 8000))
        msp.add_line((8000, 8050), (8800, 8050))
        msp.add_line((2000, 9000), (6000, 9000))
        msp.add_line((2000, 9200), (6000, 9200))
        for index in range(6):
            y = 7000 + index * 400
            msp.add_line((6000, y), (14000, y))
        doc.saveas(floor / "floor_original.dxf")
        meta = {"bbox_mm": {"xmin": 0, "ymin": 0, "xmax": 20000, "ymax": 16000}}
        (floor / "floor_original_meta.json").write_text(json.dumps(meta), encoding="utf-8")

        import subprocess

        script = _SCRIPTS / "sample_wall_conditions.py"
        prep = subprocess.run(
            [sys.executable, str(script), "--artifacts", str(art), "--floor", "1F", "--prepare-only"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert prep.returncode == 0, prep.stderr
        samples = json.loads((floor / "wall_samples" / "samples.json").read_text(encoding="utf-8"))
        host = None
        for sample in samples["samples"]:
            box = sample["bbox_mm"]
            if box["xmin"] <= 8400 <= box["xmax"] and box["ymin"] <= 8025 <= box["ymax"]:
                host = sample
                break
        assert host is not None, samples["samples"]
        box = host["bbox_mm"]
        span_x = box["xmax"] - box["xmin"]
        span_y = box["ymax"] - box["ymin"]

        def nx(x: float) -> float:
            return (x - box["xmin"]) / span_x

        def ny(y: float) -> float:
            return (box["ymax"] - y) / span_y

        observations = {
            "samples": [
                {
                    "id": host["id"],
                    "walls": [{"bbox": [nx(7900), ny(8120), nx(8900), ny(7920)]}],
                },
                {"id": "sample_02" if host["id"] == "sample_01" else "sample_01", "walls": []},
            ]
        }
        obs_path = floor / "wall_samples" / "observations.json"
        obs_path.write_text(json.dumps(observations), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(script), "--artifacts", str(art), "--floor", "1F", "--observations", str(obs_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr + done.stdout
        saved = json.loads((floor / "wall_samples" / "wall_conditions.json").read_text(encoding="utf-8"))
        assert saved["conditions"], saved
        cond = saved["conditions"][0]
        assert cond["candidate"]["gap_mm"]["min"] <= 50 <= cond["candidate"]["gap_mm"]["max"]
        assert cond["candidate"]["max_length_mm"] < 1500


if __name__ == "__main__":
    test_norm_bbox_y_down()
    test_parse_observations_pixels()
    test_short_wall_becomes_narrow_condition()
    test_two_middle_samples_and_png_axis()
    test_cli_collects_short_wall()
    print("ok")
