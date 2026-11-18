"""Reference MIDI hand assignment shared by current and legacy exporters."""
import warnings
from pathlib import Path
from typing import Any
import mido
import numpy as np
import pandas as pd
import pretty_midi

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
            "按照项目约定，将该 track 全部视为右手，左手事件数组为空。",
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

