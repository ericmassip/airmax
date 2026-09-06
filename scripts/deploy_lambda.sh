#!/usr/bin/env bash
# Idempotent script to build and deploy the SQS consumer Lambda
#
# Usage:
#     scripts/deploy_lambda.sh             # deploy code and config, leave the queue trigger as it is
#     scripts/deploy_lambda.sh --enable    # ... and switch the queue trigger on
#     scripts/deploy_lambda.sh --disable   # ... and switch it off
#
# The trigger is deliberately left alone unless you ask, and it is created switched off. Turning it on is what starts
# draining the backlog of messages.

set -euo pipefail

FUNCTION_NAME=eric-airmax-lambda-ingest-measurements
HANDLER=airmax.lambdas.ingest_measurements.handler.handler
RUNTIME=python3.14
# The zip carries psycopg's compiled `_psycopg.cpython-314-aarch64-linux-gnu.so`, so the function must run on arm64.
# Keep this in step with PLATFORM and PYTHON_VERSION in build_lambda_zip.sh.
ARCHITECTURE=arm64
TIMEOUT=60  # Generous: a batch of 500 upserts takes a few seconds, the rest is headroom for a cold start
MEMORY_SIZE=512
ROLE_NAME=lambda-execution-role

QUEUE_NAME=openaq-eric
DB_INSTANCE=eric-airmax-postgres-db
DB_USER=airmax_lambda

# Both numbers come from measuring the queue. It delivers in bursts exactly 6 hours apart, each one ~3.5k messages,
# published over ~150s and then silence. That is ~24 a second while a burst lasts, so 500 messages accumulate in ~21s.
# The window has to be longer than that: on a shorter one we would invoke early with a half-full batch and gain nothing.
#
# Bigger batches are what we want here. One batch is one connection and one transaction, and setting up the connection
# costs more than the write does. 500 turns a burst into ~7 invocations, and weighs ~1.2 MB, way under SQS's payload
# limit of 6 MB.
BATCH_SIZE=500
BATCHING_WINDOW=60
MAX_CONCURRENCY=5
VISIBILITY_TIMEOUT=$((TIMEOUT * 6 + BATCHING_WINDOW))  # AWS recommendation

export AWS_PROFILE="${AWS_PROFILE:-airmax}"
export AWS_REGION="${AWS_REGION:-eu-west-1}"
export AWS_PAGER=""

trigger_change=none
case "${1:-}" in
    --enable) trigger_change=enable ;;
    --disable) trigger_change=disable ;;
    "") ;;
    *)
        echo "usage: $(basename "$0") [--enable|--disable]" >&2
        exit 1
        ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

"$repo_root/scripts/build_lambda_zip.sh"
zip_path="$repo_root/dist/lambda.zip"

echo
echo "==> Looking up what we deploy against"
# Nothing here is hardcoded, so this script keeps working if the database is replaced or the queue is recreated
role_arn="$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)"
db_host="$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
    --query 'DBInstances[0].Endpoint.Address' --output text)"
queue_url="$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --query 'QueueUrl' --output text)"
queue_arn="$(aws sqs get-queue-attributes --queue-url "$queue_url" --attribute-names QueueArn \
    --query 'Attributes.QueueArn' --output text)"
echo "    role:     $role_arn"
echo "    database: $db_host"
echo "    queue:    $queue_arn"

# DB_NAME and DB_PORT are left to their defaults in the handler. There is no password to set: the handler mints an IAM
# token per invocation, so there is no secret here or anywhere else.
environment="Variables={DB_HOST=$db_host,DB_USER=$DB_USER}"

echo
if aws lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
    echo "==> Updating $FUNCTION_NAME"
    # Code and configuration are two calls, and Lambda refuses the second while the first is still settling, hence the
    # wait between them.
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --architectures "$ARCHITECTURE" \
        --zip-file "fileb://$zip_path" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"

    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --role "$role_arn" \
        --handler "$HANDLER" \
        --runtime "$RUNTIME" \
        --timeout "$TIMEOUT" \
        --memory-size "$MEMORY_SIZE" \
        --environment "$environment" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$FUNCTION_NAME"
else
    echo "==> Creating $FUNCTION_NAME"
    aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --role "$role_arn" \
        --handler "$HANDLER" \
        --runtime "$RUNTIME" \
        --architectures "$ARCHITECTURE" \
        --timeout "$TIMEOUT" \
        --memory-size "$MEMORY_SIZE" \
        --environment "$environment" \
        --tags "Owner=eric" \
        --zip-file "fileb://$zip_path" >/dev/null
    aws lambda wait function-active-v2 --function-name "$FUNCTION_NAME"
fi

echo
echo "==> Setting the queue's visibility timeout to ${VISIBILITY_TIMEOUT}s"
aws sqs set-queue-attributes --queue-url "$queue_url" --attributes "VisibilityTimeout=$VISIBILITY_TIMEOUT"

echo
echo "==> Wiring the queue to the function"
mapping_uuid="$(aws lambda list-event-source-mappings \
    --function-name "$FUNCTION_NAME" \
    --event-source-arn "$queue_arn" \
    --query 'EventSourceMappings[0].UUID' --output text)"

if [[ "$mapping_uuid" == "None" || -z "$mapping_uuid" ]]; then
    enabled_flag=--no-enabled
    [[ "$trigger_change" == "enable" ]] && enabled_flag=--enabled
    mapping_uuid="$(aws lambda create-event-source-mapping \
        --function-name "$FUNCTION_NAME" \
        --event-source-arn "$queue_arn" \
        --batch-size "$BATCH_SIZE" \
        --maximum-batching-window-in-seconds "$BATCHING_WINDOW" \
        --scaling-config "MaximumConcurrency=$MAX_CONCURRENCY" \
        "$enabled_flag" \
        --query 'UUID' --output text)"
    echo "    created $mapping_uuid"
else
    # A mapping in Creating/Enabling/Disabling rejects an update, so we let it settle first
    for _ in $(seq 30); do
        state="$(aws lambda get-event-source-mapping --uuid "$mapping_uuid" --query 'State' --output text)"
        [[ "$state" == "Enabled" || "$state" == "Disabled" ]] && break
        sleep 2
    done

    # The enabled flag is appended only when asked for, so a plain run leaves the trigger in whatever state it is in
    update_args=(
        --uuid "$mapping_uuid"
        --batch-size "$BATCH_SIZE"
        --maximum-batching-window-in-seconds "$BATCHING_WINDOW"
        --scaling-config "MaximumConcurrency=$MAX_CONCURRENCY"
    )
    [[ "$trigger_change" == "enable" ]] && update_args+=(--enabled)
    [[ "$trigger_change" == "disable" ]] && update_args+=(--no-enabled)
    aws lambda update-event-source-mapping "${update_args[@]}" >/dev/null
    echo "    updated $mapping_uuid"
fi

echo
echo "==> Deployed"
aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
    --query '[FunctionName,Runtime,Architectures[0],Handler,Timeout,MemorySize,LastUpdateStatus]' --output text
aws lambda get-event-source-mapping --uuid "$mapping_uuid" \
    --query '[State,BatchSize,ScalingConfig.MaximumConcurrency]' --output text
echo
echo "backlog: $(aws sqs get-queue-attributes --queue-url "$queue_url" \
    --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text) messages"
echo "logs:    aws logs tail /aws/lambda/$FUNCTION_NAME --follow --profile $AWS_PROFILE --region $AWS_REGION"
