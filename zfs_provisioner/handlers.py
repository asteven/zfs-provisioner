import asyncio
import dataclasses
import itertools
import json
import logging
import os
import sys

from functools import wraps
from typing import Optional, Dict, Set, List

import yaml

import click
import kopf

from kopf.structs import patches
from kopf.structs import resources
from kopf.clients import patching

import kubernetes


log = logging.getLogger('zfs-provisioner')


@dataclasses.dataclass
class Config:
    provisioner_name: str = 'asteven/zfs-provisioner'
    namespace: str = 'kube-system'
    dataset_mount_dir: str = '/var/lib/local-zfs-provisioner'
    container_image: str = 'asteven/zfs-provisioner'
    node_name: Optional[str] = None
    config: Optional[str] = None
    dataset_phase_annotations: Dict[str, str] = dataclasses.field(default_factory=dict)
    storage_classes: Dict[str, Dict] = dataclasses.field(default_factory=dict)


CONFIG = Config(
    dataset_phase_annotations={
        action:f'zfs-provisioner/dataset-phase-{action}'
        for action in ('create', 'delete', 'resize')
    }
)


def configure(**kwargs):
    for k,v in kwargs.items():
        if v is not None:
            setattr(CONFIG, k, v)


@dataclasses.dataclass
class StorageClass:
    """https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.17/#storageclass-v1-storage-k8s-io"""
    name: str
    provisioner: str
    allowVolumeExpansion: bool = False
    mountOptions: List[str] = None
    parameters: Dict[str, str] = dataclasses.field(default_factory=dict)
    reclaimPolicy: str = None
    volumeBindingMode: str = None

    @classmethod
    def from_dicts(cls, *dicts: List[Dict]) -> 'StorageClass':
        class_fields = {f.name for f in dataclasses.fields(cls)}
        items = itertools.chain(*[d.items() for d in dicts])
        return cls(**{k:v for k,v in items if k in class_fields})


def filter_provisioner(body, **_):
    return body.get('provisioner', None) == CONFIG.provisioner_name


@kopf.on.resume('storage.k8s.io', 'v1', 'storageclasses',
    when=filter_provisioner)
@kopf.on.create('storage.k8s.io', 'v1', 'storageclasses',
    when=filter_provisioner)
def cache_storage_class(name, body, meta, logger, **kwargs):
    """Load storage class properties and parameters from API server.
    """
    log.info('Watching for PVCs with storage class: %s', name)
    storage_class = StorageClass.from_dicts(meta, body)
    CONFIG.storage_classes[name] = storage_class
    log.debug('Caching storage class %s as: %s', name, storage_class)


def annotate_results(fn):
    """A decorator that persists handler results as annotations on the
    resource.

    Before calling a handler, load any existing results from
    annotations and pass them as the keyword argument 'results'
    to the handler.

    Store any outcome returned by the handler in the annotation.
    """
    key = 'zfs-provisioner/results'

    @wraps(fn)
    def wrapper(*args, **kwargs):
        meta = kwargs['meta']
        results_json = meta['annotations'].get(key, None)
        if results_json:
            results = json.loads(results_json)
        else:
            results = {}
        kwargs['results'] = results
        result = fn(*args, **kwargs)
        if result:
            results[fn.__name__] = result
            patch = kwargs['patch']
            patch.metadata.annotations[key] = json.dumps(results)

        # We don't return the result as we have handled it ourself.
        # Otherwise kopf tries to store it again in the objects status
        # which doesn't work anyway on k8s >= 1.16.
        #return result

    return wrapper


def get_template(template_file):
    template_path = os.path.join(os.path.dirname(__file__), 'templates', template_file)
    template = open(template_path, 'rt').read()
    return template


def get_example_pod(pod_name, node_name):
    template = get_template('example-pod.yaml')
    text = template.format(
        pod_name=pod_name,
        node_name=node_name,
    )
    data = yaml.safe_load(text)
    return data


def get_dataset_pod(pod_name, node_name, dataset_mount_dir):
    template = get_template('dataset-pod.yaml')
    text = template.format(
        pod_name=pod_name,
        node_name=node_name,
        image=CONFIG.container_image,
        dataset_mount_dir=dataset_mount_dir,
    )
    data = yaml.safe_load(text)
    return data



def filter_create_dataset(body, meta, spec, status, **_):
    """Filter function for resume, create and update handlers
    that filters out the PVCs for which the dataset creation
    process can be started.
    """
    # Only care about PVCs that are in state Pending.
    if status.get('phase', None) != 'Pending':
        return False

    # Only care about PVCs that we are not already working on.
    if CONFIG.dataset_phase_annotations['create'] in meta.annotations:
        return False

    handle_it = False
    try:
        # Only care about PVCs that have a storage class that we are responsible for.
        storage_class_name = spec['storageClassName']
        storage_class = CONFIG.storage_classes[storage_class_name]
        handle_it = True

        # Check storage class specific settings.
        if storage_class.volumeBindingMode == 'WaitForFirstConsumer':
            handle_it = 'volume.kubernetes.io/selected-node' in meta.annotations
    except KeyError:
        handle_it = False
    return handle_it


@kopf.on.resume('', 'v1', 'persistentvolumeclaims',
    when=filter_create_dataset)
@kopf.on.create('', 'v1', 'persistentvolumeclaims',
    when=filter_create_dataset)
@kopf.on.update('', 'v1', 'persistentvolumeclaims',
    when=filter_create_dataset)
@annotate_results
def create_dataset(name, namespace, body, meta, spec, status, patch, **_):
    """Schedule a pod that creates the zfs dataset.
    """
    #log.debug(f'create_dataset: status: {status}')
    #log.debug(f'create_dataset: body: {body}')
    #log.debug(f'STORAGE_CLASS_PARAMETERS: {STORAGE_CLASS_PARAMETERS}')
    log.info('Creating zfs dataset for pvc: %s', name)
    storage_class_name = spec['storageClassName']
    storage_class = CONFIG.storage_classes[storage_class_name]

    # TODO: inspect the storage class to determine how/where to create
    #   the zfs dataset. local vs on nfs server
    log.info(storage_class)

    # TODO: run the real container here instead of the example pod.
    # TODO: inspect the storage class parameters to decide how
    #       and where to run the pod.

#    template = get_template('dataset-pod.yaml')
#    text = template.format(
#        pod_name=pod_name,
#        node_name=node_name,
#        image=container_image,
#        dataset_mount_dir=dataset_mount_dir,
#    )
#    data = yaml.safe_load(text)

    action = 'create'

    #pv_name = f"pvc-{meta['uid']}"
    pod_name = f"{meta['uid']}-{action}"
    #pod_name = 'pod-12345'
    #node_name = 'eu-k8s-01'
    # TODO: get node name from config for non-local volumes.
    selected_node = meta.annotations['volume.kubernetes.io/selected-node']
    data = get_example_pod(pod_name, selected_node)

    # Make the pod a child of the PVC.
    kopf.adopt(data)

    # Label the pod for filtering in the on.event handler.
    kopf.label(data, {'zfs-provisioner/action': action})

    # Create the pod.
    api = kubernetes.client.CoreV1Api()
    obj = api.create_namespaced_pod(
        body=data,
        namespace=namespace,
    )

    # Set the initial dataset creation phase to that of the newly created pod.
    annotation = CONFIG.dataset_phase_annotations[action]
    patch.metadata.annotations[annotation] = obj.status.phase

    # Remember the pods name so that the other handler can delete it after the
    # dataset has been created.
    return {
        'pod-name': obj.metadata.name,
        'phase': obj.status.phase,
        'created': False,
        'path': '/path/to/created-dataset',
    }


pvc_resource = resources.Resource(group='', version='v1', plural='persistentvolumeclaims')

@kopf.on.event('', 'v1', 'pods', labels={'zfs-provisioner/action': kopf.PRESENT})
async def dataset_pod_event(name, event, body, meta, status, namespace, **kwargs):
    """Watch our dataset management pods for success or failures.
    """
    log.debug('pod_event: event: %s', event)
    #log.debug(f'pod_event: body: {body}')

    # Only care about changes to existing pods.
    if event['type'] == 'MODIFIED':

        phase = status['phase']
        action = meta.labels['zfs-provisioner/action']
        annotation = CONFIG.dataset_phase_annotations[action]

        log.debug('pod_event: %s: %s', action, phase)

        if phase in ('Succeeded', 'Failed'):
            log.info('dataset %s: %s -> %s', name, action, phase)
            patch = patches.Patch()
            patch.metadata.annotations[annotation] = phase

            for owner in meta.get('ownerReferences', []):
                if owner['kind'] == 'PersistentVolumeClaim':
                    await patching.patch_obj(
                        resource=pvc_resource,
                        patch=patch,
                        name=owner['name'],
                        namespace=owner.get('namespace', namespace),
                    )


def filter_create_pv(body, meta, spec, status, **_):
    """Filter function for resume, create and update handlers
    that filters out the PVCs for which the PV can be created.
    """
    # Only care about PVCs that are in state Pending.
    if status.get('phase', None) != 'Pending':
        return False

    action = 'create'
    annotation = CONFIG.dataset_phase_annotations[action]

    dataset_phase = meta.annotations.get(annotation, 'Pending')

    # Only care about PVCs for which we have tried to create the dataset.
    if dataset_phase not in ('Succeeded', 'Failed'):
        return False

    return True


@kopf.on.update('', 'v1', 'persistentvolumeclaims',
    when=filter_create_pv)
@annotate_results
def create_pv(name, namespace, body, meta, patch, results, **kwargs):
    action = 'create'
    annotation = CONFIG.dataset_phase_annotations[action]

    dataset_phase = meta.annotations.get(annotation, 'Pending')
    if dataset_phase == 'Pending':
        # TODO: should be handled by filter
        raise kopf.TemporaryError('Dataset has not been created yet.', delay=10)
    elif dataset_phase == 'Failed':
        raise kopf.HandlerFatalError('Failed to create dataset.')
    elif dataset_phase == 'Succeeded':
        api = kubernetes.client.CoreV1Api()

        pod_name = results.get('create_dataset', {}).get('pod-name', None)
        if pod_name:
            log.debug('Deleting dataset creation pod: %s', pod_name)
            api.delete_namespaced_pod(pod_name, namespace)

        # TODO: create PV
        selected_node = meta.annotations.get('volume.kubernetes.io/selected-node', 'unknown')
        log.debug('WOULD NOW CREATE THE PV')
        log.info('Creating PV for pvc: %s on node: %s', name, selected_node)


@click.command()
@click.option('--verbose', '-v', 'log_level', flag_value='info', help='set log level to info', envvar='TENANTCTL_LOG_LEVEL')
@click.option('--debug', '-d', 'log_level', flag_value='debug', help='set log level to debug', envvar='TENANTCTL_LOG_LEVEL')
def main(log_level):
    """Run this module with logging better suited to local development
    then what `kopf run` offers.
    """
    logging.basicConfig(level=logging.ERROR, format='%(levelname)s: %(message)s', stream=sys.stderr)

    global log
    log = logging.getLogger(__name__)
    if log_level:
        log.setLevel(getattr(logging, log_level.upper()))
        logging.getLogger('kopf').setLevel(getattr(logging, log_level.upper()))

    log.debug('Starting kopf ...')
    loop = asyncio.get_event_loop()
    tasks = loop.run_until_complete(kopf.spawn_tasks())
    loop.run_until_complete(kopf.run_tasks(tasks))


if __name__ == '__main__':
    main()
