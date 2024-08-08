from time import sleep, time
from kubernetes import client, config
from loguru import logger
from dynaconf import Dynaconf, Validator


def send_slack_message_job_failed() -> None:
    print("send slack message here")


def send_slack_message_job_succeeded() -> None:
    print("send slack message here")


def create_k8s_api_client(use_kube_config: bool = False) -> client:
    if use_kube_config:
        # load local kubeconfig
        config.load_kube_config()
    else:
        # or when running in a cluster, use ie. a ServiceAccount.
        config.load_incluster_config()
    k8s_client = client.BatchV1Api()

    return k8s_client


def load_settings() -> Dynaconf:
    settings = Dynaconf(
        envvar_prefix=False, environments=False, load_dotenv=False, settings_files=[]
    )
    print(settings.NAMESPACE)

    return settings


def delete_old_job_hashes(
    expiration_time_seconds: int, seen_jobs: dict[str, int]
) -> None:
    current_time = time()
    for job_hash, timestamp in seen_jobs.items():
        if current_time - timestamp > expiration_time_seconds:
            seen_jobs.pop(job_hash)


def entrypoint() -> None:
    # dict holding unique hashes of job-name and time stamp of when the job was created,
    # the value is when the job was looked at, and we use it to delete old hashes
    seen_jobs = {}

    logger.info("Loading config ...")
    settings = load_settings()
    namespace = settings.get("NAMESPACE")
    send_message_on_job_success = settings.get(
        "SEND_MESSAGE_ON_JOB_SUCCESS", default=False
    )
    loop_sleep_seconds = settings.get("LOOP_SLEEP_SECONDS", default=5)
    use_kube_config = settings.get("USE_KUBE_CONFIG", default=False)

    logger.info(f"Using namespace: '{namespace}'.")
    logger.info(f"Sleeping {loop_sleep_seconds} between API calls.")
    if send_message_on_job_success:
        logger.info("Will send a slack message on successfull job completions.")
    if use_kube_config:
        logger.info("Will use local kubeconfig for authentication.")

    logger.info("Creating API client ...")
    k8s_client = create_k8s_api_client(use_kube_config=use_kube_config)

    logger.info(f"Starting main loop, running every {loop_sleep_seconds} seconds ...")
    while True:
        jobs = k8s_client.list_namespaced_job(namespace=namespace)

        for job in jobs.items:
            job_name = job.metadata.name
            job_creation_time = str(int(job.metadata.creation_timestamp.timestamp()))
            job_hash = f"{job_name}-{job_creation_time}"
            job_status = job.status

            # if job is still running, continue
            if job_status.active:
                continue

            # if we have seen this job before, continue
            if job_hash in seen_jobs:
                continue

            if job_status.failed:
                logger.info(f"Saw failed job: {job_name}, sending slack message ...")
                send_slack_message_job_failed()

            if job_status.succeeded:
                if send_message_on_job_success:
                    logger.info(
                        f"Saw completed job: {job_name}, sending slack message ..."
                    )
                    send_slack_message_job_succeeded()

            # add job to dict, so we don't send message twice
            seen_jobs[job_hash] = time()

        # delete old job hashes
        expiration_time_seconds = 3600
        delete_old_job_hashes(
            expiration_time_seconds=expiration_time_seconds, seen_jobs=seen_jobs
        )

        # sleep until next iteration
        sleep(loop_sleep_seconds)


if __name__ == "__main__":
    logger.info("Starting ...")
    entrypoint()
