#!/usr/bin/env bash
# Build the deployment zip for the SQS consumer Lambda. It only adds dependencies and modules that it needs, to make the
# zip package as slim as possible.
#
# Usage:
#     scripts/build_lambda_zip.sh            # writes dist/lambda.zip
#     scripts/build_lambda_zip.sh --keep     # also leaves the unzipped tree for inspection

set -euo pipefail

PLATFORM=aarch64-manylinux_2_28
PYTHON_VERSION=3.14

# Only the dependencies the handler needs are copied over. boto3 is absent because the runtime provides it.
LAMBDA_PACKAGES=(
    psycopg
    psycopg-binary  # the compiled half, and the reason PLATFORM matters
)

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

build_dir="$repo_root/build/lambda"
zip_path="$repo_root/dist/lambda.zip"

rm -rf "$build_dir" "$zip_path"
mkdir -p "$build_dir" "$(dirname "$zip_path")"

echo "==> Resolving dependencies from uv.lock"
locked="$repo_root/build/locked.txt"
requirements="$repo_root/build/requirements.txt"
uv export --no-dev --no-emit-project --no-hashes --format requirements-txt > "$locked"

: > "$requirements"
for package in "${LAMBDA_PACKAGES[@]}"; do
    if ! grep -E "^${package}([=<>[;]|$)" "$locked" >> "$requirements"; then
        echo "error: '$package' is in LAMBDA_PACKAGES but not in uv.lock" >&2
        exit 1
    fi
done

echo "==> Fetching wheels for $PLATFORM / python$PYTHON_VERSION"
uv pip install \
    --target "$build_dir" \
    --python-platform "$PLATFORM" \
    --python-version "$PYTHON_VERSION" \
    --only-binary=:all: \
    --quiet \
    -r "$requirements"

echo "==> Adding application code"
mkdir -p "$build_dir/airmax"
cp airmax/__init__.py "$build_dir/airmax/"
cp -R airmax/lambdas "$build_dir/airmax/"

find "$build_dir" -type d -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "$build_dir/bin"
rm -f "$build_dir/.lock"

echo "==> Zipping"
(cd "$build_dir" && zip -qr "$zip_path" .)

echo
echo "unzipped: $(du -sh "$build_dir" | cut -f1)"
echo "zipped:   $(du -h "$zip_path" | cut -f1)   (Lambda's limit is 250 MB unzipped, 50 MB zipped upload)"
echo "psycopg:  $(ls "$build_dir" | grep -i '^psycopg' | tr '\n' ' ')"

if [[ "${1:-}" != "--keep" ]]; then
    rm -rf "$build_dir" "$locked" "$requirements"
fi
