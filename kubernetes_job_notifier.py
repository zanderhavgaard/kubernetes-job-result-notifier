"""
Send slack messages when Kubernetes Jobs finish.
"""

from enum import Enum
from dataclasses import dataclass
from time import sleep, time
from datetime import datetime
from kubernetes import client, config
from loguru import logger
from dynaconf import Dynaconf
from slack_sdk import WebhookClient


class JobState(Enum):
    """
    Represent possible states of job, makes it convenient to switch on
    """

    ACTIVE = "active"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass
class ParsedKubernetesMetadata:
    """
    Hold just the relevant job metadata for sending the slack message
    """

    name: str
    state: JobState
    creation_timestamp: datetime
    name_hash: str


def _send_slack_message_job_failed(
    webhook_url: str,
    slack_users_to_notify: list[str],
    namespace: str,
    job_metadata: ParsedKubernetesMetadata,
) -> None:
    logger.info(
        f"Sending for job: {job_metadata.name}, created at {str(job_metadata.creation_timestamp)} in namespace {namespace} ..."
    )

    # create the message blocks
    message_blocks = [
        {
            "type": "divider",
        },
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Job failed! :rotating_light:",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Job Name: `{job_metadata.name}`\n"
                f"Namespace: `{namespace}`\n"
                f"Creation timestamp `{str(job_metadata.creation_timestamp)}`",
            },
        },
    ]
    if slack_users_to_notify:
        message_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Notifying: {slack_users_to_notify}",
                },
            },
        )

    logger.info("Creating slack API client ...")
    webhook_client = WebhookClient(webhook_url)

    logger.info("Sending slack message ...")
    response = webhook_client.send(blocks=message_blocks)  # type: ignore

    if response:
        logger.info(
            f"Response received, status code: {response.status_code}, body: {response.body}"
        )

    if response.status_code == 200 and response.body == "ok":
        logger.info(f"Successfully sent slack message for job: {job_metadata.name}.")


def _send_slack_message_job_succeeded(
    webhook_url: str,
    namespace: str,
    job_metadata: ParsedKubernetesMetadata,
) -> None:
    logger.info(
        f"Sending for job: {job_metadata.name}, created at {job_metadata.creation_timestamp} in namespace {namespace} ..."
    )

    # create the message blocks
    message_blocks = [
        {
            "type": "divider",
        },
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Job Completed Successfully :white_check_mark:",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Job Name: `{job_metadata.name}`\n"
                f"Namespace: `{namespace}`\n"
                f"Creation timestamp `{str(job_metadata.creation_timestamp)}`",
            },
        },
    ]

    logger.info("Creating slack API client ...")
    webhook_client = WebhookClient(webhook_url)

    logger.info("Sending slack message ...")
    response = webhook_client.send(blocks=message_blocks)  # type: ignore

    if response:
        logger.info(
            f"Response received, status code: {response.status_code}, body: {response.body}"
        )

    if response.status_code == 200 and response.body == "ok":
        logger.info(f"Successfully sent slack message for job: {job_metadata.name}.")


def _create_kubernetes_api_client(use_kube_config: bool = False) -> client.BatchV1Api:
    if use_kube_config:
        # load local kubeconfig
        config.load_kube_config()  # type: ignore
    else:
        # or when running in a cluster, use ie. a ServiceAccount.
        config.load_incluster_config()  # type: ignore
    kubernetes_client = client.BatchV1Api()

    return kubernetes_client


def _load_settings() -> Dynaconf:
    settings = Dynaconf(
        envvar_prefix=False, environments=False, load_dotenv=False, settings_files=[]
    )
    print(settings.NAMESPACE)

    return settings


def _delete_old_job_hashes(
    expiration_time_seconds: int, seen_jobs: dict[str, float]
) -> None:
    hashes_to_delete = []
    current_time = time()
    for job_hash, timestamp in seen_jobs.items():
        if current_time - timestamp > int(expiration_time_seconds):
            hashes_to_delete.append(job_hash)
    for job_hash in hashes_to_delete:
        del seen_jobs[job_hash]


def _parse_kubernetes_jobs(
    job_list: client.V1JobList,
) -> list[ParsedKubernetesMetadata]:
    parsed_jobs = []

    for job in job_list.items:
        # assert shenanigans to satisfy mypy

        # extract metadata
        assert job
        job_metadata = job.metadata
        assert job_metadata
        assert job_metadata.name
        job_name = job_metadata.name
        job_creation_timestamp = job_metadata.creation_timestamp
        assert job_creation_timestamp
        job_creation_timestamp_str = str(int(job_creation_timestamp.timestamp()))

        # extract job status
        assert job.status
        job_status = job.status
        # job status is an int
        if job_status.active:
            job_state = JobState.ACTIVE
        elif job_status.succeeded:
            job_state = JobState.SUCCEEDED
        elif job_status.failed:
            job_state = JobState.FAILED
        else:
            raise RuntimeError(f"Could not determine job status, for job: {job.status}")

        # create a unique hashable value for the job
        job_hash = f"{job_name}-{job_creation_timestamp_str}"

        parsed_job = ParsedKubernetesMetadata(
            name=job_name,
            state=job_state,
            creation_timestamp=job_creation_timestamp,
            name_hash=job_hash,
        )

        parsed_jobs.append(parsed_job)

    return parsed_jobs


def entrypoint() -> None:
    # pylint: disable=too-many-locals
    """
    Main loop querying the Kuberntes Batch API for Job objects and sending messages based on their status.
    """
    # dict holding unique hashes of job-name and time stamp of when the job was created,
    # the value is when the job was looked at, and we use it to delete old hashes
    seen_jobs = {}

    logger.info("Loading config ...")
    settings = _load_settings()
    slack_webhook_url = settings.get("SLACK_WEBHOOK_URL")
    namespace = settings.get("NAMESPACE")
    send_message_on_job_success = settings.get(
        "SEND_MESSAGE_ON_JOB_SUCCESS", default=False
    )
    loop_sleep_seconds = settings.get("LOOP_SLEEP_SECONDS", default=5)
    use_kube_config = settings.get("USE_KUBE_CONFIG", default=False)
    # list of userid strings,
    # click on user profile in the slack ui, from the kebab menu select the "copy member id" option to get the user id
    # inject as a JSON list: 'SLACK_USERS_TO_NOTIFY='["<userid>","<userid>","<userid>"]'
    slack_users_to_notify = settings.get("SLACK_USERS_TO_NOTIFY", default=[])
    if slack_users_to_notify:
        slack_users_to_notify = [f"<@{userid}>" for userid in slack_users_to_notify]
        logger.info(f"Will notify users with userids: {slack_users_to_notify}")

    logger.info(f"Using namespace: '{namespace}'.")
    logger.info(f"Sleeping {loop_sleep_seconds} seconds between API calls.")
    if send_message_on_job_success:
        logger.info("Will send a slack message on successfull job completions.")
    if use_kube_config:
        logger.info("Will use local kubeconfig for authentication.")

    logger.info("Creating API client ...")
    kubernetes_client = _create_kubernetes_api_client(use_kube_config=use_kube_config)

    logger.info("Starting main loop ...")
    while True:
        job_list: client.V1JobList = kubernetes_client.list_namespaced_job(
            namespace=namespace
        )

        parsed_jobs = _parse_kubernetes_jobs(job_list=job_list)

        for job in parsed_jobs:

            match job.state:
                case JobState.ACTIVE:
                    # if job is still running, continue
                    continue
                case JobState.SUCCEEDED:
                    if job.name_hash in seen_jobs:
                        # if we have seen this job before, continue
                        continue
                    if send_message_on_job_success:
                        logger.info(
                            f"Saw completed job: {job.name}, sending slack message ..."
                        )
                        _send_slack_message_job_succeeded(
                            webhook_url=slack_webhook_url,
                            namespace=namespace,
                            job_metadata=job,
                        )
                        # add job to dict, so we don't send message twice
                        logger.info(f"adding hash: {job.name_hash}")
                        seen_jobs[job.name_hash] = time()
                case JobState.FAILED:
                    if job.name_hash in seen_jobs:
                        # if we have seen this job before, continue
                        continue

                    logger.info(
                        f"Saw failed job: {job.name}, sending slack message ..."
                    )
                    _send_slack_message_job_failed(
                        webhook_url=slack_webhook_url,
                        slack_users_to_notify=slack_users_to_notify,
                        namespace=namespace,
                        job_metadata=job,
                    )
                    # add job to dict, so we don't send message twice
                    logger.info(f"adding hash: {job.name_hash}")
                    seen_jobs[job.name_hash] = time()

        print("---")
        print("seen jobs")
        print(seen_jobs)
        print("---")

        # delete old job hashes
        expiration_time_seconds = 30
        logger.info("Deleting hashes ...")
        _delete_old_job_hashes(
            expiration_time_seconds=expiration_time_seconds, seen_jobs=seen_jobs
        )

        logger.info("sleeping ... zzzz")

        # sleep until next iteration
        sleep(loop_sleep_seconds)


if __name__ == "__main__":
    logger.info("Starting ...")
    entrypoint()
