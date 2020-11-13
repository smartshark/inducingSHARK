#!/usr/bin/env python

import sys

from setuptools import setup, find_packages

if not sys.version_info[0] == 3:
    print('only python3 supported!')
    sys.exit(1)

setup(
    name='inducingSHARK',
    version='1.1.0',
    description='Find bug-inducing commits.',
    install_requires=['pycoshark>=1.3.1', 'pygit2==0.26.2', 'networkx>=2.2'],
    author='atrautsch',
    author_email='alexander.trautsch@stud.uni-goettingen.de',
    url='https://github.com/smartshark/inducingSHARK',
    download_url='https://github.com/smartshark/inducingSHARK/zipball/master',
    test_suite='tests',
    packages=find_packages(),
    zip_safe=False,
    classifiers=[
        "Programming Language :: Python :: 3",
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache2.0 License",
        "Operating System :: POSIX :: Linux",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
