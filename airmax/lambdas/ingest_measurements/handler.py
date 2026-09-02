"""Lambda that ingests Airmax measurements from the OpenAQ SQS queue into Postgres."""

import logging
import os
from contextlib import closing

import boto3
import psycopg

from airmax.lambdas.ingest_measurements.ingest import parse_measurement_body, upsert_measurements

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Set by the runtime, the fallback is only for running this module outside Lambda
REGION = os.environ.get("AWS_REGION", "eu-west-1")

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "airmax")
DB_USER = os.environ["DB_USER"]

rds = boto3.client("rds", region_name=REGION)


def connect():
    """Opens a connection authenticated with an IAM token, a signed string we pass where the password goes. It expires
    after 15mins but this way nothing is stored and there is nothing to rotate."""
    token = rds.generate_db_auth_token(
        DBHostname=DB_HOST, Port=DB_PORT, DBUsername=DB_USER, Region=REGION
    )
    return psycopg.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=token, sslmode="require"
    )


def parse_measurements(records):
    """Splits a batch into the measurements we can store and a count of the messages we cannot. A message we cannot
    parse because of the message format itself is dropped, because we cannot recover from it. However, dropping is a PoC
    simplification. In production, we would keep the raw body somewhere we can read it back, so a rejected message is
    something we can investigate further.
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
        batch as a complete failure, and puts all of it back on the queue once the visibility timeout is up.

    So a message we could not parse is never what fails a batch. Those are counted in `parse_measurements` and dropped,
    and a batch full of them still returns success.
    """
    records = event["Records"]
    measurements, unparseable = parse_measurements(records)

    # Atomic transaction: psycopg opens a transaction on the first execute and `close()` rolls back anything still open.
    # `commit()` below is what makes a batch stick.
    with closing(connect()) as connection:
        result = upsert_measurements(connection, measurements)
        connection.commit()

    log.info(
        "Batch: received %d, written %d (%d new, %d updated), unparseable %d",
        len(records),
        result.measurements_created + result.measurements_updated,
        result.measurements_created,
        result.measurements_updated,
        unparseable,
    )
    return {"batchItemFailures": []}
