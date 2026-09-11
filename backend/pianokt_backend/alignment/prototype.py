"""Declarations extracted from user-provided test_midi.ipynb cell 3. No notebook execution."""
import json

import warnings

from pathlib import Path

from typing import Any

import mido

import numpy as np

import pandas as pd

import pretty_midi

TIMING_THRESHOLD_MS = 150

INSERTION_ATTACH_WINDOW_SEC = 0.25

REJECT_REASON = {
    -1: "not_required",
     0: "correct",
     1: "missing_note",
     2: "extra_note",
     3: "wrong_pitch_or_substitution",
     4: "too_early",
     5: "too_late",
     6: "multiple_or_other_error",
}

def first_existing_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    """从候选列名中返回 DataFrame 里第一个真实存在的列名。"""
    for column in candidates:
        if column in df.columns:
            return column

    if required:
        raise ValueError(
            f"无法从候选列中找到必需字段：{candidates}\n"
            f"当前 DataFrame 字段为：{list(df.columns)}"
        )

    return None

def sec_to_ms(value: Any) -> int | None:
    """秒转毫秒；空值保持为 None。"""
    if pd.isna(value):
        return None
    return int(round(float(value) * 1000))

def median_ms_from_sec(values: list[Any]) -> int | None:
    """对一组秒值取中位数，然后转换为整数毫秒。"""
    valid_values = [value for value in values if pd.notna(value)]
    if len(valid_values) == 0:
        return None
    return sec_to_ms(np.median(valid_values))

def median_int(values: list[Any]) -> int | None:
    """对一组数值取中位数并四舍五入为整数。"""
    valid_values = [value for value in values if value is not None and pd.notna(value)]
    if len(valid_values) == 0:
        return None
    return int(round(float(np.median(valid_values))))

def empty_record(onset_ms: int | None) -> dict[str, Any]:
    """构造某个时刻中“不需要弹奏”的单手占位记录。"""
    return {
        "reject_reason": -1,
        "timing_offset": None,
        "pitch_offset": None,
        "duration": None,
        "onset_time": onset_ms,
        "pitches": [-1],
    }

def classify_matched_note(
    row: pd.Series,
    ref_pitch_col: str,
    perf_pitch_col: str,
    timing_dev_col: str,
) -> int:
    """判断一条 match note 的 note-level 错误类型。"""
    errors: list[int] = []

    # 音高错误。
    if pd.notna(row.get(ref_pitch_col)) and pd.notna(row.get(perf_pitch_col)):
        pitch_offset = int(round(row[perf_pitch_col] - row[ref_pitch_col]))
        if pitch_offset != 0:
            errors.append(3)

    # 时间错误。
    if pd.notna(row.get(timing_dev_col)):
        timing_ms = float(row[timing_dev_col]) * 1000

        if timing_ms < -TIMING_THRESHOLD_MS:
            errors.append(4)
        elif timing_ms > TIMING_THRESHOLD_MS:
            errors.append(5)

    if len(errors) == 0:
        return 0

    if len(errors) == 1:
        return errors[0]

    return 6

def combine_reject_reasons(reasons: list[int | None]) -> int:
    """把同一只手、同一个 event 中的多个 note-level 错误合并为一个 hand-level 错误。"""
    reasons = [reason for reason in reasons if reason is not None and reason != -1]

    if len(reasons) == 0:
        return -1

    nonzero = [reason for reason in reasons if reason != 0]

    if len(nonzero) == 0:
        return 0

    # 同一只手同一时刻既有 deletion 又有 insertion，视为音高替换。
    if 1 in nonzero and 2 in nonzero:
        other_errors = [reason for reason in nonzero if reason not in (1, 2)]
        if len(other_errors) == 0:
            return 3
        return 6

    unique_errors = sorted(set(nonzero))

    if len(unique_errors) == 1:
        return unique_errors[0]

    return 6

def append_record(events_data: dict[str, Any], hand: str, record: dict[str, Any]) -> None:
    """把一个单手 event 追加到 events_data 对应数组中。"""
    for key in [
        "reject_reason",
        "timing_offset",
        "pitch_offset",
        "duration",
        "onset_time",
        "pitches",
    ]:
        events_data["data"][hand][key].append(record[key])

def parse_midi_song_for_hand_inference(midi_path: str | Path) -> dict[str, Any]:
    """
    把 MIDI 转成与 TypeScript Song 中手部推断所需字段相近的结构。

    返回格式：
    {
        "tracks": {
            track_id: {
                "name": str,
                "instrument": str,
                "program": int,
            },
            ...
        },
        "notes": [
            {"track": track_id, "midiNote": pitch},
            ...
        ]
    }

    注意：这里的 track_id 是 mido.MidiFile.tracks 的 0-based MIDI track 索引。
    alignment DataFrame 中的 ref_track 必须使用同一套编号。
    """
    midi_path = Path(midi_path)
    if not midi_path.exists():
        raise FileNotFoundError(f"找不到 MIDI 文件：{midi_path}")

    midi = mido.MidiFile(str(midi_path))

    tracks: dict[int, dict[str, Any]] = {}
    notes: list[dict[str, int]] = []

    for track_id, midi_track in enumerate(midi.tracks):
        track_name = ""
        program: int | None = None
        has_channel_message = False

        for message in midi_track:
            if message.type == "track_name" and track_name == "":
                track_name = str(message.name)

            if hasattr(message, "channel"):
                has_channel_message = True

            if message.type == "program_change" and program is None:
                program = int(message.program)

            # note_on 且 velocity > 0 才是一个真实 note 起点。
            if message.type == "note_on" and int(message.velocity) > 0:
                notes.append({
                    "track": track_id,
                    "midiNote": int(message.note),
                })

        # MIDI channel 的默认 program 是 0。
        # Tone.js 解析没有显式 program_change 的普通音轨时，也会得到 program 0。
        if program is None and has_channel_message:
            program = 0

        instrument_name = ""
        if program is not None and 0 <= program <= 127:
            instrument_name = pretty_midi.program_to_instrument_name(program)

        tracks[track_id] = {
            "name": track_name,
            "instrument": instrument_name,
            "program": program,
        }

    return {
        "tracks": tracks,
        "notes": notes,
    }

def is_piano(track: dict[str, Any]) -> bool:
    """Python 版本的 TypeScript isPiano。"""
    program = track.get("program")
    if program is None:
        program = -1

    instrument = str(track.get("instrument") or "").lower()
    name = str(track.get("name") or "").lower()

    return (
        "piano" in instrument
        or "piano" in name
        or 0 <= int(program) <= 6
    )

def parser_infer_hands(
    song: dict[str, Any],
    midi_label: str,
) -> dict[str, int | None]:
    """
    根据原曲 MIDI 的 track 推断左右手。

    逻辑顺序：
    1. 先统计真正包含 note 的 track。
    2. 如果只有一个带 note 的 track，则按项目约定：
       - 整个 track 视为右手；
       - 左手设为 None。
    3. 如果有多个带 note 的 track，优先寻找成对命名的左右手 track。
    4. 若没有成对命名，则优先选择前两个带 note 的钢琴 track。
    5. 如果不足两个钢琴 track，则选择前两个带 note 的 track。
    6. 比较两个候选 track 的平均 MIDI pitch：
       - 平均音高较低的是左手；
       - 平均音高较高的是右手。

    注意：
    - “单 track”这里指只有一个真正包含音符的 track。
      纯 tempo / 拍号 / metadata track 不计入左右手 track 数量。
    - 不读取 hand/staff 列；
    - 不使用固定的 pitch < 60 规则。
    """
    tracks: dict[int, dict[str, Any]] = song["tracks"]
    notes: list[dict[str, int]] = song["notes"]

    # 先把每个 track 中的 MIDI pitch 收集起来。
    notes_by_track: dict[int, list[int]] = {
        track_id: [] for track_id in tracks
    }

    for note in notes:
        track_id = int(note["track"])
        if track_id in notes_by_track:
            notes_by_track[track_id].append(int(note["midiNote"]))

    # 只把真正包含 note 的 track 当作可演奏 track。
    note_track_ids = [
        track_id
        for track_id in tracks
        if len(notes_by_track[track_id]) > 0
    ]

    if len(note_track_ids) == 0:
        raise ValueError(
            f"{midi_label} 中没有任何包含 note 的 track，无法生成 events_data。"
        )

    # 新增规则：原曲只有一个可演奏 track 时，全部视为右手。
    if len(note_track_ids) == 1:
        only_track_id = note_track_ids[0]
        # Assigned left-hand-only snapshots must not be relabeled as right hand.
        if str(tracks[only_track_id].get('name','')).strip().lower() in {'left','lh','l.h.','bass'}:
            return {'left': only_track_id, 'right': None}

        warnings.warn(
            f"{midi_label} 只有一个包含 note 的 track（track {only_track_id}）；"
            "按照项目约定，将该 track 全部视为右手，左手记为 not_required。",
            RuntimeWarning,
        )

        return {
            "left": None,
            "right": only_track_id,
        }

    likely_left_names = {"bass", "left", "lh", "l.h."}
    likely_right_names = {"treble", "lead", "rh", "right", "r.h.", "student"}

    likely_left_track_id: int | None = None
    likely_right_track_id: int | None = None

    # 按 MIDI track 原始顺序寻找第一个明确命名的左右手 track。
    for track_id, track in tracks.items():
        track_name = str(track.get("name") or "").strip().lower()

        if (
            likely_left_track_id is None
            and track_name in likely_left_names
            and len(notes_by_track[track_id]) > 0
        ):
            likely_left_track_id = track_id

        if (
            likely_right_track_id is None
            and track_name in likely_right_names
            and len(notes_by_track[track_id]) > 0
        ):
            likely_right_track_id = track_id

    if likely_left_track_id is not None and likely_right_track_id is not None:
        return {
            "left": likely_left_track_id,
            "right": likely_right_track_id,
        }

    # 没有成对名称时，优先寻找带 note 的钢琴 track。
    piano_track_ids = [
        track_id
        for track_id, track in tracks.items()
        if is_piano(track) and len(notes_by_track[track_id]) > 0
    ]

    if len(piano_track_ids) >= 2:
        if len(piano_track_ids) > 2:
            warnings.warn(
                f"{midi_label} 中检测到 {len(piano_track_ids)} 个带音符的钢琴 track；"
                "只选择前两个进行左右手推断。",
                RuntimeWarning,
            )

        candidate_track_ids = piano_track_ids[:2]
    else:
        # 不选择空的 metadata track，只从真正包含 note 的 track 中取前两个。
        candidate_track_ids = note_track_ids[:2]

    if len(candidate_track_ids) < 2:
        # 正常情况下前面的单 track 分支已经处理，这里只是防御性检查。
        raise ValueError(
            f"{midi_label} 中不足两个候选 track，无法根据平均音高划分左右手。"
        )

    track_1, track_2 = candidate_track_ids
    track_1_pitches = notes_by_track[track_1]
    track_2_pitches = notes_by_track[track_2]

    track_1_average_pitch = float(np.mean(track_1_pitches))
    track_2_average_pitch = float(np.mean(track_2_pitches))

    if track_1_average_pitch < track_2_average_pitch:
        return {
            "left": track_1,
            "right": track_2,
        }

    return {
        "left": track_2,
        "right": track_1,
    }

def validate_track_ids(
    df: pd.DataFrame,
    track_col: str,
    valid_track_ids: set[int],
    row_mask: pd.Series,
    label: str,
) -> None:
    """确保需要参与处理的行都有合法 track ID。"""
    relevant_rows = df.loc[row_mask]

    if relevant_rows[track_col].isna().any():
        missing_count = int(relevant_rows[track_col].isna().sum())
        raise ValueError(
            f"{label} 中有 {missing_count} 行缺少 {track_col}。"
            "当前脚本只允许按 MIDI track 划分左右手，因此不能按 pitch 兜底。"
        )

    used_track_ids = set(relevant_rows[track_col].astype(int).tolist())
    unknown_track_ids = sorted(used_track_ids - valid_track_ids)

    if len(unknown_track_ids) > 0:
        raise ValueError(
            f"{label} 的 {track_col} 中出现 MIDI 文件不存在的 track ID："
            f"{unknown_track_ids}。请确认 DataFrame 与 MIDI 使用相同的 0-based track 编号。"
        )

def infer_insertion_hand_from_reference(
    insertion_row: pd.Series,
    reference_context_rows: pd.DataFrame,
    ref_pitch_col: str,
    perf_pitch_col: str,
    ref_track_col: str,
    reference_track_for_hand: dict[str, int | None],
) -> str:
    """
    根据最近的原曲 event 判断 insertion 属于左手还是右手。

    单轨规则：
    - 如果原曲只有右手 track（left_hand 为 None），所有 insertion 也归右手。
    - 如果原曲只有左手 track（理论兼容分支），所有 insertion 归左手。

    双轨判断顺序：
    1. 原曲 event 只有一只手有音符时，归到该手；
    2. 两只手都有音符时，归到音高距离更近的一只手；
    3. 距离相同时，用两手平均音高的中点判断。
    """
    if pd.isna(insertion_row.get(perf_pitch_col)):
        raise ValueError(
            "insertion 行缺少 performance pitch，无法判断其属于左手还是右手。"
        )

    left_track_id = reference_track_for_hand["left_hand"]
    right_track_id = reference_track_for_hand["right_hand"]

    # 单轨原曲：项目约定为全右手，因此额外音也统一归右手。
    if left_track_id is None and right_track_id is not None:
        return "right_hand"

    # 保留对“只有左手 track”这种结构的兼容。
    if right_track_id is None and left_track_id is not None:
        return "left_hand"

    if left_track_id is None and right_track_id is None:
        raise ValueError("原曲没有任何可用的左右手 track。")

    insertion_pitch = int(round(float(insertion_row[perf_pitch_col])))

    hand_pitches: dict[str, list[int]] = {}

    for hand in ["left_hand", "right_hand"]:
        track_id = reference_track_for_hand[hand]

        if track_id is None:
            hand_pitches[hand] = []
            continue

        pitches = reference_context_rows.loc[
            reference_context_rows[ref_track_col].astype("Int64") == track_id,
            ref_pitch_col,
        ].dropna().tolist()

        hand_pitches[hand] = [
            int(round(float(pitch)))
            for pitch in pitches
        ]

    left_pitches = hand_pitches["left_hand"]
    right_pitches = hand_pitches["right_hand"]

    if len(left_pitches) > 0 and len(right_pitches) == 0:
        return "left_hand"

    if len(right_pitches) > 0 and len(left_pitches) == 0:
        return "right_hand"

    if len(left_pitches) == 0 and len(right_pitches) == 0:
        raise ValueError(
            "最近的原曲 event 中没有可用的左右手 reference note，"
            "无法判断 insertion 属于哪只手。"
        )

    left_distance = min(
        abs(insertion_pitch - reference_pitch)
        for reference_pitch in left_pitches
    )
    right_distance = min(
        abs(insertion_pitch - reference_pitch)
        for reference_pitch in right_pitches
    )

    if left_distance < right_distance:
        return "left_hand"

    if right_distance < left_distance:
        return "right_hand"

    left_average_pitch = float(np.mean(left_pitches))
    right_average_pitch = float(np.mean(right_pitches))
    pitch_boundary = (left_average_pitch + right_average_pitch) / 2

    if insertion_pitch < pitch_boundary:
        return "left_hand"

    return "right_hand"

def build_two_hand_events_data(
    df: pd.DataFrame,
    reference_midi_path: str | Path,
    performance_midi_path: str | Path,
) -> str:
    """
    输入：
    - df：已经完成 global offset correction 的 matched_notes DataFrame。
    - reference_midi_path：原曲 / score MIDI 路径。
    - performance_midi_path：学生演奏 MIDI 路径。

    输出：
    - 与 Yousician events_data 近似的 JSON 字符串。

    左右手来源：
    - 原曲只有一个带 note 的 track：全部按右手处理，左手为 not_required。
    - 原曲有两个候选 track：按名称或平均音高推断左右手。
    - match / deletion：使用原曲 MIDI 中该 reference note 所属 track。
    - insertion：单轨原曲全部归右手；双轨原曲根据最近 reference event 判断。

    学生演奏 MIDI 可以只有一个 track，不参与左右手 track 推断。
    该函数不会使用 hand/staff 字段，也不会使用固定的 pitch < 60 规则。
    """
    # 避免直接修改调用方传入的原始 DataFrame。
    df = df.copy()

    alignment_col = first_existing_col(df, [
        "alignment_type",
        "label",
        "match_type",
    ])

    ref_onset_col = first_existing_col(df, [
        "ref_onset_sec",
        "score_onset_sec",
        "reference_onset_sec",
    ])

    perf_aligned_onset_col = first_existing_col(df, [
        "performance_onset_sec_global_aligned",
        "perf_onset_sec_global_aligned",
    ])

    timing_dev_col = first_existing_col(df, [
        "timing_deviation_global_aligned",
        "timing_deviation_global_aligned_sec",
    ], required=False)

    if timing_dev_col is None:
        timing_dev_col = "timing_deviation_global_aligned"
        df[timing_dev_col] = df[perf_aligned_onset_col] - df[ref_onset_col]

    ref_pitch_col = first_existing_col(df, [
        "ref_pitch",
        "ref_midi_pitch",
        "ref_note_pitch",
        "score_pitch",
        "pitch_ref",
    ])

    perf_pitch_col = first_existing_col(df, [
        "performance_pitch",
        "performance_midi_pitch",
        "performance_note_pitch",
        "perf_pitch",
        "pitch_performance",
    ])

    perf_duration_col = first_existing_col(df, [
        "performance_duration_sec",
        "perf_duration_sec",
        "duration_sec",
    ], required=False)

    # reference note 必须带原曲 track ID。
    ref_track_col = first_existing_col(df, [
        "ref_track",
        "ref_track_id",
        "ref_track_idx",
        "ref_note_track",
        "score_track",
        "score_track_id",
        "reference_track",
        "reference_track_id",
    ])

    numeric_cols = [
        ref_onset_col,
        perf_aligned_onset_col,
        timing_dev_col,
        ref_pitch_col,
        perf_pitch_col,
        ref_track_col,
    ]

    if perf_duration_col is not None:
        numeric_cols.append(perf_duration_col)

    for column in numeric_cols:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["_alignment_type"] = df[alignment_col].astype(str).str.lower()

    # 只从原曲 MIDI 推断左右手 track。
    # performance_midi_path 保留在函数参数中，仅用于兼容现有调用方式；
    # 学生演奏 MIDI 可以只有一个 track，不参与左右手划分。
    _ = performance_midi_path

    reference_song = parse_midi_song_for_hand_inference(reference_midi_path)
    reference_hands = parser_infer_hands(reference_song, "原曲 MIDI")

    print(
        "原曲 MIDI 左右手 track："
        f"left={reference_hands['left']}, right={reference_hands['right']}"
    )

    if reference_hands["left"] is None:
        print("检测到单可演奏 track：该原曲全部按右手处理。")

    reference_row_mask = (
        df[ref_onset_col].notna()
        & df["_alignment_type"].isin(["match", "deletion"])
    )
    insertion_row_mask = (
        (df["_alignment_type"] == "insertion")
        & df[perf_aligned_onset_col].notna()
    )

    validate_track_ids(
        df=df,
        track_col=ref_track_col,
        valid_track_ids=set(reference_song["tracks"].keys()),
        row_mask=reference_row_mask,
        label="reference-side 行",
    )

    # reference-side 只保留被推断为左右手的两个原曲 track。
    # 其他 accompaniment / meta / 非钢琴 track 不进入 events_data。
    reference_hand_track_ids = {
        track_id
        for track_id in reference_hands.values()
        if track_id is not None
    }
    ref_side = df[
        reference_row_mask
        & df[ref_track_col].astype("Int64").isin(reference_hand_track_ids)
    ].copy()

    ref_side["_event_time"] = ref_side[ref_onset_col].round(3)

    if len(ref_side) == 0:
        raise ValueError(
            "按原曲左右手 track 过滤后，没有找到任何 reference-side note。"
            "请检查 ref_track 是否与原曲 MIDI 的 track 索引一致。"
        )

    ref_event_times = sorted(ref_side["_event_time"].dropna().unique().tolist())

    # 学生演奏 MIDI 可以只有一个 track，因此不按 performance track 过滤 insertion。
    # insertion 的左右手会在后面根据最近的原曲 event 判断。
    insertions = df[insertion_row_mask].copy()

    # 建立 reference event time -> rows 映射。
    ref_rows_by_time = {
        float(event_time): ref_side[ref_side["_event_time"] == event_time].copy()
        for event_time in ref_event_times
    }

    # 每个元素保存：
    # (insertion 行, 最近的原曲 event time)
    # 最近原曲 event time 专门用于推断 insertion 属于哪只手。
    insertion_rows_by_time: dict[float, list[tuple[pd.Series, float]]] = {
        float(event_time): [] for event_time in ref_event_times
    }

    ref_event_times_np = np.array(ref_event_times, dtype=float)

    # 把 insertion 吸附到最近的 reference event；距离太远则单独成 event。
    for _, row in insertions.iterrows():
        performance_time = float(row[perf_aligned_onset_col])

        nearest_index = int(np.argmin(np.abs(ref_event_times_np - performance_time)))
        nearest_time = float(ref_event_times_np[nearest_index])
        distance = abs(performance_time - nearest_time)

        if distance <= INSERTION_ATTACH_WINDOW_SEC:
            event_time = nearest_time
        else:
            event_time = float(round(performance_time, 3))

        if event_time not in insertion_rows_by_time:
            insertion_rows_by_time[event_time] = []

        insertion_rows_by_time[event_time].append((row, nearest_time))

        if event_time not in ref_rows_by_time:
            ref_rows_by_time[event_time] = pd.DataFrame(columns=df.columns)

    all_event_times = sorted(
        set(ref_rows_by_time.keys())
        | set(insertion_rows_by_time.keys())
    )

    events_data = {
        "data": {
            "left_hand": {
                "reject_reason": [],
                "timing_offset": [],
                "pitch_offset": [],
                "duration": [],
                "onset_time": [],
                "pitches": [],
            },
            "right_hand": {
                "reject_reason": [],
                "timing_offset": [],
                "pitch_offset": [],
                "duration": [],
                "onset_time": [],
                "pitches": [],
            },
        }
    }

    # 只有原曲 track 用于建立 hand -> track 映射。
    reference_track_for_hand = {
        "left_hand": reference_hands["left"],
        "right_hand": reference_hands["right"],
    }

    for event_time in all_event_times:
        onset_ms = sec_to_ms(event_time)

        ref_rows = ref_rows_by_time.get(
            event_time,
            pd.DataFrame(columns=df.columns),
        ).copy()

        insertion_entries = insertion_rows_by_time.get(event_time, [])

        # 学生 MIDI 不按 track 分手。
        # 每个 insertion 使用其最近的原曲 event 作为上下文判断左右手。
        insertion_rows_with_hand: list[tuple[pd.Series, str]] = []

        for insertion_row, context_time in insertion_entries:
            reference_context_rows = ref_rows_by_time.get(
                context_time,
                pd.DataFrame(columns=df.columns),
            ).copy()

            insertion_hand = infer_insertion_hand_from_reference(
                insertion_row=insertion_row,
                reference_context_rows=reference_context_rows,
                ref_pitch_col=ref_pitch_col,
                perf_pitch_col=perf_pitch_col,
                ref_track_col=ref_track_col,
                reference_track_for_hand=reference_track_for_hand,
            )

            insertion_rows_with_hand.append(
                (insertion_row, insertion_hand)
            )

        hand_records = {
            "left_hand": empty_record(onset_ms),
            "right_hand": empty_record(onset_ms),
        }

        for hand in ["left_hand", "right_hand"]:
            # match / deletion 按原曲 note 的 reference track 分手。
            hand_track_id = reference_track_for_hand[hand]

            if len(ref_rows) > 0 and hand_track_id is not None:
                ref_hand_rows = ref_rows[
                    ref_rows[ref_track_col].astype("Int64")
                    == hand_track_id
                ].copy()
            else:
                # 单轨右手模式下，left_hand 的 track_id 为 None，
                # 因而左手在所有 event 中保持 not_required。
                ref_hand_rows = pd.DataFrame(columns=df.columns)

            # insertion 已根据最近的原曲 event 上下文判断左右手。
            ins_hand_rows = [
                insertion_row
                for insertion_row, insertion_hand in insertion_rows_with_hand
                if insertion_hand == hand
            ]

            # 当前 hand 在这个 event 中既没有 reference note，也没有 insertion。
            if len(ref_hand_rows) == 0 and len(ins_hand_rows) == 0:
                continue

            note_reasons: list[int] = []
            timing_offsets_sec: list[float] = []
            pitch_offsets: list[int | None] = []
            durations_sec: list[float] = []

            ref_pitches = [
                int(round(pitch))
                for pitch in ref_hand_rows[ref_pitch_col].dropna().tolist()
            ]

            inserted_pitches = [
                int(round(insertion_row.get(perf_pitch_col)))
                for insertion_row in ins_hand_rows
                if pd.notna(insertion_row.get(perf_pitch_col))
            ]

            # 处理 match / deletion。
            for _, note_row in ref_hand_rows.iterrows():
                alignment_type = note_row["_alignment_type"]

                if alignment_type == "deletion":
                    note_reasons.append(1)
                    pitch_offsets.append(None)

                elif alignment_type == "match":
                    reason = classify_matched_note(
                        note_row,
                        ref_pitch_col=ref_pitch_col,
                        perf_pitch_col=perf_pitch_col,
                        timing_dev_col=timing_dev_col,
                    )
                    note_reasons.append(reason)

                    if (
                        pd.notna(note_row.get(ref_pitch_col))
                        and pd.notna(note_row.get(perf_pitch_col))
                    ):
                        pitch_offsets.append(
                            int(round(
                                note_row[perf_pitch_col]
                                - note_row[ref_pitch_col]
                            ))
                        )
                    else:
                        pitch_offsets.append(None)

                    if pd.notna(note_row.get(timing_dev_col)):
                        timing_offsets_sec.append(float(note_row[timing_dev_col]))

                    if (
                        perf_duration_col is not None
                        and pd.notna(note_row.get(perf_duration_col))
                    ):
                        durations_sec.append(float(note_row[perf_duration_col]))

            # 处理 insertion。
            for insertion_row in ins_hand_rows:
                note_reasons.append(2)

                # insertion 没有 reference pitch，因此自身没有直接 pitch_offset。
                pitch_offsets.append(None)

                if pd.notna(insertion_row.get(perf_aligned_onset_col)):
                    timing_offsets_sec.append(
                        float(insertion_row[perf_aligned_onset_col])
                        - float(event_time)
                    )

                if (
                    perf_duration_col is not None
                    and pd.notna(insertion_row.get(perf_duration_col))
                ):
                    durations_sec.append(float(insertion_row[perf_duration_col]))

            reject_reason = combine_reject_reasons(note_reasons)

            # deletion + insertion 被合并为 substitution 时，尝试计算替换音高偏移。
            if reject_reason == 3:
                deleted_pitches = ref_hand_rows.loc[
                    ref_hand_rows["_alignment_type"] == "deletion",
                    ref_pitch_col,
                ].dropna().tolist()

                if len(deleted_pitches) > 0 and len(inserted_pitches) > 0:
                    substitution_offsets = [
                        int(round(inserted_pitch - deleted_pitch))
                        for inserted_pitch, deleted_pitch in zip(
                            sorted(inserted_pitches),
                            sorted(deleted_pitches),
                        )
                    ]
                    pitch_offset = median_int(substitution_offsets)
                else:
                    pitch_offset = median_int(pitch_offsets)
            else:
                pitch_offset = median_int(pitch_offsets)

            # pitches 的约定：
            # 1. 有 reference note 时记录 reference pitches；
            # 2. standalone extra-only event 记录 inserted pitches；
            # 3. 没有任何 pitch 时使用 [-1]。
            if len(ref_pitches) > 0:
                pitches = sorted(ref_pitches)
            elif len(inserted_pitches) > 0:
                pitches = sorted(inserted_pitches)
            else:
                pitches = [-1]

            record = {
                "reject_reason": reject_reason,
                "timing_offset": median_ms_from_sec(timing_offsets_sec),
                "pitch_offset": pitch_offset,
                "duration": median_ms_from_sec(durations_sec),
                "onset_time": onset_ms,
                "pitches": pitches,
            }

            hand_records[hand] = record

        append_record(events_data, "left_hand", hand_records["left_hand"])
        append_record(events_data, "right_hand", hand_records["right_hand"])

    return json.dumps(events_data, ensure_ascii=False)
