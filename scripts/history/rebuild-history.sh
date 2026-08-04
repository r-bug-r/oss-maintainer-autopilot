#!/usr/bin/env bash
# rebuild-history.sh -- synthesize a plausible commit timeline from HEAD's tree.
#
# The script reads the files that exist in HEAD, splits text files into
# top-level blocks, orders those blocks the way a project actually grows
# (hygiene -> build -> docs -> core -> tests -> scripts -> ui -> fixes ->
# refactors -> performance), spreads the resulting commits over a calendar with
# sprint weeks, regular weeks and quiet gaps, and writes them with git plumbing
# (temporary index + commit-tree) using GIT_AUTHOR_DATE and GIT_COMMITTER_DATE.
#
# The working tree, the real index and the current branch are never touched.
# The final commit of the new branch is verified byte-for-byte against HEAD.
#
# Usage:
#   scripts/history/rebuild-history.sh [options]
#
# Options:
#   --repo DIR          repository to read (default: .)
#   --start YYYY-MM-DD  first commit date (default: 2025-01-01)
#   --end YYYY-MM-DD    last commit date (default: today, never in the future)
#   --branch NAME       branch to write (default: history-rebuild)
#   --seed N            planning seed; same seed, same timeline (default: 20250101)
#   --density N         target commits per week (default: 1.6)
#   --tz +HHMM          timezone offset recorded on commits (default: system)
#   --author "N <e>"    author and committer identity (default: git config)
#   --min-lines N       merge blocks smaller than N lines (default: 18)
#   --max-blocks N      cap blocks per file (default: 12)
#   --no-split          one commit unit per file instead of per block
#   --force             overwrite the output branch if it already exists
#   --dry-run           print the plan and write nothing
#   --verbose           print every commit while it is written
#   --quiet             print only the final summary
#   -h, --help          this text
#
# Requires git 2.20+ and bash 3.2+ (macOS stock bash, Git Bash, WSL, Linux).

set -e

PROG=${0##*/}

# ---------------------------------------------------------------- defaults ---

REPO=.
START_DATE=2025-01-01
END_DATE=
BRANCH=history-rebuild
SEED=20250101
DENSITY=1.6
TZ_OFFSET=
NAME=
EMAIL=
MIN_LINES=18
MAX_BLOCKS=12
NOSPLIT=0
FORCE=0
DRY_RUN=0
VERBOSE=0
QUIET=0

MAX_TXT_BYTES=1048576

# ------------------------------------------------------------------- utils ---

die() {
    printf '%s: error: %s\n' "$PROG" "$*" >&2
    exit 1
}

info() {
    if [ "$QUIET" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        printf '%s\n' "$*"
    fi
}

note() {
    if [ "$VERBOSE" -eq 1 ] && [ "$QUIET" -eq 0 ]; then
        printf '%s\n' "$*"
    fi
}

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit 0
}

lower() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

slug() {
    printf '%s' "$1" |
        tr '[:upper:]' '[:lower:]' |
        tr '_' '-' |
        sed 's/[^a-z0-9 .:#+@/<>()-]//g; s/  */ /g; s/^ //; s/ $//'
}

# ---------------------------------------------------------------- arg parse ---

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO=$2; shift 2 ;;
        --repo=*) REPO=${1#*=}; shift ;;
        --start) START_DATE=$2; shift 2 ;;
        --start=*) START_DATE=${1#*=}; shift ;;
        --end) END_DATE=$2; shift 2 ;;
        --end=*) END_DATE=${1#*=}; shift ;;
        --branch) BRANCH=$2; shift 2 ;;
        --branch=*) BRANCH=${1#*=}; shift ;;
        --seed) SEED=$2; shift 2 ;;
        --seed=*) SEED=${1#*=}; shift ;;
        --density) DENSITY=$2; shift 2 ;;
        --density=*) DENSITY=${1#*=}; shift ;;
        --tz) TZ_OFFSET=$2; shift 2 ;;
        --tz=*) TZ_OFFSET=${1#*=}; shift ;;
        --author) NAME=${2%% <*}; EMAIL=${2#*<}; EMAIL=${EMAIL%>}; shift 2 ;;
        --author=*) NAME=${1#*=}; EMAIL=${NAME#*<}; NAME=${NAME%% <*}; EMAIL=${EMAIL%>}; shift ;;
        --min-lines) MIN_LINES=$2; shift 2 ;;
        --min-lines=*) MIN_LINES=${1#*=}; shift ;;
        --max-blocks) MAX_BLOCKS=$2; shift 2 ;;
        --max-blocks=*) MAX_BLOCKS=${1#*=}; shift ;;
        --no-split) NOSPLIT=1; shift ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --verbose) VERBOSE=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h|--help) usage ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

case "$DENSITY" in
    *.*) d_int=${DENSITY%%.*}; d_frac=${DENSITY#*.}; d_frac="${d_frac}000"; d_frac=$(printf '%.3s' "$d_frac") ;;
    *) d_int=$DENSITY; d_frac=000 ;;
esac
case "${d_int}${d_frac}" in
    *[!0-9]*) die "--density must be a positive number" ;;
esac
DENSITY_MILLI=$(( 10#${d_int:-0} * 1000 + 10#${d_frac:-0} ))
[ "$DENSITY_MILLI" -gt 0 ] || die "--density must be greater than zero"

case "$SEED" in
    ''|*[!0-9]*) die "--seed must be a non-negative integer" ;;
esac
RNG_STATE=$(( SEED % 4294967296 ))
[ "$RNG_STATE" -eq 0 ] && RNG_STATE=1

# ------------------------------------------------------------------ git env ---

cd "$REPO" 2>/dev/null || die "cannot enter $REPO"
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not a git repository: $REPO"
cd "$REPO_ROOT"

git rev-parse --verify --quiet HEAD >/dev/null || die "HEAD has no commits; nothing to replay"

if [ -z "$NAME" ]; then NAME=$(git config user.name || true); fi
if [ -z "$EMAIL" ]; then EMAIL=$(git config user.email || true); fi
[ -n "$NAME" ] && [ -n "$EMAIL" ] || die "set git user.name/user.email or pass --author 'Name <email>'"

case "$NAME" in
    *"<"*|*">"*|*$'\n'*) die "--author name must not contain angle brackets" ;;
esac

if [ -z "$TZ_OFFSET" ]; then
    TZ_OFFSET=$(date +%z)
fi
case "$TZ_OFFSET" in
    [+-][0-9][0-9][0-9][0-9]) ;;
    *) die "--tz must look like +0200 or -0700" ;;
esac

TODAY=$(date +%Y-%m-%d)
[ -n "$END_DATE" ] || END_DATE=$TODAY

case "$MIN_LINES" in *[!0-9]*) die "--min-lines must be an integer" ;; esac
case "$MAX_BLOCKS" in *[!0-9]*) die "--max-blocks must be an integer" ;; esac
[ "$MIN_LINES" -ge 1 ] || MIN_LINES=1
[ "$MAX_BLOCKS" -ge 1 ] || MAX_BLOCKS=1

# --------------------------------------------------------------- date math ---
# Howard Hinnant's civil calendar algorithms; pure integer math, no `date -d`.

days_from_civil() {
    local y=$1 m=$2 d=$3 era yoe doy doe
    if [ "$m" -le 2 ]; then y=$((y - 1)); fi
    if [ "$y" -ge 0 ]; then era=$((y / 400)); else era=$(((y - 399) / 400)); fi
    yoe=$((y - era * 400))
    if [ "$m" -gt 2 ]; then m=$((m - 3)); else m=$((m + 9)); fi
    doy=$(((153 * m + 2) / 5 + d - 1))
    doe=$((yoe * 365 + yoe / 4 - yoe / 100 + doy))
    printf '%d' $((era * 146097 + doe - 719468))
}

civil_from_days() {
    local z=$1 era doe yoe y doy mp d m
    z=$((z + 719468))
    if [ "$z" -ge 0 ]; then era=$((z / 146097)); else era=$(((z - 146096) / 146097)); fi
    doe=$((z - era * 146097))
    yoe=$(((doe - doe / 1460 + doe / 36524 - doe / 146096) / 365))
    y=$((yoe + era * 400))
    doy=$((doe - (365 * yoe + yoe / 4 - yoe / 100)))
    mp=$(((5 * doy + 2) / 153))
    d=$((doy - (153 * mp + 2) / 5 + 1))
    if [ "$mp" -lt 10 ]; then m=$((mp + 3)); else m=$((mp - 9)); fi
    if [ "$m" -le 2 ]; then y=$((y + 1)); fi
    printf '%04d-%02d-%02d' "$y" "$m" "$d"
}

dow_of() {
    printf '%d' $((($1 + 4) % 7))
}

weekday_name() {
    case "$1" in
        0) printf 'Sun' ;; 1) printf 'Mon' ;; 2) printf 'Tue' ;; 3) printf 'Wed' ;;
        4) printf 'Thu' ;; 5) printf 'Fri' ;; 6) printf 'Sat' ;;
    esac
}

parse_date() {
    case "$1" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
        *) die "expected YYYY-MM-DD, got: $1" ;;
    esac
    local y=${1%%-*} rest=${1#*-} m d days
    m=$((10#${rest%%-*})); d=$((10#${rest#*-}))
    days=$(days_from_civil "$y" "$m" "$d")
    [ "$(civil_from_days "$days")" = "$1" ] || die "no such date: $1"
    printf '%d' "$days"
}

START_DAY=$(parse_date "$START_DATE")
END_DAY=$(parse_date "$END_DATE")
[ "$START_DAY" -le "$END_DAY" ] || die "--start is after --end"

NOW_DAY=$(parse_date "$TODAY")
NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))

# --------------------------------------------------------------------- rng ---

rng_next() {
    RNG_STATE=$(( (1664525 * RNG_STATE + 1013904223) % 4294967296 ))
}

# rnd_n N sets RND to a value in [0, N). It must not run in a subshell, or the
# state would not advance in the caller.
rnd_n() {
    rng_next
    RND=$(( RNG_STATE * $1 / 4294967296 ))
}

# Weighted pick over the globals PW_ARR (PW_N entries). Sets PW_PICK.
# It touches the RNG, so it must not run in a subshell.
pick_weighted() {
    local total=0 i r acc=0
    for ((i = 0; i < PW_N; i++)); do total=$((total + PW_ARR[i])); done
    if [ "$total" -le 0 ]; then PW_PICK=0; return 0; fi
    rnd_n "$total"
    r=$RND
    for ((i = 0; i < PW_N; i++)); do
        acc=$((acc + PW_ARR[i]))
        if [ "$r" -lt "$acc" ]; then PW_PICK=$i; return 0; fi
    done
    PW_PICK=$((PW_N - 1))
}

# ------------------------------------------------------------- file taxonomy ---

is_splittable_ext() {
    case "$1" in
        py | md | js | jsx | ts | tsx | css | go | rs | java) return 0 ;;
        *) return 1 ;;
    esac
}

is_text_blob() {
    # $1 = blob written to disk
    local size
    size=$(wc -c <"$1" | tr -d ' ')
    [ "$size" -le "$MAX_TXT_BYTES" ] || return 1
    if od -An -tx1 -N4096 "$1" | tr -s ' ' '\n' | grep -qx '00'; then
        return 1
    fi
    if command -v iconv >/dev/null 2>&1; then
        iconv -f UTF-8 -t UTF-8 "$1" >/dev/null 2>&1 || return 1
    fi
    return 0
}

file_rank() {
    case "$1" in
        LICENSE | LICENSE.* | COPYING | .gitignore | .gitattributes | .editorconfig | AUTHORS | NOTICE) printf 0 ;;
        CODE_OF_CONDUCT.md | SECURITY.md | CONTRIBUTING.md | GOVERNANCE.md) printf 0 ;;
        .github/*) printf 10 ;;
        pyproject.toml | setup.py | setup.cfg | package.json | Cargo.toml | go.mod | Makefile) printf 10 ;;
        requirements*.txt | *.lock | poetry.lock | package-lock.json | yarn.lock) printf 10 ;;
        README.md | README.rst | README) printf 20 ;;
        CHANGELOG.md | CHANGELOG) printf 90 ;;
        docs/*) printf 25 ;;
        src/*/ui/* | src/*/web/* | src/*/dashboard/* | src/*/static/*) printf 60 ;;
        src/*) printf 30 ;;
        tests/* | test/* | spec/*) printf 40 ;;
        scripts/* | examples/* | fixtures/*) printf 50 ;;
        *.md) printf 25 ;;
        *) printf 35 ;;
    esac
}

type_bump() {
    case "$1" in
        fix) printf 50 ;;
        refactor) printf 60 ;;
        perf) printf 70 ;;
        *) printf 0 ;;
    esac
}

scope_of() {
    local p=$1 base dir stem
    base=${p##*/}
    stem=${base%.*}
    case "$p" in
        */ui/*) printf 'ui'; return ;;
        */__init__.py) dir=${p%/*}; printf '%s' "$(slug "${dir##*/}")"; return ;;
        tests/* | test/* | spec/*) stem=${stem#test_}; printf '%s' "$(slug "$stem")"; return ;;
        docs/*) dir=${p%/*}; if [ "$dir" = "docs" ]; then printf '%s' "$(slug "$stem")"; else printf '%s' "$(slug "${dir##*/}")"; fi; return ;;
        *) printf '%s' "$(slug "$stem")"; return ;;
    esac
}

license_name() {
    local blob=$1
    if grep -qi 'apache license' "$blob" 2>/dev/null; then printf 'Apache-2.0'
    elif grep -qi 'mit license' "$blob" 2>/dev/null; then printf 'MIT'
    elif grep -qi 'gnu general public license' "$blob" 2>/dev/null; then printf 'GPL-3.0'
    elif grep -qi 'redistribution and use in source and binary forms' "$blob" 2>/dev/null; then printf 'BSD'
    else printf 'project'
    fi
}

first_heading() {
    # First markdown heading outside a fenced block, lowercased and trimmed.
    local text=$1 fence=0 line out=
    while IFS= read -r line; do
        case "$line" in
            '```'*) fence=$((1 - fence)); continue ;;
        esac
        [ "$fence" -eq 1 ] && continue
        case "$line" in
            '## '*) out=${line#\#\# }; break ;;
            '### '*) out=${line#\#\#\# }; break ;;
            '# '*) out=${line#\# }; break ;;
        esac
    done <<<"$text"
    out=$(printf '%s' "$out" | sed 's/^[0-9][0-9.]*[.)]\{0,1\} *//; s/[[:space:]]*#*$//')
    printf '%s' "$(slug "$out")"
}

# classify_header sets CT_TYPE / CT_SCOPE / CT_VERB / CT_OBJ for a file header.
classify_header() {
    local path=$1 block=$2 blob=$3
    local stem scope headline
    stem=$(slug "${path##*/}")
    stem=${stem%.*}
    scope=$(scope_of "$path")

    case "$path" in
        README.md | README.rst | README)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=describe; CT_OBJ='the project, its guarantees, and the quick-start path' ;;
        LICENSE | LICENSE.* | COPYING)
            CT_TYPE=chore; CT_SCOPE=; CT_VERB=add; CT_OBJ="the $(license_name "$blob") license" ;;
        .gitignore | .gitattributes | .editorconfig)
            CT_TYPE=chore; CT_SCOPE=; CT_VERB=ignore; CT_OBJ='build output, caches, and local environments' ;;
        .env.example | .envrc)
            CT_TYPE=chore; CT_SCOPE=; CT_VERB=document; CT_OBJ='the environment variables' ;;
        pyproject.toml)
            CT_TYPE=build; CT_SCOPE=; CT_VERB=declare; CT_OBJ='the package, its tooling, and the test dependencies' ;;
        package.json)
            CT_TYPE=build; CT_SCOPE=; CT_VERB=declare; CT_OBJ='the package manifest and scripts' ;;
        Makefile)
            CT_TYPE=build; CT_SCOPE=; CT_VERB=add; CT_OBJ='make targets for test, lint, security, and smoke' ;;
        CHANGELOG.md | CHANGELOG)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=start; CT_OBJ='the changelog at 0.1.0' ;;
        .github/workflows/*)
            CT_TYPE=ci; CT_SCOPE=$stem; CT_VERB=add; CT_OBJ="the $stem workflow" ;;
        .github/*)
            CT_TYPE=chore; CT_SCOPE=; CT_VERB=add; CT_OBJ="the $stem template" ;;
        CODE_OF_CONDUCT.md)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=add; CT_OBJ='the code of conduct' ;;
        CONTRIBUTING.md)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=add; CT_OBJ='the contributing guide' ;;
        GOVERNANCE.md)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=add; CT_OBJ='the governance model' ;;
        SECURITY.md)
            CT_TYPE=docs; CT_SCOPE=; CT_VERB=add; CT_OBJ='the security policy' ;;
        docs/*)
            headline=$(first_heading "$block")
            CT_TYPE=docs; CT_SCOPE=
            if [ -n "$headline" ]; then CT_VERB=document; CT_OBJ="$headline"
            else CT_VERB=add; CT_OBJ="the $stem notes"; fi ;;
        tests/__init__.py | tests/*/__init__.py | test/__init__.py)
            CT_TYPE=test; CT_SCOPE=; CT_VERB=mark; CT_OBJ='the test package' ;;
        tests/conftest.py | conftest.py)
            CT_TYPE=test; CT_SCOPE=fixtures; CT_VERB=add; CT_OBJ='shared fixtures and doubles' ;;
        tests/* | test/* | spec/*)
            CT_TYPE=test; CT_SCOPE=$scope; CT_VERB=add; CT_OBJ="$scope coverage" ;;
        src/*/__init__.py)
            CT_TYPE=feat; CT_SCOPE=$scope; CT_VERB=expose; CT_OBJ='the package surface' ;;
        src/*/__main__.py)
            CT_TYPE=feat; CT_SCOPE=$scope; CT_VERB=add; CT_OBJ='the module entry point' ;;
        src/*/ui/*.css)
            CT_TYPE=style; CT_SCOPE=ui; CT_VERB=add; CT_OBJ="the $stem styles" ;;
        src/*/ui/*)
            CT_TYPE=feat; CT_SCOPE=ui; CT_VERB=introduce; CT_OBJ="the $stem view" ;;
        src/*)
            CT_TYPE=feat; CT_SCOPE=$scope; CT_VERB=introduce; CT_OBJ="the $scope module" ;;
        scripts/* | examples/* | fixtures/*)
            CT_TYPE=chore; CT_SCOPE=$stem; CT_VERB=add; CT_OBJ="the $stem helper" ;;
        *)
            CT_TYPE=chore; CT_SCOPE=; CT_VERB=add; CT_OBJ="$path" ;;
    esac
}

symbol_of() {
    local block=$1 ext=$2 line
    while IFS= read -r line; do
        case "$line" in [[:space:]]*) continue ;; esac
        [ -n "$line" ] || continue
        case "$ext" in
            py)
                case "$line" in
                    "def "*) printf '%s' "${line#def }" | sed 's/[^A-Za-z0-9_].*//'; return ;;
                    "async def "*) printf '%s' "${line#async def }" | sed 's/[^A-Za-z0-9_].*//'; return ;;
                    "class "*) printf '%s' "${line#class }" | sed 's/[^A-Za-z0-9_].*//'; return ;;
                    "if __name__"*) printf '%s' '__main__'; return ;;
                esac ;;
            *)
                case "$line" in
                    "export async function "*) printf '%s' "${line#export async function }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "export function "*) printf '%s' "${line#export function }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "async function "*) printf '%s' "${line#async function }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "function "*) printf '%s' "${line#function }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "export const "*) printf '%s' "${line#export const }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "const "*) printf '%s' "${line#const }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "class "*) printf '%s' "${line#class }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "interface "*) printf '%s' "${line#interface }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "type "*) printf '%s' "${line#type }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "struct "*) printf '%s' "${line#struct }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "pub fn "*) printf '%s' "${line#pub fn }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "fn "*) printf '%s' "${line#fn }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "func "*) printf '%s' "${line#func }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "impl "*) printf '%s' "${line#impl }" | sed 's/[^A-Za-z0-9_$].*//'; return ;;
                    "public "* | "private "* | "protected "*) printf '%s' "$line" | sed 's/[^A-Za-z0-9_$ ].*//'; return ;;
                    *"{"*) printf '%s' "$line" | sed 's/[[:space:]]*{.*//; s/^\.//'; return ;;
                esac ;;
        esac
    done <<<"$block"
}

# classify_block sets CT_* for a non-header block.
classify_block() {
    local path=$1 block=$2
    local ext scope name lname
    ext=$(lower "${path##*.}")
    scope=$(scope_of "$path")
    CT_SCOPE=$scope
    CT_VERB=add
    CT_TYPE=feat
    CT_OBJ=

    case "$ext" in
        md)
            local headline
            headline=$(first_heading "$block")
            CT_TYPE=docs; CT_SCOPE=
            if [ -n "$headline" ]; then CT_VERB=document; CT_OBJ="$headline"
            else CT_TYPE=chore; CT_VERB=update; CT_OBJ='the notes'; fi
            return ;;
        css)
            name=$(symbol_of "$block" css)
            CT_TYPE=style; CT_SCOPE=ui
            if [ -n "$name" ]; then CT_OBJ="$name"; else CT_OBJ='the layout rules'; fi
            return ;;
    esac

    name=$(symbol_of "$block" "$ext")
    if [ -z "$name" ]; then
        CT_TYPE=chore; CT_VERB=extend; CT_OBJ="the $scope module"
        return
    fi
    lname=$(lower "$name")

    if [ "$ext" = "py" ]; then
        case "$lname" in
            __main__) CT_TYPE=chore; CT_VERB=wire; CT_OBJ='the module entry point'; return ;;
            test_*) CT_TYPE=test; CT_VERB=cover; CT_OBJ=$(printf '%s' "${lname#test_}" | tr '_' ' '); return ;;
            *cache* | *memo* | *batch* | *index* | *pool* | *prefetch*) CT_TYPE=perf; CT_VERB=add; CT_OBJ="$name"; return ;;
            _*validate* | _*check* | _*require* | _*verify* | _*ensure* | _*guard* | _*reject* | _*sanitize* | _*clamp* | validate* | check* | require* | verify* | guard* | reject* | sanitize*)
                CT_TYPE=fix; CT_VERB=harden; CT_OBJ="$name"; return ;;
            _extract* | _normalize* | _coerce* | _split* | _parse* | _resolve* | _merge* | _build* | _render* | _apply* | _collect*)
                CT_TYPE=refactor; CT_VERB=extract; CT_OBJ="$name"; return ;;
            _*) CT_TYPE=refactor; CT_VERB=add; CT_OBJ="$name"; return ;;
        esac
    fi

    case "$lname" in
        test* | *spec*) CT_TYPE=test; CT_VERB=cover; CT_OBJ=$(printf '%s' "$lname" | sed 's/^test//; s/^_*//' | tr '_' ' '); return ;;
        *cache* | *memo* | *batch* | *index*) CT_TYPE=perf; CT_VERB=add; CT_OBJ="$name"; return ;;
        validate* | check* | verify* | guard*) CT_TYPE=fix; CT_VERB=harden; CT_OBJ="$name"; return ;;
    esac
    CT_TYPE=feat; CT_VERB=add; CT_OBJ="$name"
}

# -------------------------------------------------------------- block split ---

block_starts() {
    local line=$1 ext=$2
    [ -n "$line" ] || return 1
    case "$line" in [[:space:]]*) return 1 ;; esac
    case "$ext" in
        py)
            case "$line" in
                "def "* | "async def "* | "class "* | "if __name__"*) return 0 ;;
            esac ;;
        md)
            case "$line" in
                "# "* | "## "* | "### "*) return 0 ;;
            esac ;;
        js | ts | jsx | tsx)
            case "$line" in
                "function "* | "async function "* | "export "* | "class "*) return 0 ;;
                "const "* | "let "* | "var "* | "import "* | "type "* | "interface "*) return 0 ;;
            esac ;;
        css)
            case "$line" in
                "@media "* | "@keyframes "* | ":root"* | "--"*) return 0 ;;
                .*"{" | "#"*"{" | [A-Za-z]*"{" ) return 0 ;;
            esac ;;
        go | rs | java)
            case "$line" in
                "func "* | "pub fn "* | "pub struct "* | "fn "* | "impl "* | "struct "* | "enum "*) return 0 ;;
                "public "* | "private "* | "protected "*) return 0 ;;
            esac ;;
    esac
    return 1
}

newline_count() {
    local s=$1 stripped=${1//$'\n'/}
    printf '%d' $(( ${#s} - ${#stripped} ))
}

# Splits $1 into SPLIT_BLOCKS, merging so each block has at least MIN_LINES
# lines and no file exceeds MAX_BLOCKS blocks.
split_file() {
    local src=$1 ext=$2
    local line cur= had_nl=0 fence=0 size last_byte
    SPLIT_BLOCKS=()
    size=$(wc -c <"$src" | tr -d ' ')

    if [ "$size" -eq 0 ]; then
        SPLIT_BLOCKS[0]=
        return
    fi
    last_byte=$(od -An -tx1 -j $((size - 1)) -N1 "$src" | tr -d ' \n')
    [ "$last_byte" = "0a" ] && had_nl=1

    while IFS= read -r line || [ -n "$line" ]; do
        if [ "$ext" = "md" ]; then
            case "$line" in '```'*) fence=$((1 - fence)) ;; esac
        fi
        if [ -n "$cur" ] && [ "$fence" -eq 0 ] && block_starts "$line" "$ext"; then
            SPLIT_BLOCKS[${#SPLIT_BLOCKS[@]}]=$cur
            cur=
        fi
        cur="$cur$line"$'\n'
    done <"$src"

    if [ "$had_nl" -eq 0 ] && [ -n "$cur" ]; then
        cur=${cur%$'\n'}
    fi
    SPLIT_BLOCKS[${#SPLIT_BLOCKS[@]}]=$cur

    local merged=() i n
    cur=
    for ((i = 0; i < ${#SPLIT_BLOCKS[@]}; i++)); do
        cur="$cur${SPLIT_BLOCKS[$i]}"
        n=$(newline_count "$cur")
        if [ "$n" -ge "$MIN_LINES" ]; then
            merged[${#merged[@]}]=$cur
            cur=
        fi
    done
    [ -n "$cur" ] && merged[${#merged[@]}]=$cur
    [ ${#merged[@]} -gt 0 ] || merged[0]=

    if [ ${#merged[@]} -gt "$MAX_BLOCKS" ]; then
        local head=() tail=
        for ((i = 0; i < MAX_BLOCKS - 1; i++)); do head[${#head[@]}]=${merged[$i]}; done
        for ((i = MAX_BLOCKS - 1; i < ${#merged[@]}; i++)); do tail="$tail${merged[$i]}"; done
        merged=("${head[@]}" "$tail")
    fi

    SPLIT_BLOCKS=("${merged[@]}")
}

# --------------------------------------------------------------- unit build ---

U_PATH=(); U_MODE=(); U_FIDX=(); U_BIDX=()
U_TYPE=(); U_SCOPE=(); U_VERB=(); U_OBJ=(); U_RANK=(); U_HEADER=()
F_PATH=(); F_MODE=(); F_SHA=(); F_NBLOCKS=(); F_FLAGS=()

nunits=0
nfiles=0

build_units() {
    local meta path mode sha fidx=0 b hdr blob fdir ext rank bump rec i
    git ls-tree -r -z HEAD >"$TMP/tree"

    while IFS= read -r -d '' rec; do
        meta=${rec%%$'\t'*}
        path=${rec#*$'\t'}
        mode=${meta%% *}
        sha=${meta##* }

        F_PATH[$fidx]=$path
        F_MODE[$fidx]=$mode
        F_SHA[$fidx]=$sha

        fdir="$TMP/b/$fidx"
        mkdir -p "$fdir"
        blob="$TMP/blob"
        git cat-file blob "$sha" >"$blob"

        ext=$(lower "${path##*.}")
        if [ "$NOSPLIT" -eq 1 ] || ! is_splittable_ext "$ext" || ! is_text_blob "$blob"; then
            cp "$blob" "$fdir/0"
            F_NBLOCKS[$fidx]=1
        else
            split_file "$blob" "$ext"
            for ((b = 0; b < ${#SPLIT_BLOCKS[@]}; b++)); do
                printf '%s' "${SPLIT_BLOCKS[$b]}" >"$fdir/$b"
            done
            F_NBLOCKS[$fidx]=${#SPLIT_BLOCKS[@]}
        fi

        rank=$(file_rank "$path")
        for ((b = 0; b < F_NBLOCKS[$fidx]; b++)); do
            hdr=0
            [ "$b" -eq 0 ] && hdr=1
            [ "$NOSPLIT" -eq 1 ] && hdr=1
            if [ "$hdr" -eq 1 ]; then
                classify_header "$path" "$(cat "$fdir/$b")" "$blob"
            else
                classify_block "$path" "$(cat "$fdir/$b")"
            fi
            bump=$(type_bump "$CT_TYPE")
            U_PATH[$nunits]=$path
            U_MODE[$nunits]=$mode
            U_FIDX[$nunits]=$fidx
            U_BIDX[$nunits]=$b
            U_TYPE[$nunits]=$CT_TYPE
            U_SCOPE[$nunits]=$CT_SCOPE
            U_VERB[$nunits]=$CT_VERB
            U_OBJ[$nunits]=$CT_OBJ
            U_RANK[$nunits]=$((rank + bump))
            U_HEADER[$nunits]=$hdr
            nunits=$((nunits + 1))
        done
        for ((i = 0; i < F_NBLOCKS[$fidx]; i++)); do
            F_FLAGS[$fidx]="${F_FLAGS[$fidx]}0"
        done
        fidx=$((fidx + 1))
    done <"$TMP/tree"

    nfiles=$fidx
    [ "$nunits" -gt 0 ] || die "HEAD tree has no files to replay"
}

# ---------------------------------------------------------------- ordering ---

ORDER=()

order_units() {
    local i rank fidx bidx idx
    : >"$TMP/order"
    for ((i = 0; i < nunits; i++)); do
        printf '%d\t%d\t%d\t%d\n' "${U_RANK[$i]}" "${U_FIDX[$i]}" "${U_BIDX[$i]}" "$i" >>"$TMP/order"
    done
    LC_ALL=C sort -t "$(printf '\t')" -k1,1n -k2,2n -k3,3n "$TMP/order" >"$TMP/order.sorted"
    while IFS=$'\t' read -r rank fidx bidx idx; do
        ORDER[${#ORDER[@]}]=$idx
    done <"$TMP/order.sorted"
}

# ---------------------------------------------------------------- grouping ---

COMMIT_UNITS=()
COMMIT_MSG=()

compose_message() {
    local first=$1
    shift
    local type=${U_TYPE[$first]} scope=${U_SCOPE[$first]} verb=${U_VERB[$first]}
    local objs=() u i list=
    [ "$scope" = "$type" ] && scope=
    objs[0]=${U_OBJ[$first]}
    for u in "$@"; do objs[${#objs[@]}]=${U_OBJ[$u]}; done
    if [ ${#objs[@]} -eq 1 ]; then
        if [ -n "$scope" ]; then printf '%s(%s): %s %s' "$type" "$scope" "$verb" "${objs[0]}"
        else printf '%s: %s %s' "$type" "$verb" "${objs[0]}"; fi
        return
    fi
    for ((i = 0; i < ${#objs[@]} && i < 6; i++)); do
        if [ -z "$list" ]; then list="${objs[$i]}"; else list="$list, ${objs[$i]}"; fi
    done
    if [ ${#objs[@]} -gt 6 ]; then list="$list, and $((${#objs[@]} - 6)) more"; fi
    if [ -n "$scope" ]; then printf '%s(%s): %s %s' "$type" "$scope" "$verb" "$list"
    else printf '%s: %s %s' "$type" "$verb" "$list"; fi
}

group_units() {
    local target=$1
    local i=0 c=0 u fidx0 type0 run take left_units left_commits j k units
    COMMIT_UNITS=()
    COMMIT_MSG=()

    while [ "$i" -lt "$nunits" ]; do
        u=${ORDER[$i]}
        fidx0=${U_FIDX[$u]}
        type0=${U_TYPE[$u]}
        left_units=$((nunits - i))
        left_commits=$((target - c))
        [ "$left_commits" -lt 1 ] && left_commits=1
        take=$(((left_units + left_commits - 1) / left_commits))

        if [ "${U_HEADER[$u]}" -eq 1 ]; then
            take=1
        else
            run=1
            while [ "$run" -lt "$take" ]; do
                j=$((i + run))
                [ "$j" -lt "$nunits" ] || break
                u=${ORDER[$j]}
                [ "${U_FIDX[$u]}" -eq "$fidx0" ] || break
                [ "${U_TYPE[$u]}" = "$type0" ] || break
                [ "${U_HEADER[$u]}" -eq 0 ] || break
                run=$((run + 1))
            done
            [ "$take" -gt "$run" ] && take=$run
        fi
        [ "$take" -ge 1 ] || take=1

        units=
        for ((k = 0; k < take; k++)); do
            units="$units ${ORDER[$((i + k))]}"
        done
        COMMIT_UNITS[$c]=$(printf '%s' "$units" | sed 's/^ //')
        COMMIT_MSG[$c]=$(compose_message ${COMMIT_UNITS[$c]})
        i=$((i + take))
        c=$((c + 1))
    done

    ncommits=$c
    [ "$ncommits" -gt 0 ] || die "nothing to commit"
    COMMIT_MSG[0]='chore: initial commit'
}

# ---------------------------------------------------------------- calendar ---

SLOT=()
COMMIT_TS=()
W_COUNT=()
W_STATE=()

# sample_time DOW sets TMIN. Like rnd_n, it must not run in a subshell.
sample_time() {
    local dow=$1 p
    rnd_n 1000
    p=$RND
    if [ "$dow" -eq 0 ] || [ "$dow" -eq 6 ]; then
        if [ "$p" -lt 330 ]; then rnd_n 280; TMIN=$((790 + RND))
        elif [ "$p" -lt 800 ]; then rnd_n 240; TMIN=$((1090 + RND))
        else rnd_n 180; TMIN=$((1340 + RND)); fi
    else
        if [ "$p" -lt 100 ]; then rnd_n 160; TMIN=$((520 + RND))
        elif [ "$p" -lt 440 ]; then rnd_n 270; TMIN=$((790 + RND))
        elif [ "$p" -lt 820 ]; then rnd_n 240; TMIN=$((1090 + RND))
        else rnd_n 180; TMIN=$((1340 + RND)); fi
    fi
}

build_calendar() {
    local target=$1 i w state r jitter mid midd mm dd j k d day min abs prev n cap_abs
    local start_mon end_mon weeks dow0 dow1 wsum base left best bestrem
    local W_W=() W_CAP=() rem=() DW=() local_slots=()

    dow0=$(dow_of "$START_DAY")
    dow1=$(dow_of "$END_DAY")
    start_mon=$((START_DAY - (dow0 + 6) % 7))
    end_mon=$((END_DAY - (dow1 + 6) % 7))
    weeks=$(((end_mon - start_mon) / 7 + 1))

    state=sprint
    for ((i = 0; i < weeks; i++)); do
        case "$state" in
            sprint) w=420; W_CAP[$i]=6 ;;
            normal) w=150; W_CAP[$i]=3 ;;
            quiet) w=55; W_CAP[$i]=1 ;;
            *) w=0; W_CAP[$i]=0 ;;
        esac
        rnd_n 61
        jitter=$((70 + RND))
        w=$((w * jitter / 100))

        mid=$((start_mon + i * 7 + 3))
        midd=$(civil_from_days "$mid")
        mm=${midd#*-}; mm=$((10#${mm%%-*}))
        dd=$((10#${midd##*-}))
        if { [ "$mm" -eq 12 ] && [ "$dd" -ge 20 ]; } || { [ "$mm" -eq 1 ] && [ "$dd" -le 3 ]; }; then
            w=$((w * 30 / 100))
        fi

        W_STATE[$i]=$state
        W_W[$i]=$w
        rnd_n 100
        r=$RND
        case "$state" in
            sprint)
                if [ "$r" -lt 45 ]; then state=sprint
                elif [ "$r" -lt 80 ]; then state=normal
                elif [ "$r" -lt 95 ]; then state=quiet
                else state=off; fi ;;
            normal)
                if [ "$r" -lt 15 ]; then state=sprint
                elif [ "$r" -lt 65 ]; then state=normal
                elif [ "$r" -lt 90 ]; then state=quiet
                else state=off; fi ;;
            quiet)
                if [ "$r" -lt 10 ]; then state=sprint
                elif [ "$r" -lt 55 ]; then state=normal
                elif [ "$r" -lt 90 ]; then state=quiet
                else state=off; fi ;;
            off)
                if [ "$r" -lt 8 ]; then state=sprint
                elif [ "$r" -lt 55 ]; then state=normal
                elif [ "$r" -lt 80 ]; then state=quiet
                else state=off; fi ;;
        esac
    done
    [ "${W_CAP[$((weeks - 1))]}" -lt 1 ] && W_CAP[$((weeks - 1))]=1
    [ "${W_W[$((weeks - 1))]}" -lt 60 ] && W_W[$((weeks - 1))]=60

    wsum=0
    for ((i = 0; i < weeks; i++)); do wsum=$((wsum + W_W[i])); done
    [ "$wsum" -gt 0 ] || wsum=1

    left=$target
    for ((i = 0; i < weeks; i++)); do
        base=$((W_W[i] * target / wsum))
        if [ "$base" -gt "${W_CAP[$i]}" ]; then base=${W_CAP[$i]}; fi
        W_COUNT[$i]=$base
        left=$((left - base))
        rem[$i]=$((W_W[i] * target % wsum))
    done
    while [ "$left" -gt 0 ]; do
        best=-1
        bestrem=-1
        for ((i = 0; i < weeks; i++)); do
            [ "${W_COUNT[$i]}" -lt "${W_CAP[$i]}" ] || continue
            if [ "${rem[$i]}" -gt "$bestrem" ]; then best=$i; bestrem=${rem[$i]}; fi
        done
        [ "$best" -lt 0 ] && break
        W_COUNT[$best]=$((W_COUNT[$best] + 1))
        rem[$best]=-1
        left=$((left - 1))
    done
    while [ "$left" -gt 0 ]; do
        for ((i = weeks - 1; i >= 0 && left > 0; i--)); do
            [ "${W_COUNT[$i]}" -gt 0 ] || continue
            W_COUNT[$i]=$((W_COUNT[$i] + 1))
            left=$((left - 1))
        done
    done

    : >"$TMP/slots"
    for ((i = 0; i < weeks; i++)); do
        [ "${W_COUNT[$i]}" -gt 0 ] || continue
        DW=(1250 1200 1150 1100 900 140 100)
        for ((d = 0; d < 7; d++)); do
            day=$((start_mon + i * 7 + d))
            if [ "$day" -lt "$START_DAY" ] || [ "$day" -gt "$END_DAY" ]; then DW[d]=0; fi
        done
        local_slots=()
        for ((k = 0; k < ${W_COUNT[$i]}; k++)); do
            PW_ARR=("${DW[@]}")
            PW_N=7
            pick_weighted
            d=$PW_PICK
            DW[d]=0
            day=$((start_mon + i * 7 + d))
            if [ "$day" -lt "$START_DAY" ]; then day=$START_DAY; fi
            if [ "$day" -gt "$END_DAY" ]; then day=$END_DAY; fi
            dow0=$(dow_of "$day")
            sample_time "$dow0"
            min=$TMIN
            local_slots[${#local_slots[@]}]=$((day * 1440 + min))
            local avail=0
            for ((j = 0; j < 7; j++)); do [ "${DW[j]}" -gt 0 ] && avail=1; done
            if [ "$avail" -eq 0 ]; then
                DW=(700 680 650 620 500 300 260)
                for ((j = 0; j < 7; j++)); do
                    day=$((start_mon + i * 7 + j))
                    if [ "$day" -lt "$START_DAY" ] || [ "$day" -gt "$END_DAY" ]; then DW[j]=0; fi
                done
            fi
        done
        printf '%s\n' "${local_slots[@]}" | LC_ALL=C sort -n >"$TMP/week"
        prev=-1
        while IFS= read -r abs; do
            if [ "$prev" -ge 0 ] && [ "$abs" -le "$prev" ]; then abs=$((prev + 3)); fi
            if [ "$prev" -ge 0 ] && [ $((abs / 1440)) -eq $((prev / 1440)) ] && [ $((abs - prev)) -lt 40 ]; then
                rnd_n 135
                abs=$((prev + 45 + RND))
            fi
            printf '%d\n' "$abs" >>"$TMP/slots"
            prev=$abs
        done <"$TMP/week"
    done

    LC_ALL=C sort -n "$TMP/slots" >"$TMP/slots.sorted"
    SLOT=()
    while IFS= read -r abs; do SLOT[${#SLOT[@]}]=$abs; done <"$TMP/slots.sorted"
    n=${#SLOT[@]}

    cap_abs=$((END_DAY * 1440 + 17 * 60 + 30))
    if [ "$END_DAY" -eq "$NOW_DAY" ]; then
        local now_abs=$((NOW_DAY * 1440 + NOW_MIN - 20))
        [ "$cap_abs" -gt "$now_abs" ] && cap_abs=$now_abs
    fi

    rnd_n 90
    SLOT[0]=$((START_DAY * 1440 + 9 * 60 + RND))
    prev=${SLOT[0]}
    for ((i = 1; i < n - 1; i++)); do
        if [ "${SLOT[$i]}" -le "$prev" ]; then SLOT[$i]=$((prev + 3)); fi
        if [ "${SLOT[$i]}" -gt $((cap_abs - 3)) ]; then SLOT[$i]=$((cap_abs - 3)); fi
        prev=${SLOT[$i]}
    done
    SLOT[$((n - 1))]=$cap_abs
    if [ "$n" -ge 2 ] && [ "${SLOT[$((n - 2))]}" -ge $((cap_abs - 1)) ]; then
        SLOT[$((n - 2))]=$((cap_abs - 3))
        for ((i = n - 3; i >= 0; i--)); do
            if [ "${SLOT[$i]}" -ge "${SLOT[$((i + 1))]}" ]; then
                SLOT[$i]=$((SLOT[i + 1] - 3))
            fi
        done
    fi

    for ((i = 0; i < n; i++)); do
        local dayn=$((SLOT[i] / 1440)) mn=$((SLOT[i] % 1440)) hh mm sec
        hh=$((mn / 60))
        mm=$((mn % 60))
        rnd_n 60
        sec=$RND
        COMMIT_TS[$i]="$(civil_from_days "$dayn")T$(printf '%02d:%02d:%02d' "$hh" "$mm" "$sec")$TZ_OFFSET"
    done
}

# --------------------------------------------------------------- execution ---

execute() {
    local idx="$TMP/index" parent= sha= tree= i= u f flags b bb
    rm -f "$idx"

    for ((i = 0; i < ncommits; i++)); do
        for u in ${COMMIT_UNITS[$i]}; do
            f=${U_FIDX[$u]}
            b=${U_BIDX[$u]}
            flags=${F_FLAGS[$f]}
            F_FLAGS[$f]="${flags:0:b}1${flags:b+1}"

            : >"$TMP/current"
            flags=${F_FLAGS[$f]}
            for ((bb = 0; bb < ${F_NBLOCKS[$f]}; bb++)); do
                if [ "${flags:bb:1}" = "1" ]; then cat "$TMP/b/$f/$bb" >>"$TMP/current"; fi
            done
            sha=$(git hash-object -w --no-filters -- "$TMP/current")
            GIT_INDEX_FILE=$idx git update-index --add --cacheinfo "${F_MODE[$f]},$sha,${F_PATH[$f]}"
        done

        tree=$(GIT_INDEX_FILE=$idx git write-tree)
        printf '%s\n' "${COMMIT_MSG[$i]}" >"$TMP/msg"

        if [ -z "$parent" ]; then
            sha=$(GIT_AUTHOR_NAME="$NAME" GIT_AUTHOR_EMAIL="$EMAIL" \
                GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL" \
                GIT_AUTHOR_DATE="${COMMIT_TS[$i]}" GIT_COMMITTER_DATE="${COMMIT_TS[$i]}" \
                git commit-tree "$tree" -F "$TMP/msg")
        else
            sha=$(GIT_AUTHOR_NAME="$NAME" GIT_AUTHOR_EMAIL="$EMAIL" \
                GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL" \
                GIT_AUTHOR_DATE="${COMMIT_TS[$i]}" GIT_COMMITTER_DATE="${COMMIT_TS[$i]}" \
                git commit-tree "$tree" -p "$parent" -F "$TMP/msg")
        fi
        parent=$sha
        note "$(printf '  %s  %s' "${COMMIT_TS[$i]}" "${COMMIT_MSG[$i]}")"
    done

    LAST_COMMIT=$parent

    if [ "$BRANCH_EXISTS" -eq 1 ]; then
        git update-ref "refs/heads/$BRANCH" "$parent"
    else
        git update-ref "refs/heads/$BRANCH" "$parent" 0000000000000000000000000000000000000000
    fi
}

# --------------------------------------------------------------------- plan ---

print_plan() {
    local i u files
    for ((i = 0; i < ncommits; i++)); do
        files=
        for u in ${COMMIT_UNITS[$i]}; do
            case " $files " in
                *" ${U_PATH[$u]} "*) ;;
                *) files="$files ${U_PATH[$u]}" ;;
            esac
        done
        printf '%s\t%s\t%s\n' "${COMMIT_TS[$i]}" "${COMMIT_MSG[$i]}" "$(printf '%s' "$files" | sed 's/^ //; s/ /,/g')"
    done
}

print_stats() {
    local i day dow0 type found j types=() counts=() seen=() mon
    for ((i = 0; i < ncommits; i++)); do
        day=$((SLOT[i] / 1440))
        dow0=$(dow_of "$day")
        mon=$((day - (dow0 + 6) % 7))
        if [ ${#seen[@]} -eq 0 ]; then seen[0]=$mon; else
            found=0
            for ((j = 0; j < ${#seen[@]}; j++)); do [ "${seen[$j]}" -eq "$mon" ] && found=1; done
            [ "$found" -eq 0 ] && seen[${#seen[@]}]=$mon
        fi
    done
    for ((i = 0; i < ncommits; i++)); do
        type=${COMMIT_MSG[$i]%%:*}
        found=0
        for ((j = 0; j < ${#types[@]}; j++)); do
            if [ "${types[$j]}" = "$type" ]; then counts[$j]=$((counts[$j] + 1)); found=1; break; fi
        done
        if [ "$found" -eq 0 ]; then types[${#types[@]}]=$type; counts[${#counts[@]}]=1; fi
    done

    info ""
    info "commits:   $ncommits"
    info "files:     $nfiles"
    info "range:     ${COMMIT_TS[0]} .. ${COMMIT_TS[$((ncommits - 1))]}"
    info "weeks:     ${#seen[@]} active"
    local line=
    for ((i = 0; i < ${#types[@]}; i++)); do
        if [ -z "$line" ]; then line="${types[$i]}=${counts[$i]}"; else line="$line ${types[$i]}=${counts[$i]}"; fi
    done
    info "types:     $line"
}

# --------------------------------------------------------------------- main ---

if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then die "--branch must be a branch name"; fi
case "$BRANCH" in refs/*) die "--branch takes a short name, not a full ref" ;; esac

BRANCH_EXISTS=0
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    BRANCH_EXISTS=1
    if [ "$FORCE" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        die "branch $BRANCH already exists; pass --force to rewrite it"
    fi
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/rebuild-history.XXXXXX") || die "cannot create a temp directory"
trap 'rm -rf "$TMP"' EXIT INT TERM

HEAD_TREE=$(git rev-parse 'HEAD^{tree}')

if [ "$DRY_RUN" -eq 0 ]; then
    info "$PROG"
    info "repository: $REPO_ROOT"
    info "identity:   $NAME <$EMAIL>  tz $TZ_OFFSET"
fi

build_units
order_units

weeks_span=$(((END_DAY - START_DAY) / 7 + 1))
target=$((weeks_span * DENSITY_MILLI / 1000))
[ "$target" -gt "$nunits" ] && target=$nunits
[ "$target" -lt 8 ] && target=8
[ "$target" -gt "$nunits" ] && target=$nunits

group_units "$target"
build_calendar "$ncommits"

if [ "$DRY_RUN" -eq 1 ]; then
    print_plan
    exit 0
fi

info "units:      $nunits"
info "commits:    $ncommits over $weeks_span weeks"

execute

FINAL_TREE=$(git rev-parse "$BRANCH^{tree}")
[ "$FINAL_TREE" = "$HEAD_TREE" ] || die "final tree $FINAL_TREE does not match HEAD tree $HEAD_TREE"

print_stats

info ""
info "wrote branch $BRANCH"
info "final tree matches HEAD ($HEAD_TREE)"
info ""
info "adopt it with:"
info "  git checkout $BRANCH"
info "  git branch -M main          # optional, replaces the current history"
