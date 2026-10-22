"""Single orchestrator for wall corrections.

Order, protect/demote priority, and post-passes live only here.
Heuristic implementations are in ``correct._rules``.

NOTE: Do not use ``from correct._rules import *`` — star-import skips
leading-underscore names (``_entities``, ``_paint_layer``, …), which
raises NameError at runtime. Import every symbol explicitly, like
``correct.primitives``.
"""

from __future__ import annotations

from typing import Any

from ezdxf.document import Drawing

from correct._rules import (
    _dxftype, _entities, _fold_class_layers, _is_stair_nosing_seg, _is_stair_tread_seg,
    _lwpoly_axis_square_box, _near_seat_door_wall, _paint_layer, AxisSeg, BASE_COLOR,
    BASE_LAYER, WALL_COLOR, WALL_LAYER, add_closed_door_wall_lines,
    add_corner_closed_door_walls, add_paired_swing_walls, add_swing_closed_chords,
    add_swing_leaf_walls, close_leaking_wall_ends, correct_band_leaf_doors,
    correct_capped_leaf_doors, correct_control_room_bottom_door,
    correct_door_leaves_and_flanks, correct_filled_opening_doors,
    correct_header_slat_doors, correct_hinge_panel_doors, correct_inline_swing_doors,
    correct_inwall_swing_doors, correct_leaf_span_door, correct_locker_partition,
    correct_louver_doors, correct_narrow_face_doors, correct_offset_hinge_leaves,
    correct_offset_swing_opening, correct_panel_face_doors, correct_radius_jamb_doors,
    correct_return_sided_doors, correct_room_header_doors,
    correct_runthrough_frame_doors, correct_split_face_doors, correct_split_wall_door,
    correct_tall_opening_doors, correct_walls_around_doors, correct_x_block_doors,
    demote_bay_panel_sills, demote_bed_ward_interiors, demote_closed_furniture_boxes,
    demote_elevator_door_back_faces, demote_fitness_equipment, demote_hospital_beds,
    demote_in_bboxes, demote_landscape_walls, demote_meeting_room_interiors,
    demote_seat_cushion_line_boxes,
    demote_open_hall_center_walls, demote_parallel_grid_axis_walls,
    demote_pictogram_columns, demote_serving_counter_walls,
    demote_swing_hinge_door_leaves, demote_thin_swing_leaf_lines, door_side_wall_ids,
    filter_promote_away_from_elevator_doors, filter_promote_away_from_grid_axes,
    filter_promote_away_from_open_halls, find_demote_wall_entities,
    find_hbeam_column_entities, find_line_h_symbol_column_entities,
    find_promote_segments, finish_pocket_sliding_doors,
    iter_axis_segs, mark_doors_and_columns, open_serving_corner_door,
    open_waiting_room_top_door, promote_aligned_door_mates, promote_aligned_wall_gaps,
    promote_angled_door_flanks, promote_angled_end_door_jambs,
    promote_bathroom_label_walls, promote_bay_window_outlines,
    promote_orthogonal_bay_windows,
    promote_bottom_column_run, promote_butt_partitions, promote_capped_triple_walls,
    promote_capped_windows, promote_closed_square_columns,
    promote_concentric_double_square_columns, promote_round_columns,
    find_round_column_rings,
    promote_collinear_room_walls, promote_collinear_swing_sides,
    promote_collinear_wall_continuations, promote_column_adjacent_wall_pairs,
    promote_control_booth_bottom, promote_corner_wall_squares,
    promote_corridor_door_flanks, promote_corridor_walls, promote_crossed_x_walls,
    promote_curved_corner_walls, promote_diagonal_hatch_walls,
    promote_diagonal_strip_windows,
    promote_diagonal_wall_turns,     promote_door_opening_flanks,
    promote_door_side_corner_runs, promote_door_side_doubles,
    promote_wall_between_adjacent_doors,
    promote_double_sash_windows, promote_elevator_enclosure_walls,
    promote_end_wall_columns, promote_equipment_store_walls,
    promote_exec_meeting_right, promote_grid_door_flanks, promote_h_symbol_columns,
    promote_hall_beam_span, promote_hbeam_columns, promote_hbeam_sleeves,
    promote_in_bboxes, promote_leaf_jambs, promote_leaf_side_walls,
    promote_line_sash_windows, promote_line_swing_door_jambs,
    promote_locker_floor_walls, promote_marked_door_flanks,
    promote_mullion_diagonal_walls, promote_mullion_strip_windows,
    promote_nonarea_enclosures, promote_office_label_walls,
    promote_one_sided_swing_gaps, promote_opposed_door_sides,
    promote_outside_band_face, promote_partition_extensions,
    promote_projection_junctions, promote_return_door_sides,
    promote_room_corner_returns, promote_room_door_sides, promote_room_row_dividers,
    promote_sanitary_fixtures, promote_service_core_walls,
    promote_serving_end_door_flanks, promote_shifted_corner_faces,
    promote_short_connected_doubles, promote_slanted_double_walls,
    promote_small_meeting_bottom, promote_stacked_room_side_wall,
    promote_stage_enclosure_walls, promote_stair_enclosure_walls,
    promote_stair_eps_party_wall, promote_stair_label_walls, promote_strip_windows,
    promote_swing_attached_doubles, promote_swing_opposite_walls,
    promote_swing_side_walls, promote_tbd_beam_span, promote_thick_wall_mates,
    promote_triple_diagonal_walls, promote_vision_bathrooms,
    promote_vision_office_walls, promote_vision_outer_walls,
    promote_waiting_store_wall, promote_wall_end_returns,
    promote_wall_face_continuations, promote_wall_mate_faces,
    promote_wall_square_columns, promote_wash_room_edges, promote_x_cover_squares,
    promote_zigzag_door_sides, protect_corridor_wall_entities,
    protect_elevator_enclosure_entities, protect_stair_enclosure_entities,
    remove_swing_squares, review_demote_keep_ids, review_demote_ids,
    separate_arrow_sliding_doors,
    separate_diagonal_observation_windows, separate_diagonal_pocket_face_doors,
    separate_diagonal_swing_doors, separate_locker_entrance_doors,
    separate_partial_arc_radial_swing_doors,
    separate_lr_arrow_sliding_doors, separate_observation_windows,
    separate_pocket_face_doors, separate_short_staggered_doors,
    separate_side_pocket_sliding_doors,
    separate_inwall_line_sliding_doors,
    separate_sliding_door_panels, separate_stacked_sliding_panels,
    separate_stepped_sliding_doors, separate_track_sliding_doors,
    separate_wide_staggered_doors
)

def apply_corrections(
    doc: Drawing,
    *,
    review: dict[str, Any] | None = None,
    do_gap_promote: bool = True,
    do_short_demote: bool = False,
    do_box_demote: bool = True,
    do_corridor_promote: bool = True,
    do_open_hall_demote: bool = True,
    do_stair_promote: bool = True,
    do_elevator_promote: bool = True,
    do_column_promote: bool = True,
) -> dict[str, Any]:
    """In-place modify doc modelspace WALL layer. Returns stats."""
    msp = doc.modelspace()
    _fold_class_layers(msp)
    review = review or {}
    # 70 mm: 엘리베이터 문 어깨(리턴)·짧은 문틀까지 후보에 포함
    # (기본 500 mm면 door_return 250 mm 등이 통째로 빠짐)
    segs = iter_axis_segs(msp, min_len_mm=70.0)

    hbeam_ids: set[int] = set()
    if do_column_promote:
        hbeam_ids = find_hbeam_column_entities(msp)
    # 휠체어 표식 등. 이미 WALL인 심볼은 기둥 protect에 넣지 않고 demote한다.
    pictogram_demote = demote_pictogram_columns(msp)
    hbeam_ids -= pictogram_demote

    # promote 후보를 demote 전에 확정 (넓은 demote_bbox가 이웃 WALL을
    # 지우면 갭/연속방 승격 맥락이 사라져 한 칸만 회색으로 남는 문제 방지)
    promote: list[AxisSeg] = []
    if do_gap_promote:
        promote.extend(find_promote_segments(segs))
    if do_corridor_promote:
        promote.extend(promote_corridor_walls(segs))
        promote.extend(promote_corridor_door_flanks(segs))
    promote.extend(promote_collinear_room_walls(segs))
    promote.extend(promote_butt_partitions(segs))
    promote.extend(promote_room_corner_returns(segs))
    promote.extend(promote_wall_mate_faces(segs))
    n_stair_promote = 0
    if do_stair_promote:
        stair_prom = promote_stair_enclosure_walls(msp, segs)
        n_stair_promote = len(stair_prom)
        promote.extend(stair_prom)
    n_elev_promote = 0
    if do_elevator_promote:
        elev_prom = promote_elevator_enclosure_walls(msp, segs)
        n_elev_promote = len(elev_prom)
        promote.extend(elev_prom)
    promote.extend(
        promote_in_bboxes(segs, review.get("promote_bboxes") or [])
    )
    promote.extend(
        promote_room_row_dividers(segs, review.get("promote_bboxes") or [])
    )
    # 강당 중앙 통로·보이드를 WALL로 승격하지 않음
    if do_open_hall_demote:
        promote = filter_promote_away_from_open_halls(promote, msp, segs)
        promote.extend(promote_stage_enclosure_walls(msp, segs))
    # 엘리베이터 문·후면은 승격 금지 (측벽만 벽)
    if do_elevator_promote:
        promote = filter_promote_away_from_elevator_doors(promote, msp)
    # 기둥 축 격자(CEN 장축 여러 줄)는 승격 금지
    promote = filter_promote_away_from_grid_axes(promote, segs)
    # 운동기구·정원/조경은 승격 금지
    if do_box_demote:
        furn_ids = demote_fitness_equipment(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_meeting_room_interiors(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_seat_cushion_line_boxes(
            msp, exclude_ids=hbeam_ids, layers=(WALL_LAYER, BASE_LAYER)
        )
        furn_ids |= demote_bed_ward_interiors(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_hospital_beds(msp, exclude_ids=hbeam_ids)
        furn_ids |= demote_landscape_walls(msp, segs, exclude_ids=hbeam_ids)
        furn_ids |= demote_serving_counter_walls(msp, segs, exclude_ids=hbeam_ids)
        promote = [s for s in promote if id(s.entity) not in furn_ids]

    open_hall_demote: set[int] = set()
    if do_open_hall_demote:
        open_hall_demote = demote_open_hall_center_walls(msp, segs)

    grid_axis_demote = demote_parallel_grid_axis_walls(
        segs, exclude_ids=hbeam_ids
    )

    elev_door_demote: set[int] = set()
    if do_elevator_promote:
        elev_door_demote = demote_elevator_door_back_faces(msp, segs)

    protect_ids = protect_corridor_wall_entities(
        segs, exclude_ids=open_hall_demote
    )
    if do_stair_promote:
        protect_ids |= protect_stair_enclosure_entities(msp, segs)
    if do_elevator_promote:
        protect_ids |= protect_elevator_enclosure_entities(msp, segs)
    protect_ids |= hbeam_ids

    demote_ids: set[int] = set()
    if do_short_demote:
        demote_ids |= find_demote_wall_entities(segs)
    furniture_box_demote: set[int] = set()
    fitness_demote: set[int] = set()
    landscape_demote: set[int] = set()
    if do_box_demote:
        furniture_box_demote = demote_closed_furniture_boxes(msp, exclude_ids=hbeam_ids)
        fitness_demote = demote_fitness_equipment(msp, segs, exclude_ids=hbeam_ids)
        fitness_demote |= demote_meeting_room_interiors(msp, segs, exclude_ids=hbeam_ids)
        fitness_demote |= demote_seat_cushion_line_boxes(msp, exclude_ids=hbeam_ids)
        fitness_demote |= demote_bed_ward_interiors(msp, segs, exclude_ids=hbeam_ids)
        fitness_demote |= demote_hospital_beds(msp, exclude_ids=hbeam_ids)
        fitness_demote |= demote_serving_counter_walls(msp, segs, exclude_ids=hbeam_ids)
        landscape_demote = demote_landscape_walls(msp, segs, exclude_ids=hbeam_ids)
        demote_ids |= furniture_box_demote
        demote_ids |= fitness_demote
        demote_ids |= landscape_demote
    # review demote 는 protect 차감 대상이 아니다.
    # 과대 bbox 는 normalize_bbox_list 에서 이미 skip 되므로,
    # 남은 박스는 Vision 명시 판정 → 복도·계단·엘리베이터·H-Beam protect 보다 우선.
    # 침대 demote는 평행 이중·윤곽만. 가구는 창문 keep에 막히지 않음. 그 외는 문 양옆·창문·기둥 유지.
    review_boxes = review.get("demote_bboxes") or []
    review_demote = review_demote_ids(msp, review_boxes, column_ids=hbeam_ids)
    door_sides = door_side_wall_ids(msp, review_boxes)
    n_door_side = 0
    for entity in _entities(msp):
        if id(entity) in door_sides and _paint_layer(entity, WALL_LAYER, WALL_COLOR):
            n_door_side += 1
    n_protected = len(demote_ids & protect_ids)
    demote_ids -= protect_ids
    demote_ids |= review_demote
    # 오픈홀 중앙·엘리베이터 문/후면·가구·운동기구·정원은 protect보다 우선 demote
    demote_ids |= open_hall_demote
    promote = [
        s
        for s in promote
        if not _is_stair_tread_seg(s, segs)
        and not _is_stair_nosing_seg(s, segs)
        and not _near_seat_door_wall(s.is_v, s.ortho, segs)
    ]
    demote_ids |= pictogram_demote
    demote_ids |= elev_door_demote
    demote_ids |= furniture_box_demote
    demote_ids |= fitness_demote
    demote_ids |= landscape_demote

    n_demoted = 0
    for e in _entities(msp):
        if e.dxf.layer == WALL_LAYER and id(e) in demote_ids:
            msp.delete_entity(e)
            n_demoted += 1

    # 축 격자는 삭제하지 않고 BASE로 되돌린다 (후반 격자 판별에 남겨 둔다).
    n_grid_axis_demoted = 0
    for e in _entities(msp):
        if id(e) in grid_axis_demote and _paint_layer(e, BASE_LAYER, BASE_COLOR):
            n_demoted += 1
            n_grid_axis_demoted += 1

    seen: set[tuple[float, float, float, float]] = set()
    n_promoted = n_door_side
    for s in promote:
        key = (round(s.x0, 1), round(s.y0, 1), round(s.x1, 1), round(s.y1, 1))
        if key in seen:
            continue
        seen.add(key)
        e = s.entity
        # BASE LINE 은 레이어를 바꿔 회색이 남지 않게 한다 (겹친 WALL 추가 금지).
        # LWPOLYLINE 일부만 승격할 때는 새 LINE 을 추가한다.
        if (
            e is not None
            and getattr(e, "dxftype", lambda: "")() == "LINE"
            and getattr(e.dxf, "layer", None) == BASE_LAYER
        ):
            e.dxf.layer = WALL_LAYER
            try:
                e.dxf.color = WALL_COLOR
            except Exception:  # noqa: BLE001
                pass
        else:
            msp.add_line(
                (s.x0, s.y0),
                (s.x1, s.y1),
                dxfattribs={"layer": WALL_LAYER, "color": WALL_COLOR},
            )
        n_promoted += 1

    # promote 로 들어온 홀 중앙선도 제거 (1차 demote 이후 추가분)
    n_open_hall_post = 0
    if do_open_hall_demote:
        segs_after = iter_axis_segs(msp)
        post_demote = demote_open_hall_center_walls(msp, segs_after)
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_demote:
                msp.delete_entity(e)
                n_demoted += 1
                n_open_hall_post += 1

    # promote 로 다시 올라온 기둥 축 격자 → BASE로 되돌림
    n_grid_axis_post = 0
    segs_grid = iter_axis_segs(msp, min_len_mm=70.0)
    post_grid = demote_parallel_grid_axis_walls(segs_grid, exclude_ids=hbeam_ids)
    for e in _entities(msp):
        if id(e) in post_grid and _paint_layer(e, BASE_LAYER, BASE_COLOR):
            n_demoted += 1
            n_grid_axis_post += 1

    # promote 로 다시 올라온 엘리베이터 문/후면 제거
    n_elev_door_post = 0
    if do_elevator_promote:
        segs_after = iter_axis_segs(msp, min_len_mm=70.0)
        post_elev = demote_elevator_door_back_faces(msp, segs_after)
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_elev:
                msp.delete_entity(e)
                n_demoted += 1
                n_elev_door_post += 1

    # promote 로 다시 올라온 운동기구·정원 제거
    n_furniture_post = 0
    if do_box_demote:
        segs_after = iter_axis_segs(msp, min_len_mm=70.0)
        post_furn = demote_closed_furniture_boxes(msp, exclude_ids=hbeam_ids)
        post_furn |= demote_fitness_equipment(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_meeting_room_interiors(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_seat_cushion_line_boxes(msp, exclude_ids=hbeam_ids)
        post_furn |= demote_bed_ward_interiors(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_hospital_beds(msp, exclude_ids=hbeam_ids)
        post_furn |= demote_serving_counter_walls(msp, segs_after, exclude_ids=hbeam_ids)
        post_furn |= demote_landscape_walls(msp, segs_after, exclude_ids=hbeam_ids)
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_furn:
                msp.delete_entity(e)
                n_demoted += 1
                n_furniture_post += 1
    # H-Beam 기둥: demote 이후 BASE→WALL (가구 demote에 안 걸림)
    n_column_promoted = 0
    if do_column_promote:
        n_column_promoted = promote_hbeam_columns(msp)
        n_column_promoted += promote_hbeam_sleeves(msp)
    # 승격으로 다시 빨개진 픽토그램(장애인 표식) 제거
    n_pictogram = len(pictogram_demote)
    post_pic = demote_pictogram_columns(msp)
    for e in _entities(msp):
        if e.dxf.layer == WALL_LAYER and id(e) in post_pic:
            msp.delete_entity(e)
            n_demoted += 1
            if id(e) not in pictogram_demote:
                n_pictogram += 1

    # 문짝은 벽이 아니고, 개구 양옆은 벽. promote 이후에 개구를 끊는다.
    _n_door_cut, _n_door_flank = correct_walls_around_doors(msp)
    # 문 양옆 승격이 배식대 안을 다시 벽으로 만들지 않는다. H-Beam 은 남긴다.
    if do_box_demote:
        segs_serving = iter_axis_segs(msp, min_len_mm=70.0)
        post_serving = demote_serving_counter_walls(
            msp, segs_serving, exclude_ids=hbeam_ids
        )
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_serving:
                msp.delete_entity(e)
                n_demoted += 1

    # 대각선으로만 표시된 문의 양옆. 배식대 demote 뒤에 올려 경계벽이 다시 지워지지 않게 한다.
    n_promoted += promote_marked_door_flanks(msp)
    n_promoted += promote_grid_door_flanks(msp)
    n_promoted += promote_serving_end_door_flanks(msp)
    n_promoted += promote_stacked_room_side_wall(msp)
    n_promoted += open_serving_corner_door(msp)
    n_promoted += promote_control_booth_bottom(msp)
    n_promoted += promote_stair_eps_party_wall(msp)
    n_promoted += promote_waiting_store_wall(msp)
    n_promoted += promote_hall_beam_span(msp)
    n_promoted += promote_tbd_beam_span(msp)
    n_promoted += promote_bottom_column_run(msp)
    n_promoted += open_waiting_room_top_door(msp)
    n_promoted += promote_wash_room_edges(msp)
    n_promoted += promote_equipment_store_walls(msp)
    n_promoted += promote_small_meeting_bottom(msp)
    n_promoted += promote_exec_meeting_right(msp)
    # 벽면 정사각 기둥. H-Beam 집계(`n_column_promoted`)와 분리한다.
    n_wall_square_promoted = promote_wall_square_columns(msp)
    # 선 네 개가 아니라 닫힌 폴리라인만 있는 빈 정사각(클럽하우스 주방 모서리).
    n_wall_square_promoted += promote_closed_square_columns(msp)
    # 바깥·안쪽 동심 사각(평행 이중선) 기둥. 내부 선 때문에 벽면 정사각 규칙이 건너뛴다.
    n_wall_square_promoted += promote_concentric_double_square_columns(msp)
    # 동심 원/호(평행 이중 원) 둥근 기둥.
    n_wall_square_promoted += promote_round_columns(msp)
    n_promoted += n_wall_square_promoted
    # 조정실 아래 문짝이 문 옆 승격에 같이 올라온 것을 되돌린다.
    n_ctrl_demote, n_ctrl_promote = correct_control_room_bottom_door(msp)
    n_demoted += n_ctrl_demote
    n_promoted += n_ctrl_promote
    n_leaf_demote, n_leaf_promote = correct_leaf_span_door(msp)
    n_demoted += n_leaf_demote
    n_promoted += n_leaf_promote
    n_ang_demote, n_ang_promote = promote_angled_door_flanks(msp)
    n_demoted += n_ang_demote
    n_promoted += n_ang_promote
    n_split_demote, n_split_promote = correct_split_wall_door(msp)
    n_demoted += n_split_demote
    n_promoted += n_split_promote
    n_side_demote, n_side_promote = promote_room_door_sides(msp)
    n_demoted += n_side_demote
    n_promoted += n_side_promote
    n_swing_demote, n_swing_promote = correct_inwall_swing_doors(msp)
    n_demoted += n_swing_demote
    n_promoted += n_swing_promote
    n_ret_demote, n_ret_promote = correct_return_sided_doors(msp)
    n_demoted += n_ret_demote
    n_promoted += n_ret_promote
    n_band_demote, n_band_promote = correct_band_leaf_doors(msp)
    n_demoted += n_band_demote
    n_promoted += n_band_promote
    n_narrow_demote, n_narrow_promote = correct_narrow_face_doors(msp)
    n_demoted += n_narrow_demote
    n_promoted += n_narrow_promote
    n_fill_demote, n_fill_promote = correct_filled_opening_doors(msp)
    n_demoted += n_fill_demote
    n_promoted += n_fill_promote
    n_jamb_demote, n_jamb_promote = promote_angled_end_door_jambs(msp)
    n_demoted += n_jamb_demote
    n_promoted += n_jamb_promote
    n_off_demote, n_off_promote = correct_offset_swing_opening(msp)
    n_demoted += n_off_demote
    n_promoted += n_off_promote
    n_run_demote, n_run_promote = correct_runthrough_frame_doors(msp)
    n_demoted += n_run_demote
    n_promoted += n_run_promote
    n_header_demote, n_header_promote = correct_room_header_doors(msp)
    n_demoted += n_header_demote
    n_promoted += n_header_promote
    n_split_demote, n_split_promote = correct_split_face_doors(msp)
    n_demoted += n_split_demote
    n_promoted += n_split_promote
    n_hinge_demote, n_hinge_promote = correct_hinge_panel_doors(msp)
    n_demoted += n_hinge_demote
    n_promoted += n_hinge_promote
    n_jamb_door_demote, n_jamb_door_promote = correct_radius_jamb_doors(msp)
    n_demoted += n_jamb_door_demote
    n_promoted += n_jamb_door_promote
    n_promoted += promote_partition_extensions(msp)
    n_promoted += promote_one_sided_swing_gaps(msp)
    n_opposed_demote, n_opposed_promote = promote_opposed_door_sides(msp)
    n_demoted += n_opposed_demote
    n_promoted += n_opposed_promote
    n_promoted += promote_aligned_wall_gaps(msp)
    n_return_demote, n_return_promote = promote_return_door_sides(msp)
    n_demoted += n_return_demote
    n_promoted += n_return_promote
    n_promoted += promote_corner_wall_squares(msp)
    n_face_demote, n_face_promote = promote_collinear_swing_sides(msp)
    n_demoted += n_face_demote
    n_promoted += n_face_promote
    n_promoted += promote_wall_face_continuations(msp)
    n_hinge_leaf_demote, n_hinge_leaf_promote = correct_offset_hinge_leaves(msp)
    n_demoted += n_hinge_leaf_demote
    n_promoted += n_hinge_leaf_promote
    n_locker_demote, n_locker_promote = correct_locker_partition(msp)
    n_demoted += n_locker_demote
    n_promoted += n_locker_promote
    n_panel_demote, n_panel_promote = correct_panel_face_doors(msp)
    n_demoted += n_panel_demote
    n_promoted += n_panel_promote
    n_promoted += promote_locker_floor_walls(msp)
    n_x_demote, n_x_promote = correct_x_block_doors(msp)
    n_demoted += n_x_demote
    n_promoted += n_x_promote
    n_end_col = promote_end_wall_columns(msp)
    n_wall_square_promoted += n_end_col
    n_promoted += n_end_col
    n_demoted += demote_bay_panel_sills(msp)
    n_cap_demote, n_cap_promote = correct_capped_leaf_doors(msp)
    n_demoted += n_cap_demote
    n_promoted += n_cap_promote
    n_zig_demote, n_zig_promote = promote_zigzag_door_sides(msp)
    n_demoted += n_zig_demote
    n_promoted += n_zig_promote
    n_promoted += promote_outside_band_face(msp)
    n_tall_demote, n_tall_promote = correct_tall_opening_doors(msp)
    n_demoted += n_tall_demote
    n_promoted += n_tall_promote
    n_louver_demote, n_louver_promote = correct_louver_doors(msp)
    n_demoted += n_louver_demote
    n_promoted += n_louver_promote
    n_header_demote, n_header_promote = correct_header_slat_doors(msp)
    n_demoted += n_header_demote
    n_promoted += n_header_promote

    # 가장 중요한 두 원칙. 다른 보정 뒤에 적용해 문 개구와 기둥 주변이 남게 한다.
    # 1) 문은 내리고 양옆은 벽. 2) 기둥은 벽, 기둥에 닿는 이중 평행선도 벽.
    n_h_sym, h_boxes = promote_h_symbol_columns(msp)
    n_column_promoted += n_h_sym
    n_promoted += n_h_sym
    _line_h_ids, line_h_boxes = find_line_h_symbol_column_entities(msp)
    square_boxes: list[tuple[float, float, float, float]] = []
    for ent in _entities(msp):
        if _dxftype(ent) != "LWPOLYLINE":
            continue
        if getattr(ent.dxf, "layer", None) != WALL_LAYER:
            continue
        box = _lwpoly_axis_square_box(ent, min_mm=400.0, max_mm=1500.0)
        if box is None:
            continue
        square_boxes.append(box)
    round_boxes = [
        (cx - r_out, cy - r_out, cx + r_out, cy + r_out)
        for cx, cy, _ri, r_out, _ents in find_round_column_rings(msp)
    ]
    n_promoted += promote_column_adjacent_wall_pairs(
        msp, h_boxes + square_boxes + line_h_boxes + round_boxes
    )
    n_leaf_side_demote, n_leaf_side_promote = correct_door_leaves_and_flanks(msp)
    n_demoted += n_leaf_side_demote
    n_promoted += n_leaf_side_promote
    n_promoted += promote_swing_opposite_walls(msp)
    # 30° 호+방사 문짝(1/4원 아님) — 표준 스윙 닫기 전에 DOOR·개구를 잡는다.
    n_partial_demote, n_partial_promote = separate_partial_arc_radial_swing_doors(msp)
    n_demoted += n_partial_demote
    n_promoted += n_partial_promote
    n_promoted += add_closed_door_wall_lines(msp)
    n_demoted += remove_swing_squares(msp)
    n_promoted += promote_swing_attached_doubles(msp)
    n_promoted += separate_sliding_door_panels(msp)
    n_promoted += promote_line_swing_door_jambs(msp)
    n_promoted += promote_thick_wall_mates(msp)
    # 벽이 닫히지 않아 면적이 밖으로 새면, 그 벽 끝을 따라 닫는다.
    n_leak_closed = close_leaking_wall_ends(msp)
    n_promoted += n_leak_closed
    # 앞선 승격이 여닫이문 잎을 다시 벽으로 올린다. 잎만 되돌린다.
    n_demoted += demote_thin_swing_leaf_lines(msp)
    n_promoted += promote_bay_window_outlines(msp)
    n_inline_demote, n_inline_promote = correct_inline_swing_doors(msp)
    n_demoted += n_inline_demote
    n_promoted += n_inline_promote
    n_promoted += promote_leaf_side_walls(msp)
    n_promoted += promote_aligned_door_mates(msp)
    n_promoted += promote_leaf_jambs(msp)
    n_promoted += promote_swing_side_walls(msp)
    n_promoted += add_swing_leaf_walls(msp)
    n_promoted += add_corner_closed_door_walls(msp)
    n_step_demote, n_step_promote = separate_stepped_sliding_doors(msp)
    n_demoted += n_step_demote
    n_promoted += n_step_promote
    n_pocket_demote, n_pocket_promote = finish_pocket_sliding_doors(msp)
    n_demoted += n_pocket_demote
    n_promoted += n_pocket_promote
    # 벽선이 개구를 지나 한 줄로 남아 있으면 앞선 닫기에서 끊김을 못 본다.
    # 선이 나뉜 뒤에 그 구간을 다시 닫는다.
    n_promoted += add_closed_door_wall_lines(msp)
    n_promoted += promote_projection_junctions(msp)
    n_promoted += promote_shifted_corner_faces(msp)
    n_promoted += promote_short_connected_doubles(msp)
    # 앞선 승격이 스윙 힌지 문짝을 다시 올려도, 면적 경계로 남지 않게 마지막에 내린다.
    # 닫힌 현은 그 다음에 추가해서 문짝 내리기가 지우지 않게 한다.
    n_demoted += demote_swing_hinge_door_leaves(msp)
    n_promoted += add_swing_closed_chords(msp)
    # 턱이 있는 미닫이는 마지막에 문짝만 내리고, 양끝 벽은 남긴다.
    n_track_demote, n_track_promote = separate_track_sliding_doors(msp)
    n_demoted += n_track_demote
    n_promoted += n_track_promote
    n_stack_demote, n_stack_promote = separate_stacked_sliding_panels(msp)
    n_demoted += n_stack_demote
    n_promoted += n_stack_promote
    n_diag_demote, n_diag_promote = separate_diagonal_swing_doors(msp)
    n_demoted += n_diag_demote
    n_promoted += n_diag_promote
    n_pocket_face_demote, n_pocket_face_promote = separate_pocket_face_doors(msp)
    n_demoted += n_pocket_face_demote
    n_promoted += n_pocket_face_promote
    n_side_pocket_demote, n_side_pocket_promote = separate_side_pocket_sliding_doors(msp)
    n_demoted += n_side_pocket_demote
    n_promoted += n_side_pocket_promote
    # 후반 promote 전에 한 번 DOOR로 두어 다시 WALL로 올라가지 않게 한다.
    n_inwall_demote, n_inwall_promote = separate_inwall_line_sliding_doors(msp)
    n_demoted += n_inwall_demote
    n_promoted += n_inwall_promote
    n_diag_pocket_demote, n_diag_pocket_promote = separate_diagonal_pocket_face_doors(msp)
    n_demoted += n_diag_pocket_demote
    n_promoted += n_diag_pocket_promote
    n_arrow_demote, n_arrow_promote = separate_arrow_sliding_doors(msp)
    n_demoted += n_arrow_demote
    n_promoted += n_arrow_promote
    n_lr_arrow_demote, n_lr_arrow_promote = separate_lr_arrow_sliding_doors(msp)
    n_demoted += n_lr_arrow_demote
    n_promoted += n_lr_arrow_promote
    n_short_demote, n_short_promote = separate_short_staggered_doors(msp)
    n_demoted += n_short_demote
    n_promoted += n_short_promote
    n_locker_demote, n_locker_promote = separate_locker_entrance_doors(msp)
    n_demoted += n_locker_demote
    n_promoted += n_locker_promote
    n_wide_demote, n_wide_promote = separate_wide_staggered_doors(msp)
    n_demoted += n_wide_demote
    n_promoted += n_wide_promote
    _n_obs_window, n_obs_wall = separate_observation_windows(msp)
    n_promoted += _n_obs_window + n_obs_wall
    _n_diag_window, n_diag_wall = separate_diagonal_observation_windows(msp)
    n_promoted += _n_diag_window + n_diag_wall
    n_cap_window, n_cap_wall = promote_capped_windows(msp)
    n_promoted += n_cap_window + n_cap_wall
    n_sash_window, n_sash_wall = promote_double_sash_windows(msp)
    n_promoted += n_sash_window + n_sash_wall
    n_line_window, n_line_wall = promote_line_sash_windows(msp)
    n_promoted += n_line_window + n_line_wall
    n_strip_window, n_strip_wall = promote_strip_windows(msp)
    n_promoted += n_strip_window + n_strip_wall
    n_diag_strip_window, n_diag_strip_wall = promote_diagonal_strip_windows(msp)
    n_promoted += n_diag_strip_window + n_diag_strip_wall
    n_mullion_window, n_mullion_wall = promote_mullion_strip_windows(msp)
    n_promoted += n_mullion_window + n_mullion_wall
    # 직교 베이: WINDOW 사이 갭의 return·바깥면. 창 레이어 분리 뒤에 올린다.
    n_promoted += promote_orthogonal_bay_windows(msp)
    # 문 가상 닫힘으로 생긴 사각 슬리브(문옆 기둥)도 H/슬리브 규칙으로 올린다.
    if do_column_promote:
        n_column_promoted += promote_hbeam_columns(msp)
    # 문·기둥 레이어 분리는 모든 promote 이후에 한다. 중간에 나누면
    # 후반 promote/H-Beam 잔여가 COLUMN에 남아 밀집 기둥처럼 보인다.
    n_promoted += promote_door_opening_flanks(msp)
    n_promoted += promote_wall_between_adjacent_doors(msp)
    n_promoted += promote_door_side_doubles(msp)
    n_promoted += promote_door_side_corner_runs(msp)
    n_promoted += promote_service_core_walls(msp)
    n_promoted += promote_sanitary_fixtures(msp)
    n_promoted += promote_vision_bathrooms(msp, review.get("promote_bboxes") or [])
    n_promoted += promote_vision_outer_walls(msp, review.get("promote_bboxes") or [])
    n_promoted += promote_vision_office_walls(msp, review.get("promote_bboxes") or [])
    n_promoted += promote_stair_label_walls(msp)
    n_promoted += promote_nonarea_enclosures(msp)
    n_promoted += promote_bathroom_label_walls(msp)
    n_promoted += promote_office_label_walls(msp)
    n_promoted += promote_collinear_wall_continuations(msp)
    n_promoted += promote_diagonal_wall_turns(msp)
    n_promoted += promote_mullion_diagonal_walls(msp)
    n_promoted += promote_capped_triple_walls(msp)
    n_promoted += promote_diagonal_hatch_walls(msp)
    n_promoted += promote_wall_end_returns(msp)
    n_promoted += promote_crossed_x_walls(msp)
    n_promoted += promote_x_cover_squares(msp)
    n_promoted += promote_triple_diagonal_walls(msp)
    n_promoted += promote_slanted_double_walls(msp)
    # 곡선 모서리는 양끝이 WALL에 닿아야 한다. 기울어진 이중선 승격 뒤에 올린다.
    n_promoted += promote_curved_corner_walls(msp)
    # 앞선 문 처리가 가로·세로가 아닌 양개문은 닫지 않는다. 마지막에 힌지 사이를 잇는다.
    n_promoted += add_paired_swing_walls(msp)

    # 후반 promote_* 가 다시 올린 병원 침대·의자/방석·접객실 잔여 WALL도 제거한다.
    if do_box_demote:
        final_beds = demote_hospital_beds(msp, exclude_ids=hbeam_ids)
        segs_final_furn = iter_axis_segs(msp, min_len_mm=70.0)
        final_furn = demote_seat_cushion_line_boxes(msp, exclude_ids=hbeam_ids)
        final_furn |= demote_meeting_room_interiors(
            msp, segs_final_furn, exclude_ids=hbeam_ids
        )
        final_furn |= final_beds
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in final_furn:
                msp.delete_entity(e)
                n_demoted += 1

    # 후반 promote_* 가 다시 올린 Vision demote(가구 등)도 제거한다.
    final_review_boxes = review.get("demote_bboxes") or []
    if final_review_boxes:
        final_review = review_demote_ids(
            msp, final_review_boxes, column_ids=hbeam_ids
        )
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in final_review:
                msp.delete_entity(e)
                n_demoted += 1

    # 침대 bbox 등으로 포셰 외곽이 지워진 뒤에도 사선 채움 띠는 다시 벽으로 둔다.
    n_promoted += promote_diagonal_hatch_walls(msp)

    # 사선 채움 재승격이 침대 매트리스를 다시 WALL로 올리면 한 번 더 demote.
    if do_box_demote:
        post_hatch_beds = demote_hospital_beds(msp, exclude_ids=hbeam_ids)
        for e in _entities(msp):
            if e.dxf.layer == WALL_LAYER and id(e) in post_hatch_beds:
                msp.delete_entity(e)
                n_demoted += 1

    # 후반 promote_* 가 다시 올린 축 격자도 BASE로 되돌린다.
    segs_final = iter_axis_segs(msp, min_len_mm=70.0)
    final_grid = demote_parallel_grid_axis_walls(segs_final, exclude_ids=hbeam_ids)
    for e in _entities(msp):
        if id(e) in final_grid and _paint_layer(e, BASE_LAYER, BASE_COLOR):
            n_demoted += 1
            n_grid_axis_post += 1

    # 후반 demote 가 스윙문 가상 닫힘·이중벽 바깥면을 지울 수 있어 마지막에 다시 잇는다.
    n_promoted += add_closed_door_wall_lines(msp)

    # 후반 promote 가 BASE 이중 문짝을 다시 WALL로 올릴 수 있어, 레이어 분리 직전에 DOOR로 둔다.
    # 김해보음 3F 4인실#3(~1.42 m·양끝 턱)처럼 1.4 m를 넘는 LINE 이중 미닫이도 포함한다.
    n_inwall_demote, n_inwall_promote = separate_inwall_line_sliding_doors(msp)
    n_demoted += n_inwall_demote
    n_promoted += n_inwall_promote

    marked = mark_doors_and_columns(msp)

    n_wall = sum(1 for e in _entities(msp) if e.dxf.layer == WALL_LAYER)
    n_base = sum(1 for e in _entities(msp) if e.dxf.layer == BASE_LAYER)
    return {
        "n_demoted": n_demoted,
        "n_promoted": n_promoted,
        "n_corridor_protected": n_protected,
        "n_open_hall_demoted": len(open_hall_demote) + n_open_hall_post,
        "n_pictogram_demoted": n_pictogram,
        "n_stair_promote_candidates": n_stair_promote,
        "n_elevator_promote_candidates": n_elev_promote,
        "n_elevator_door_demoted": len(elev_door_demote) + n_elev_door_post,
        "n_column_promoted": n_column_promoted,
        "n_wall_square_promoted": n_wall_square_promoted,
        "n_leak_closed": n_leak_closed,
        "n_wall_after": n_wall,
        "n_base": n_base,
        "n_door": marked["n_door"],
        "n_window": marked["n_window"],
        "n_column": marked["n_column"],
        "doors": marked["doors"],
        "windows": marked["windows"],
        "columns": marked["columns"],
        "review_demote_bboxes": len(review.get("demote_bboxes") or []),
        "review_promote_bboxes": len(review.get("promote_bboxes") or []),
    }
