import logging
import signal

import boto3
from django.core.management.base import BaseCommand

from airmax.ingest import StoreResult, parse_message, store

log = logging.getLogger(__name__)

QUEUE_NAME = "openaq-eric"
REGION = "eu-west-1"

WAIT_TIME = 20  # Long polling; short polling samples a subset of servers and returns empty a lot
BATCH_SIZE = 10  # SQS limit without Lambda


class Command(BaseCommand):
    help = (
        "Poll the OpenAQ SQS queue continuously and upsert what it delivers. "
        "Credentials come from the environment — export AWS_PROFILE=airmax."
    )

    def add_arguments(self, parser):
        parser.add_argument("--queue", default=QUEUE_NAME)
        parser.add_argument("--region", default=REGION)
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete each batch after its write commits. Off by default: the queue holds a "
            "four-day backlog that exists exactly once, and deleting while debugging burns it.",
        )
        parser.add_argument(
            "--batches",
            type=int,
            help="Stop after this many receives. Default is to run until interrupted.",
        )

    def handle(self, *args, queue, region, delete, batches, **options):
        """Receives, writes, then deletes — in that order, one batch at a time.

        Nothing is ever deleted before its write has committed, so a crash costs at most a
        redelivery, which the upsert absorbs. An empty receive is the normal state: the
        simulator fires a burst roughly every six hours and the queue is quiet in between.
        """
        sqs = boto3.client("sqs", region_name=region)
        queue_url = sqs.get_queue_url(QueueName=queue)["QueueUrl"]
        log.info("Polling %s (deleting after write: %s)", queue_url, delete)

        self.stop_requested = False
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

        totals = StoreResult()
        polls = received = written = failed = 0

        while not self.stop_requested and (batches is None or polls < batches):
            messages = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=BATCH_SIZE,
                WaitTimeSeconds=WAIT_TIME,
            ).get("Messages", [])
            polls += 1
            received += len(messages)
            if not messages:
                # The expected state between bursts, and once every twenty seconds it is not
                # worth an INFO line each time.
                log.debug("Received nothing, %s empty", QUEUE_NAME)
                continue

            measurements, handles, batch_failed = self.parse_batch(messages)
            failed += batch_failed

            # Both of these are transactions of their own: `store` commits before anything is
            # deleted, and a shutdown between the two only replays the batch.
            result = store(measurements)
            if delete and handles:
                self.delete_batch(sqs, queue_url, handles)

            totals += result
            batch_written = result.measurements_created + result.measurements_updated
            written += batch_written
            log.info(
                "Batch: received %d, written %d (%d new, %d updated), failed %d",
                len(messages),
                batch_written,
                result.measurements_created,
                result.measurements_updated,
                batch_failed,
            )

        log.info(
            "Stopped after %d polls: received %d, written %d (%d new, %d updated), failed %d, "
            "%d locations new",
            polls,
            received,
            written,
            totals.measurements_created,
            totals.measurements_updated,
            failed,
            totals.locations_created,
        )

    def request_stop(self, signum, frame):
        """Sets the flag the loop reads, so a stop lands between batches and the closing totals
        still get logged. This is tidiness, not safety — the write is atomic and the delete
        follows its commit, so a hard kill at any point costs a redelivery at worst.

        SQS's long poll is not interruptible, so stopping can take up to the wait time.
        """
        self.stop_requested = True
        log.info("Stop requested, finishing the current batch")

    def parse_batch(self, messages):
        """Splits a received batch into what can be stored and what cannot.

        `parse_message` raises for anything it cannot turn into a storable measurement — a payload
        that does not parse, and one whose parameter/unit pair the check constraint would refuse.
        Catching that per message is what keeps a single bad record out of `store`'s one
        `bulk_create`, where it would roll back the nine good rows beside it.

        A dropped message is not deleted, so it returns after the visibility timeout rather than
        vanishing, and a genuinely poisonous one would come back forever. Bounding that is a
        redrive policy's job: it parks the message in a dead-letter queue after N receives, where
        it keeps its body and can be inspected and redriven back to the source. Not attached yet —
        `maxReceiveCount` counts receives rather than failures, so with `--delete` off the whole
        backlog would migrate to the DLQ. It only makes sense once deletion is permanently on.
        """
        measurements, handles, failed = [], [], 0

        for message in messages:
            try:
                measurement = parse_message(message)
            except (ValueError, KeyError, TypeError) as error:
                failed += 1
                log.warning("Skipped message %s: %s", message.get("MessageId"), error)
                continue

            measurements.append(measurement)
            handles.append(message["ReceiptHandle"])

        return measurements, handles, failed

    def delete_batch(self, sqs, queue_url, handles):
        """Deletes the handles whose rows are already committed. A receive brings at most ten
        messages and a delete accepts ten, so this is always a single call."""
        response = sqs.delete_message_batch(
            QueueUrl=queue_url,
            Entries=[{"Id": str(i), "ReceiptHandle": handle} for i, handle in enumerate(handles)],
        )
        log.info("Deleted %d messages", len(response.get("Successful", [])))
        for failure in response.get("Failed", []):
            # The row is written either way, so a failed delete costs a redelivery, not data.
            log.warning("Delete failed for entry %s: %s", failure["Id"], failure.get("Message"))
