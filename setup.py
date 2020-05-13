from setuptools import setup, find_packages

command = 'zfs-provisioner'
name = 'zfs_provisioner'

setup(
    name=name,
    version='0.1.0',
    author='Steven Armstrong',
    author_email='steven-%s@armstrong.cc' % name,
    description='ZFS volume provisioner for kubernetes.',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
        'Operating System :: POSIX :: Linux',
    ],
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click',
        'pyyaml',
        'kopf',
        'kubernetes',
        'kubernetes_asyncio'
        'bitmath',
    ],
    entry_points={
        'console_scripts': [
            '{command} = {name}.cli:main'.format(command=command, name=name)
        ],
    },
)

