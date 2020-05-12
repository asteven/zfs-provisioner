import asyncio
import dataclasses
import logging

from typing import Optional, Dict, List

import bitmath
import kopf
import kubernetes_asyncio
import yaml

from . import get_template
from .handlers import CONFIG

log = logging.getLogger('zfs-provisioner')


EVENTS: Dict = {
    'create': {},
    'delete': {},
}

POD_TEMPLATE: str = get_template('dataset-pod.yaml')

ACTION_ANNOTATION = 'zfs-provisioner/action-test'


@dataclasses.dataclass
class Dataset():
    name: str
    parent: str
    mount_point: str
    selected_node: str
    size: str = None

    @property
    def full_name(self):
        return f'{self.parent}/{self.name}'

    async def create(self, namespace):
        return await create(self, namespace)

    async def delete(self, namespace):
        return await delete(self, namespace)


def size_in_bytes(size):
    if size[-1:] == 'i':
        # Turn e.g. Gi into GiB so that bitmath understands it.
        size = size + 'B'
    return int(bitmath.parse_string(size).bytes)


def _get_pod(pod_name, node_name, image, dataset_mount_dir, pod_args):
    text = POD_TEMPLATE.format(
        pod_name=pod_name,
        node_name=node_name,
        image=image,
        dataset_mount_dir=dataset_mount_dir,
        log_level=logging.getLevelName(log.getEffectiveLevel()),
    )
    data = yaml.safe_load(text)
    data['spec']['containers'][0]['args'] = pod_args
    return data


async def _run_pod(action, pod_name, body, namespace):
    # Label the pod for filtering in the on.event handler.
    kopf.label(body, {ACTION_ANNOTATION: action})
    event = asyncio.Event()

    obj = None
    async with kubernetes_asyncio.client.ApiClient() as api:
        v1 = kubernetes_asyncio.client.CoreV1Api(api)
        obj = await v1.create_namespaced_pod(
            body=body,
            namespace=namespace,
        )
        EVENTS[action][obj.metadata.uid] = event

    # TODO: handle timeout and error
    log.debug('waiting for pod_event: %s', pod_name)
    await event.wait()
    log.debug('pod_event has been set: %s', pod_name)
    return obj


@kopf.on.event('', 'v1', 'pods', labels={ACTION_ANNOTATION: kopf.PRESENT})
async def on_event(event, name, namespace, meta, status, **_):
    log.debug('datasets.on_event: %s: %s', name, event)

    # Only care about changes to existing pods.
    if event['type'] == 'MODIFIED':

        phase = status['phase']

        if phase in ('Succeeded', 'Failed'):
            action = meta.labels[ACTION_ANNOTATION]
            log.debug('dataset %s: %s -> %s', name, action, phase)

            # Mark this event as done.
            pod_event = EVENTS[action].pop(meta.uid)
            pod_event.set()

            # All done. Delete the pod.
            # TODO: in case of failure get errors and store them somewhere?
            if phase == 'Succeeded':
                # For now keep failed pods around for inspection.
                async with kubernetes_asyncio.client.ApiClient() as api:
                    v1 = kubernetes_asyncio.client.CoreV1Api(api)
                    log.debug('deleting dataset handling pod: %s', name)
                    await v1.delete_namespaced_pod(name, namespace)


async def create(dataset: Dataset, namespace: str):
    """
    - run pod that creates the dataset
    - wait for it to complete
    - return success or error message
    """
    log.debug('dataset.create: %s in namespace: %s', dataset, namespace)

    action = 'create'
    pod_name = f'{dataset.name}-{action}'

    pod_args = ['dataset', 'create']
    if dataset.size:
        size = size_in_bytes(dataset.size)
        pod_args.extend(['--refquota', str(size)])
    pod_args.append(dataset.full_name)
    pod_args.append(dataset.mount_point)

    log.debug('dataset.create: pod_args: %s', pod_args)

    body = _get_pod(pod_name, dataset.selected_node,
        CONFIG.container_image, CONFIG.dataset_mount_dir,
        pod_args)

    return await _run_pod(action, pod_name, body, namespace)


async def delete(dataset, namespace):
    """
    - run pod that destroys the dataset
    - wait for it to complete
    - return success or error message
    """
    log.debug('dataset.delete: %s in namespace: %s', dataset, namespace)

    action = 'delete'
    pod_name = f'{dataset.name}-{action}'

    pod_args = ['dataset', 'destroy']
    pod_args.append(dataset.full_name)
    pod_args.append(dataset.mount_point)

    log.debug('dataset.delete: pod_args: %s', pod_args)

    body = _get_pod(pod_name, dataset.selected_node,
        CONFIG.container_image, CONFIG.dataset_mount_dir,
        pod_args)

    return await _run_pod(action, pod_name, body, namespace)


async def resize(dataset, namespace):
    """
    - run pod that resizes the dataset
    - wait for it to complete
    - return success or error message
    """
    raise NotImplementedError()
