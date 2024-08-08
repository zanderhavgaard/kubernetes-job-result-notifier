from time import sleep
from kubernetes import client, config

# load local kubeconfig
config.load_kube_config()

# or when running in a cluster, use ie. a ServiceAccount.
# config.load_incluster_config()

k8s_client = client.BatchV1Api()

namespace = "assets"

finished_jobs = {}


def send_slack_message():
    print("send slack message here")


send_message_on_job_success = False
loop_sleep_seconds = 5

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
        if job_hash in finished_jobs:
            continue

        if job_status.failed:
            send_slack_message()

        if job_status.succeeded:
            if send_message_on_job_success:
                send_slack_message()

        # add job to dict, so we don't send message twice
        finished_jobs[job_hash] = True

    sleep(loop_sleep_seconds)
