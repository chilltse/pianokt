# 通用 Segment 边界契约

此前 Greensleeves 修复位于 scan_catalog 通用逻辑，无曲目名称或ID特例，但仅放宽首部静音，未完整支持尾部静音和跨切点音符。本次改为单一共享函数 backend/pianokt_backend/segment_contract.py::validate_segment_bounds。

规则：边界有限、非负、起点小于终点、有序且不重叠；每个有效音符的完整 [start,end] 必须属于一个 Segment。首尾及中间仅可省略没有音符的时间；任何遗漏、在音符延音期间开始/结束、内部切点截断均拒绝。1微秒容差仅用于浮点误差，不是演奏阈值。原有Segment时长审核仍有效。

同步端 scan_catalog 和仓库 audit_producer 均直接调用共享函数。本地 pianokt_300_auto/build.py、phrase_v5.py 也直接导入同一函数，因此生产器需在已安装 pianokt-backend 的 Python 环境运行。没有复制另一套规则。build.write_clip 在写出前检查相交音符完整包含于片段，并移除原先丢弃短于25ms音符的逻辑；metadata保留边界完整精度。脚本备份为 *.before_shared_segment_contract.bak。

回归覆盖首尾静音、内部静音、遗漏短音符、跨起止边界音符、内部截断、重叠和非有限值。未重新生成已有MIDI、未运行远端sync、未更改远端数据库。
