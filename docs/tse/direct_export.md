在(.venv)下

python -m pip install --no-deps -e ./backend



set -a
source backend/.env.local
set +a

pianokt-data export-attempts \
  --source direct \
  --environment production \
  --cutoff 2026-11-05T00:00:00Z \
  --output-prefix datasets/attempts-时间


  pianokt-data export-training \
  --source direct \
  --environment production \
  --cutoff 2026-11-06T00:00:00Z \
  --output-key datasets/training-20261106-new.json


gcloud storage cp \
  "${RAW_ROOT%/}/datasets/training-20261106-new.json" \
  docs/tse/training-20261106-keyboard-new.json
  


  ## 转化成按学生划分的训练数据

