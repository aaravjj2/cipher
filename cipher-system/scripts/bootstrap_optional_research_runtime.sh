#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_DIR="${REPOSITORY_ROOT}/.venv-research-py312"
BOOTSTRAP_ENV="${HOME}/.local/uv-bootstrap"
UV_BIN="${UV_BIN:-${BOOTSTRAP_ENV}/bin/uv}"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-python3}"
HF_HOME="${HF_HOME:-${HOME}/.cache/huggingface}"

cd "${REPOSITORY_ROOT}"

if [[ ! -x "${UV_BIN}" ]]; then
  "${PYTHON_BOOTSTRAP}" -m venv "${BOOTSTRAP_ENV}"
  "${BOOTSTRAP_ENV}/bin/python" -m pip install --upgrade pip uv
fi

"${UV_BIN}" python install 3.12
"${UV_BIN}" venv --python 3.12 "${ENV_DIR}"
"${UV_BIN}" pip install --python "${ENV_DIR}/bin/python" -r requirements.txt
"${UV_BIN}" pip install --python "${ENV_DIR}/bin/python" -r requirements-research-engines.txt
"${UV_BIN}" pip install \
  --python "${ENV_DIR}/bin/python" \
  --index-url https://download.pytorch.org/whl/cpu \
  'torch==2.13.0+cpu'
"${UV_BIN}" pip install --python "${ENV_DIR}/bin/python" -r requirements-research-support.txt

"${ENV_DIR}/bin/python" -m pip check
"${ENV_DIR}/bin/python" cipher-system/scripts/check_research_engine_runtime.py
HF_HOME="${HF_HOME}" "${ENV_DIR}/bin/python" \
  cipher-system/scripts/prefetch_research_models.py --smoke
"${ENV_DIR}/bin/python" cipher-system/scripts/audit_research_infrastructure.py \
  --offline --smoke

printf '\nOptional Cipher research runtime is ready at %s\n' "${ENV_DIR}"
printf 'Docker/LEAN host readiness and GitHub authentication are reported separately by the infrastructure audit.\n'
