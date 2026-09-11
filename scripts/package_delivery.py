"""Package the complete app and generate a hash-based comparison with the input ZIP."""
import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path,PurePosixPath

def included(name):
    parts=PurePosixPath(name).parts
    excluded={'.git','node_modules','.terraform','__MACOSX','__pycache__','.pytest_cache','.react-router','.venv'}
    if parts and parts[0] in {'build','data'}: return False
    if any(x in excluded or x.endswith('.egg-info') for x in parts): return False
    base=parts[-1] if parts else ''
    if base in {'.DS_Store','tsconfig.tsbuildinfo','terraform.tfvars'}: return False
    if base.startswith('.env') and not base.endswith('.example'): return False
    if any(base.endswith(s) for s in ['.pyc','.tfplan','.tfstate','.tfstate.backup']): return False
    return True

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--original',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(); root=Path(__file__).resolve().parents[1];out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    original={}
    with zipfile.ZipFile(args.original) as z:
        for info in z.infolist():
            parts=PurePosixPath(info.filename).parts
            if info.is_dir() or not parts or parts[0]!='pianokt': continue
            name='/'.join(parts[1:])
            if name and included(name): original[name]=hashlib.sha256(z.read(info)).hexdigest()
    change=root/'docs/CHANGES_ZH.md';change.touch(exist_ok=True)
    current={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file() and included(p.relative_to(root).as_posix())}
    added=sorted(set(current)-set(original))
    modified=sorted(k for k,p in current.items() if k in original and hashlib.sha256(p.read_bytes()).hexdigest()!=original[k])
    removed=sorted(set(original)-set(current))
    unchanged=len(current)-len(added)-len(modified)
    lines=['# 相对原 PianoKT 的变更清单','',
      '以用户上传的 pianokt(1).zip 为基线，按同名文件内容 SHA-256 比较。保留原应用目录、播放器主体、歌曲与音色资源；新增 Python 后端和云端数据管道。下方为实际文件集合，不是计划清单。','',
      f'新增 {len(added)} 个文件，修改 {len(modified)} 个文件，保留 {unchanged} 个同内容文件；业务源文件删除 {len(removed)} 个。构建产物、依赖目录、Git 历史、本地数据、状态和真实环境变量文件不纳入比较或交付。','',
      '## 修改的含义','',
      '| 位置 | 实际变化 |','|---|---|',
      '| Challenge 保存入口 | 同时创建参考快照和演奏文件，保存终止播放位置及练习上下文，调用 GCS 上传链路 |',
      '| challenge-history/api.ts | 删除新录音的 Supabase Storage 上传实现，导出 GCS 保存函数；旧录音只保留下载兼容 |',
      '| MIDI recorder | Challenge 的每个 note-on/off 采样歌曲时钟，减少 100 ms 静默刷新带来的事件时间量化 |',
      '| Recordings 页面 | 增加 alignment 状态与 JSON 下载、演示推荐入口 |',
      '| Auth / Avatar / Leaderboard | 修复原有 TypeScript 联合类型和可空 Supabase 引用检查，未更换原页面结构 |',
      '| analytics | 缺少可选 GA 配置时不再令生产构建中断 |',
      '| package-lock.json | 重新解析并锁定安装后的前端依赖；package.json 的应用依赖声明保留 |',
      '| README / ignore 配置 | 新后端入口、文档导航与本地数据/状态排除 |','',
      '## 新增内容的职责','',
      '- backend/pianokt_backend/api.py：Supabase token 与原白名单验证、GCS signed URL、finalize、状态与下载、推荐与反馈。',
      '- worker.py / worker_api.py：事务 outbox、lease、超时隔离、真实对齐、不可变结果复用与状态回写。',
      '- alignment/：提取 notebook 函数、MAD 时间校正、matcher 轨号恢复、明确左手单轨处理、逐音符与左右手结果。',
      '- pipeline/：Raw → Bronze → Silver → Gold、Delta MERGE、quality、completed 发布协议、Spark AvailableNow、训练快照。',
      '- supabase 009：在线状态和 outbox 新表/策略/触发器；额外提供专用后端角色与演示曲目种子。',
      '- infra/gcp：私有 bucket、服务账号和 IAM、Pub/Sub 与死信、两个 Cloud Run Service、两个 Job 和 Scheduler。',
      '- tests / scripts / docs：可复现测试、合成 MIDI、打包工具、教学文档和部署路线图。','',
      '## 没有交付为真实模型的部分','',
      '没有训练 AKT/DKT，没有声称估计了校准能力或学习增益，没有自动生成新 MIDI 曲目。推荐为明确标识的黑盒演示实现。没有在用户云项目执行部署。未来工作与验收条件见部署路线图。','',
      '## 实际修改文件','',*['- `'+p+'`' for p in modified],'','## 实际新增文件','',*['- `'+p+'`' for p in added]]
    if removed: lines+=['','## 未保留的源文件','',*['- `'+p+'`' for p in removed]]
    change.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    archive=out/'PianoKT-GCS-Lakehouse.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,p in sorted(current.items()):z.write(p,'pianokt/'+name)
    for file in ['PIANOKT_TEACHING_GUIDE_ZH.md','DEPLOYMENT_AND_ROADMAP_ZH.md','CHANGES_ZH.md','VALIDATION_ZH.md']:
        shutil.copy2(root/'docs'/file,out/file)
    (out/'SHA256.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert 'pianokt/backend/pianokt_backend/api.py' in z.namelist()
        assert 'pianokt/public/music/songs/ode-to-joy.mid' in z.namelist()
        assert any(n.startswith('pianokt/src/features/data/') for n in z.namelist())
    print(f'Packaged {len(current)} files; {len(added)} added, {len(modified)} modified, {unchanged} unchanged, {len(removed)} removed; {archive.stat().st_size} bytes')

if __name__=='__main__':main()
