import logging
import os
import sys

import click

from . import zfs


@click.group(name='zfs-provisioner')
@click.option('--verbose', '-v', 'log_level', flag_value='info', help='set log level to info', envvar='ZFS_PROVISIONER_LOG_LEVEL')
@click.option('--debug', '-d', 'log_level', flag_value='debug', help='set log level to debug', envvar='ZFS_PROVISIONER_LOG_LEVEL')
@click.pass_context
def main(ctx, log_level):
    """ZFS volume provisoner for kubernetes.
    """
    setattr(ctx, 'obj', {})

    logging.basicConfig(level=logging.ERROR, format='%(levelname)s: %(message)s', stream=sys.stdout)

    log = logging.getLogger('zfs-provisioner')
    if log_level:
        log.setLevel(getattr(logging, log_level.upper()))
    ctx.obj['log_level'] = log_level
    ctx.obj['log'] = log


@main.command(name='controller', short_help='start controller')
@click.option('--provisioner', 'provisioner_name', help='Specify Provisioner name.',
    envvar='PROVISIONER_NAME')
@click.option('--namespace', help='The namespace the Provisioner is running in.',
    envvar='NAMESPACE')
@click.option('--config', help='Provisioner configuration file.', envvar='CONFIG')
@click.option('--dataset-mount-dir', help='Directory under which to mount the created persistent volumes.',
    envvar='DATASET_MOUNT_DIR')
@click.option('--node-name', help='The name of the node on which the provisioner is running.',
    envvar='NODE_NAME')
@click.option('--kl', 'set_kopf_log_level', help='also set kopf\'s log level',
    is_flag=True, default=False)
@click.pass_context
def controller(ctx, provisioner_name, namespace, config, node_name, dataset_mount_dir, set_kopf_log_level):
    log = ctx.obj['log']
    log.debug('controller: provisioner_name: %s', provisioner_name)
    log.debug('controller: namespace: %s', namespace)
    log.debug('controller: config: %s', config)
    log.debug('controller: dataset_mount_dir: %s', dataset_mount_dir)
    log.debug('controller: node_name: %s', node_name)

    if set_kopf_log_level:
        logging.getLogger('kopf').setLevel(log.getEffectiveLevel())

    # Import handlers module so they are registered with kopf.
    from . import handlers
    # Pass cli options to handlers.
    handlers.configure(
        provisioner_name=provisioner_name,
        namespace=namespace,
        config=config,
        node_name=node_name,
        dataset_mount_dir=dataset_mount_dir,
    )

    log.info('Starting controller ...')
    from kopf.reactor import running
    running.run()


@main.group(name='dataset', short_help='manage datasets')
@click.pass_context
def dataset(ctx):
    pass


@dataset.command(name='create', short_help='create dataset')
@click.argument('dataset')
@click.argument('mountpoint')
@click.option('--quota', help='Quota of the dataset.')
@click.option('--refquota', help='Refquota of the dataset.')
@click.pass_context
def dataset_create(ctx, dataset, mountpoint, quota, refquota):
    """Create the given DATASET and mount it to MOUNTPOINT
    while optionally setting a quota and/or refquota.

    Ensure that the parent dataset, determined from DATASET,
    exists and ensure it has safe permissions.
    """
    log = ctx.obj['log']
    log.debug('%s: %s', ctx.info_name, ctx.params)

    # Ensure we have parent dataset whith mountpoint set to legacy.
    parent = os.path.split(dataset)[0]
    zfs.ensure(parent, mountpoint='legacy')

    # Ensure the mountpoints parent folder exists and has safe permissions.
    mountpoint_dir = os.path.split(mountpoint)[0]
    os.makedirs(mountpoint_dir, mode=0o700, exist_ok=True)
    os.chmod(mountpoint_dir, 0o700)

    # Create our dataset and ensure it is writable by the pod.
    zfs.create(dataset, mountpoint=mountpoint, quota=quota, refquota=refquota)
    os.chmod(mountpoint, 0o777)


@dataset.command(name='destroy', short_help='destroy dataset')
@click.argument('dataset')
@click.argument('mountpoint')
@click.pass_context
def dataset_destroy(ctx, dataset, mountpoint):
    """Destroy the given DATASET and delete it's former MOUNTPOINT.
    """
    log = ctx.obj['log']
    log.debug('%s: %s', ctx.info_name, ctx.params)

    # Destroy the dataset.
    zfs.destroy(dataset)

    # Delete the mountpint.
    os.rmdir(mountpoint)


if __name__ == '__main__':
    main()
