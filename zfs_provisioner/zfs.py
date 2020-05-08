import logging
import subprocess

log = logging.getLogger('zfs-provisioner')

from . import Error


class ZfsCommandError(Error):
    """Error that happened while running a `zfs` command.
    """
    pass


def create(dataset, *args, **properties):
    """Create the given dataset with the given properties.
    """
    cmd = ['zfs', 'create']
    cmd.extend(args)
    for k,v in properties.items():
        if v is not None:
            cmd.extend(['-o', f'{k}={v}'])
    cmd.append(dataset)
    log.debug('zfs.create: %s', cmd)

    try:
        subprocess.check_call(cmd)
    except subprocess.SubprocessError as e:
        log.error(e)
        raise ZfsCommandError(f'Failed to create dataset "{dataset}" running command: {cmd}') from e


def ensure(dataset, *args, **properties):
    """Ensure the given dataset exists
    with the given properties.
    """
    cmd = ['zfs', 'list', '-Hp', dataset]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.SubprocessError as e:
        if e.output and b'dataset does not exist' in e.output:
            return create(dataset, *args, **properties)
        else:
            raise ZfsCommandError(f'Failed to list dataset "{dataset}" running command: {cmd}') from e
    else:
        # Dataset exists, ensure properties are correct.
        set_properties(dataset, **properties)


def destroy(dataset, *args):
    """Destroy the given dataset.
    """
    cmd = ['zfs', 'destroy']
    cmd.extend(args)
    cmd.append(dataset)
    log.debug('zfs.destroy: %s', cmd)

    return
    try:
        subprocess.check_call(cmd)
    except subprocess.SubprocessError as e:
        log.error(e)
        raise ZfsCommandError(f'Failed to destroy dataset "{dataset}" running command: {cmd}') from e


def set_properties(dataset, **properties):
    """Set the given properties on the given dataset.
    """
    cmd = ['zfs', 'set']
    for k,v in properties.items():
        cmd.append(f'{k}={v}')
    cmd.append(dataset)
    log.debug('zfs.set_properties: %s', cmd)
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.SubprocessError as e:
        raise ZfsCommandError(f'Failed to set properties on dataset "{dataset}" running command: {cmd}') from e


def get_properties(dataset, *keys):
    """Get the current properties of the given dataset.
    """
    cmd = ['zfs', 'get', '-Hp']
    cmd.append(','.join(keys))
    cmd.append(dataset)
    log.debug('zfs.get_properties: %s', cmd)
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except subprocess.SubprocessError as e:
        raise ZfsCommandError(f'Failed to get properties for dataset "{dataset}" running command: {cmd}') from e

    output = output.decode('utf-8')
    properties = {}
    for line in output.split('\n'):
        line = line.strip()
        if line:
            parts = line.split('\t')
            properties[parts[1]] = parts[2]
    return properties

