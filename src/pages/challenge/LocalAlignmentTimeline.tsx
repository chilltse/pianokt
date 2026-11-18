export function LocalAlignmentTimeline({ result }: { result: Record<string, any> }) {
  const notes = (result.notes ?? []) as Array<Record<string, any>>
  const values = notes
    .flatMap((note) => [note.ref_onset_sec, note.performance_onset_sec])
    .filter((value) => typeof value === 'number')
  const pitches = notes
    .flatMap((note) => [note.ref_pitch, note.performance_pitch])
    .filter((value) => typeof value === 'number')
  const maxTime = Math.max(1, ...values)
  const minPitch = Math.max(0, Math.min(48, ...pitches) - 2)
  const maxPitch = Math.min(127, Math.max(72, ...pitches) + 2)
  const width = 960,
    height = 360,
    left = 42,
    bottom = 28
  const x = (time: number) => left + (time / maxTime) * (width - left - 12)
  const y = (pitch: number) =>
    16 + ((maxPitch - pitch) / Math.max(1, maxPitch - minPitch)) * (height - bottom - 18)
  return (
    <div className="mt-4 overflow-x-auto rounded border border-slate-200 bg-slate-950 p-2">
      <svg
        width={width}
        height={height}
        className="min-w-[960px]"
        aria-label="Alignment piano-roll timeline"
      >
        <text x={left} y={height - 5} fill="#94a3b8" fontSize="11">
          0s
        </text>
        <text x={width - 12} y={height - 5} textAnchor="end" fill="#94a3b8" fontSize="11">
          {maxTime.toFixed(2)}s
        </text>
        {Array.from({ length: maxPitch - minPitch + 1 }, (_, index) => minPitch + index)
          .filter((pitch) => pitch % 2 === 0)
          .map((pitch) => (
            <g key={pitch}>
              <line
                x1={left}
                x2={width - 12}
                y1={y(pitch)}
                y2={y(pitch)}
                stroke="#243040"
                strokeWidth="1"
              />
              <text x={left - 6} y={y(pitch) + 3} textAnchor="end" fill="#94a3b8" fontSize="10">
                {pitch}
              </text>
            </g>
          ))}
        {notes.map(
          (note, index) =>
            note.ref_onset_sec != null &&
            note.performance_onset_sec != null && (
              <line
                key={`line-${index}`}
                x1={x(note.ref_onset_sec)}
                y1={y(note.ref_pitch)}
                x2={x(note.performance_onset_sec)}
                y2={y(note.performance_pitch)}
                stroke={note.is_correct ? '#34d399' : '#f59e0b'}
                strokeWidth="1.5"
              />
            ),
        )}
        {notes.map((note, index) => (
          <g key={`note-${index}`}>
            <title>{`${note.alignment_type}; R ${note.ref_pitch ?? '–'} → P ${note.performance_pitch ?? '–'}; reject ${note.reject_reason}`}</title>
            {note.ref_onset_sec != null &&
              (note.alignment_type === 'deletion' ? (
                <path
                  d={`M ${x(note.ref_onset_sec) - 5} ${y(note.ref_pitch) - 5} L ${x(note.ref_onset_sec) + 5} ${y(note.ref_pitch) + 5} M ${x(note.ref_onset_sec) - 5} ${y(note.ref_pitch) + 5} L ${x(note.ref_onset_sec) + 5} ${y(note.ref_pitch) - 5}`}
                  stroke="#fb7185"
                  strokeWidth="2"
                />
              ) : (
                <circle cx={x(note.ref_onset_sec)} cy={y(note.ref_pitch)} r="4" fill="#60a5fa" />
              ))}
            {note.performance_onset_sec != null &&
              (note.alignment_type === 'insertion' ? (
                <path
                  d={`M ${x(note.performance_onset_sec) - 5} ${y(note.performance_pitch) - 5} L ${x(note.performance_onset_sec) + 5} ${y(note.performance_pitch) + 5} M ${x(note.performance_onset_sec) - 5} ${y(note.performance_pitch) + 5} L ${x(note.performance_onset_sec) + 5} ${y(note.performance_pitch) - 5}`}
                  stroke="#fb7185"
                  strokeWidth="2"
                />
              ) : (
                <rect
                  x={x(note.performance_onset_sec) - 3}
                  y={y(note.performance_pitch) - 3}
                  width="6"
                  height="6"
                  fill="#fb923c"
                />
              ))}
          </g>
        ))}
      </svg>
      <p className="px-2 pt-1 text-xs text-slate-300">
        蓝圆：reference；橙方：performance；绿线：正确配对；黄线：配对但有错误；红叉：漏音或多音。悬停标记查看详情。
      </p>
    </div>
  )
}
