from kubernetes import client, config

# load local kubeconfig
config.load_kube_config()

# or when running in a cluster, use ie. a ServiceAccount.
# config.load_incluster_config()

k8s_client = client.BatchV1Api()

namespace = "assets"

cron_jobs = k8s_client.list_namespaced_cron_job(namespace=namespace)

print(cron_jobs)
