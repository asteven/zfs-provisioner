import asyncio
import dataclasses
import itertools
import json
import logging
import os
import sys

from typing import Optional, Dict, List

import click
import kopf
import kubernetes_asyncio
import yaml


from . import get_template


log = logging.getLogger('zfs-provisioner')


@dataclasses.dataclass
class Config:
    provisioner_name: str = 'asteven/zfs-provisioner'
    namespace: str = 'kube-system'
    default_parent_dataset: str = 'chaos/data/zfs-provisioner'
    dataset_mount_dir: str = '/var/lib/zfs-provisioner'
    container_image: str = 'asteven/zfs-provisioner'
    node_name: Optional[str] = None
    config: Optional[str] = None
    dataset_phase_annotations: Dict[str, str] = dataclasses.field(default_factory=dict)
    storage_classes: Dict[str, Dict] = dataclasses.field(default_factory=dict)
    dataset_annotation: str = 'zfs-provisioner/dataset'


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


# Has the be below CONFIG.
from . import datasets


@dataclasses.dataclass
class StorageClass:
    """https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.17/#storageclass-v1-storage-k8s-io"""
    name: str
    provisioner: str
    allowVolumeExpansion: bool = False
    mountOptions: List[str] = None
    parameters: Dict[str, str] = dataclasses.field(default_factory=dict)
    reclaimPolicy: str = 'Delete'
    volumeBindingMode: str = 'VolumeBindingImmediate'

    # Constants
    MODE_LOCAL: str = 'local'
    MODE_NFS: str = 'nfs'
    RECLAIM_POLICY_DELETE: str = 'Delete'
    RECLAIM_POLICY_RETAIN: str = 'Retain'

    @classmethod
    def from_dicts(cls, *dicts: List[Dict]) -> 'StorageClass':
        class_fields = {f.name for f in dataclasses.fields(cls)}
        items = itertools.chain(*[d.items() for d in dicts])
        return cls(**{k:v for k,v in items if k in class_fields})


@kopf.on.startup()
async def startup(logger, **kwargs):
    # Load kubernetes_asyncio config as kopf does not do that automatically for us.
    try:
        # Try incluster config first.
        kubernetes_asyncio.config.load_incluster_config()
    except kubernetes_asyncio.config.ConfigException:
        # Fall back to regular config.
        await kubernetes_asyncio.config.load_kube_config()


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
async def create_dataset(name, namespace, body, meta, spec, patch, logger, **_):
    """Schedule a pod that creates the zfs dataset.
    Create the persistent volume to fullfill this claim.
    """
    storage_class_name = spec['storageClassName']
    storage_class = CONFIG.storage_classes[storage_class_name]

    pv_name = f'pvc-{meta.uid}'

    storage_class_mode = storage_class.parameters.get('mode', 'local')
    if storage_class_mode == storage_class.MODE_LOCAL:
        selected_node = meta.annotations['volume.kubernetes.io/selected-node']
        # TODO: get/check parent dataset override from config
        parent_dataset = CONFIG.default_parent_dataset
        dataset_name = pv_name
        mount_point = os.path.join(CONFIG.dataset_mount_dir, pv_name)

        storage = None
        try:
            storage = spec['resources']['requests']['storage']
        except KeyError as e:
            log.error(e)

        dataset = datasets.Dataset(
            name=dataset_name,
            parent=parent_dataset,
            mount_point=mount_point,
            selected_node=selected_node,
            size=storage,
        )
        message = f'zfs dataset {selected_node}:{dataset.full_name}'
        log.info('%s: creating %s', name, message)
        # TODO: proper error handling with exceptions
        obj = await dataset.create(namespace)
        kopf.info(body, reason='Created', message=f'created {message}')
        log.debug('obj: %s', obj)

        # Store dataset for later use in deletion handler.
        patch.metadata.annotations[CONFIG.dataset_annotation] = json.dumps(dataclasses.asdict(dataset))

    #elif storage_class_mode == storage_class.MODE_NFS:
    #   - get nfs server node name from config, schedule create pod create
    #   - setup nfs export, if at all possible using zfs property instead of exportfs
    else:
        raise kopf.HandlerFatalError(f'Unsupported storage class mode: {storage_class_mode}')


    template = get_template('pvc.yaml')
    text = template.format(
        provisioner_name=storage_class.provisioner,
        pv_name=pv_name,
        access_mode=spec['accessModes'][0],
        storage=spec['resources']['requests']['storage'],
        pvc_name=name,
        pvc_namespace=namespace,
        local_path=mount_point,
        selected_node_name=selected_node,
        storage_class_name=storage_class_name,
        volume_mode=spec['volumeMode'],
        reclaim_policy=storage_class.reclaimPolicy,
    )
    data = yaml.safe_load(text)

    message = f'persistent volume {pv_name}'
    log.info('%s: creating %s', name, message)
    async with kubernetes_asyncio.client.ApiClient() as api:
        v1 = kubernetes_asyncio.client.CoreV1Api(api)
        obj = await v1.create_persistent_volume(
            body=data,
        )
    kopf.info(body, reason='Bound', message=f'bound {message}')


def filter_delete_dataset(body, meta, spec, status, **_):
    """Filter function for delete handlers that filters out the PVCs for which
    the dataset deletion process can be started.
    """
    # Only care about PVCs that are in state Pending.
    # TODO: in which phases can we safely delete a dataset?
    #if status.get('phase', None) != 'Pending':
    #    return False

    # Only care about PVCs that we are not already working on.
    if CONFIG.dataset_phase_annotations['delete'] in meta.annotations:
        return False

    handle_it = False
    try:
        # Only care about PVCs that have a storage class that we are responsible for.
        storage_class_name = spec['storageClassName']
        storage_class = CONFIG.storage_classes[storage_class_name]
        handle_it = True

        # Check storage class specific settings.
        if storage_class.reclaimPolicy == storage_class.RECLAIM_POLICY_DELETE:
            handle_it = True
    except KeyError:
        handle_it = False
    return handle_it


@kopf.on.delete('', 'v1', 'persistentvolumeclaims',
    when=filter_delete_dataset)
async def delete_dataset(name, namespace, body, meta, spec, **_):
    """Schedule a pod that deletes the zfs dataset.
    """
    storage_class_name = spec['storageClassName']
    storage_class = CONFIG.storage_classes[storage_class_name]

    storage_class_mode = storage_class.parameters.get('mode', 'local')
    if storage_class_mode == storage_class.MODE_LOCAL:
        # TODO: do I really have to care about storage class mode when
        #       taking the dataset from a annotation or is everything already
        #       correct for any mode?

        if storage_class.reclaimPolicy == storage_class.RECLAIM_POLICY_DELETE:
            # Only delete datasets if reclaimPolicy says so.
            dataset_dict = json.loads(meta.annotations[CONFIG.dataset_annotation])
            dataset = datasets.Dataset(**dataset_dict)
            log.debug('delete_dataset: %s', dataset)

            message = f'zfs dataset {dataset.selected_node}:{dataset.full_name}'
            log.info('%s: deleting %s', name, message)
            # TODO: proper error handling with exceptions
            obj = await dataset.delete(namespace)
            kopf.info(body, reason='Deleted', message='deleted {message}')
            log.debug('obj: %s', obj)

        # Delete the persistent volume.
        pv_name = spec['volumeName']
        message = f'persistent volume {pv_name}'
        log.info('%s: deleting %s', name, message)
        async with kubernetes_asyncio.client.ApiClient() as api:
            v1 = kubernetes_asyncio.client.CoreV1Api(api)
            obj = await v1.delete_persistent_volume(pv_name)
        kopf.info(body, reason='Unbound', message='unbound {message}')

    #elif storage_class_mode == storage_class.MODE_NFS:
    #   - get nfs server node name from config, schedule delete pod there
    #   - unexport nfs share, if at all possible using zfs property instead of exportfs
    else:
        raise kopf.HandlerFatalError(f'Unsupported storage class mode: {storage_class_mode}')


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
