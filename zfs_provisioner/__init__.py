import os

class Error(Exception):
    """Base class for all errors in the zfs_provisioner.
    """
    pass


def get_template(template_file):
    template_path = os.path.join(os.path.dirname(__file__), 'templates', template_file)
    template = open(template_path, 'rt').read()
    return template
