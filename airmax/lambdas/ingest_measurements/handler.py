"""Lambda that ingests Airmax measurements from the OpenAQ SQS queue into Postgres."""

import logging
import os

import boto3
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webappconf.settings")

# Populates the app registry, which `airmax.lambdas.ingest_measurements.ingest` needs before it can import models. Opens no
# database connection — that waits for the first query, by which time we have set a password.
django.setup()

from django.db import connections

from airmax.lambdas.ingest_measurements.ingest import parse_measurement_body, upsert_measurements

log = logging.getLogger(__name__)

# Set by the runtime, the fallback is only for running this module outside Lambda
REGION = os.environ.get("AWS_REGION", "eu-west-1")

rds = boto3.client("rds", region_name=REGION)


def refresh_auth_token():
    """Mints an IAM auth token and sets it as the connection's password. The token is a signed string we pass instead of
    an actual password. That way nothing is stored and there is nothing to rotate. It expires after 15 minutes, so we
    mint one per invocation and close the connection afterwards rather than track expiry.

    It goes on the wrapper's own `settings_dict` rather than on `settings.DATABASES`, because Django already built the
    connection when we imported the models.
    """
    connection = connections["default"]
    connection.settings_dict["PASSWORD"] = rds.generate_db_auth_token(
        DBHostname=connection.settings_dict["HOST"],
        Port=int(connection.settings_dict["PORT"]),
        DBUsername=connection.settings_dict["USER"],
        Region=REGION,
    )


def parse_measurements(records):
    """Splits a batch into the measurements we can store and a count of the messages we cannot.

    A message we cannot parse is dropped: we log it, count it and move on. Retrying it would be pointless, because the
    three exceptions below are all faults in the message itself so they would keep erroring every time. Handing them
    back as batch item failures would just send them round the queue again until the retention expired. Anything else
    raises and takes the whole batch back to the queue, which is what we want: those failures are transient, and the
    upsert makes the replay harmless.

    Dropping is a PoC simplification. In production we would keep the raw body somewhere we can read it back, so a
    rejected message is something we can investigate rather than a number in a log.
    """
    measurements, unparseable = [], 0

    for record in records:
        try:
            measurements.append(parse_measurement_body(record["body"]))
        except (ValueError, KeyError, TypeError) as error:
            unparseable += 1
            log.warning("Dropped unparseable message %s: %s", record.get("messageId"), error)

    return measurements, unparseable


def handler(event, context):
    """
    Writes one batch of SQS records. A batch ends one of two ways:
        * We return, handing back an empty `batchItemFailures`, and the event source reads that as a complete success
        and deletes every message in the batch.
        * Something raises and then the `return` below never runs at all: Lambda sees an unhandled exception, treats the
        batch as a complete failure, and puts all of it back on the queue once the visibility timeout is up. Both
        `upsert_measurements` and the queue's own deduplication make that replay harmless.

    So a message we could not parse is never what fails a batch. Those are counted in `parse_measurements` and dropped,
    and a batch full of them still returns success.
    """
    records = event["Records"]
    try:
        refresh_auth_token()
        measurements, unparseable = parse_measurements(records)
        result = upsert_measurements(measurements)
        log.info(
            "Batch: received %d, written %d (%d new, %d updated), unparseable %d",
            len(records),
            result.measurements_created + result.measurements_updated,
            result.measurements_created,
            result.measurements_updated,
            unparseable,
        )
        return {"batchItemFailures": []}
    finally:
        connections.close_all()
