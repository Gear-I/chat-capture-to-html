#!/usr/bin/env bash
# Run chat-capture-to-html on Linux or macOS.
#
#   scripts/run.sh [--update] [--open] [--python PY] [--no-venv] CAPTURE_FOLDER [tool options...]
#
# It keeps its own Python environment in .venv next to this repository,
# installs the tool there, reinstalls it whenever the code changes, and checks
# that the version that runs is the version in this folder before running.
# Everything after the script's own options goes to the tool unchanged, e.g.
#
#   scripts/run.sh --open ~/captures/screenshots -o ~/reports --no-ocr --diagnose
#
#   --update     git pull before running
#   --open       open the report when done (only when -o names an .html file
#                or a folder with exactly one new report)
#   --python PY  Python to build the environment with (default: python3)
#   --no-venv    use that Python directly instead of .venv (it must already
#                have the tool's dependencies)
#
# The capture folder is only read. Write reports somewhere else (-o).

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="python3"
update=0
open_after=0
use_venv=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update) update=1; shift ;;
        --open) open_after=1; shift ;;
        --python) python="${2:?--python needs a value}"; shift 2 ;;
        --no-venv) use_venv=0; shift ;;
        -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) break ;;
    esac
done
if [[ $# -lt 1 ]]; then
    echo "usage: scripts/run.sh [--update] [--open] [--python PY] [--no-venv] CAPTURE_FOLDER [tool options...]" >&2
    exit 64
fi

say() { printf '%s\n' "$*" >&2; }

if [[ $update -eq 1 ]]; then
    say "updating: git pull"
    git -C "$here" pull --ff-only
fi

if ! command -v "$python" >/dev/null 2>&1; then
    say "error: $python not found (use --python to name another)"
    exit 69
fi

if [[ $use_venv -eq 1 ]]; then
    venv="$here/.venv"
    if [[ ! -x "$venv/bin/python" ]]; then
        say "creating $venv"
        "$python" -m venv "$venv"
    fi
    py="$venv/bin/python"
    # Reinstall when the code changed since the last install, so an old build is never run.
    stamp="$venv/.chat-capture-to-html.stamp"
    state="$(cd "$here" && { git rev-parse HEAD 2>/dev/null || true; git status --porcelain -- chat_capture_to_html pyproject.toml 2>/dev/null || true; } | "$py" -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
    if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$state" ]]; then
        say "installing chat-capture-to-html into $venv"
        "$py" -m pip install --quiet --upgrade pip >/dev/null
        "$py" -m pip install --quiet --force-reinstall --no-deps "$here"
        "$py" -m pip install --quiet "$here"
        printf '%s' "$state" > "$stamp"
    fi
else
    py="$python"
fi

# The version that will run must be the version in this folder.
want="$(sed -n 's/^version = "\(.*\)"/\1/p' "$here/pyproject.toml" | head -n 1)"
have="$(cd / && "$py" -c 'import chat_capture_to_html as c; print(c.__version__)' 2>/dev/null || true)"
if [[ $use_venv -eq 0 && -z "$have" ]]; then
    # --no-venv without an install: run straight from this folder
    export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
    have="$(cd / && "$py" -c 'import chat_capture_to_html as c; print(c.__version__)' 2>/dev/null || true)"
fi
if [[ "$have" != "$want" ]]; then
    say "error: the installed tool is version '${have:-none}', but this folder has $want."
    say "Run without --no-venv, or reinstall: $py -m pip install --force-reinstall --no-deps \"$here\""
    exit 70
fi

before="$(mktemp)"
trap 'rm -f "$before"' EXIT
out=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "-o" || "${args[$i]}" == "--output" ]] && (( i + 1 < ${#args[@]} )); then
        out="${args[$((i + 1))]}"
    fi
done
sleep 1  # file times have one-second resolution on some systems; reports written after this count as new

set +e
"$py" -m chat_capture_to_html "$@"
status=$?
set -e

if [[ $open_after -eq 1 && -n "$out" ]]; then
    report=""
    if [[ "$out" == *.html || "$out" == *.htm ]]; then
        report="$out"
    elif [[ -d "$out" ]]; then
        fresh=()  # no mapfile: macOS ships bash 3.2
        while IFS= read -r f; do fresh+=("$f"); done < <(find "$out" -maxdepth 1 -name '*.html' -newer "$before" 2>/dev/null)
        if [[ ${#fresh[@]} -eq 1 ]]; then
            report="${fresh[0]}"
        elif [[ ${#fresh[@]} -gt 1 ]]; then
            say "several reports written to $out; open the one you want"
        fi
    fi
    if [[ -n "$report" && -f "$report" ]]; then
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$report" >/dev/null 2>&1 &
        elif command -v open >/dev/null 2>&1; then
            open "$report"
        else
            say "report: $report"
        fi
    fi
fi

# The tool's exit code is passed on: 0 all verified, 2 a hash problem, 3 no OCR engine.
exit "$status"
