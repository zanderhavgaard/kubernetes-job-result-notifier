from time import sleep, time
from datetime import datetime
from kubernetes import config
from kubernetes.client import BatchV1Api
from loguru import logger
from dynaconf import Dynaconf, Validator
from slack_sdk import WebhookClient


def send_slack_message_job_failed(
    webhook_url: str,
    slack_users_to_notify: list[str],
    namespace: str,
    job_name: str,
    job_creation_timestamp: datetime,
) -> None:
    logger.info(
        f"Sending for job: {job_name}, created at {job_creation_timestamp} in namespace {namespace} ..."
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
                "text": f"Job failed! :rotating_light:",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Job Name: `{job_name}`\n"
                f"Namespace: `{namespace}`\n"
                f"Creation timestamp `{str(job_creation_timestamp)}`",
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
    response = webhook_client.send(blocks=message_blocks)

    if response:
        logger.info(
            f"Response received, status code: {response.status_code}, body: {response.body}"
        )

    if response.status_code == 200 and response.body == "ok":
        logger.info(f"Successfully sent slack message for job: {job_name}.")


def send_slack_message_job_succeeded(
    webhook_url: str,
    slack_users_to_notify: list[str],
    namespace: str,
    job_name: str,
    job_creation_timestamp: datetime,
) -> None:
    logger.info(
        f"Sending for job: {job_name}, created at {job_creation_timestamp} in namespace {namespace} ..."
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
                "text": f"Job Completed Successfully :white_check_mark:",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Job Name: `{job_name}`\n"
                f"Namespace: `{namespace}`\n"
                f"Creation timestamp `{str(job_creation_timestamp)}`",
            },
        },
    ]
    # TODO: consider if this is useful
    # if slack_users_to_notify:
    #     message_blocks.append(
    #         {
    #             "type": "section",
    #             "text": {
    #                 "type": "mrkdwn",
    #                 "text": f"Notifying: {slack_users_to_notify}",
    #             },
    #         },
    #     )

    logger.info("Creating slack API client ...")
    webhook_client = WebhookClient(webhook_url)

    logger.info("Sending slack message ...")
    response = webhook_client.send(blocks=message_blocks)

    if response:
        logger.info(
            f"Response received, status code: {response.status_code}, body: {response.body}"
        )

    if response.status_code == 200 and response.body == "ok":
        logger.info(f"Successfully sent slack message for job: {job_name}.")


def create_kubernetes_api_client(use_kube_config: bool = False) -> BatchV1Api:
    if use_kube_config:
        # load local kubeconfig
        config.load_kube_config()
    else:
        # or when running in a cluster, use ie. a ServiceAccount.
        config.load_incluster_config()
    kubernetes_client = BatchV1Api()

    return kubernetes_client


def load_settings() -> Dynaconf:
    settings = Dynaconf(
        envvar_prefix=False, environments=False, load_dotenv=False, settings_files=[]
    )
    print(settings.NAMESPACE)

    return settings


def delete_old_job_hashes(
    expiration_time_seconds: int, seen_jobs: dict[str, int]
) -> None:
    hashes_to_delete = []
    current_time = time()
    for job_hash, timestamp in seen_jobs.items():
        if current_time - timestamp > expiration_time_seconds:
            hashes_to_delete.append(job_hash)
    for hash in hashes_to_delete:
        del seen_jobs[hash]


def entrypoint() -> None:
    # dict holding unique hashes of job-name and time stamp of when the job was created,
    # the value is when the job was looked at, and we use it to delete old hashes
    seen_jobs = {}

    logger.info("Loading config ...")
    settings = load_settings()
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
    print(slack_users_to_notify)
    print(type(slack_users_to_notify))
    print(type(slack_users_to_notify[0]))
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
    kubernetes_client = create_kubernetes_api_client(use_kube_config=use_kube_config)

    logger.info(f"Starting main loop ...")
    while True:
        jobs = kubernetes_client.list_namespaced_job(namespace=namespace)

        for job in jobs.items:
            job_name = job.metadata.name
            job_creation_time = job.metadata.creation_timestamp
            job_creation_timestamp_str = str(int(job_creation_time.timestamp()))
            job_hash = f"{job_name}-{job_creation_timestamp_str}"
            job_status = job.status

            # if job is still running, continue
            if job_status.active:
                continue

            # if we have seen this job before, continue
            if job_hash in seen_jobs:
                continue

            if job_status.failed:
                logger.info(f"Saw failed job: {job_name}, sending slack message ...")
                send_slack_message_job_failed(
                    webhook_url=slack_webhook_url,
                    slack_users_to_notify=slack_users_to_notify,
                    job_name=job_name,
                    job_creation_timestamp=job_creation_time,
                    namespace=namespace,
                )

            if job_status.succeeded:
                if send_message_on_job_success:
                    logger.info(
                        f"Saw completed job: {job_name}, sending slack message ..."
                    )
                    send_slack_message_job_succeeded(
                        webhook_url=slack_webhook_url,
                        slack_users_to_notify=slack_users_to_notify,
                        job_name=job_name,
                        job_creation_timestamp=job_creation_time,
                        namespace=namespace,
                    )

            # add job to dict, so we don't send message twice
            logger.info(f"adding hash: {job_hash}")
            seen_jobs[job_hash] = time()

        # delete old job hashes
        expiration_time_seconds = 30
        logger.info("Deleting hashes ...")
        delete_old_job_hashes(
            expiration_time_seconds=expiration_time_seconds, seen_jobs=seen_jobs
        )

        logger.info("sleeping ... zzzz")

        # sleep until next iteration
        sleep(loop_sleep_seconds)


if __name__ == "__main__":
    logger.info("Starting ...")
    entrypoint()
